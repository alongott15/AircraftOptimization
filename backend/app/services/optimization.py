"""OptimizationService — compliance → scoring → CP-SAT, persistence, assignment (OPT §7, ARCHITECTURE §3.1)."""

from __future__ import annotations

import json
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.db import orm
from app.domain.compliance.consumption import mission_end, mission_window
from app.domain.compliance.evaluator import EligibilityResult, LimitProjection, evaluate_eligibility
from app.domain.models import Aircraft, MissionRequest, OperationalStatus
from app.domain.optimization.cpsat_selector import ForcedTailConflictError, SelectionCandidate, select
from app.domain.optimization.expiry_window import NO_WINDOWS, evaluate_windows
from app.domain.optimization.scoring import ScoredCandidate, ScoringInputs, score_candidate
from app.domain.policy import PlanningPolicy
from app.domain.reason_codes import Reason, WarningCode
from app.ml.provider import RiskInfo
from app.ml.utilization import Utilization
from app.repositories import store
from app.services.container import Container
from app.services.context import FleetContext, load_context
from app.services.errors import ServiceError
from app.services.fleet import MAX_FORECAST_DAYS, predicted_due
from app.services.missions import overrides_of, to_domain

RUN_MAX_AGE = timedelta(minutes=30)
HANGAR_HORIZON = timedelta(weeks=8)


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def iso_week(dt: datetime) -> str:
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _reasons(rs: tuple[Reason, ...]) -> list[dict[str, Any]]:
    return [r.to_dict() for r in rs]


def _projections(ps: tuple[LimitProjection, ...]) -> list[dict[str, object]]:
    return [p.to_dict() for p in ps]


def effective_policy(base: PlanningPolicy, overrides: dict[str, Any], preset: str | None) -> PlanningPolicy:
    """Admin policy + a mission's allowed choices. A mission can never loosen a safety buffer (BR-02/04)."""
    allowed = {k: v for k, v in overrides.items() if k in ("weights_preset", "duration_buffer_pct")}
    if allowed.get("duration_buffer_pct") is not None:
        allowed["duration_buffer_pct"] = max(float(allowed["duration_buffer_pct"]), base.duration_buffer_pct)
    policy = base.with_overrides(allowed)
    if preset:
        policy = policy.with_overrides({"weights_preset": preset})
    return policy


def _week_or_none(dt: datetime | None, now: datetime) -> str | None:
    if dt is None or dt > now + HANGAR_HORIZON:
        return None
    return iso_week(max(dt, now))


def _next_inspection_from_projections(r: EligibilityResult, now: datetime, rates: dict[str, float]
                                      ) -> tuple[datetime | None, datetime | None]:
    before: list[datetime] = []
    after: list[datetime] = []
    for p in r.projections:
        if p.source != "INSPECTION" or p.outcome == "MISSING":
            continue
        if p.due_at is not None:
            due = datetime.fromisoformat(p.due_at.replace("Z", "+00:00"))
            before.append(due)
            after.append(due)
        elif p.remaining_now is not None:
            rate = rates.get(p.unit, 0.0)
            if rate > 1e-6 and abs(p.remaining_now / rate) <= MAX_FORECAST_DAYS:
                before.append(now + timedelta(days=p.remaining_now / rate))
                after.append(now + timedelta(days=max(p.remaining_now - (p.consumption or 0.0), 0.0) / rate))
    return (min(before) if before else None, min(after) if after else None)


def hangar_base_load(fc: FleetContext, util: Utilization) -> dict[tuple[str, str], int]:
    load: dict[tuple[str, str], int] = defaultdict(int)
    for ac in fc.aircraft:
        if ac.status != OperationalStatus.OPERATIONAL:
            continue
        rates = dict(util.rates.get(ac.aircraft_id, {}))
        dates = [d for d in (predicted_due(i, fc.now, rates) for i in fc.status(ac).items
                             if i.source == "INSPECTION") if d is not None]
        week = _week_or_none(min(dates) if dates else None, fc.now)
        if week:
            load[(ac.base_code, week)] += 1
    return load


def evaluate_all(fc: FleetContext, mission: MissionRequest, policy: PlanningPolicy) -> list[EligibilityResult]:
    return [evaluate_eligibility(a, mission, policy, fc.eval_ctx) for a in fc.aircraft]


_MISMATCH = frozenset({"ENGINE_REQUIREMENT_NOT_MET", "ENDURANCE_EXCEEDED", "QUALIFICATION_MISSING"})


def _type_mismatch(cand: dict[str, Any]) -> bool:
    """Aircraft that can never fly this mission sort after near-misses in the excluded list."""
    return any(r["code"] in _MISMATCH for r in cand["reasons"])


def _candidate_dict(ac: Aircraft, r: EligibilityResult, sc: ScoredCandidate | None, rank: int | None,
                    risk: RiskInfo | None) -> dict[str, Any]:
    return {
        "aircraft_id": ac.aircraft_id, "tail_number": ac.tail_number, "type_code": ac.aircraft_type.type_code,
        "base_code": ac.base_code, "eligible": r.eligible, "score": sc.score if sc else None, "rank": rank,
        "score_breakdown": {k: v.to_dict() for k, v in sc.breakdown.items()} if sc else None,
        "reasons": _reasons(r.reasons), "warnings": _reasons(sc.warnings if sc else r.warnings),
        "unblock_hints": _reasons(r.unblock_hints), "projections": _projections(r.projections),
        "risk_probability": risk.probability if risk else None,
        "week_before": sc.inputs.week_before if sc else None, "week_after": sc.inputs.week_after if sc else None,
    }


def score_eligible(fc: FleetContext, eligible: list[EligibilityResult], mission: MissionRequest,
                   policy: PlanningPolicy, risks: dict[int, RiskInfo] | None, util: Utilization,
                   base_load: dict[tuple[str, str], int],
                   capacity: dict[tuple[str, str], int]) -> list[ScoredCandidate]:
    """Score eligible aircraft for one mission (OPT §3). Shared by single-mission and horizon planning."""
    by_id = fc.by_id()
    scored: list[ScoredCandidate] = []
    for r in eligible:
        ac = by_id[r.aircraft_id]
        rates = dict(util.rates.get(ac.aircraft_id, {}))
        before, after = _next_inspection_from_projections(r, fc.now, rates)
        wb, wa = _week_or_none(before, fc.now), _week_or_none(after, fc.now)
        mean, sd = util.type_fh30.get(ac.aircraft_type.type_code, (0.0, 0.0))
        load_after = base_load.get((ac.base_code, wa), 0) - (1 if wa and wa == wb else 0) if wa else 0
        windows = evaluate_windows(r, mission.expiry_windows, mission_end(mission.start_at, mission.duration_hours)
                                   ) if mission.expiry_windows else NO_WINDOWS
        inputs = ScoringInputs(
            windows=windows,
            type_code=ac.aircraft_type.type_code, base_code=ac.base_code,
            p_defect=(risks[ac.aircraft_id].probability if risks and ac.aircraft_id in risks else None),
            fh_last_30d=util.fh_last_30d.get(ac.aircraft_id, 0.0), type_mean_fh30=mean, type_sd_fh30=sd,
            week_before=wb, week_after=wa, hangar_load_after=max(load_after, 0),
            hangar_cap_after=capacity.get((ac.base_code, wa), 0) if wa else 0)
        scored.append(score_candidate(r, inputs, mission.departure_base_code, policy))
    return scored


def run_optimization(c: Container, s: Session, mission_row: orm.MissionRow, user_id: int,
                     preset: str | None) -> dict[str, Any]:
    t_start = time.perf_counter()
    base_policy = store.load_policy(s)
    overrides = overrides_of(mission_row)
    policy = effective_policy(base_policy, overrides, preset or overrides.get("weights_preset"))
    fc = load_context(c, s, policy)
    tails_by_id = {a.aircraft_id: a.tail_number for a in fc.aircraft}
    mission = to_domain(s, mission_row, tails_by_id)
    results = evaluate_all(fc, mission, policy)
    by_id = fc.by_id()

    forced_bad = [r for r in results if r.tail_number in mission.force_include and not r.eligible]
    if forced_bad:
        store.write_audit(s, at=fc.now, user_id=user_id, action="FORCE_INCLUDE_REJECTED", entity_type="mission",
                          entity_id=str(mission_row.mission_id),
                          details={"tails": [r.tail_number for r in forced_bad]})
        s.commit()
        first = forced_bad[0]
        raise ServiceError(422, "FORCE_INCLUDE_INELIGIBLE", "A forced aircraft is not eligible", extra={
            "params": {"tail_number": first.tail_number},
            "reasons": {r.tail_number: _reasons(r.reasons) for r in forced_bad}})

    eligible = [r for r in results if r.eligible]
    risks = c.ml.predict_risk(fc.aircraft, fc.now)
    util = c.ml.utilization(fc.aircraft, fc.now)
    base_load = hangar_base_load(fc, util)
    capacity = store.hangar_capacity(s)
    scored = score_eligible(fc, eligible, mission, policy, risks, util, base_load, capacity)

    forced_ids = frozenset(by_id_tail for by_id_tail in (a.aircraft_id for a in fc.aircraft)
                           if tails_by_id[by_id_tail] in mission.force_include)
    try:
        selection = select(
            [SelectionCandidate(sc.aircraft_id, sc.inputs.type_code, sc.inputs.base_code, sc.score,
                                sc.inputs.week_before, sc.inputs.week_after) for sc in scored],
            mission.required_count, force_include_ids=forced_ids, same_type=mission.same_type_required,
            base_load=base_load, capacity=capacity)
    except ForcedTailConflictError as exc:
        raise ServiceError(422, "FORCE_INCLUDE_INELIGIBLE", str(exc), extra={"params": {"tail_number": ""}}) from exc

    ranked = sorted(scored, key=lambda sc: (-sc.score, sc.aircraft_id))
    rank_of = {sc.aircraft_id: i + 1 for i, sc in enumerate(ranked)}
    scored_by_id = {sc.aircraft_id: sc for sc in scored}
    candidates = []
    for r in results:
        ac = by_id[r.aircraft_id]
        candidates.append(_candidate_dict(ac, r, scored_by_id.get(r.aircraft_id), rank_of.get(r.aircraft_id),
                                          risks.get(r.aircraft_id) if risks else None))
    candidates.sort(key=lambda x: (not x["eligible"], x["rank"] or 999, _type_mismatch(x), len(x["reasons"]),
                                   x["tail_number"]))
    nearest = []
    if selection.shortfall > 0 or not eligible:
        inel = sorted((r for r in results if not r.eligible), key=lambda r: (len(r.reasons), r.tail_number))
        nearest = [r.aircraft_id for r in inel[:3]]
    run_warnings = [] if risks is not None else [
        Reason(WarningCode.ML_MODEL_UNAVAILABLE, {"model_name": "defect_risk"}).to_dict()]
    run_id = str(uuid.uuid4())
    duration_ms = int((time.perf_counter() - t_start) * 1000)
    model_versions = c.ml.model_versions()
    data_snapshot_at = min([*fc.eval_ctx.data_load_status.values(), *(a.source_updated_at for a in fc.aircraft)],
                           default=fc.now)
    result = {
        "run_id": run_id, "mission_id": mission_row.mission_id, "requested_at": iso(fc.now),
        "clock_now": iso(fc.now), "solver_status": selection.status, "duration_ms": duration_ms,
        "weights_preset": policy.weights_preset, "model_versions": model_versions,
        "required": mission.required_count, "n_aircraft": len(results), "n_eligible": len(eligible),
        "shortfall": selection.shortfall,
        "sets": [{"set_index": st.set_index, "objective": st.objective, "aircraft_ids": list(st.aircraft_ids),
                  "tail_numbers": [tails_by_id[a] for a in st.aircraft_ids]} for st in selection.sets],
        "candidates": candidates, "nearest_to_eligible": nearest, "run_warnings": run_warnings,
        "policy": policy.to_dict(),
    }
    s.add(orm.OptimizationRunRow(
        run_id=run_id, mission_id=mission_row.mission_id, requested_by=user_id, requested_at=fc.now,
        clock_now=fc.now, data_snapshot_at=data_snapshot_at, policy_json=json.dumps(policy.to_dict()),
        weights_json=json.dumps(dict(policy.weights)), model_versions_json=json.dumps(model_versions),
        solver_status=selection.status, objective_value=selection.sets[0].objective if selection.sets else None,
        n_aircraft=len(results), n_eligible=len(eligible), shortfall=selection.shortfall, duration_ms=duration_ms,
        result_json=json.dumps(result, ensure_ascii=False)))
    s.flush()
    for st in selection.sets:
        s.add(orm.OptimizationSetRow(run_id=run_id, set_index=st.set_index, objective_value=st.objective,
                                     aircraft_ids=json.dumps(list(st.aircraft_ids))))
    for cand in candidates:
        s.add(orm.OptimizationCandidateRow(
            run_id=run_id, aircraft_id=cand["aircraft_id"], eligible=cand["eligible"], score=cand["score"],
            rank=cand["rank"], score_breakdown_json=json.dumps(cand["score_breakdown"]),
            reasons_json=json.dumps(cand["reasons"], ensure_ascii=False),
            warnings_json=json.dumps(cand["warnings"], ensure_ascii=False),
            unblock_json=json.dumps(cand["unblock_hints"], ensure_ascii=False),
            projections_json=json.dumps(cand["projections"])))
    if mission_row.status in ("DRAFT", "OPTIMIZED"):
        mission_row.status = "OPTIMIZED"
    mission_row.updated_at = fc.now
    store.write_audit(s, at=fc.now, user_id=user_id, action="OPTIMIZE", entity_type="mission",
                      entity_id=str(mission_row.mission_id),
                      details={"run_id": run_id, "n_eligible": len(eligible), "solver_status": selection.status})
    return result


def run_dict(row: orm.OptimizationRunRow) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(row.result_json)
    return data


def assign(c: Container, s: Session, mission_row: orm.MissionRow, run_id: str, set_index: int,
           user_id: int) -> list[dict[str, Any]]:
    with c.assign_lock:  # re-validation and write must not interleave with another assignment
        out = _assign(c, s, mission_row, run_id, set_index, user_id)
        s.commit()
    return out


def _assign(c: Container, s: Session, mission_row: orm.MissionRow, run_id: str, set_index: int,
            user_id: int) -> list[dict[str, Any]]:
    run = store.get_run(s, run_id)
    if run is None or run.mission_id != mission_row.mission_id:
        raise ServiceError(404, "NOT_FOUND", "run not found for this mission")
    if mission_row.status == "CANCELLED":
        raise ServiceError(409, "MISSION_NOT_EDITABLE", "mission is cancelled")
    now = c.clock.now()
    if now - run.clock_now > RUN_MAX_AGE:
        raise ServiceError(409, "RUN_STALE", "optimization run is older than 30 minutes")
    sets = {st["set_index"]: st for st in run_dict(run)["sets"]}
    if set_index not in sets:
        raise ServiceError(422, "VALIDATION_ERROR", "unknown set", errors=[
            {"field": "set_index", "code": "INVALID_VALUE", "params": {}}])
    chosen_ids = list(sets[set_index]["aircraft_ids"])
    # S4: re-validate eligibility against current data before committing.
    policy = effective_policy(store.load_policy(s), overrides_of(mission_row), None)
    fc = load_context(c, s, policy)
    tails_by_id = {a.aircraft_id: a.tail_number for a in fc.aircraft}
    mission = to_domain(s, mission_row, tails_by_id)
    by_id = fc.by_id()
    changed = {}
    for aid in chosen_ids:
        r = evaluate_eligibility(by_id[aid], mission, policy, fc.eval_ctx)
        if not r.eligible:
            changed[r.tail_number] = _reasons(r.reasons)
    if changed:
        raise ServiceError(409, "ELIGIBILITY_CHANGED", "eligibility changed since the run", extra={"reasons": changed})
    for old in store.mission_assignments(s, mission_row.mission_id):
        old.status = "CANCELLED"
    w_start, w_end = mission_window(mission, policy)
    out = []
    for aid in chosen_ids:
        s.add(orm.MissionAssignmentRow(mission_id=mission_row.mission_id, aircraft_id=aid, run_id=run_id,
                                       set_index=set_index, window_start=w_start, window_end=w_end, status="ACTIVE",
                                       assigned_by=user_id, assigned_at=now))
        out.append({"aircraft_id": aid, "tail_number": tails_by_id[aid], "window_start": iso(w_start),
                    "window_end": iso(w_end), "status": "ACTIVE", "run_id": run_id, "set_index": set_index})
    mission_row.status, mission_row.updated_at = "ASSIGNED", now
    store.write_audit(s, at=now, user_id=user_id, action="ASSIGN", entity_type="mission",
                      entity_id=str(mission_row.mission_id),
                      details={"run_id": run_id, "set_index": set_index, "tails": [o["tail_number"] for o in out]})
    return out


def eligibility_check(c: Container, s: Session, mission: MissionRequest, tails: list[str] | None,
                      overrides: dict[str, Any]) -> list[dict[str, Any]]:
    policy = effective_policy(store.load_policy(s), overrides, None)
    fc = load_context(c, s, policy)
    wanted = set(tails) if tails else None
    out = []
    for ac in fc.aircraft:
        if wanted is not None and ac.tail_number not in wanted:
            continue
        r = evaluate_eligibility(ac, mission, policy, fc.eval_ctx)
        out.append(_candidate_dict(ac, r, None, None, None))
    return out


def assignments_at_risk(s: Session, fc: FleetContext) -> list[dict[str, Any]]:
    """Future assigned missions whose tail is no longer eligible (S4 re-check, shown as dashboard alerts).

    Catches anything that changed after assignment: new defects or data, a cancelled/unassigned earlier mission whose
    flight was resetting the idle clock, a re-planned mission that now consumes more of a shared limit.
    """
    base = store.load_policy(s)
    by_id = fc.by_id()
    tails = {a.aircraft_id: a.tail_number for a in fc.aircraft}
    out: list[dict[str, Any]] = []
    for row, aircraft_ids in store.future_assignments(s, fc.now):
        mission = to_domain(s, row, tails)
        policy = effective_policy(base, overrides_of(row), None)
        for aid in aircraft_ids:
            ac = by_id.get(aid)
            r = evaluate_eligibility(ac, mission, policy, fc.eval_ctx) if ac else None
            if r is None or not r.eligible:
                out.append({"aircraft_id": aid, "tail_number": tails.get(aid, str(aid)), "source": "ASSIGNMENT",
                            "ref": row.name, "unit": None, "status": "EXPIRED", "remaining": None,
                            "due_at": iso(row.start_at), "missing_kind": None, "missing_code": None})
    return out
