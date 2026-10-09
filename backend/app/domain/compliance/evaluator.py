"""evaluate_eligibility — rules R1…R13 (OPTIMIZATION_ENGINE.md §2.5).

Invariants: every rule is evaluated (BR-15), missing data is never OK (S1), ML is never consulted (S2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from app.domain.compliance.consumption import (
    FlightLoad,
    buffered_hours,
    calendar_deadline,
    consumption,
    mission_end,
    mission_window,
)
from app.domain.compliance.items import (
    DAY,
    ItemKind,
    Outcome,
    Projection,
    collect_items,
    project,
    stale_sources,
)
from app.domain.compliance.qualifications import qualification_reasons
from app.domain.compliance.unblock import unblock_hints
from app.domain.models import (
    KNOWN_RESTRICTIONS,
    Aircraft,
    CommittedMission,
    EngineRequirement,
    EvaluationContext,
    ItemSource,
    MissionRequest,
    MissionType,
    OperationalStatus,
    Severity,
    Unit,
)
from app.domain.numeric import lt, r2
from app.domain.policy import PlanningPolicy
from app.domain.reason_codes import Reason, ReasonCode, WarningCode, dedupe


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class LimitProjection:
    source: str
    ref: str
    unit: str
    interval: float | None
    remaining_now: float | None
    consumption: float | None
    remaining_after: float | None
    due_at: str | None
    outcome: str

    def to_dict(self) -> dict[str, object]:
        return {
            "source": self.source, "ref": self.ref, "unit": self.unit, "interval": self.interval,
            "remaining_now": None if self.remaining_now is None else r2(self.remaining_now),
            "consumption": None if self.consumption is None else r2(self.consumption),
            "remaining_after": None if self.remaining_after is None else r2(self.remaining_after),
            "due_at": self.due_at, "outcome": self.outcome,
        }


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    aircraft_id: int
    tail_number: str
    eligible: bool
    reasons: tuple[Reason, ...]
    warnings: tuple[Reason, ...]
    projections: tuple[LimitProjection, ...]
    unblock_hints: tuple[Reason, ...] = field(default_factory=tuple)


def engine_ok(requirement: EngineRequirement, engine_count: int) -> bool:
    if requirement == EngineRequirement.SINGLE:
        return engine_count == 1
    if requirement == EngineRequirement.TWIN:
        return engine_count == 2
    if requirement == EngineRequirement.MIN_2:
        return engine_count >= 2
    return True


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def flight_end(c: CommittedMission) -> datetime:
    return c.start_at + timedelta(hours=c.duration_hours)


def _others(ac: Aircraft, m: MissionRequest) -> list[CommittedMission]:
    return [c for c in sorted(ac.commitments, key=lambda c: (c.start_at, c.mission_id)) if c.mission_id != m.mission_id]


def committed_since(commitments: list[CommittedMission], since: datetime, until: datetime) -> list[CommittedMission]:
    """Assigned flights that may not be in a counter snapshot taken at `since` and that finish by `until` (BR-02).

    A flight that ended before `now` but after the snapshot is in neither the counters nor the future plan, so it
    is counted here. The snapshot test uses the assignment window end (buffered duration + turnaround), so a flight
    that lands late or is logged late is still counted; a flight overlapping the snapshot is counted in full.
    """
    return [c for c in commitments if c.window_end > since and flight_end(c) <= until]


def commitment_policy(mission_policy: PlanningPolicy, base: PlanningPolicy | None) -> PlanningPolicy:
    """Other flights are costed with the larger of the base and this mission's buffers (never smaller)."""
    if base is None:
        return mission_policy
    return replace(
        mission_policy,
        duration_buffer_pct=max(mission_policy.duration_buffer_pct, base.duration_buffer_pct),
        min_duration_buffer_hours=max(mission_policy.min_duration_buffer_hours, base.min_duration_buffer_hours),
        landing_buffer=max(mission_policy.landing_buffer, base.landing_buffer),
    )


def committed_use(commitments: list[CommittedMission], unit: Unit, tg_weight: float, policy: PlanningPolicy,
                  engine_count: int) -> float:
    """Per-flight consumption (buffers apply to every flight, not to the sum)."""
    return sum(consumption(unit, FlightLoad.of_commitment(c), policy, engine_count, tg_weight) for c in commitments)


def _item_reason(p: Projection, mission_end_at: datetime, required: float | None) -> Reason | None:
    item = p.item
    if p.outcome == Outcome.MISSING:
        return Reason(ReasonCode.DATA_MISSING, {"item_kind": item.missing_kind, "item_code": item.missing_code})
    if p.outcome == Outcome.OK or not item.blocking or item.source in (ItemSource.QUALIFICATION, ItemSource.IDLE):
        return None
    expired = p.outcome == Outcome.EXPIRED
    if item.source == ItemSource.INSPECTION:
        code = ReasonCode.INSPECTION_EXPIRED if expired else ReasonCode.INSPECTION_DUE_BEFORE_MISSION_END
        key = "inspection_code"
    elif item.source == ItemSource.PERMIT:
        code = ReasonCode.PERMIT_EXPIRED if expired else ReasonCode.PERMIT_EXPIRES_BEFORE_MISSION_END
        key = "permit_type_code"
    else:
        code = ReasonCode.DEFERRAL_EXPIRED if expired else ReasonCode.DEFERRAL_EXPIRES_BEFORE_MISSION_END
        key = "ma_number"
    params: dict[str, str | int | float | None] = {key: item.ref, "unit": str(item.unit)}
    if item.kind == ItemKind.CALENDAR:
        assert item.due_at is not None
        if expired:
            params["expired_at" if item.source != ItemSource.INSPECTION else "due_at"] = _iso(item.due_at)
        else:
            params["due_at" if item.source == ItemSource.INSPECTION else "expires_at"] = _iso(item.due_at)
            params["mission_end"] = _iso(mission_end_at)
    elif expired:
        assert p.remaining_now is not None
        params["over_by"] = r2(-p.remaining_now)
    else:
        assert p.remaining_now is not None
        params["remaining"] = r2(p.remaining_now)
        params["required"] = r2(required if required is not None else 0.0)
    return Reason(code, params)


def evaluate_eligibility(
    ac: Aircraft, m: MissionRequest, policy: PlanningPolicy, ctx: EvaluationContext
) -> EligibilityResult:
    now = ctx.now
    engines = ac.aircraft_type.engine_count
    load = FlightLoad.of_mission(m)
    deadline = calendar_deadline(m.start_at, m.duration_hours, policy)
    end_at = mission_end(m.start_at, m.duration_hours)
    reasons: list[Reason] = []
    warnings: list[Reason] = []

    # R1 operational status
    if ac.status != OperationalStatus.OPERATIONAL:
        reasons.append(Reason(ReasonCode.AIRCRAFT_NOT_OPERATIONAL, {"status": str(ac.status)}))
    # R2 overlapping assignments (window includes buffer + turnaround)
    w_start, w_end = mission_window(m, policy)
    for c in sorted(ac.commitments, key=lambda c: (c.window_start, c.mission_id)):
        if c.mission_id != m.mission_id and _overlaps(w_start, w_end, c.window_start, c.window_end):
            reasons.append(Reason(ReasonCode.ALREADY_ASSIGNED, {
                "mission_id": c.mission_id, "mission_name": c.mission_name,
                "window_start": _iso(c.window_start), "window_end": _iso(c.window_end)}))
    # R3 engines
    if not engine_ok(m.engine_requirement, engines):
        reasons.append(Reason(ReasonCode.ENGINE_REQUIREMENT_NOT_MET,
                              {"required": str(m.engine_requirement), "actual": engines}))
    # R4 endurance (an unreadable endurance is missing data, never "enough")
    needed = buffered_hours(m.duration_hours, policy)
    if not math.isfinite(ac.aircraft_type.max_endurance_hours):
        reasons.append(Reason(ReasonCode.DATA_MISSING,
                              {"item_kind": "AIRCRAFT_TYPE", "item_code": ac.aircraft_type.type_code}))
    elif lt(ac.aircraft_type.max_endurance_hours, needed):
        reasons.append(Reason(ReasonCode.ENDURANCE_EXCEEDED,
                              {"required_hours": r2(needed), "max_hours": r2(ac.aircraft_type.max_endurance_hours)}))
    # R5 grounding MAs
    grounding = [ma for ma in ac.open_mas if ma.severity == Severity.GROUNDING]
    for ma in grounding:
        reasons.append(Reason(ReasonCode.GROUNDING_MA_OPEN, {"ma_number": ma.ma_number, "zone_code": ma.zone_code}))

    # R6–R8, R10, R11 items
    for kind, code in ac.data_issues:
        reasons.append(Reason(ReasonCode.DATA_MISSING, {"item_kind": kind, "item_code": code}))
    items = collect_items(ac, ctx)
    cpol = commitment_policy(policy, ctx.commitment_policy)
    others = _others(ac, m)
    later = [c for c in others if c.start_at >= m.start_at]
    upcoming = [c for c in others if c.start_at >= now and flight_end(c) <= m.start_at]
    idle_reference = ac.last_flight_at
    if upcoming:
        last_committed_end = max(flight_end(c) for c in upcoming)
        idle_reference = max(idle_reference, last_committed_end) if idle_reference else last_committed_end
    projections: list[Projection] = []
    failing: list[Projection] = []
    idle_failed = False
    for item in items:
        before = after = 0.0
        if item.kind == ItemKind.COUNTER and not item.is_missing:
            # never trust a snapshot time later than now (future as_of is also reported as DATA_STALE)
            since = min(item.as_of or now, now)
            before = committed_use(committed_since(others, since, m.start_at), item.unit, item.tg_weight, cpol,
                                   engines)
            after = committed_use(later, item.unit, item.tg_weight, cpol, engines)
        p = project(item, now=now, load=load, committed_use=before, engine_count=engines, policy=policy,
                    start_at=m.start_at, deadline=deadline, idle_reference=idle_reference)
        p = replace(p, later=after)
        projections.append(p)
        if item.source == ItemSource.IDLE and p.outcome in (Outcome.EXPIRED, Outcome.DUE_BEFORE_END):
            idle_days = r2((m.start_at - idle_reference) / DAY) if idle_reference else None
            idle_params = {"idle_days": idle_days, "limit_days": r2(item.interval or 0)}
            if policy.idle_policy == "WARN":
                warnings.append(Reason(WarningCode.IDLE_CHECK_REQUIRED, idle_params))
            else:
                reasons.append(Reason(ReasonCode.IDLE_LIMIT_EXCEEDED, idle_params))
                idle_failed = True
            continue
        if item.source == ItemSource.QUALIFICATION:
            continue  # handled by R9 for required qualifications only
        required = None
        if p.consumption is not None:
            required = p.consumption
        reason = _item_reason(p, end_at, required)
        if reason is not None:
            reasons.append(reason)
            if p.outcome != Outcome.MISSING:
                failing.append(p)
                _tolerance_warning(p, warnings)
        elif p.outcome == Outcome.OK:
            _ok_item_warnings(p, policy, end_at, warnings)
            later_reason = _later_commitment_reason(p, later, cpol, engines)
            if later_reason is not None:
                reasons.append(later_reason)
                failing.append(replace(p, outcome=Outcome.DUE_BEFORE_END))  # for unblock hints
        elif item.source == ItemSource.PERMIT and not item.blocking:
            warnings.append(Reason(WarningCode.NON_GROUNDING_PERMIT_INVALID,
                                   {"permit_type_code": item.ref, "outcome": str(p.outcome)}))

    # R9 qualifications
    qual_reasons = qualification_reasons(ac, m.required_qualifications, now, m.start_at, deadline)
    reasons.extend(qual_reasons)
    for p in projections:
        if (p.item.source == ItemSource.QUALIFICATION and p.item.ref in m.required_qualifications
                and p.outcome == Outcome.OK):
            _ok_item_warnings(p, policy, end_at, warnings)
    # R11 staleness
    reasons.extend(_staleness_reasons(ac, ctx, policy))
    # R12 manual exclusion
    if ac.tail_number in m.force_exclude:
        reasons.append(Reason(ReasonCode.MANUALLY_EXCLUDED, {}))
    # R13 FERRY_ONLY / unknown restrictions (unknown ⇒ conflict, fail-safe)
    reasons.extend(_restriction_conflicts(ac, m, now))

    final_reasons = dedupe(reasons)
    hints: tuple[Reason, ...] = ()
    if final_reasons:
        failing_quals = [str(r.params["qualification_code"]) for r in qual_reasons
                         if r.code in (ReasonCode.QUALIFICATION_EXPIRED,
                                       ReasonCode.QUALIFICATION_EXPIRES_BEFORE_MISSION_END)]
        hints = unblock_hints(failing, policy=policy, start_at=m.start_at,
                              idle_failed=idle_failed, failing_qualifications=failing_quals,
                              grounding_mas=[ma.ma_number for ma in grounding])
    return EligibilityResult(
        aircraft_id=ac.aircraft_id,
        tail_number=ac.tail_number,
        eligible=not final_reasons,
        reasons=final_reasons,
        warnings=dedupe(warnings),
        projections=tuple(_to_limit_projection(p) for p in projections),
        unblock_hints=dedupe(list(hints)),
    )


def _later_commitment_reason(
    p: Projection, later: list[CommittedMission], policy: PlanningPolicy, engines: int
) -> Reason | None:
    """Flying this mission must not push an already-assigned later flight of the tail over a usage limit."""
    item = p.item
    if (item.kind != ItemKind.COUNTER or not item.blocking or p.remaining_after is None
            or item.source not in (ItemSource.INSPECTION, ItemSource.PERMIT, ItemSource.DEFERRAL)):
        return None
    remaining = p.remaining_after
    for c in later:
        remaining -= consumption(item.unit, FlightLoad.of_commitment(c), policy, engines, item.tg_weight)
        if lt(remaining, 0):
            return Reason(ReasonCode.JEOPARDIZES_LATER_ASSIGNMENT, {
                "mission_id": c.mission_id, "mission_name": c.mission_name, "source": str(item.source),
                "ref": item.ref, "unit": str(item.unit), "over_by": r2(-remaining)})
    return None


def _tolerance_warning(p: Projection, warnings: list[Reason]) -> None:
    item = p.item
    if (item.source == ItemSource.INSPECTION and item.kind == ItemKind.COUNTER and item.tolerance > 0
            and p.remaining_after is not None and p.remaining_after + item.tolerance >= 0):
        warnings.append(Reason(WarningCode.TOLERANCE_AVAILABLE, {
            "inspection_code": item.ref, "unit": str(item.unit), "tolerance": r2(item.tolerance)}))


def _ok_item_warnings(p: Projection, policy: PlanningPolicy, end_at: datetime, warnings: list[Reason]) -> None:
    item = p.item
    if item.kind == ItemKind.COUNTER and item.interval and p.remaining_after is not None:
        if p.remaining_after < item.interval * policy.low_margin_pct / 100.0:
            warnings.append(Reason(WarningCode.LOW_MARGIN_AFTER_MISSION, {
                "source": str(item.source), "ref": item.ref, "unit": str(item.unit),
                "remaining_after": r2(p.remaining_after)}))
    elif (item.kind == ItemKind.CALENDAR and item.due_at is not None
          and item.due_at < end_at + timedelta(days=policy.expiring_soon_days)):
            warnings.append(Reason(WarningCode.EXPIRES_SOON_AFTER_MISSION, {
                "source": str(item.source), "ref": item.ref, "due_at": _iso(item.due_at)}))


def _staleness_reasons(ac: Aircraft, ctx: EvaluationContext, policy: PlanningPolicy) -> list[Reason]:
    return [Reason(ReasonCode.DATA_STALE, params) for params in stale_sources(ac, ctx, policy.max_staleness_hours)]


def _restriction_conflicts(ac: Aircraft, m: MissionRequest, now: datetime) -> list[Reason]:
    out: list[Reason] = []
    for p in sorted(ac.permits, key=lambda p: (p.permit_type_code, p.permit_id)):
        if not p.is_active or (p.expires_at is not None and p.expires_at <= now):
            continue
        for restriction in p.restrictions:
            if (restriction == "FERRY_ONLY" and m.mission_type != MissionType.FERRY) or (
                    restriction not in KNOWN_RESTRICTIONS):
                out.append(Reason(ReasonCode.PERMIT_RESTRICTION_CONFLICT, {
                    "permit_type_code": p.permit_type_code, "restriction": restriction,
                    "mission_type": str(m.mission_type)}))
    return out


def _to_limit_projection(p: Projection) -> LimitProjection:
    item = p.item
    return LimitProjection(
        source=str(item.source), ref=item.ref, unit=str(item.unit), interval=item.interval,
        remaining_now=p.remaining_now, consumption=p.consumption, remaining_after=p.remaining_after,
        due_at=_iso(item.due_at) if item.due_at else None, outcome=str(p.outcome),
    )
