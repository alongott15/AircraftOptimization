"""Compliance items: every limit that can expire, collected per aircraft, then projected.

An *item* is mission-independent (what is due and what is the current value).
A *projection* applies a mission (or the null mission for fleet status) to an item.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from app.domain.compliance.consumption import FlightLoad, consumption
from app.domain.models import (
    COUNTER_UNITS,
    TG_WEIGHTED_UNITS,
    Aircraft,
    EvaluationContext,
    InspectionLimit,
    ItemSource,
    Permit,
    Severity,
    Unit,
)
from app.domain.numeric import lt, r2
from app.domain.policy import PlanningPolicy
from app.domain.reason_codes import ParamValue

DAY = timedelta(days=1)
# Timestamps further than this in the future are not trusted (time-zone or clock errors in the source).
CLOCK_SKEW = timedelta(minutes=15)


class ItemKind(StrEnum):
    COUNTER = "COUNTER"
    CALENDAR = "CALENDAR"
    IDLE = "IDLE"


class Outcome(StrEnum):
    OK = "OK"
    EXPIRED = "EXPIRED"
    DUE_BEFORE_END = "DUE_BEFORE_END"
    MISSING = "MISSING"


@dataclass(frozen=True, slots=True)
class Item:
    source: ItemSource
    ref: str  # inspection code | permit type | MA number | qualification code | IDLE_CHECK
    unit: Unit
    kind: ItemKind
    current: float | None = None  # COUNTER: effective current (incl. tg weighting); IDLE: last flight known
    due_value: float | None = None
    due_at: datetime | None = None
    interval: float | None = None
    tolerance: float = 0.0
    tg_weight: float = 1.0
    zone_code: str | None = None
    blocking: bool = True
    missing_kind: str | None = None  # set when data is missing (fail-safe, S1)
    missing_code: str | None = None
    as_of: datetime | None = None  # COUNTER: oldest snapshot time of the counters behind `current`

    @property
    def is_missing(self) -> bool:
        return self.missing_kind is not None


@dataclass(frozen=True, slots=True)
class Projection:
    item: Item
    outcome: Outcome
    remaining_now: float | None  # unit of the item; days for CALENDAR/IDLE
    consumption: float | None
    remaining_after: float | None
    committed: float = 0.0  # consumption of assigned flights between the counter snapshot and this mission
    later: float = 0.0  # consumption of this tail's assigned flights after this mission


def _finite(value: float | None) -> bool:
    return value is not None and math.isfinite(value)


def _effective_counter(ac: Aircraft, unit: Unit, tg_weight: float) -> tuple[float | None, str | None, datetime | None]:
    """Current value for a counter limit; returns (value, missing_unit, as_of). NaN/inf counts as missing (S1)."""
    base = ac.counter(unit)
    if base is None or not _finite(base.value):
        return None, str(unit), None
    value, as_of = base.value, base.as_of
    if unit in TG_WEIGHTED_UNITS and tg_weight > 0:
        tg = ac.counter(Unit.TG)
        if tg is None or not _finite(tg.value):
            return None, str(Unit.TG), None
        value += tg_weight * tg.value
        as_of = min(as_of, tg.as_of)
    return value, None, as_of


_MISSING_KIND = {
    ItemSource.INSPECTION: "INSPECTION",
    ItemSource.PERMIT: "PERMIT",
    ItemSource.DEFERRAL: "DEFERRAL",
    ItemSource.IDLE: "INSPECTION_LIMIT",
}


def _sanitize(item: Item) -> Item:
    """A limit whose numbers are NaN/inf is unknown, never OK (S1)."""
    if item.is_missing or item.kind == ItemKind.CALENDAR:
        return item
    if item.kind == ItemKind.COUNTER and not _finite(item.current):
        return replace(item, missing_kind="COUNTER", missing_code=str(item.unit))
    if not _finite(item.due_value) or (item.kind == ItemKind.IDLE and not _finite(item.current)):
        return replace(item, missing_kind=_MISSING_KIND.get(item.source, "INSPECTION"), missing_code=item.ref)
    return item


def collect_items(ac: Aircraft, ctx: EvaluationContext) -> tuple[Item, ...]:
    items: list[Item] = []
    items.extend(_inspection_items(ac, ctx))
    items.extend(_permit_items(ac, ctx))
    items.extend(_deferral_items(ac))
    items.extend(_qualification_items(ac))
    return tuple(_sanitize(i) for i in items)


def _inspection_items(ac: Aircraft, ctx: EvaluationContext) -> list[Item]:
    out: list[Item] = []
    type_code = ac.aircraft_type.type_code
    limits = list(ac.inspection_limits)
    if not any(lim.unit != Unit.IDLE_DAY for lim in limits):
        # A type without any inspection programme is a data gap, not a clean bill of health (BR-01).
        out.append(Item(source=ItemSource.INSPECTION, ref=type_code, unit=Unit.CAL_DAY, kind=ItemKind.CALENDAR,
                        missing_kind="INSPECTION_LIMIT", missing_code=type_code))
    if not any(lim.unit == Unit.IDLE_DAY for lim in limits):
        # BR-13 fallback: the type's idle limit applies even without an IDLE_DAY limit row.
        if _finite(float(ac.aircraft_type.idle_limit_days)) and ac.aircraft_type.idle_limit_days > 0:
            limits.append(InspectionLimit("IDLE_CHECK", Unit.IDLE_DAY, float(ac.aircraft_type.idle_limit_days)))
        else:
            out.append(Item(source=ItemSource.IDLE, ref="IDLE_CHECK", unit=Unit.IDLE_DAY, kind=ItemKind.IDLE,
                            missing_kind="INSPECTION_LIMIT", missing_code=type_code))
    limit_keys = {(lim.inspection_type_code, lim.unit) for lim in limits}
    for d in sorted(ac.inspection_dues, key=lambda x: (x.inspection_type_code, x.unit)):
        if (d.inspection_type_code, d.unit) not in limit_keys:
            # A due row without a limit cannot be projected (unknown interval/weighting) ⇒ fail-safe.
            kind = ItemKind.CALENDAR if d.unit == Unit.CAL_DAY else ItemKind.COUNTER
            out.append(Item(source=ItemSource.INSPECTION, ref=d.inspection_type_code, unit=d.unit, kind=kind,
                            missing_kind="INSPECTION_LIMIT", missing_code=d.inspection_type_code))
    for lim in sorted(limits, key=lambda x: (x.inspection_type_code, x.unit)):
        code = lim.inspection_type_code
        base = Item(source=ItemSource.INSPECTION, ref=code, unit=lim.unit, kind=ItemKind.COUNTER,
                    interval=lim.interval_value, tolerance=lim.tolerance_value, tg_weight=lim.tg_weight,
                    zone_code=lim.zone_code)
        if lim.unit == Unit.IDLE_DAY:
            if ac.last_flight_at is None:
                out.append(Item(kind=ItemKind.IDLE, missing_kind="LAST_FLIGHT", missing_code=ac.tail_number,
                                source=ItemSource.IDLE, ref=code, unit=lim.unit, interval=lim.interval_value))
            else:
                idle_now = (ctx.now - ac.last_flight_at) / DAY
                out.append(Item(source=ItemSource.IDLE, ref=code, unit=lim.unit, kind=ItemKind.IDLE,
                                current=idle_now, due_value=lim.interval_value, interval=lim.interval_value))
            continue
        due = ac.due(code, lim.unit)
        if lim.unit == Unit.CAL_DAY:
            cal = replace(base, kind=ItemKind.CALENDAR)
            if due is None or due.due_at is None:
                out.append(replace(cal, missing_kind="INSPECTION", missing_code=code))
            else:
                out.append(replace(cal, due_at=due.due_at))
            continue
        if lim.unit not in COUNTER_UNITS:
            continue
        current, missing_unit, as_of = _effective_counter(ac, lim.unit, lim.tg_weight)
        if missing_unit is not None:
            out.append(replace(base, missing_kind="COUNTER", missing_code=missing_unit))
        elif due is None or due.due_value is None:
            out.append(replace(base, missing_kind="INSPECTION", missing_code=code))
        else:
            out.append(replace(base, current=current, due_value=due.due_value, as_of=as_of))
    return out


def _permit_items(ac: Aircraft, ctx: EvaluationContext) -> list[Item]:
    out: list[Item] = []
    # A permit counts only once it has been issued: a pre-entered renewal must not hide the gap after the
    # current permit expires (validity is [issued_at, expires_at)).
    active = [p for p in ac.permits if p.is_active and p.issued_at <= ctx.now + CLOCK_SKEW]
    present_types = {p.permit_type_code for p in active}
    for missing in sorted(ctx.required_permit_types - present_types):
        out.append(Item(source=ItemSource.PERMIT, ref=missing, unit=Unit.CAL_DAY, kind=ItemKind.CALENDAR,
                        missing_kind="PERMIT", missing_code=missing))
    # For each type keep only the best active permit: the latest-expiring dated one. An undated permit never
    # supersedes a dated one (a NULL expiry is not "valid forever").
    best: dict[str, Permit] = {}
    for p in sorted(active, key=lambda p: (p.permit_type_code, p.permit_id)):
        current_best = best.get(p.permit_type_code)
        if current_best is None or _expiry_rank(p) > _expiry_rank(current_best):
            best[p.permit_type_code] = p
    for type_code in sorted(best):
        p = best[type_code]
        blocking = p.is_grounding or type_code in ctx.required_permit_types
        if p.expires_at is not None:
            out.append(Item(source=ItemSource.PERMIT, ref=type_code, unit=Unit.CAL_DAY, kind=ItemKind.CALENDAR,
                            due_at=p.expires_at, blocking=blocking))
        elif p.limit_unit is None and blocking:
            out.append(Item(source=ItemSource.PERMIT, ref=type_code, unit=Unit.CAL_DAY, kind=ItemKind.CALENDAR,
                            missing_kind="PERMIT", missing_code=type_code))
        if p.limit_unit is None and p.limit_value is not None:
            out.append(Item(source=ItemSource.PERMIT, ref=type_code, unit=Unit.CAL_DAY, kind=ItemKind.COUNTER,
                            blocking=blocking, missing_kind="PERMIT", missing_code=type_code))
        if p.limit_unit is not None:
            current, missing_unit, as_of = _effective_counter(ac, p.limit_unit, 1.0)
            if missing_unit is not None or p.limit_value is None:
                out.append(Item(source=ItemSource.PERMIT, ref=type_code, unit=p.limit_unit, kind=ItemKind.COUNTER,
                                blocking=blocking, missing_kind="COUNTER" if missing_unit else "PERMIT",
                                missing_code=missing_unit or type_code))
            else:
                out.append(Item(source=ItemSource.PERMIT, ref=type_code, unit=p.limit_unit, kind=ItemKind.COUNTER,
                                current=current, due_value=p.limit_value, blocking=blocking, as_of=as_of))
    return out


def _expiry_rank(p: Permit) -> tuple[int, float]:
    return (1, p.expires_at.timestamp()) if p.expires_at is not None else (0, 1.0 if p.limit_unit else 0.0)


def _deferral_items(ac: Aircraft) -> list[Item]:
    out: list[Item] = []
    for ma in ac.open_mas:
        if (ma.severity == Severity.DEFERRED and not ma.has_deferral) or (
                ma.deferral_limit_value is not None and ma.deferral_limit_unit is None):
            # A deferred defect without a usable limit cannot be shown to be valid (BR-07, S1).
            out.append(Item(source=ItemSource.DEFERRAL, ref=ma.ma_number, unit=Unit.CAL_DAY, kind=ItemKind.CALENDAR,
                            zone_code=ma.zone_code, missing_kind="DEFERRAL", missing_code=ma.ma_number))
            continue
        if not ma.has_deferral:
            continue
        if ma.deferral_expires_at is not None:
            out.append(Item(source=ItemSource.DEFERRAL, ref=ma.ma_number, unit=Unit.CAL_DAY, kind=ItemKind.CALENDAR,
                            due_at=ma.deferral_expires_at, zone_code=ma.zone_code))
        if ma.deferral_limit_unit is not None:
            base = Item(source=ItemSource.DEFERRAL, ref=ma.ma_number, unit=ma.deferral_limit_unit,
                        kind=ItemKind.COUNTER, zone_code=ma.zone_code)
            current, missing_unit, as_of = _effective_counter(ac, ma.deferral_limit_unit, 1.0)
            if missing_unit is not None:
                out.append(replace(base, missing_kind="COUNTER", missing_code=missing_unit))
            elif ma.deferral_limit_value is None:
                out.append(replace(base, missing_kind="DEFERRAL", missing_code=ma.ma_number))
            else:
                out.append(replace(base, current=current, due_value=ma.deferral_limit_value, as_of=as_of))
    return out


def _qualification_items(ac: Aircraft) -> list[Item]:
    return [
        Item(source=ItemSource.QUALIFICATION, ref=q.qualification_code, unit=Unit.CAL_DAY, kind=ItemKind.CALENDAR,
             due_at=q.valid_to, blocking=False)
        for q in sorted(ac.qualifications, key=lambda q: q.qualification_code)
        if q.valid_to is not None
    ]


def project(
    item: Item,
    *,
    now: datetime,
    load: FlightLoad | None,
    committed_use: float = 0.0,
    engine_count: int,
    policy: PlanningPolicy,
    start_at: datetime | None,
    deadline: datetime | None,
    idle_reference: datetime | None,
) -> Projection:
    """Project one item to the mission (load/deadline) or, with load=None, to `now` only."""
    if item.is_missing:
        return Projection(item, Outcome.MISSING, None, None, None)
    if item.kind == ItemKind.COUNTER:
        assert item.current is not None
        assert item.due_value is not None
        use = consumption(item.unit, load, policy, engine_count, item.tg_weight) if load else 0.0
        before = committed_use
        remaining_now = item.due_value - item.current
        remaining_after = item.due_value - (item.current + before + use)
        if lt(remaining_now, 0):
            return Projection(item, Outcome.EXPIRED, remaining_now, use + before, remaining_after, before)
        if lt(remaining_after, 0):
            return Projection(item, Outcome.DUE_BEFORE_END, remaining_now, use + before, remaining_after, before)
        return Projection(item, Outcome.OK, remaining_now, use + before, remaining_after, before)
    if item.kind == ItemKind.CALENDAR:
        assert item.due_at is not None
        remaining_now = (item.due_at - now) / DAY
        limit_at = deadline or now
        remaining_after = (item.due_at - limit_at) / DAY
        if item.due_at <= now:
            return Projection(item, Outcome.EXPIRED, remaining_now, None, remaining_after)
        if item.due_at < limit_at:
            return Projection(item, Outcome.DUE_BEFORE_END, remaining_now, None, remaining_after)
        return Projection(item, Outcome.OK, remaining_now, None, remaining_after)
    # IDLE
    assert item.current is not None
    assert item.due_value is not None
    idle_now = item.current
    if start_at is not None and idle_reference is not None:
        idle_at_start = (start_at - idle_reference) / DAY
    else:
        idle_at_start = idle_now
    remaining_now = item.due_value - idle_now
    remaining_after = item.due_value - idle_at_start
    if lt(remaining_now, 0):
        return Projection(item, Outcome.EXPIRED, remaining_now, None, remaining_after)
    if lt(remaining_after, 0):
        return Projection(item, Outcome.DUE_BEFORE_END, remaining_now, None, remaining_after)
    return Projection(item, Outcome.OK, remaining_now, None, remaining_after)


def stale_sources(ac: Aircraft, ctx: EvaluationContext, max_staleness_hours: float) -> list[dict[str, ParamValue]]:
    """Feeds, aircraft rows, counters and the last flight that are too old, never loaded, or in the future (FR-71, S1).

    A timestamp ahead of `now` (e.g. local time read as UTC) is as untrustworthy as an old one: it would make
    flights look "already counted" or reset the idle clock. Such entries carry a negative `age_hours`.
    """
    limit = timedelta(hours=max_staleness_hours)
    hours = timedelta(hours=1)
    out: list[dict[str, ParamValue]] = []

    def check(at: datetime, params: dict[str, ParamValue], *, old_is_stale: bool = True) -> None:
        age = ctx.now - at
        if at > ctx.now + CLOCK_SKEW or (old_is_stale and age > limit):
            out.append({**params, "age_hours": r2(age / hours)})

    for source in sorted(ctx.expected_feeds - set(ctx.data_load_status)):
        out.append({"source_name": source, "age_hours": None})
    for source, loaded_at in sorted(ctx.data_load_status.items()):
        check(loaded_at, {"source_name": source})
    check(ac.source_updated_at, {"source_name": "aircraft"})
    for unit, counter in sorted(ac.counters.items()):
        check(counter.as_of, {"source_name": "usage_counter", "unit": str(unit)})
    if ac.last_flight_at is not None:
        check(ac.last_flight_at, {"source_name": "last_flight"}, old_is_stale=False)  # old = idle, not stale
    return out
