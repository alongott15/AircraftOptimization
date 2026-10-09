"""Fleet status, aircraft detail, alerts and timeline (API.md §3.3–3.4)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.domain.compliance.qualifications import effective_qualifications
from app.domain.compliance.status import AircraftStatus, ItemStatus
from app.domain.models import STATUS_SEVERITY, Aircraft, ComplianceStatus, OperationalStatus, Permit, Severity, Unit
from app.ml.provider import RiskInfo
from app.ml.utilization import Utilization
from app.services.context import FleetContext
from app.services.errors import not_found


def iso(dt: datetime | None) -> str | None:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _worst(statuses: list[ComplianceStatus]) -> ComplianceStatus:
    return max(statuses, key=lambda s: STATUS_SEVERITY[s], default=ComplianceStatus.OK)


MAX_FORECAST_DAYS = 36_500  # beyond ~100 years a forecast is meaningless (and would overflow datetime)


def predicted_due(item: ItemStatus, now: datetime, rates: dict[str, float] | None) -> datetime | None:
    if item.due_at is not None:
        return item.due_at
    if item.remaining is None or item.kind != "COUNTER" or not rates:
        return None
    rate = rates.get(item.unit, 0.0)
    if rate <= 1e-6 or abs(item.remaining / rate) > MAX_FORECAST_DAYS:
        return None
    return now + timedelta(days=item.remaining / rate)


def _risk_dict(risk: RiskInfo | None, version: str | None) -> dict[str, Any]:
    if risk is None:
        return {"probability": None, "band": "UNAVAILABLE", "model_version": version, "top_factors": []}
    return {"probability": risk.probability, "band": risk.band, "model_version": version,
            "top_factors": list(risk.top_factors)}


def summary_row(ac: Aircraft, st: AircraftStatus, risk: RiskInfo | None) -> dict[str, Any]:
    nd = st.next_due()
    open_mas = ac.open_mas
    return {
        "aircraft_id": ac.aircraft_id, "tail_number": ac.tail_number, "type_code": ac.aircraft_type.type_code,
        "engine_count": ac.aircraft_type.engine_count, "base_code": ac.base_code,
        "operational_status": str(ac.status), "derived_status": st.derived_status,
        "compliance_status": str(st.compliance_status), "expired_count": st.expired_count,
        "expiring_soon_count": st.expiring_soon_count, "open_ma_count": len(open_mas),
        "grounding_ma_count": sum(1 for m in open_mas if m.severity == Severity.GROUNDING),
        "available": st.available,
        "risk_band": risk.band if risk else "UNAVAILABLE", "risk_probability": risk.probability if risk else None,
        "next_due": None if nd is None else {
            "kind": nd.source, "code": nd.ref, "unit": nd.unit, "due_at": iso(nd.due_at), "remaining": nd.remaining,
            "status": str(nd.status)},
    }


def fleet_summary(fc: FleetContext) -> dict[str, Any]:
    statuses = [(a, fc.status(a)) for a in fc.aircraft]
    total = len(statuses)
    available = sum(1 for _, st in statuses if st.available)
    by_type: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "available": 0})
    for a, st in statuses:
        by_type[a.aircraft_type.type_code]["total"] += 1
        by_type[a.aircraft_type.type_code]["available"] += int(st.available)
    return {
        "as_of": iso(fc.now), "total_aircraft": total, "available_now": available,
        "availability_pct": round(100.0 * available / total, 1) if total else 0.0,
        "aircraft_with_expired_items": sum(1 for _, st in statuses if st.compliance_status == ComplianceStatus.EXPIRED),
        "items_expiring_7d": sum(st.expiring_soon_count for _, st in statuses),
        "aircraft_unknown": sum(1 for _, st in statuses if st.compliance_status == ComplianceStatus.UNKNOWN),
        "open_grounding_mas": sum(1 for a, _ in statuses for m in a.open_mas if m.severity == Severity.GROUNDING),
        "in_maintenance": sum(1 for a, _ in statuses if a.status == OperationalStatus.IN_MAINTENANCE),
        "by_type": [{"type_code": t, **v} for t, v in sorted(by_type.items())],
    }


@dataclass(frozen=True, slots=True)
class ExpiryFilter:
    """Timeline filter: items in `units` (all usage/calendar units if empty) due by `until` (FR-13/14)."""

    units: frozenset[str]
    until: datetime
    rates: Mapping[int, Mapping[str, float]]

    def item_date(self, ac: Aircraft, i: ItemStatus, now: datetime) -> datetime | None:
        """Due date (actual, or forecast for usage items) when inside the filter, else None.

        An already-expired item always matches (dated now when it has no due date or forecast) — the filter must
        never hide an overdue item just because its usage cannot be forecast.
        """
        if i.source == "IDLE" or i.status == ComplianceStatus.UNKNOWN:
            return None
        if self.units and i.unit not in self.units:
            return None
        when = predicted_due(i, now, dict(self.rates.get(ac.aircraft_id, {})))
        if i.status == ComplianceStatus.EXPIRED:
            return min(when, now) if when is not None else now
        return when if when is not None and when <= self.until else None


@dataclass(frozen=True, slots=True)
class BandFilter:
    """Fleet-list filter (FR-14): per activity unit, a band "תוך … ועד …" in that unit (days for CAL_DAY) of what
    remains *now*. An aircraft matches when any item in a band's unit lies inside it.

    Fail-safe visibility: an aircraft with stale data, unreadable source rows, an UNKNOWN item or an EXPIRED item in a
    filtered unit always matches — the filter narrows the list, it never hides a problem."""

    bands: tuple[tuple[str, float, float], ...]

    def matches(self, st: AircraftStatus) -> bool:
        if st.stale or st.data_issues:
            return True
        units = {b[0] for b in self.bands}
        for i in st.items:
            if i.source == "IDLE":
                continue
            if i.status == ComplianceStatus.UNKNOWN and (not i.unit or i.unit in units):
                return True
            if i.unit in units and (i.status == ComplianceStatus.EXPIRED or i.remaining is None):
                return True
        for unit, low, high in self.bands:
            for i in st.items:
                if i.source != "IDLE" and i.unit == unit and i.remaining is not None and low <= i.remaining <= high:
                    return True
        return False


def list_aircraft(fc: FleetContext, risks: dict[int, RiskInfo] | None, *, type_code: str | None,
                  base_code: str | None, status: str | None, compliance_status: list[str] | None,
                  qualification_code: str | None, q: str | None, sort: str,
                  expiry: BandFilter | None = None) -> list[dict[str, Any]]:
    rows = []
    for ac in fc.aircraft:
        st = fc.status(ac)
        if type_code and ac.aircraft_type.type_code != type_code:
            continue
        if base_code and ac.base_code != base_code:
            continue
        if status and st.derived_status != status:
            continue
        if compliance_status and str(st.compliance_status) not in compliance_status:
            continue
        if qualification_code and qualification_code not in {x.qualification_code for x in ac.qualifications}:
            continue
        if q and q.strip().upper() not in ac.tail_number.upper():
            continue
        if expiry is not None and not expiry.matches(st):
            continue
        rows.append(summary_row(ac, st, (risks or {}).get(ac.aircraft_id)))
    if sort == "tail_number":
        rows.sort(key=lambda r: r["tail_number"])
    elif sort == "next_due":
        rows.sort(key=lambda r: (r["next_due"] is None, r["next_due"]["remaining"] if r["next_due"] else 0))
    else:  # default: expired first, then unknown, expiring, ok (DESIGN_SYSTEM §5 rule 1)
        rows.sort(key=lambda r: (-STATUS_SEVERITY[ComplianceStatus(r["compliance_status"])], r["tail_number"]))
    return rows


def aircraft_detail(fc: FleetContext, tail: str, risk: RiskInfo | None, risk_version: str | None,
                    util: Utilization | None) -> dict[str, Any]:
    ac = fc.by_tail().get(tail)
    if ac is None:
        raise not_found("aircraft")
    st = fc.status(ac)
    rates = dict(util.rates.get(ac.aircraft_id, {})) if util else None
    items_by: dict[tuple[str, str], list[ItemStatus]] = defaultdict(list)
    for item in st.items:
        items_by[(item.source, item.ref)].append(item)

    inspections: list[dict[str, Any]] = []
    idle: dict[str, Any] | None = None
    for (source, ref), items in sorted(items_by.items()):
        if source == "IDLE":
            it = items[0]
            idle = {"idle_days": st.idle_days, "limit_days": it.interval, "status": str(it.status)}
            continue
        if source != "INSPECTION":
            continue
        preds = [p for p in (predicted_due(i, fc.now, rates) for i in items) if p is not None]
        inspections.append({
            "inspection_code": ref, "zone_code": items[0].zone_code, "status": str(_worst([i.status for i in items])),
            "limits": [{"unit": i.unit, "due_value": i.due_value, "due_at": iso(i.due_at), "remaining": i.remaining,
                        "interval": i.interval, "status": str(i.status)} for i in items],
            "predicted_due_at": iso(min(preds)) if preds else None,
        })
    inspections.sort(key=lambda x: (-STATUS_SEVERITY[ComplianceStatus(x["status"])], x["inspection_code"]))

    permits: list[dict[str, Any]] = []
    shown_types: set[str] = set()
    def _latest_first(p: Permit) -> tuple[str, float]:
        return (p.permit_type_code, -(p.expires_at.timestamp() if p.expires_at else 9e12))

    for p in sorted(ac.permits, key=_latest_first):
        if not p.is_active or p.permit_type_code in shown_types:
            continue
        shown_types.add(p.permit_type_code)
        rel = items_by.get(("PERMIT", p.permit_type_code), [])
        cal = next((i for i in rel if i.kind == "CALENDAR"), None)
        permits.append({
            "permit_id": p.permit_id, "permit_type_code": p.permit_type_code, "permit_number": p.permit_number,
            "issued_at": iso(p.issued_at), "expires_at": iso(p.expires_at), "restrictions": list(p.restrictions),
            "status": str(_worst([i.status for i in rel])), "remaining_days": cal.remaining if cal else None,
        })
    for source, ref in items_by:
        if source == "PERMIT" and ref not in shown_types:
            permits.append({"permit_id": None, "permit_type_code": ref, "permit_number": None, "issued_at": None,
                            "expires_at": None, "restrictions": [], "status": str(ComplianceStatus.UNKNOWN),
                            "remaining_days": None})
    permits.sort(key=lambda x: (-STATUS_SEVERITY[ComplianceStatus(x["status"])], x["permit_type_code"]))

    mas: list[dict[str, Any]] = []
    for m in ac.open_mas:
        rel = items_by.get(("DEFERRAL", m.ma_number), [])
        deferral = None
        if m.has_deferral:
            cal = next((i for i in rel if i.kind == "CALENDAR"), None)
            deferral = {"expires_at": iso(m.deferral_expires_at), "limit_unit": m.deferral_limit_unit,
                        "limit_value": m.deferral_limit_value, "status": str(_worst([i.status for i in rel])),
                        "remaining_days": cal.remaining if cal else None}
        mas.append({
            "ma_number": m.ma_number, "title": m.title, "description": m.description, "ata_chapter": m.ata_chapter,
            "zone_code": m.zone_code, "severity": str(m.severity), "mel_category": m.mel_category,
            "opened_at": iso(m.opened_at), "deferral": deferral,
            "affects_qualification_code": m.affects_qualification_code, "status": "OPEN",
        })
    sev_rank = {"GROUNDING": 0, "DEFERRED": 1, "MONITOR": 2}
    mas.sort(key=lambda x: (sev_rank.get(x["severity"], 3), x["ma_number"]))

    quals = []
    for q in effective_qualifications(ac, fc.now):
        rel = items_by.get(("QUALIFICATION", str(q["qualification_code"])), [])
        quals.append({**q, "status": str(_worst([i.status for i in rel]))})

    counters = [{"unit": str(u), "value": round(c.value, 2), "as_of": iso(c.as_of)}
                for u, c in sorted(ac.counters.items(), key=lambda kv: list(Unit).index(kv[0]))]
    oldest = min([ac.source_updated_at, *fc.eval_ctx.data_load_status.values()])
    detail = summary_row(ac, st, risk)
    detail.update({
        "serial_number": ac.serial_number, "manufacture_date": ac.manufacture_date.isoformat(),
        "type_name_he": ac.aircraft_type.name_he, "last_flight_at": iso(ac.last_flight_at),
        "model": {"model_shape": ac.aircraft_type.model_shape, "engine_count": ac.aircraft_type.engine_count,
                  "zones": _zones_for(ac.aircraft_type.engine_count)},
        "counters": counters, "inspections": inspections, "permits": permits, "maintenance_actions": mas,
        "qualifications": quals, "idle": idle, "risk": _risk_dict(risk, risk_version),
        "data_freshness": {"oldest_source_updated_at": iso(oldest), "is_stale": st.stale},
        "max_endurance_hours": ac.aircraft_type.max_endurance_hours,
    })
    return detail


def _zones_for(engine_count: int) -> list[str]:
    zones = ["NOSE", "COCKPIT", "FUSELAGE", "WING_L", "WING_R", "ENGINE_1"]
    if engine_count >= 2:
        zones.append("ENGINE_2")
    return [*zones, "GEAR_NOSE", "GEAR_MAIN", "TAIL"]


def fleet_alerts(fc: FleetContext, statuses: list[str], limit: int, extra: list[dict[str, Any]] | None = None,
                 units: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    wanted = set(statuses)
    out: list[dict[str, Any]] = [a for a in extra or [] if a["status"] in wanted]
    for ac in fc.aircraft:
        st = fc.status(ac)
        for i in st.items:
            # unit-less alerts (data issues) are safety alerts: never filtered out by units
            if str(i.status) in wanted and (not units or not i.unit or i.source == "IDLE" or i.unit in units):
                out.append({"aircraft_id": ac.aircraft_id, "tail_number": ac.tail_number, "source": i.source,
                            "ref": i.ref, "unit": i.unit, "status": str(i.status), "remaining": i.remaining,
                            "due_at": iso(i.due_at), "missing_kind": i.missing_kind, "missing_code": i.missing_code})
        if "EXPIRED" in wanted:  # grounding MAs have no unit — always shown
            for m in ac.open_mas:
                if m.severity == Severity.GROUNDING:
                    out.append({"aircraft_id": ac.aircraft_id, "tail_number": ac.tail_number,
                                "source": "MAINTENANCE_ACTION", "ref": m.ma_number, "unit": None,
                                "status": "EXPIRED", "remaining": None, "due_at": None, "missing_kind": None,
                                "missing_code": None})
    out.sort(key=lambda x: (-STATUS_SEVERITY[ComplianceStatus(x["status"])],
                            x["remaining"] if x["remaining"] is not None else -1e9, x["tail_number"]))
    return out[:limit]


def fleet_timeline(fc: FleetContext, days: int, util: Utilization, units: frozenset[str] = frozenset(),
                   until: datetime | None = None) -> list[dict[str, Any]]:
    """Items expiring within `days` — or until `until` when given — optionally only in `units` (FR-13/14)."""
    window = ExpiryFilter(units, until or fc.now + timedelta(days=days), util.rates)
    out = []
    for ac in fc.aircraft:
        st = fc.status(ac)
        for i in st.items:
            when = window.item_date(ac, i, fc.now)
            if when is None:
                continue
            out.append({"aircraft_id": ac.aircraft_id, "tail_number": ac.tail_number, "source": i.source,
                        "ref": i.ref, "unit": i.unit, "status": str(i.status), "date": iso(when),
                        "predicted": i.due_at is None})
    out.sort(key=lambda x: (x["date"], x["tail_number"]))
    return out


def data_freshness(fc: FleetContext) -> list[dict[str, Any]]:
    limit = timedelta(hours=fc.policy.max_staleness_hours)
    return [{"source_name": name, "loaded_at": iso(at), "is_stale": fc.now - at > limit}
            for name, at in sorted(fc.eval_ctx.data_load_status.items())]
