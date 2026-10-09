"""Horizon planning service: plan several open missions together, then apply after re-validation (FR-46)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db import orm
from app.domain.compliance.consumption import FlightLoad, consumption, mission_end, mission_window
from app.domain.compliance.evaluator import EligibilityResult, evaluate_eligibility
from app.domain.compliance.items import ItemKind, collect_items
from app.domain.models import ItemSource, MissionRequest
from app.domain.optimization.horizon import (
    HorizonMission,
    HorizonOption,
    LimitKey,
    check_plan,
    plan_horizon,
)
from app.domain.optimization.scoring import ScoredCandidate
from app.domain.policy import PlanningPolicy
from app.repositories import store
from app.services.container import Container
from app.services.context import FleetContext, load_context
from app.services.errors import ServiceError, not_found
from app.services.missions import overrides_of, to_domain
from app.services.optimization import effective_policy, hangar_base_load, score_eligible

OPEN_STATUSES = ("DRAFT", "OPTIMIZED")
DEFAULT_HORIZON = timedelta(days=14)
COUNTER_SOURCES = (ItemSource.INSPECTION, ItemSource.PERMIT, ItemSource.DEFERRAL)


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


@dataclass
class HorizonModel:
    fc: FleetContext
    rows: list[orm.MissionRow]
    missions: dict[int, MissionRequest]
    policies: dict[int, PlanningPolicy]
    results: dict[int, list[EligibilityResult]]
    scored: dict[int, list[ScoredCandidate]] = field(default_factory=dict)
    overlaps: frozenset[tuple[int, int]] = frozenset()
    room: dict[tuple[int, LimitKey], float] = field(default_factory=dict)
    use: dict[tuple[int, int, LimitKey], float] = field(default_factory=dict)

    @property
    def eligible_pairs(self) -> frozenset[tuple[int, int]]:
        return frozenset((r.aircraft_id, mid) for mid, rs in self.results.items() for r in rs if r.eligible)


def _select_missions(s: Session, now: datetime, mission_ids: list[int] | None, date_from: datetime | None,
                     date_to: datetime | None) -> list[orm.MissionRow]:
    if mission_ids:
        rows = []
        for mid in sorted(set(mission_ids)):
            row = store.get_mission(s, mid)
            if row is None:
                raise not_found("mission")
            if row.status not in OPEN_STATUSES:
                raise ServiceError(409, "MISSION_NOT_EDITABLE", f"mission {mid} is not open for planning")
            rows.append(row)
        return rows
    start = date_from or now
    end = date_to or start + DEFAULT_HORIZON
    return store.open_missions_between(s, OPEN_STATUSES, start, end)


def _build(c: Container, s: Session, rows: list[orm.MissionRow], preset: str | None) -> HorizonModel:
    base_policy = store.load_policy(s)
    fc = load_context(c, s, base_policy)
    tails = {a.aircraft_id: a.tail_number for a in fc.aircraft}
    model = HorizonModel(fc=fc, rows=rows, missions={}, policies={}, results={})
    planned = {r.mission_id for r in rows}
    for row in rows:
        policy = effective_policy(base_policy, overrides_of(row), preset or overrides_of(row).get("weights_preset"))
        mission = to_domain(s, row, tails)
        model.missions[row.mission_id] = mission
        model.policies[row.mission_id] = policy
        model.results[row.mission_id] = [evaluate_eligibility(a, mission, policy, fc.eval_ctx) for a in fc.aircraft]
    # overlaps between planned missions (window = buffered duration + turnaround)
    windows = {mid: mission_window(m, model.policies[mid]) for mid, m in model.missions.items()}
    ids = sorted(windows)
    model.overlaps = frozenset(
        (a, b) for i, a in enumerate(ids) for b in ids[i + 1:]
        if windows[a][0] < windows[b][1] and windows[b][0] < windows[a][1])
    # cumulative usage model per counter limit
    for ac in fc.aircraft:
        engines = ac.aircraft_type.engine_count
        existing = [cm for cm in ac.commitments if cm.mission_id not in planned]
        for item in collect_items(ac, fc.eval_ctx):
            if (item.kind != ItemKind.COUNTER or item.is_missing or not item.blocking
                    or item.source not in COUNTER_SOURCES or item.due_value is None or item.current is None):
                continue
            key = f"{item.source}:{item.ref}:{item.unit}"
            # flights after the counter snapshot are not in `current` yet (BR-02) — same rule as committed_since
            since = min(item.as_of or fc.now, fc.now)
            committed = sum(consumption(item.unit, FlightLoad.of_commitment(cm), base_policy, engines, item.tg_weight)
                            for cm in existing if cm.window_end > since)
            model.room[(ac.aircraft_id, key)] = item.due_value - item.current - committed
            for mid, mission in model.missions.items():
                model.use[(ac.aircraft_id, mid, key)] = consumption(
                    item.unit, FlightLoad.of_mission(mission), model.policies[mid], engines, item.tg_weight)
    return model


def _reject_ineligible_forced(model: HorizonModel) -> None:
    """OPT §5.3 — a forced tail that is not eligible is an error, never silently dropped."""
    bad: dict[str, Any] = {}
    first: tuple[int, str] | None = None
    for mid in sorted(model.missions):
        results = {r.tail_number: r for r in model.results[mid]}
        for tail in sorted(model.missions[mid].force_include):
            r = results.get(tail)
            if r is None or not r.eligible:
                bad.setdefault(str(mid), {})[tail] = [x.to_dict() for x in r.reasons] if r else []
                first = first or (mid, tail)
    if first is not None:
        raise ServiceError(422, "FORCE_INCLUDE_INELIGIBLE", "A forced aircraft is not eligible", extra={
            "params": {"tail_number": first[1], "mission_id": first[0]}, "reasons": bad})


def plan(c: Container, s: Session, *, user_id: int, mission_ids: list[int] | None, date_from: datetime | None,
         date_to: datetime | None, preset: str | None) -> dict[str, Any]:
    started = time.perf_counter()
    rows = _select_missions(s, c.clock.now(), mission_ids, date_from, date_to)
    model = _build(c, s, rows, preset)
    fc = model.fc
    _reject_ineligible_forced(model)
    risks = c.ml.predict_risk(fc.aircraft, fc.now)
    util = c.ml.utilization(fc.aircraft, fc.now)
    base_load = hangar_base_load(fc, util)
    capacity = store.hangar_capacity(s)
    by_id = fc.by_id()
    tail_ids = {a.tail_number: a.aircraft_id for a in fc.aircraft}
    options: list[HorizonOption] = []
    scores: dict[tuple[int, int], float] = {}
    for mid, mission in model.missions.items():
        eligible = [r for r in model.results[mid] if r.eligible]
        for sc in score_eligible(fc, eligible, mission, model.policies[mid], risks, util, base_load, capacity):
            options.append(HorizonOption(sc.aircraft_id, mid, sc.score, sc.inputs.type_code))
            scores[(sc.aircraft_id, mid)] = sc.score
    hmissions = [HorizonMission(mid, m.required_count, m.same_type_required,
                                frozenset(tail_ids[t] for t in m.force_include if t in tail_ids))
                 for mid, m in model.missions.items()]
    result = plan_horizon(hmissions, options, model.overlaps, model.room, model.use)
    out_missions: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda r: (r.start_at, r.mission_id)):
        mid = row.mission_id
        assigned = result.assignments.get(mid, ())
        out_missions.append({
            "mission_id": mid, "name": row.name, "start_at": iso(row.start_at),
            "end_at": iso(mission_end(row.start_at, row.est_duration_hours)),
            "required": row.required_aircraft_count,
            "eligible_count": sum(1 for r in model.results[mid] if r.eligible),
            "assigned": [{"aircraft_id": aid, "tail_number": by_id[aid].tail_number, "score": scores.get((aid, mid))}
                         for aid in assigned],
            "shortfall": result.shortfall.get(mid, row.required_aircraft_count),
            "overlaps_with": sorted(b if a == mid else a for a, b in model.overlaps if mid in (a, b)),
        })
    payload = {
        "solver_status": result.status, "objective": result.objective,
        "duration_ms": int((time.perf_counter() - started) * 1000), "clock_now": iso(fc.now),
        "missions": out_missions,
        "assignments": [{"mission_id": m["mission_id"], "aircraft_ids": [a["aircraft_id"] for a in m["assigned"]]}
                        for m in out_missions],
    }
    store.write_audit(s, at=fc.now, user_id=user_id, action="HORIZON_PLAN", entity_type="mission",
                      entity_id=None, details={"missions": [r.mission_id for r in rows], "status": result.status})
    return payload


def apply(c: Container, s: Session, *, user_id: int, assignments: list[tuple[int, list[int]]]) -> dict[str, Any]:
    """Re-validate the whole plan against current data (S4) and commit it atomically."""
    with c.assign_lock:
        result = _apply(c, s, user_id=user_id, assignments=assignments)
        s.commit()
    return result


def _apply(c: Container, s: Session, *, user_id: int, assignments: list[tuple[int, list[int]]]) -> dict[str, Any]:
    rows = _select_missions(s, c.clock.now(), [mid for mid, _ in assignments], None, None)
    model = _build(c, s, rows, None)
    plan_map = dict(assignments)
    required = {r.mission_id: r.required_aircraft_count for r in rows}
    violations = check_plan(plan_map, required, model.eligible_pairs, model.overlaps, model.room, model.use)
    if violations:
        raise ServiceError(409, "PLAN_CONFLICT", "plan is no longer valid", extra={
            "violations": [{"code": v.code, "mission_id": v.mission_id, "aircraft_id": v.aircraft_id,
                            "detail": v.detail} for v in violations]})
    now = c.clock.now()
    by_id = model.fc.by_id()
    applied = []
    for row in rows:
        for old in store.mission_assignments(s, row.mission_id):
            old.status = "CANCELLED"
        w_start, w_end = mission_window(model.missions[row.mission_id], model.policies[row.mission_id])
        for aid in sorted(set(plan_map.get(row.mission_id, []))):
            s.add(orm.MissionAssignmentRow(mission_id=row.mission_id, aircraft_id=aid, run_id=None, set_index=None,
                                           window_start=w_start, window_end=w_end, status="ACTIVE",
                                           assigned_by=user_id, assigned_at=now))
        if plan_map.get(row.mission_id):
            row.status, row.updated_at = "ASSIGNED", now
        applied.append({"mission_id": row.mission_id,
                        "tail_numbers": [by_id[a].tail_number for a in sorted(set(plan_map.get(row.mission_id, [])))]})
    store.write_audit(s, at=now, user_id=user_id, action="HORIZON_ASSIGN", entity_type="mission", entity_id=None,
                      details={"applied": applied})
    return {"applied": applied}
