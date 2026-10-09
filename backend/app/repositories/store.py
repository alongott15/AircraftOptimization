"""App-store repositories (missions, runs, assignments, policy, users, audit, hangar, ML registry).

Queries only — no business logic. Returns ORM rows or plain domain values.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import orm
from app.domain.models import CommittedMission
from app.domain.policy import WEIGHT_PRESETS, PlanningPolicy, normalize_weights

_log = logging.getLogger(__name__)


# ── policy ──────────────────────────────────────────────────────────────
def load_policy(s: Session) -> PlanningPolicy:
    values = {r.policy_key: json.loads(r.value_json) for r in s.scalars(select(orm.PlanningPolicyRow))}
    return replace(PlanningPolicy().with_overrides(values), preset_weights=load_weights(s))


def load_weights(s: Session) -> dict[str, dict[str, float]]:
    """Preset weights from the app store; an unreadable row falls back to the built-in preset (ranking only)."""
    out = {code: dict(w) for code, w in WEIGHT_PRESETS.items()}
    for row in s.scalars(select(orm.WeightPresetRow)):
        try:
            out[row.preset_code] = normalize_weights(json.loads(row.weights_json))
        except (ValueError, TypeError):
            _log.warning("weight_preset.unreadable", extra={"preset_code": row.preset_code})  # built-in used
    return out


def save_weights(s: Session, presets: dict[str, dict[str, float]]) -> None:
    for code, weights in presets.items():
        row = s.get(orm.WeightPresetRow, code)
        if row is None:
            s.add(orm.WeightPresetRow(preset_code=code, weights_json=json.dumps(weights)))
        else:
            row.weights_json = json.dumps(weights)


def tg_overrides(s: Session) -> dict[tuple[str, str, str], float]:
    """(type_code, inspection_type_code, unit) → admin-set touch-and-go weight (FR-60)."""
    return {(r.type_code, r.inspection_type_code, r.unit_code): r.tg_weight
            for r in s.scalars(select(orm.TgWeightOverrideRow))}


def save_tg_overrides(s: Session, rows: list[tuple[str, str, str, float | None]], user_id: int,
                      now: datetime) -> None:
    for type_code, code, unit, weight in rows:
        row = s.get(orm.TgWeightOverrideRow, (type_code, code, unit))
        if weight is None:
            if row is not None:
                s.delete(row)
        elif row is None:
            s.add(orm.TgWeightOverrideRow(type_code=type_code, inspection_type_code=code, unit_code=unit,
                                          tg_weight=weight, updated_by=user_id, updated_at=now))
        else:
            row.tg_weight, row.updated_by, row.updated_at = weight, user_id, now


def save_policy(s: Session, values: dict[str, Any], user_id: int, now: datetime) -> None:
    for key, value in values.items():
        row = s.get(orm.PlanningPolicyRow, key)
        if row is None:
            s.add(orm.PlanningPolicyRow(policy_key=key, value_json=json.dumps(value), updated_by=user_id,
                                        updated_at=now))
        else:
            row.value_json, row.updated_by, row.updated_at = json.dumps(value), user_id, now


# ── users ───────────────────────────────────────────────────────────────
def get_user_by_username(s: Session, username: str) -> orm.AppUserRow | None:
    return s.scalars(select(orm.AppUserRow).where(orm.AppUserRow.username == username)).first()


def get_user(s: Session, user_id: int) -> orm.AppUserRow | None:
    return s.get(orm.AppUserRow, user_id)


# ── audit (append-only) ─────────────────────────────────────────────────
def write_audit(s: Session, *, at: datetime, user_id: int | None, action: str, entity_type: str | None = None,
                entity_id: str | None = None, details: dict[str, Any] | None = None,
                request_id: str | None = None, client_ip: str | None = None) -> None:
    s.add(orm.AuditLogRow(at=at, user_id=user_id, action=action, entity_type=entity_type, entity_id=entity_id,
                          details_json=json.dumps(details, ensure_ascii=False) if details else None,
                          request_id=request_id, client_ip=client_ip))


def list_audit(s: Session, *, page: int, page_size: int, user_id: int | None, action: str | None,
               date_from: datetime | None, date_to: datetime | None) -> tuple[list[orm.AuditLogRow], int]:
    q = select(orm.AuditLogRow)
    if user_id is not None:
        q = q.where(orm.AuditLogRow.user_id == user_id)
    if action:
        q = q.where(orm.AuditLogRow.action == action)
    if date_from:
        q = q.where(orm.AuditLogRow.at >= date_from)
    if date_to:
        q = q.where(orm.AuditLogRow.at <= date_to)
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = s.scalars(q.order_by(orm.AuditLogRow.audit_id.desc()).offset((page - 1) * page_size).limit(page_size))
    return list(rows), total


# ── missions ────────────────────────────────────────────────────────────
def get_mission(s: Session, mission_id: int) -> orm.MissionRow | None:
    return s.get(orm.MissionRow, mission_id)


def mission_qualifications(s: Session, mission_id: int) -> list[str]:
    rows = s.scalars(select(orm.MissionQualificationRow.qualification_code)
                     .where(orm.MissionQualificationRow.mission_id == mission_id))
    return sorted(rows)


def mission_forces(s: Session, mission_id: int) -> list[orm.MissionForceRow]:
    return list(s.scalars(select(orm.MissionForceRow).where(orm.MissionForceRow.mission_id == mission_id)))


def list_missions(s: Session, *, page: int, page_size: int, status: str | None,
                  date_from: datetime | None, date_to: datetime | None) -> tuple[list[orm.MissionRow], int]:
    q = select(orm.MissionRow)
    if status:
        q = q.where(orm.MissionRow.status == status)
    if date_from:
        q = q.where(orm.MissionRow.start_at >= date_from)
    if date_to:
        q = q.where(orm.MissionRow.start_at <= date_to)
    total = s.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = s.scalars(q.order_by(orm.MissionRow.start_at.desc(), orm.MissionRow.mission_id.desc())
                     .offset((page - 1) * page_size).limit(page_size))
    return list(rows), total


def open_missions_between(s: Session, statuses: tuple[str, ...], start: datetime, end: datetime
                          ) -> list[orm.MissionRow]:
    q = (select(orm.MissionRow).where(orm.MissionRow.status.in_(statuses), orm.MissionRow.start_at >= start,
                                      orm.MissionRow.start_at <= end)
         .order_by(orm.MissionRow.start_at, orm.MissionRow.mission_id))
    return list(s.scalars(q))


def replace_mission_children(s: Session, mission_id: int, qualifications: list[str],
                             forces: list[tuple[int, str]]) -> None:
    for r in s.scalars(select(orm.MissionQualificationRow).where(orm.MissionQualificationRow.mission_id == mission_id)):
        s.delete(r)
    for f in mission_forces(s, mission_id):
        s.delete(f)
    s.flush()
    for q in sorted(set(qualifications)):
        s.add(orm.MissionQualificationRow(mission_id=mission_id, qualification_code=q))
    for aircraft_id, mode in forces:
        s.add(orm.MissionForceRow(mission_id=mission_id, aircraft_id=aircraft_id, mode=mode))


# ── assignments ─────────────────────────────────────────────────────────
def active_commitments(s: Session) -> dict[int, list[CommittedMission]]:
    """aircraft_id → committed missions (ACTIVE assignments of non-cancelled missions)."""
    q = (select(orm.MissionAssignmentRow, orm.MissionRow)
         .join(orm.MissionRow, orm.MissionRow.mission_id == orm.MissionAssignmentRow.mission_id)
         .where(orm.MissionAssignmentRow.status == "ACTIVE", orm.MissionRow.status != "CANCELLED"))
    out: dict[int, list[CommittedMission]] = defaultdict(list)
    for a, m in s.execute(q).all():
        out[a.aircraft_id].append(CommittedMission(
            mission_id=m.mission_id, mission_name=m.name, start_at=m.start_at, duration_hours=m.est_duration_hours,
            landings=m.est_landings, touch_and_goes=m.est_touch_and_goes, engine_starts=m.est_engine_starts,
            window_start=a.window_start, window_end=a.window_end))
    return out


def future_assignments(s: Session, now: datetime) -> list[tuple[orm.MissionRow, list[int]]]:
    """Missions that start after `now` with their ACTIVE assigned aircraft ids."""
    q = (select(orm.MissionRow, orm.MissionAssignmentRow.aircraft_id)
         .join(orm.MissionAssignmentRow, orm.MissionRow.mission_id == orm.MissionAssignmentRow.mission_id)
         .where(orm.MissionAssignmentRow.status == "ACTIVE", orm.MissionRow.status != "CANCELLED",
                orm.MissionRow.start_at > now)
         .order_by(orm.MissionRow.start_at, orm.MissionRow.mission_id, orm.MissionAssignmentRow.aircraft_id))
    grouped: dict[int, tuple[orm.MissionRow, list[int]]] = {}
    for m, aircraft_id in s.execute(q).all():
        grouped.setdefault(m.mission_id, (m, []))[1].append(aircraft_id)
    return list(grouped.values())


def mission_assignments(s: Session, mission_id: int, active_only: bool = True) -> list[orm.MissionAssignmentRow]:
    q = select(orm.MissionAssignmentRow).where(orm.MissionAssignmentRow.mission_id == mission_id)
    if active_only:
        q = q.where(orm.MissionAssignmentRow.status == "ACTIVE")
    return list(s.scalars(q.order_by(orm.MissionAssignmentRow.aircraft_id)))


# ── optimization runs ───────────────────────────────────────────────────
def get_run(s: Session, run_id: str) -> orm.OptimizationRunRow | None:
    return s.get(orm.OptimizationRunRow, run_id)


def list_runs(s: Session, mission_id: int) -> list[orm.OptimizationRunRow]:
    return list(s.scalars(select(orm.OptimizationRunRow).where(orm.OptimizationRunRow.mission_id == mission_id)
                          .order_by(orm.OptimizationRunRow.requested_at.desc())))


def latest_run(s: Session, mission_id: int) -> orm.OptimizationRunRow | None:
    runs = list_runs(s, mission_id)
    return runs[0] if runs else None


# ── hangar ──────────────────────────────────────────────────────────────
def hangar_capacity(s: Session) -> dict[tuple[str, str], int]:
    return {(r.base_code, r.iso_week): r.slots for r in s.scalars(select(orm.HangarCapacityRow))}


def save_hangar_capacity(s: Session, rows: list[tuple[str, str, int]]) -> None:
    for base, week, slots in rows:
        row = s.get(orm.HangarCapacityRow, (base, week))
        if row is None:
            s.add(orm.HangarCapacityRow(base_code=base, iso_week=week, slots=slots))
        else:
            row.slots = slots


# ── ML registry ─────────────────────────────────────────────────────────
def active_model(s: Session, model_name: str) -> orm.MlModelRow | None:
    return s.scalars(select(orm.MlModelRow).where(orm.MlModelRow.model_name == model_name,
                                                  orm.MlModelRow.status == "ACTIVE")
                     .order_by(orm.MlModelRow.trained_at.desc())).first()


def list_models(s: Session) -> list[orm.MlModelRow]:
    return list(s.scalars(select(orm.MlModelRow).order_by(orm.MlModelRow.model_name,
                                                          orm.MlModelRow.trained_at.desc())))


def register_model(s: Session, row: orm.MlModelRow) -> None:
    for old in s.scalars(select(orm.MlModelRow).where(orm.MlModelRow.model_name == row.model_name,
                                                      orm.MlModelRow.status == "ACTIVE")):
        old.status = "ARCHIVED"
    s.add(row)
