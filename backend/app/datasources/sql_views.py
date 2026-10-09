"""Generic reader for the canonical fleet views `ao_v_*` (DATABASE.md §3, §7).

Used by VerticaSource in production. The same SQL runs on any engine that exposes the views, which lets the test suite
verify the view contract on SQLite against the POC adapter, query for query.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol

from app.datasources.base import FleetSnapshot, FlightRecord, InspectionEventRecord, MaRecord
from app.domain.models import (
    Aircraft,
    AircraftQualification,
    AircraftType,
    Counter,
    InspectionDue,
    InspectionLimit,
    MaintenanceAction,
    OperationalStatus,
    Permit,
    Severity,
    Unit,
)

Row = Mapping[str, Any]

VIEWS = (
    "aircraft_type",
    "base",
    "aircraft",
    "activity_unit",
    "usage_counter",
    "flight_log",
    "qualification",
    "aircraft_qualification",
    "zone",
    "ata_zone_map",
    "inspection_type",
    "inspection_limit",
    "aircraft_inspection_due",
    "inspection_event",
    "permit_type",
    "aircraft_permit",
    "maintenance_action",
    "data_load_status",
)


class QueryRunner(Protocol):
    """Executes read-only SQL with `:name` placeholders and returns rows as dicts."""

    def fetch(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]: ...

    def bind_datetime(self, value: datetime) -> Any: ...


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _req_dt(value: Any) -> datetime:
    parsed = _dt(value)
    if parsed is None:
        raise ValueError("required timestamp is NULL")
    return parsed


def _date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _f(value: Any) -> float | None:
    """NULL, NaN and ±inf are all "unknown" (S1) — never a number that compares as OK."""
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _nonneg(value: float | None) -> float | None:
    return value if value is not None and value >= 0 else None


def _req_f(value: Any) -> float:
    number = _f(value)
    if number is None:
        raise ValueError("required number is NULL or not finite")
    return number


def _flag(value: Any) -> bool:
    """Safety flags (is_grounding, is_required) default to TRUE when NULL."""
    return True if value is None else bool(value)


def _unit(value: Any) -> Unit | None:
    return Unit(str(value)) if value else None


def _severity(value: Any) -> Severity:
    """An unknown or NULL MA severity is treated as grounding (fail-safe)."""
    try:
        return Severity(str(value).upper())
    except ValueError:
        return Severity.GROUNDING


# Placeholder for an aircraft whose type row is missing: kept visible, never eligible (DATA_MISSING).
UNKNOWN_SHAPE = "SINGLE_PROP_LOW_WING"
PARSE_ERRORS = (ValueError, TypeError, KeyError)
PERMIT_STATUSES = frozenset({"ACTIVE", "REVOKED"})


class SqlViewSource:
    def __init__(
        self,
        runner: QueryRunner,
        schema: str = "aircraft_opt",
        label: str = "aircraft_opt_snapshot",
        view_prefix: str = "ao_v_",
    ) -> None:
        if schema and not re.fullmatch(r"[A-Za-z_]\w{0,62}", schema):
            raise ValueError("invalid schema name")
        self._run = runner
        self._prefix = f"{schema}." if schema else ""
        self._view_prefix = view_prefix  # "" ⇒ read the POC tables directly (same names and columns as the views)
        self._label = label

    def _v(self, name: str) -> str:
        if name not in VIEWS:
            raise ValueError(f"unknown view {name}")
        return f"{self._prefix}{self._view_prefix}{name}"

    def _select(
        self, view: str, where: str = "", params: Mapping[str, Any] | None = None, order: str = ""
    ) -> list[dict[str, Any]]:
        sql = f"SELECT /*+label({self._label})*/ * FROM {self._v(view)}"  # noqa: S608 — view names are whitelisted
        if where:
            sql += f" WHERE {where}"
        if order:
            sql += f" ORDER BY {order}"
        return self._run.fetch(sql, params)

    # ── snapshot ────────────────────────────────────────────────────────
    def load_snapshot(self) -> FleetSnapshot:
        """Rows are parsed defensively: a bad row never crashes the snapshot and never reads as "OK" (S1).

        Unreadable or invalid limit, due, permit and MA rows, duplicate due/counter rows, unknown permit statuses,
        unknown types and statuses all become `data_issues` (⇒ DATA_MISSING) on the affected aircraft. Lake primary
        keys are not trusted, so duplicates are never resolved by query order.
        """
        issues: dict[int, list[tuple[str, str]]] = defaultdict(list)
        type_issues: dict[str, list[tuple[str, str]]] = defaultdict(list)
        types: dict[str, AircraftType] = {}
        for r in self._select("aircraft_type"):
            try:
                endurance = _f(r["max_endurance_hours"])
                if int(r["engine_count"]) <= 0:
                    raise ValueError("engine_count must be positive")
                types[r["type_code"]] = AircraftType(
                    r["type_code"],
                    r["name_he"],
                    int(r["engine_count"]),
                    math.nan if endurance is None else endurance,
                    int(r["idle_limit_days"] or 0),
                    r["model_shape"] or UNKNOWN_SHAPE,
                )
            except PARSE_ERRORS:
                continue
        zones = {r["inspection_type_code"]: r["zone_code"] for r in self._select("inspection_type")}
        limits: dict[str, list[InspectionLimit]] = defaultdict(list)
        for r in self._select("inspection_limit"):
            try:
                tg_weight = _req_f(r["tg_weight"]) if r["tg_weight"] is not None else 1.0
                if tg_weight < 0:
                    raise ValueError("tg_weight must not be negative")
                limits[r["type_code"]].append(
                    InspectionLimit(
                        r["inspection_type_code"],
                        Unit(r["unit_code"]),  # an unknown unit (e.g. a new APU_HR) is an issue, never ignored
                        _req_f(r["interval_value"]),
                        _f(r["tolerance_value"]) or 0.0,
                        tg_weight,
                        zones.get(r["inspection_type_code"]),
                    )
                )
            except PARSE_ERRORS:
                type_issues[str(r.get("type_code") or "")].append(
                    ("INSPECTION_LIMIT", str(r.get("inspection_type_code") or ""))
                )
        counters: dict[int, dict[Unit, Counter]] = defaultdict(dict)
        for r in self._select("usage_counter"):
            try:
                unit = Unit(r["unit_code"])
            except ValueError:
                continue  # a unit no limit uses is irrelevant; limits/dues in that unit are flagged above/below
            try:
                aid, counter = int(r["aircraft_id"]), Counter(unit, _req_f(r["value"]), _req_dt(r["as_of"]))
            except PARSE_ERRORS:
                continue  # the counter is then missing ⇒ DATA_MISSING for every limit that needs it
            if unit in counters[aid]:
                issues[aid].append(("COUNTER", str(unit)))  # duplicate rows: no "last one wins"
            counters[aid][unit] = counter
        dues: dict[int, list[InspectionDue]] = defaultdict(list)
        for r in self._select("aircraft_inspection_due"):
            try:
                aid = int(r["aircraft_id"])
            except PARSE_ERRORS:
                continue
            try:
                due = InspectionDue(
                    r["inspection_type_code"],
                    Unit(r["unit_code"]),
                    _f(r["due_value"]),
                    _dt(r["due_at"]),
                    _f(r["last_done_value"]),
                    _dt(r["last_done_at"]),
                )
            except PARSE_ERRORS:
                issues[aid].append(("INSPECTION", str(r.get("inspection_type_code") or "")))
                continue
            if any(d.inspection_type_code == due.inspection_type_code and d.unit == due.unit for d in dues[aid]):
                issues[aid].append(("INSPECTION", due.inspection_type_code))
            dues[aid].append(due)
        permit_types = {r["permit_type_code"]: r for r in self._select("permit_type")}
        permits: dict[int, list[Permit]] = defaultdict(list)
        for r in self._select("aircraft_permit"):
            ptype = permit_types.get(r["permit_type_code"])
            try:
                permits[int(r["aircraft_id"])].append(
                    Permit(
                        int(r["permit_id"]),
                        r["permit_type_code"],
                        r["permit_number"],
                        _req_dt(r["issued_at"]),
                        _dt(r["expires_at"]),
                        _unit(r["limit_unit_code"]),
                        _f(r["limit_value"]),
                        tuple(x.strip() for x in (r["restrictions"] or "").split(",") if x.strip()),
                        str(r["status"] or "").upper(),
                        _flag(ptype["is_grounding"]) if ptype else True,
                    )
                )
                if str(r["status"] or "").upper() not in PERMIT_STATUSES:
                    # e.g. SUSPENDED/NULL: unknown validity, and its restrictions would silently stop applying
                    issues[int(r["aircraft_id"])].append(("PERMIT", str(r["permit_type_code"])))
            except PARSE_ERRORS:
                if r.get("aircraft_id") is not None:
                    issues[int(r["aircraft_id"])].append(("PERMIT", str(r.get("permit_type_code") or "")))
        mas: dict[int, list[MaintenanceAction]] = defaultdict(list)
        # Anything not explicitly CLOSED is open: a NULL or unknown status must not hide a grounding defect.
        for r in self._select("maintenance_action", "status IS NULL OR UPPER(status) <> :closed", {"closed": "CLOSED"}):
            try:
                mas[int(r["aircraft_id"])].append(
                    MaintenanceAction(
                        ma_number=r["ma_number"],
                        title=r["title"],
                        severity=_severity(r["severity"]),
                        is_open=True,
                        opened_at=_req_dt(r["opened_at"]),
                        ata_chapter=None if r["ata_chapter"] is None else int(r["ata_chapter"]),
                        zone_code=r["zone_code"],
                        mel_category=r["mel_category"],
                        description=r["description"],
                        deferral_expires_at=_dt(r["deferral_expires_at"]),
                        deferral_limit_unit=_unit(r["deferral_limit_unit"]),
                        deferral_limit_value=_f(r["deferral_limit_value"]),
                        affects_qualification_code=r["affects_qualification_code"],
                    )
                )
            except PARSE_ERRORS:
                if r.get("aircraft_id") is not None:
                    issues[int(r["aircraft_id"])].append(("MAINTENANCE_ACTION", str(r.get("ma_number") or "")))
        quals: dict[int, dict[str, AircraftQualification]] = defaultdict(dict)
        for r in self._select("aircraft_qualification"):
            try:
                q = AircraftQualification(r["qualification_code"], _req_dt(r["valid_from"]), _dt(r["valid_to"]))
                aid = int(r["aircraft_id"])
            except PARSE_ERRORS:
                continue
            # duplicates resolve conservatively: latest start, earliest end (None = open-ended)
            prev = quals[aid].get(q.qualification_code)
            if prev is not None:
                ends = [e for e in (prev.valid_to, q.valid_to) if e is not None]
                q = AircraftQualification(
                    q.qualification_code, max(prev.valid_from, q.valid_from), min(ends) if ends else None
                )
            quals[aid][q.qualification_code] = q
        aircraft: list[Aircraft] = []
        for r in self._select("aircraft", order="aircraft_id"):
            aid = int(r["aircraft_id"])
            row_issues = list(issues[aid]) + type_issues[str(r["type_code"] or "")]
            aircraft_type = types.get(r["type_code"])
            if aircraft_type is None:
                row_issues.append(("AIRCRAFT_TYPE", str(r["type_code"] or "")))
                aircraft_type = AircraftType(str(r["type_code"] or ""), "", 0, math.nan, 0, UNKNOWN_SHAPE)
            try:
                status = OperationalStatus(str(r["status"]).upper())
            except ValueError:
                row_issues.append(("AIRCRAFT_STATUS", str(r["status"] or "")))
                status = OperationalStatus.GROUNDED
            try:
                manufactured = _date(r["manufacture_date"])
            except PARSE_ERRORS:
                manufactured = date(1970, 1, 1)
            aircraft.append(
                Aircraft(
                    aircraft_id=aid,
                    tail_number=r["tail_number"],
                    serial_number=r["serial_number"],
                    aircraft_type=aircraft_type,
                    base_code=r["base_code"],
                    status=status,
                    manufacture_date=manufactured,
                    last_flight_at=_dt(r["last_flight_at"]),
                    # unknown freshness ⇒ treated as very old ⇒ DATA_STALE
                    source_updated_at=_dt(r["source_updated_at"]) or datetime(1970, 1, 1, tzinfo=UTC),
                    counters=dict(counters[aid]),
                    inspection_limits=tuple(limits[r["type_code"]]),
                    inspection_dues=tuple(dues[aid]),
                    permits=tuple(permits[aid]),
                    maintenance_actions=tuple(mas[aid]),
                    qualifications=tuple(sorted(quals[aid].values(), key=lambda q: q.qualification_code)),
                    data_issues=tuple(row_issues),
                )
            )
        required = frozenset(c for c, p in permit_types.items() if _flag(p["is_required"]))
        load = {r["source_name"]: at for r in self._select("data_load_status") if (at := _dt(r["loaded_at"]))}
        return FleetSnapshot(tuple(aircraft), required, load)

    # ── reference / history ─────────────────────────────────────────────
    def load_reference(self) -> dict[str, Any]:
        limits: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in self._select("inspection_limit"):
            limits[r["inspection_type_code"]].append(
                {
                    "type_code": r["type_code"],
                    "unit": r["unit_code"],
                    "interval": _f(r["interval_value"]),
                    "tolerance": _f(r["tolerance_value"]) or 0.0,
                    # same rule as the snapshot: NULL ⇒ 1.0; NaN/inf/negative ⇒ unreadable (None)
                    "tg_weight": 1.0 if r["tg_weight"] is None else _nonneg(_f(r["tg_weight"])),
                }
            )
        return {
            "aircraft_types": [
                {
                    "type_code": r["type_code"],
                    "name_he": r["name_he"],
                    "engine_count": int(r["engine_count"]),
                    "max_endurance_hours": float(r["max_endurance_hours"]),
                    "idle_limit_days": int(r["idle_limit_days"]),
                    "model_shape": r["model_shape"],
                }
                for r in self._select("aircraft_type", order="type_code")
            ],
            "bases": [
                {"base_code": r["base_code"], "name_he": r["name_he"]} for r in self._select("base", order="base_code")
            ],
            "qualifications": [
                {"qualification_code": r["qualification_code"], "name_he": r["name_he"]}
                for r in self._select("qualification")
            ],
            "activity_units": [
                {"unit_code": r["unit_code"], "name_he": r["name_he"], "short_he": r["short_he"], "kind": r["kind"]}
                for r in self._select("activity_unit")
            ],
            "inspection_types": [
                {
                    "inspection_type_code": r["inspection_type_code"],
                    "name_he": r["name_he"],
                    "zone_code": r["zone_code"],
                    "limits": limits[r["inspection_type_code"]],
                }
                for r in self._select("inspection_type")
            ],
            "permit_types": [
                {
                    "permit_type_code": r["permit_type_code"],
                    "name_he": r["name_he"],
                    "is_grounding": _flag(r["is_grounding"]),
                    "is_required": _flag(r["is_required"]),
                }
                for r in self._select("permit_type")
            ],
            "zones": [{"zone_code": r["zone_code"], "name_he": r["name_he"]} for r in self._select("zone")],
        }

    def flights_since(self, since: datetime) -> list[FlightRecord]:
        rows = self._select(
            "flight_log",
            "takeoff_at >= :since",
            {"since": self._run.bind_datetime(since)},
            order="aircraft_id, takeoff_at",
        )
        return [
            FlightRecord(
                int(r["aircraft_id"]),
                r["mission_type"],
                _f(r["planned_duration_hours"]),
                _req_dt(r["takeoff_at"]),
                _req_dt(r["landing_at"]),
                float(r["flight_hours"]),
                int(r["landings"]),
                int(r["touch_and_goes"]),
                int(r["engine_starts"]),
            )
            for r in rows
        ]

    def maintenance_history(self) -> list[MaRecord]:
        return [
            MaRecord(int(r["aircraft_id"]), r["severity"], _req_dt(r["opened_at"]), _dt(r["closed_at"]))
            for r in self._select("maintenance_action", order="opened_at")
        ]

    def inspection_events(self) -> list[InspectionEventRecord]:
        return [
            InspectionEventRecord(
                int(r["aircraft_id"]), r["inspection_type_code"], _req_dt(r["performed_at"]), float(r["fh_at"])
            )
            for r in self._select("inspection_event", order="performed_at")
        ]


class SqlAlchemyRunner:
    """Runner over any SQLAlchemy engine (used for SQLite contract tests and Postgres/Vertica dialects)."""

    def __init__(self, engine: Any) -> None:
        self._engine = engine

    def fetch(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        from sqlalchemy import text

        with self._engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(text(sql), dict(params or {}))]

    def bind_datetime(self, value: datetime) -> Any:
        if self._engine.dialect.name == "sqlite":
            return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return value


def column_names(runner: QueryRunner, schema: str, view: str) -> Sequence[str]:
    prefix = f"{schema}." if schema else ""
    rows = runner.fetch(f"SELECT * FROM {prefix}ao_v_{view} WHERE 1 = 0")  # noqa: S608 — whitelisted names
    return list(rows[0].keys()) if rows else []
