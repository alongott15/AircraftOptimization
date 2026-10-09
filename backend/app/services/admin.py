"""Thin services for auth, reference, policy, hangar, audit, runs and ML admin (keeps the API off repositories)."""

from __future__ import annotations

import json
import math
import re
import threading
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import orm
from app.domain.models import KNOWN_RESTRICTIONS, EngineRequirement, MissionType
from app.domain.policy import normalize_weights
from app.domain.reason_codes import ReasonCode, UnblockCode, WarningCode
from app.repositories import store
from app.security.auth import SessionUser, hash_password, verify_password
from app.security.rbac import ROLES
from app.services.container import Container
from app.services.context import FleetContext
from app.services.errors import ServiceError, not_found


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


# ── users / auth ───────────────────────────────────────────────────────
def _session_user(row: orm.AppUserRow) -> SessionUser:
    return SessionUser(row.user_id, row.username, row.display_name, row.role)


def active_user(s: Session, user_id: int) -> SessionUser | None:
    row = store.get_user(s, user_id)
    return _session_user(row) if row and row.is_active else None


def authenticate(s: Session, username: str, password: str) -> SessionUser | None:
    row = store.get_user_by_username(s, username)
    if row is None or not row.is_active or row.oidc_subject or not verify_password(row.password_hash, password):
        return None
    return _session_user(row)


USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,49}")
MIN_PASSWORD_LENGTH = 12


def _check_password(username: str, password: str) -> None:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ServiceError(422, "VALIDATION_ERROR", f"password must have at least {MIN_PASSWORD_LENGTH} characters")
    if password.lower() == username.lower():
        raise ServiceError(422, "VALIDATION_ERROR", "password must differ from the username")


def create_local_user(s: Session, username: str, display_name: str, role: str, password: str,
                      now: datetime) -> SessionUser:
    """POC local account (APP_AUTH_MODE=local). Audited as USER_CREATE; the password is never logged."""
    if not USERNAME_RE.fullmatch(username):
        raise ServiceError(422, "VALIDATION_ERROR", "username: 3-50 chars, lowercase letters, digits, . _ -")
    if role not in ROLES:
        raise ServiceError(422, "VALIDATION_ERROR", f"role must be one of {', '.join(ROLES)}")
    if store.get_user_by_username(s, username) is not None:
        raise ServiceError(409, "CONFLICT", "username already exists")
    _check_password(username, password)
    row = orm.AppUserRow(username=username, display_name=(display_name or username)[:100],
                         password_hash=hash_password(password), role=role, is_active=True, created_at=now)
    s.add(row)
    s.flush()
    store.write_audit(s, at=now, user_id=None, action="USER_CREATE", entity_type="user", entity_id=str(row.user_id),
                      details={"username": username, "role": role, "via": "cli"})
    return _session_user(row)


def set_local_password(s: Session, username: str, password: str, now: datetime) -> None:
    """Reset a local account's password (never an SSO-linked one). Audited as USER_PASSWORD_SET."""
    row = store.get_user_by_username(s, username)
    if row is None:
        raise not_found("user")
    if row.oidc_subject:
        raise ServiceError(409, "CONFLICT", "SSO users have no local password")
    _check_password(username, password)
    row.password_hash = hash_password(password)
    s.flush()
    store.write_audit(s, at=now, user_id=None, action="USER_PASSWORD_SET", entity_type="user",
                      entity_id=str(row.user_id), details={"username": username, "via": "cli"})


def upsert_sso_user(s: Session, subject: str, username: str, display_name: str, role: str,
                    now: datetime) -> SessionUser:
    """SSO users are keyed by issuer+subject (never by username) and never linked to local password accounts.

    The role is refreshed from the IdP on every login; disabled users are rejected before anything is changed.
    """
    row = s.scalars(select(orm.AppUserRow).where(orm.AppUserRow.oidc_subject == subject)).first()
    if row is not None:
        if not row.is_active:
            raise ServiceError(403, "FORBIDDEN", "user disabled")
        row.display_name, row.role = display_name, role
    else:
        if store.get_user_by_username(s, username) is not None:
            raise ServiceError(403, "FORBIDDEN", "username already used by another account")
        row = orm.AppUserRow(username=username, display_name=display_name, password_hash=None, oidc_subject=subject,
                             role=role, is_active=True, created_at=now)
        s.add(row)
    s.flush()
    return _session_user(row)


def user_dict(u: SessionUser) -> dict[str, Any]:
    return {"user_id": u.user_id, "username": u.username, "display_name": u.display_name, "role": u.role}


def db_ok(s: Session) -> bool:
    try:
        s.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# ── reference ──────────────────────────────────────────────────────────
def reference(c: Container, s: Session) -> dict[str, Any]:
    ref = c.ds.load_reference()
    ref.update({
        "restrictions": sorted(KNOWN_RESTRICTIONS),
        "weight_presets": store.load_weights(s),
        "mission_types": [str(m) for m in MissionType],
        "engine_requirements": [str(e) for e in EngineRequirement],
        "codes": {"reasons": [str(x) for x in ReasonCode], "warnings": [str(x) for x in WarningCode],
                  "unblock": [str(x) for x in UnblockCode]},
    })
    return ref


# ── audit ──────────────────────────────────────────────────────────────
def audit(s: Session, **kwargs: Any) -> None:
    store.write_audit(s, **kwargs)


def audit_page(s: Session, *, page: int, page_size: int, user_id: int | None, action: str | None,
               date_from: datetime | None, date_to: datetime | None) -> dict[str, Any]:
    rows, total = store.list_audit(s, page=page, page_size=page_size, user_id=user_id, action=action,
                                   date_from=date_from, date_to=date_to)
    users = {u.user_id: u.username for u in s.query(orm.AppUserRow).all()}
    items = [{"audit_id": r.audit_id, "at": iso(r.at), "user_id": r.user_id, "username": users.get(r.user_id or -1),
              "action": r.action, "entity_type": r.entity_type, "entity_id": r.entity_id,
              "details": json.loads(r.details_json) if r.details_json else None, "request_id": r.request_id}
             for r in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# ── missions / runs ────────────────────────────────────────────────────
def list_missions(s: Session, **kwargs: Any) -> tuple[list[orm.MissionRow], int]:
    return store.list_missions(s, **kwargs)


def run_summaries(s: Session, mission_id: int) -> list[dict[str, Any]]:
    return [{"run_id": r.run_id, "requested_at": iso(r.requested_at), "solver_status": r.solver_status,
             "n_eligible": r.n_eligible, "shortfall": r.shortfall, "duration_ms": r.duration_ms}
            for r in store.list_runs(s, mission_id)]


def run_detail(s: Session, run_id: str) -> dict[str, Any]:
    row = store.get_run(s, run_id)
    if row is None:
        raise not_found("optimization run")
    data: dict[str, Any] = json.loads(row.result_json)
    return data


def unassign(s: Session, m: orm.MissionRow, now: datetime) -> None:
    for a in store.mission_assignments(s, m.mission_id):
        a.status = "CANCELLED"
    if m.status == "ASSIGNED":
        m.status = "OPTIMIZED"
    m.updated_at = now


# ── policy / hangar ────────────────────────────────────────────────────
def policy(s: Session) -> dict[str, Any]:
    return store.load_policy(s).to_dict()


def update_policy(s: Session, values: dict[str, Any], user_id: int, now: datetime) -> dict[str, Any]:
    store.save_policy(s, values, user_id, now)
    s.flush()
    return policy(s)


def weights(s: Session) -> dict[str, dict[str, float]]:
    return store.load_weights(s)


def update_weights(s: Session, presets: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    try:
        clean = {code: normalize_weights(w) for code, w in presets.items()}
    except ValueError as exc:
        raise ServiceError(422, "VALIDATION_ERROR", str(exc), errors=[
            {"field": "weights", "code": "INVALID_VALUE", "params": {}}]) from exc
    store.save_weights(s, clean)
    s.flush()
    return weights(s)


TG_UNITS = ("LDG", "CYC")


def tg_weights(c: Container, s: Session) -> list[dict[str, Any]]:
    """Every LDG/CYC limit with its reference weight (lake) and the admin override, if any."""
    overrides = store.tg_overrides(s)
    out = []
    for it in c.ds.load_reference()["inspection_types"]:
        for lim in it["limits"]:
            reference = lim["tg_weight"]
            if lim["unit"] not in TG_UNITS or reference is None:  # unreadable lake weight ⇒ DATA_MISSING already
                continue
            key = (lim["type_code"], it["inspection_type_code"], lim["unit"])
            override = overrides.get(key)
            effective = reference if override is None or not math.isfinite(override) else max(reference, override)
            out.append({"type_code": key[0], "inspection_type_code": key[1], "unit": key[2],
                        "reference_tg_weight": reference, "override_tg_weight": override,
                        "effective_tg_weight": effective})
    return sorted(out, key=lambda r: (r["type_code"], r["inspection_type_code"], r["unit"]))


def update_tg_weights(c: Container, s: Session, rows: list[tuple[str, str, str, float | None]], user_id: int,
                      now: datetime) -> list[dict[str, Any]]:
    reference = {(r["type_code"], r["inspection_type_code"], r["unit"]): r["reference_tg_weight"]
                 for r in tg_weights(c, s)}
    unknown = [r for r in rows if (r[0], r[1], r[2]) not in reference]
    if unknown:
        raise ServiceError(422, "VALIDATION_ERROR", "unknown limit", errors=[
            {"field": "limit", "code": "INVALID_VALUE", "params": {"limit": f"{u[0]}/{u[1]}/{u[2]}"}}
            for u in unknown])
    too_low = [r for r in rows if r[3] is not None and r[3] < reference[(r[0], r[1], r[2])]]
    if too_low:  # a lower weight would under-project against the maintenance system's due value
        raise ServiceError(422, "VALIDATION_ERROR", "override below the reference weight", errors=[
            {"field": "tg_weight", "code": "VALUE_TOO_SMALL", "params": {"min": reference[(r[0], r[1], r[2])]}}
            for r in too_low])
    store.save_tg_overrides(s, rows, user_id, now)
    s.flush()
    return tg_weights(c, s)


def hangar(s: Session, base_code: str | None) -> list[dict[str, Any]]:
    rows = [{"base_code": b, "iso_week": w, "slots": n} for (b, w), n in sorted(store.hangar_capacity(s).items())]
    return [r for r in rows if base_code is None or r["base_code"] == base_code]


def save_hangar(s: Session, rows: list[tuple[str, str, int]]) -> None:
    store.save_hangar_capacity(s, rows)


# ── flights ────────────────────────────────────────────────────────────
def flights_for(c: Container, fc: FleetContext, tail: str) -> list[dict[str, Any]]:
    ac = fc.by_tail().get(tail)
    if ac is None:
        raise not_found("aircraft")
    flights = [f for f in c.ds.flights_since(fc.now - timedelta(days=90)) if f.aircraft_id == ac.aircraft_id]
    flights.sort(key=lambda f: f.takeoff_at, reverse=True)
    return [{"takeoff_at": iso(f.takeoff_at), "landing_at": iso(f.landing_at), "mission_type": f.mission_type,
             "flight_hours": f.flight_hours, "landings": f.landings, "touch_and_goes": f.touch_and_goes,
             "engine_starts": f.engine_starts} for f in flights]


# ── ML admin ───────────────────────────────────────────────────────────
def models(s: Session) -> list[dict[str, Any]]:
    return [{"model_name": r.model_name, "version": r.version, "status": r.status, "trained_at": iso(r.trained_at),
             "metrics": json.loads(r.metrics_json)} for r in store.list_models(s)]


_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()


def start_retrain(c: Container, background: BackgroundTasks, model_name: str) -> str:
    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        _JOBS[job_id] = {"job_id": job_id, "model_name": model_name, "status": "RUNNING", "result": None}

    def _run() -> None:
        from pathlib import Path

        from app.db.session import session_scope
        from app.ml.registry import save_model
        from app.ml.train import build_history, train_defect_risk, train_duration_buffer

        try:
            now = c.clock.now()
            load = c.ds.load_snapshot().data_load_status
            if load:
                now = min(now, max(load.values()))
            history = build_history(c.ds, now)
            trainer = train_defect_risk if model_name == "defect_risk" else train_duration_buffer
            payload, metrics, data_hash, card = trainer(history, now)
            with session_scope(c.settings.database_url) as s:
                row = save_model(s, Path(c.settings.models_dir), model_name, payload, metrics, data_hash, now, card)
                version = row.version
            c.ml.invalidate()
            result = {"status": "DONE", "result": {"version": version, "metrics": metrics}}
        except Exception as exc:
            result = {"status": "FAILED", "result": {"error": type(exc).__name__}}
        with _JOBS_LOCK:
            _JOBS[job_id].update(result)

    background.add_task(_run)
    return job_id


def job_status(job_id: str) -> dict[str, Any]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise ServiceError(404, "NOT_FOUND", "job not found")
    return dict(job)
