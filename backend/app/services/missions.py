"""Mission CRUD and conversion to the domain MissionRequest."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db import orm
from app.domain.models import WINDOW_UNITS, EngineRequirement, ExpiryWindow, MissionRequest, MissionType, Unit
from app.repositories import store
from app.services.errors import ServiceError, not_found, validation

EDITABLE = ("DRAFT", "OPTIMIZED")


_log = logging.getLogger(__name__)


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _resolve_tails(tails: list[str], by_tail: dict[str, int], field: str) -> list[int]:
    ids = []
    for t in tails:
        if t not in by_tail:
            raise validation(field, "UNKNOWN_TAIL", tail_number=t)
        ids.append(by_tail[t])
    return ids


def validate_codes(payload: dict[str, Any], reference: dict[str, Any]) -> None:
    quals = {q["qualification_code"] for q in reference["qualifications"]}
    for q in payload["required_qualifications"]:
        if q not in quals:
            raise validation("required_qualifications", "UNKNOWN_QUALIFICATION", qualification_code=q)
    if payload["departure_base_code"] not in {b["base_code"] for b in reference["bases"]}:
        raise validation("departure_base_code", "UNKNOWN_BASE")
    squadrons = {q["squadron_code"] for q in reference.get("squadrons", [])}
    for code in payload.get("squadron_codes") or []:
        if code not in squadrons:
            raise validation("squadron_codes", "UNKNOWN_SQUADRON", squadron_code=code)
    types = {t["type_code"] for t in reference["aircraft_types"]}
    for code in payload.get("aircraft_type_codes") or []:
        if code not in types:
            raise validation("aircraft_type_codes", "UNKNOWN_TYPE", type_code=code)


def upsert_mission(s: Session, payload: dict[str, Any], *, user_id: int, now: datetime, by_tail: dict[str, int],
                   reference: dict[str, Any], mission: orm.MissionRow | None = None) -> orm.MissionRow:
    if payload["start_at"] < now - timedelta(minutes=5):
        raise validation("start_at", "START_IN_PAST")
    validate_codes(payload, reference)
    include = _resolve_tails(payload.get("force_include", []), by_tail, "force_include")
    exclude = _resolve_tails(payload.get("force_exclude", []), by_tail, "force_exclude")
    if set(include) & set(exclude):
        raise validation("force_include", "TAIL_FORCED_BOTH_WAYS")
    if len(include) > payload["required_aircraft_count"]:
        raise validation("force_include", "VALUE_TOO_LARGE", max=payload["required_aircraft_count"])
    overrides = {k: v for k, v in (payload.get("overrides") or {}).items() if v is not None}
    windows = [{"unit": w["unit"], "within": float(w["within"]), "until": float(w["until"])}
               for w in payload.get("expiry_windows") or []]
    if windows:
        overrides[WINDOWS_KEY] = windows
    fields = {
        "name": payload["name"].strip(), "mission_type": payload["mission_type"],
        "required_aircraft_count": payload["required_aircraft_count"], "start_at": payload["start_at"],
        "est_duration_hours": payload["est_duration_hours"], "est_landings": payload["est_landings"],
        "est_touch_and_goes": payload["est_touch_and_goes"], "est_engine_starts": payload["est_engine_starts"],
        "departure_base_code": payload["departure_base_code"], "engine_requirement": payload["engine_requirement"],
        "same_type_required": payload["same_type_required"], "priority": payload["priority"],
        "notes": payload.get("notes"), "overrides_json": json.dumps(overrides) if overrides else None,
    }
    if mission is None:
        mission = orm.MissionRow(status="DRAFT", created_by=user_id, created_at=now, updated_at=now, **fields)
        s.add(mission)
        s.flush()
    else:
        if mission.status not in EDITABLE:
            raise ServiceError(409, "MISSION_NOT_EDITABLE", "Only draft/optimized missions can be edited")
        for k, v in fields.items():
            setattr(mission, k, v)
        mission.status, mission.updated_at = "DRAFT", now
    store.replace_mission_children(s, mission.mission_id, payload["required_qualifications"],
                                   [(i, "INCLUDE") for i in include] + [(i, "EXCLUDE") for i in exclude])
    store.replace_mission_scope(s, mission.mission_id, payload.get("squadron_codes") or [],
                                payload.get("aircraft_type_codes") or [])
    s.flush()
    return mission


def get_mission_or_404(s: Session, mission_id: int) -> orm.MissionRow:
    m = store.get_mission(s, mission_id)
    if m is None:
        raise not_found("mission")
    return m


WINDOWS_KEY = "expiry_windows"  # stored in mission.overrides_json (planner preferences live together)


# Older missions may still store calendar/landing buffer overrides; they are ignored (admin policy applies).
MISSION_OVERRIDE_KEYS = ("duration_buffer_pct", "weights_preset")


def overrides_of(m: orm.MissionRow) -> dict[str, Any]:
    """The mission's allowed planner choices (FH buffer suggestion, preset) — bands are returned separately."""
    raw = json.loads(m.overrides_json) if m.overrides_json else {}
    return {k: v for k, v in raw.items() if k in MISSION_OVERRIDE_KEYS} if isinstance(raw, dict) else {}


def _parse_window(w: Any) -> ExpiryWindow | None:
    try:
        window = ExpiryWindow(unit=Unit(w["unit"]), within=float(w["within"]), until=float(w["until"]))
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    ok = math.isfinite(window.within) and math.isfinite(window.until) and 0 <= window.within < window.until
    return window if ok and window.unit in WINDOW_UNITS else None


def _windows(m: orm.MissionRow) -> list[ExpiryWindow]:
    """Stored bands; an unreadable entry is skipped and logged — windows only affect ranking, and this is also read
    on the safety re-check paths (assign, horizon apply, at-risk alerts), which must never fail because of it."""
    raw = json.loads(m.overrides_json) if m.overrides_json else {}
    stored = raw.get(WINDOWS_KEY) if isinstance(raw, dict) else None
    out = []
    for w in stored if isinstance(stored, list) else []:
        parsed = _parse_window(w)
        if parsed is None:
            _log.warning("mission.expiry_window.unreadable", extra={"mission_id": m.mission_id})
        else:
            out.append(parsed)
    return out


def windows_of(m: orm.MissionRow) -> list[dict[str, Any]]:
    return [{"unit": str(w.unit), "within": w.within, "until": w.until} for w in _windows(m)]


def _domain_windows(m: orm.MissionRow) -> tuple[ExpiryWindow, ...]:
    return tuple(_windows(m))


def to_domain(s: Session, m: orm.MissionRow, tails_by_id: dict[int, str]) -> MissionRequest:
    forces = store.mission_forces(s, m.mission_id)
    squadrons, types = store.mission_scope(s, m.mission_id)
    return MissionRequest(
        name=m.name, mission_type=MissionType(m.mission_type), required_count=m.required_aircraft_count,
        start_at=m.start_at, duration_hours=m.est_duration_hours, landings=m.est_landings,
        touch_and_goes=m.est_touch_and_goes, engine_starts=m.est_engine_starts,
        departure_base_code=m.departure_base_code,
        required_qualifications=frozenset(store.mission_qualifications(s, m.mission_id)),
        engine_requirement=EngineRequirement(m.engine_requirement), same_type_required=m.same_type_required,
        force_include=frozenset(tails_by_id[f.aircraft_id] for f in forces if f.mode == "INCLUDE"),
        force_exclude=frozenset(tails_by_id[f.aircraft_id] for f in forces if f.mode == "EXCLUDE"),
        mission_id=m.mission_id, expiry_windows=_domain_windows(m),
        squadron_codes=frozenset(squadrons), type_codes=frozenset(types))


def mission_out(s: Session, m: orm.MissionRow, tails_by_id: dict[int, str]) -> dict[str, Any]:
    forces = store.mission_forces(s, m.mission_id)
    scope = store.mission_scope(s, m.mission_id)
    latest = store.latest_run(s, m.mission_id)
    return {
        "mission_id": m.mission_id, "name": m.name, "mission_type": m.mission_type, "status": m.status,
        "required_aircraft_count": m.required_aircraft_count, "start_at": iso(m.start_at),
        "end_at": iso(m.start_at + timedelta(hours=m.est_duration_hours)),
        "est_duration_hours": m.est_duration_hours, "est_landings": m.est_landings,
        "est_touch_and_goes": m.est_touch_and_goes, "est_engine_starts": m.est_engine_starts,
        "departure_base_code": m.departure_base_code,
        "required_qualifications": store.mission_qualifications(s, m.mission_id),
        "engine_requirement": m.engine_requirement, "same_type_required": m.same_type_required,
        "priority": m.priority, "notes": m.notes,
        "force_include": sorted(tails_by_id[f.aircraft_id] for f in forces if f.mode == "INCLUDE"),
        "force_exclude": sorted(tails_by_id[f.aircraft_id] for f in forces if f.mode == "EXCLUDE"),
        "overrides": overrides_of(m), "expiry_windows": windows_of(m),
        "squadron_codes": scope[0], "aircraft_type_codes": scope[1],
        "created_by": m.created_by, "created_at": iso(m.created_at),
        "updated_at": iso(m.updated_at), "latest_run_id": latest.run_id if latest else None,
        "assignments": [
            {"aircraft_id": a.aircraft_id, "tail_number": tails_by_id.get(a.aircraft_id), "window_start":
             iso(a.window_start), "window_end": iso(a.window_end), "status": a.status, "run_id": a.run_id,
             "set_index": a.set_index}
            for a in store.mission_assignments(s, m.mission_id)],
    }


def duplicate(s: Session, m: orm.MissionRow, user_id: int, now: datetime) -> orm.MissionRow:
    copy = orm.MissionRow(**{c.name: getattr(m, c.name) for c in orm.MissionRow.__table__.columns
                             if c.name not in ("mission_id",)})
    copy.name = f"{m.name} (2)"[:120]
    copy.status, copy.created_by, copy.created_at, copy.updated_at = "DRAFT", user_id, now, now
    s.add(copy)
    s.flush()
    forces = store.mission_forces(s, m.mission_id)
    store.replace_mission_children(s, copy.mission_id, store.mission_qualifications(s, m.mission_id),
                                   [(f.aircraft_id, f.mode) for f in forces])
    store.replace_mission_scope(s, copy.mission_id, *store.mission_scope(s, m.mission_id))
    return copy


def cancel(s: Session, m: orm.MissionRow, now: datetime) -> None:
    for a in store.mission_assignments(s, m.mission_id):
        a.status = "CANCELLED"
    m.status, m.updated_at = "CANCELLED", now
