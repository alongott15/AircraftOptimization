"""Unblock hints: what would make an ineligible aircraft eligible (OPTIMIZATION_ENGINE.md §2.7)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.domain.compliance.items import Outcome, Projection
from app.domain.models import ItemSource, Unit
from app.domain.numeric import floor2
from app.domain.policy import PlanningPolicy
from app.domain.reason_codes import Reason, UnblockCode


def max_duration_for_remaining(remaining: float, policy: PlanningPolicy) -> float:
    """Largest D with D + max(D·pct, floor) ≤ remaining (two regimes of the FH buffer)."""
    pct = policy.duration_buffer_pct / 100.0
    floor = policy.min_duration_buffer_hours
    candidates: list[float] = []
    if pct > 0:
        threshold = floor / pct  # D at which the pct buffer overtakes the floor
        d_pct = remaining / (1 + pct)
        if d_pct >= threshold:
            candidates.append(d_pct)
        candidates.append(min(remaining - floor, threshold))
    else:
        candidates.append(remaining - floor)
    best = max(candidates)
    return floor2(best) if best > 0 else 0.0


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def unblock_hints(
    failing: list[Projection],
    *,
    policy: PlanningPolicy,
    start_at: datetime,
    idle_failed: bool,
    failing_qualifications: list[str],
    grounding_mas: list[str],
) -> tuple[Reason, ...]:
    hints: list[Reason] = []
    max_hours: float | None = None
    max_landings: int | None = None
    latest_end: datetime | None = None

    for p in failing:
        item = p.item
        if p.outcome == Outcome.DUE_BEFORE_END and item.due_value is not None and item.current is not None:
            room = item.due_value - item.current - p.committed - p.later
            if item.unit == Unit.FH:
                d = max_duration_for_remaining(room, policy)
                if d > 0:
                    max_hours = d if max_hours is None else min(max_hours, d)
            elif item.unit in (Unit.LDG, Unit.CYC):
                n = math.floor(room - policy.landing_buffer + 1e-9)
                if n >= 1:
                    max_landings = n if max_landings is None else min(max_landings, n)
        if p.outcome == Outcome.DUE_BEFORE_END and item.due_at is not None:
            candidate = item.due_at - timedelta(hours=policy.calendar_buffer_hours)
            if candidate > start_at:
                latest_end = candidate if latest_end is None else min(latest_end, candidate)

    if max_hours is not None:
        hints.append(Reason(UnblockCode.UNBLOCK_MAX_DURATION, {"max_hours": max_hours}))
    if max_landings is not None:
        hints.append(Reason(UnblockCode.UNBLOCK_MAX_LANDINGS, {"max_landings_plus_tg": max_landings}))
    if latest_end is not None:
        hints.append(Reason(UnblockCode.UNBLOCK_LATEST_END, {"latest_end": _iso(latest_end)}))

    seen: set[str] = set()
    for p in failing:
        item = p.item
        key = f"{item.source}:{item.ref}"
        if key in seen or p.outcome == Outcome.MISSING:
            continue
        seen.add(key)
        if item.source == ItemSource.INSPECTION:
            hints.append(Reason(UnblockCode.UNBLOCK_PERFORM_INSPECTION, {"inspection_code": item.ref}))
        elif item.source == ItemSource.PERMIT:
            hints.append(Reason(UnblockCode.UNBLOCK_RENEW_PERMIT, {"permit_type_code": item.ref}))
        elif item.source == ItemSource.DEFERRAL:
            hints.append(Reason(UnblockCode.UNBLOCK_CLOSE_MA, {"ma_number": item.ref}))
    for code in failing_qualifications:
        hints.append(Reason(UnblockCode.UNBLOCK_RENEW_QUALIFICATION, {"qualification_code": code}))
    for ma_number in grounding_mas:
        hints.append(Reason(UnblockCode.UNBLOCK_CLOSE_MA, {"ma_number": ma_number}))
    if idle_failed:
        hints.append(Reason(UnblockCode.UNBLOCK_IDLE_CHECK, {}))
    return tuple(hints)
