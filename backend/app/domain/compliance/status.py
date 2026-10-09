"""Fleet compliance status without a mission (OPTIMIZATION_ENGINE.md §2.8)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.compliance.items import Item, ItemKind, Outcome, collect_items, project, stale_sources
from app.domain.models import (
    STATUS_SEVERITY,
    Aircraft,
    ComplianceStatus,
    EvaluationContext,
    ItemSource,
    OperationalStatus,
    Unit,
)
from app.domain.numeric import le, r2
from app.domain.policy import PlanningPolicy


@dataclass(frozen=True, slots=True)
class ItemStatus:
    source: str
    ref: str
    unit: str
    kind: str
    status: ComplianceStatus
    remaining: float | None  # unit of the item; days for calendar/idle
    due_value: float | None
    due_at: datetime | None
    interval: float | None
    zone_code: str | None
    blocking: bool
    missing_kind: str | None = None
    missing_code: str | None = None


@dataclass(frozen=True, slots=True)
class AircraftStatus:
    aircraft_id: int
    compliance_status: ComplianceStatus
    derived_status: str
    items: tuple[ItemStatus, ...]
    stale: bool
    idle_days: float | None
    data_issues: tuple[tuple[str, str], ...] = ()

    @property
    def expired_count(self) -> int:
        return sum(1 for i in self.items if i.status == ComplianceStatus.EXPIRED)

    @property
    def expiring_soon_count(self) -> int:
        return sum(1 for i in self.items if i.status == ComplianceStatus.EXPIRING_SOON)

    @property
    def available(self) -> bool:
        """Could fly now: operational, not grounded, no blocking expired/unknown item."""
        if self.derived_status != OperationalStatus.OPERATIONAL or self.stale or self.data_issues:
            return False
        return not any(
            i.blocking and i.status in (ComplianceStatus.EXPIRED, ComplianceStatus.UNKNOWN) for i in self.items
        )

    def next_due(self) -> ItemStatus | None:
        """Most urgent item: worst status first, then smallest normalized remaining."""
        def urgency(i: ItemStatus) -> tuple[int, float]:
            if i.remaining is None:
                return (-STATUS_SEVERITY[i.status], 0.0)
            norm = i.remaining / i.interval if i.interval else i.remaining / 365.0
            return (-STATUS_SEVERITY[i.status], norm)
        candidates = [i for i in self.items if i.source != ItemSource.IDLE or i.status != ComplianceStatus.OK]
        return min(candidates, key=urgency) if candidates else None


_ISSUE_SOURCE = {
    "INSPECTION": "INSPECTION",
    "INSPECTION_LIMIT": "INSPECTION",
    "PERMIT": "PERMIT",
    "DEFERRAL": "DEFERRAL",
    "MAINTENANCE_ACTION": "MAINTENANCE_ACTION",
    "QUALIFICATION": "QUALIFICATION",
}


def _item_status(item: Item, remaining: float | None, outcome: Outcome, policy: PlanningPolicy) -> ComplianceStatus:
    if outcome == Outcome.MISSING or remaining is None:
        return ComplianceStatus.UNKNOWN
    if outcome == Outcome.EXPIRED:
        return ComplianceStatus.EXPIRED
    if item.kind in (ItemKind.CALENDAR, ItemKind.IDLE):
        return ComplianceStatus.EXPIRING_SOON if le(remaining, policy.expiring_soon_days) else ComplianceStatus.OK
    abs_threshold = policy.expiring_soon_fh if item.unit == Unit.FH else policy.expiring_soon_ldg
    if le(remaining, abs_threshold):
        return ComplianceStatus.EXPIRING_SOON
    if item.interval and le(remaining, item.interval * policy.expiring_soon_pct / 100.0):
        return ComplianceStatus.EXPIRING_SOON
    return ComplianceStatus.OK


def aircraft_status(ac: Aircraft, policy: PlanningPolicy, ctx: EvaluationContext) -> AircraftStatus:
    statuses: list[ItemStatus] = []
    for item in collect_items(ac, ctx):
        p = project(item, now=ctx.now, load=None, engine_count=ac.aircraft_type.engine_count,
                    policy=policy, start_at=None, deadline=None, idle_reference=None)
        status = _item_status(item, p.remaining_now, p.outcome, policy)
        statuses.append(ItemStatus(
            source=str(item.source), ref=item.ref, unit=str(item.unit), kind=str(item.kind), status=status,
            remaining=None if p.remaining_now is None else r2(p.remaining_now),
            due_value=item.due_value, due_at=item.due_at, interval=item.interval, zone_code=item.zone_code,
            blocking=item.blocking, missing_kind=item.missing_kind, missing_code=item.missing_code,
        ))
    for kind, code in ac.data_issues:  # unreadable source rows: shown as UNKNOWN items, never silently OK
        statuses.append(ItemStatus(
            source=_ISSUE_SOURCE.get(kind, "AIRCRAFT"), ref=code, unit="", kind="DATA", status=ComplianceStatus.UNKNOWN,
            remaining=None, due_value=None, due_at=None, interval=None, zone_code=None, blocking=True,
            missing_kind=kind, missing_code=code))
    stale = bool(stale_sources(ac, ctx, policy.max_staleness_hours))
    worst = ComplianceStatus.OK
    for s in statuses:
        if STATUS_SEVERITY[s.status] > STATUS_SEVERITY[worst]:
            worst = s.status
    if (stale or ac.data_issues) and STATUS_SEVERITY[worst] < STATUS_SEVERITY[ComplianceStatus.UNKNOWN]:
        worst = ComplianceStatus.UNKNOWN
    derived = str(ac.status)
    if ac.status == OperationalStatus.OPERATIONAL and ac.has_open_grounding_ma:
        derived = str(OperationalStatus.GROUNDED)
    idle_days = r2((ctx.now - ac.last_flight_at) / timedelta(days=1)) if ac.last_flight_at else None
    return AircraftStatus(ac.aircraft_id, worst, derived, tuple(statuses), stale, idle_days, ac.data_issues)


def is_blocking_expired(status: AircraftStatus) -> bool:
    return any(i.status == ComplianceStatus.EXPIRED for i in status.items)


def to_iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None
