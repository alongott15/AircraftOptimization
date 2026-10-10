"""Maintenance board, manual scheduling and mission-driven recommendations (FR-90…FR-92).

The board lists the work each aircraft needs (domain/maintenance.py) merged with the manual schedule kept in the app
store. Planning only: a card is "verified" when the source data shows the work done — moving it to DONE never makes
an aircraft eligible (S1, golden rule 7).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db import orm
from app.domain.compliance.evaluator import evaluate_eligibility
from app.domain.maintenance import (
    Requirement,
    TaskAction,
    aircraft_requirements,
    recommend,
    requirement_for,
    task_done,
    task_key,
)
from app.domain.models import Aircraft
from app.repositories import store
from app.services.container import Container
from app.services.context import FleetContext, load_context
from app.services.errors import ServiceError, not_found, validation
from app.services.missions import overrides_of, to_domain
from app.services.optimization import effective_policy

OPEN_STATUSES = ("DRAFT", "OPTIMIZED")
RECOMMEND_DAYS = 14
MAX_MISSIONS = 50  # recommendations re-run the engine per mission × aircraft — bound the work per request
PLAN_PAST, PLAN_FUTURE = timedelta(days=365), timedelta(days=730)  # accepted planning window around now
VERIFIED_FOR = timedelta(days=14)  # verified cards stay on the board this long after their last update
SCHEDULED_STATUSES = ("SCHEDULED", "IN_PROGRESS")


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


# ── recommendations (FR-92) ────────────────────────────────────────────
def mission_recommendations(s: Session, fc: FleetContext, *, mission_id: int | None = None,
                            days: int = RECOMMEND_DAYS) -> list[dict[str, Any]]:
    """For each mission: shortfall and the excluded aircraft that maintenance would make eligible (verified)."""
    base = store.load_policy(s)
    if mission_id is not None:
        row = store.get_mission(s, mission_id)
        if row is None:
            raise not_found("mission")
        rows = [row]
    else:
        rows = store.open_missions_between(s, OPEN_STATUSES, fc.now, fc.now + timedelta(days=days))
        rows = sorted(rows, key=lambda r: (r.start_at, r.mission_id))[:MAX_MISSIONS]
    tails = {a.aircraft_id: a.tail_number for a in fc.aircraft}
    by_id = fc.by_id()
    out: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: (r.start_at, r.mission_id)):
        policy = effective_policy(base, overrides_of(row), None)
        mission = to_domain(s, row, tails)
        results = [evaluate_eligibility(a, mission, policy, fc.eval_ctx) for a in fc.aircraft]
        rec = recommend(fc.aircraft, results, mission, policy, fc.eval_ctx)
        out.append({
            "mission_id": row.mission_id, "name": row.name, "start_at": iso(row.start_at),
            "required": row.required_aircraft_count, "eligible_count": rec.eligible_count,
            "shortfall": rec.shortfall, "complete_by": iso(rec.complete_by),
            "candidates": [{
                "aircraft_id": cand.aircraft_id, "tail_number": cand.tail_number,
                "type_code": by_id[cand.aircraft_id].aircraft_type.type_code,
                "base_code": by_id[cand.aircraft_id].base_code,
                "tasks": [{"item_key": task_key(cand.aircraft_id, a, r), "action": str(a), "ref": r}
                          for a, r in cand.tasks],
            } for cand in rec.candidates],
        })
    return out


# ── board (FR-90) ──────────────────────────────────────────────────────
@dataclass
class _Entry:
    ac: Aircraft
    req: Requirement
    plan: orm.MaintenancePlanRow | None


Unblocks = dict[str, list[dict[str, Any]]]


def _active(s: Session, fc: FleetContext
            ) -> tuple[dict[str, _Entry], list[tuple[Aircraft, orm.MaintenancePlanRow]], Unblocks]:
    """Open tasks (with the plan of their current cycle), plans of earlier cycles whose work the source data shows
    done, and missions each task unblocks. A plan only ever attaches to the cycle it was made for (BR-16)."""
    unblocks: Unblocks = defaultdict(list)
    for rec in mission_recommendations(s, fc):
        if rec["shortfall"] <= 0:
            continue  # recommendations only matter where a mission is short of aircraft
        for cand in rec["candidates"]:
            for task in cand["tasks"]:
                unblocks[task["item_key"]].append(
                    {"mission_id": rec["mission_id"], "name": rec["name"], "start_at": rec["start_at"]})
    by_id = fc.by_id()
    active: dict[str, _Entry] = {}
    for ac in fc.aircraft:
        st = fc.status(ac)
        for req in aircraft_requirements(ac, st):
            active[req.key] = _Entry(ac, req, None)
    for key in unblocks:  # needed by a mission although not yet due by the fleet thresholds
        if key not in active:
            aid, action, ref = key.split(":", 2)
            ac = by_id[int(aid)]
            active[key] = _Entry(ac, requirement_for(ac, fc.status(ac), TaskAction(action), ref), None)
    verified: list[tuple[Aircraft, orm.MaintenancePlanRow]] = []
    for plan in store.maintenance_plans(s):
        ac_or_none = by_id.get(plan.aircraft_id)
        if ac_or_none is None:
            continue
        ac = ac_or_none
        st = fc.status(ac)
        action = TaskAction(plan.action)
        entry = active.get(plan.item_key)
        current = entry.req if entry else requirement_for(ac, st, action, plan.ref)
        if current.fingerprint == plan.fingerprint and not task_done(ac, st, action, plan.ref, plan.fingerprint):
            if entry is None:  # planned ahead of the fleet thresholds; open until the source shows it done
                entry = active[plan.item_key] = _Entry(ac, current, None)
            entry.plan = plan
        elif (task_done(ac, st, action, plan.ref, plan.fingerprint) and plan.status != "REQUIRED"
              and plan.updated_at >= fc.now - VERIFIED_FOR):
            verified.append((ac, plan))
        # otherwise: the plan of an earlier cycle — kept as history, never shown on the current card
    return active, verified, unblocks


def _matches(ac: Aircraft, base_code: str | None, squadron_code: str | None, type_code: str | None) -> bool:
    return ((base_code is None or ac.base_code == base_code)
            and (squadron_code is None or ac.squadron_code == squadron_code)
            and (type_code is None or ac.aircraft_type.type_code == type_code))


def _aircraft_fields(ac: Aircraft) -> dict[str, Any]:
    return {"aircraft_id": ac.aircraft_id, "tail_number": ac.tail_number, "type_code": ac.aircraft_type.type_code,
            "base_code": ac.base_code, "squadron_code": ac.squadron_code}


def _plan_fields(plan: orm.MaintenancePlanRow | None) -> dict[str, Any]:
    return {"planned_start": iso(plan.planned_start) if plan else None,
            "planned_end": iso(plan.planned_end) if plan else None,
            "assignee": plan.assignee if plan else None, "notes": plan.notes if plan else None,
            "updated_at": iso(plan.updated_at) if plan else None}


def _item(e: _Entry, unblocks: list[dict[str, Any]]) -> dict[str, Any]:
    r = e.req
    return {"card_id": r.key, "item_key": r.key, **_aircraft_fields(e.ac), "action": str(r.action), "ref": r.ref,
            "compliance_status": str(r.status), "blocking": r.blocking, "due_at": iso(r.due_at),
            "remaining": r.remaining, "unit": r.unit, "zone_code": r.zone_code,
            "status": e.plan.status if e.plan else "REQUIRED", **_plan_fields(e.plan), "unblocks": unblocks}


def _verified_item(fc: FleetContext, ac: Aircraft, plan: orm.MaintenancePlanRow) -> dict[str, Any]:
    """A plan of an earlier cycle whose work the source data shows done — with the task's *current* status."""
    now = requirement_for(ac, fc.status(ac), TaskAction(plan.action), plan.ref)
    return {"card_id": f"{plan.item_key}#{plan.plan_id}", "item_key": plan.item_key, **_aircraft_fields(ac),
            "action": plan.action, "ref": plan.ref, "compliance_status": str(now.status), "blocking": False,
            "due_at": iso(now.due_at), "remaining": now.remaining, "unit": now.unit, "zone_code": now.zone_code,
            "status": "VERIFIED", **_plan_fields(plan), "unblocks": []}


_ORDER = {"REQUIRED": 0, "SCHEDULED": 1, "IN_PROGRESS": 2, "DONE": 3, "VERIFIED": 4}
_SEVERITY = {"EXPIRED": 0, "UNKNOWN": 1, "EXPIRING_SOON": 2, "OK": 3}


def board(c: Container, s: Session, *, base_code: str | None = None, squadron_code: str | None = None,
          type_code: str | None = None) -> dict[str, Any]:
    fc = load_context(c, s)
    active, verified, unblocks = _active(s, fc)
    items = [_item(e, unblocks.get(key, [])) for key, e in active.items()
             if _matches(e.ac, base_code, squadron_code, type_code)]
    items += [_verified_item(fc, ac, plan) for ac, plan in verified
              if _matches(ac, base_code, squadron_code, type_code)]
    items.sort(key=lambda i: (_ORDER[i["status"]], not i["blocking"], not i["unblocks"],
                              _SEVERITY.get(i["compliance_status"], 9), i["planned_start"] or "",
                              i["due_at"] or "9999", i["card_id"]))
    return {"as_of": iso(fc.now), "items": items}


# ── manual scheduling (FR-91) ──────────────────────────────────────────
def update_task(c: Container, s: Session, *, item_key: str, status: str, planned_start: datetime | None,
                planned_end: datetime | None, assignee: str | None, notes: str | None,
                user_id: int) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Save the schedule of an open task; returns (board item, plan before the change) for the audit trail."""
    if status in SCHEDULED_STATUSES and planned_start is None:
        raise validation("planned_start", "FIELD_REQUIRED")
    if planned_start and planned_end and planned_end < planned_start:
        raise validation("planned_end", "INVALID_VALUE")
    fc = load_context(c, s)
    for name, value in (("planned_start", planned_start), ("planned_end", planned_end)):
        if value is not None and not fc.now - PLAN_PAST <= value <= fc.now + PLAN_FUTURE:
            raise validation(name, "INVALID_VALUE")
    active, _, unblocks = _active(s, fc)
    entry = active.get(item_key)
    if entry is None:
        raise ServiceError(404, "NOT_FOUND", "task not found or already done in the source data")
    before = None if entry.plan is None else {"status": entry.plan.status, **_plan_fields(entry.plan)}
    plan = store.plan_for_cycle(s, item_key=item_key, fingerprint=entry.req.fingerprint,  # this cycle's row only
                                aircraft_id=entry.ac.aircraft_id, tail_number=entry.ac.tail_number,
                                action=str(entry.req.action), ref=entry.req.ref, now=fc.now)
    plan.status, plan.planned_start, plan.planned_end = status, planned_start, planned_end
    plan.assignee = assignee.strip() if assignee and assignee.strip() else None
    plan.notes = notes.strip() if notes and notes.strip() else None
    plan.updated_by, plan.updated_at = user_id, fc.now
    s.flush()
    entry.plan = plan
    return _item(entry, unblocks.get(item_key, [])), before
