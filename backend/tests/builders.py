"""Test data builders (CODE_STYLE §5): an_aircraft().with_inspection(...).build()."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from app.domain.models import (
    EXPECTED_FEEDS,
    Aircraft,
    AircraftQualification,
    AircraftType,
    CommittedMission,
    Counter,
    EngineRequirement,
    EvaluationContext,
    InspectionDue,
    InspectionLimit,
    MaintenanceAction,
    MissionRequest,
    MissionType,
    OperationalStatus,
    Permit,
    Severity,
    Unit,
)

T0 = datetime(2026, 1, 15, 8, 0, tzinfo=UTC)
REQUIRED_PERMITS = frozenset({"COA", "REG", "RADIO", "INSURANCE"})
TYPES = {
    "SE-100": AircraftType("SE-100", "SE", 1, 4.5, 30, "SINGLE_PROP_LOW_WING"),
    "TP-200": AircraftType("TP-200", "TP", 2, 6.0, 45, "TWIN_TURBOPROP_HIGH_WING"),
    "JT-300": AircraftType("JT-300", "JT", 2, 9.0, 45, "TWIN_JET_LOW_WING"),
}


class AircraftBuilder:
    def __init__(self, type_code: str = "TP-200") -> None:
        self.type = TYPES[type_code]
        self.counters = {Unit.FH: 1000.0, Unit.LDG: 500.0, Unit.TG: 100.0, Unit.CYC: 500.0, Unit.ENG_START: 1000.0}
        # Every real type has an inspection programme; a type without one is DATA_MISSING (BR-01).
        self.limits: list[InspectionLimit] = [InspectionLimit("IDLE_CHECK", Unit.IDLE_DAY, self.type.idle_limit_days),
                                              InspectionLimit("BASE_ANNUAL", Unit.CAL_DAY, 365)]
        self.dues: list[InspectionDue] = [InspectionDue("BASE_ANNUAL", Unit.CAL_DAY, None, T0 + timedelta(days=300))]
        self.permits = [Permit(i, code, f"{code}-1", T0 - timedelta(days=100), T0 + timedelta(days=200), None, None,
                               (), "ACTIVE") for i, code in enumerate(sorted(REQUIRED_PERMITS), start=1)]
        self.mas: list[MaintenanceAction] = []
        self.quals = [AircraftQualification(q, T0 - timedelta(days=500), None) for q in ("IFR", "NIGHT", "PAX")]
        self.status = OperationalStatus.OPERATIONAL
        self.last_flight: datetime | None = T0 - timedelta(days=1)
        self.commitments: list[CommittedMission] = []
        self.source_updated_at = T0
        self.tail = "4X-T01"

    def tail_number(self, tail: str) -> AircraftBuilder:
        self.tail = tail
        return self

    def counter(self, unit: Unit, value: float | None) -> AircraftBuilder:
        if value is None:
            self.counters.pop(unit, None)
        else:
            self.counters[unit] = value
        return self

    def limit(self, code: str, unit: Unit, interval: float, *, due_value: float | None = None,
              due_at: datetime | None = None, tolerance: float = 0.0, tg_weight: float = 1.0,
              add_due: bool = True) -> AircraftBuilder:
        self.limits.append(InspectionLimit(code, unit, interval, tolerance, tg_weight))
        if add_due:
            self.dues.append(InspectionDue(code, unit, due_value, due_at))
        return self

    def remaining(self, code: str, unit: Unit, remaining: float, interval: float = 100.0,
                  tg_weight: float = 1.0, tolerance: float = 0.0) -> AircraftBuilder:
        current = self.counters[unit] + (tg_weight * self.counters[Unit.TG] if unit in (Unit.LDG, Unit.CYC) else 0)
        return self.limit(code, unit, interval, due_value=current + remaining, tg_weight=tg_weight,
                          tolerance=tolerance)

    def permit(self, code: str, expires_at: datetime | None, restrictions: tuple[str, ...] = (),
               status: str = "ACTIVE") -> AircraftBuilder:
        self.permits = [p for p in self.permits if p.permit_type_code != code]
        self.permits.append(Permit(len(self.permits) + 10, code, f"{code}-X", T0 - timedelta(days=10), expires_at,
                                   None, None, restrictions, status))
        return self

    def no_permit(self, code: str) -> AircraftBuilder:
        self.permits = [p for p in self.permits if p.permit_type_code != code]
        return self

    def ma(self, number: str, severity: Severity = Severity.DEFERRED, **kw: object) -> AircraftBuilder:
        self.mas.append(MaintenanceAction(ma_number=number, title="t", severity=severity, is_open=True,
                                          opened_at=T0 - timedelta(days=1), **kw))  # type: ignore[arg-type]
        return self

    def qual(self, code: str, valid_to: datetime | None = None) -> AircraftBuilder:
        self.quals = [q for q in self.quals if q.qualification_code != code]
        self.quals.append(AircraftQualification(code, T0 - timedelta(days=500), valid_to))
        return self

    def with_status(self, status: OperationalStatus) -> AircraftBuilder:
        self.status = status
        return self

    def last_flight_at(self, at: datetime | None) -> AircraftBuilder:
        self.last_flight = at
        return self

    def commitment(self, mission_id: int, start: datetime, hours: float, landings: int = 1, tg: int = 0,
                   window_end: datetime | None = None) -> AircraftBuilder:
        end = window_end or start + timedelta(hours=hours + 1.3)
        self.commitments.append(CommittedMission(mission_id, f"M{mission_id}", start, hours, landings, tg, 1,
                                                 start, end))
        return self

    def updated_at(self, at: datetime) -> AircraftBuilder:
        self.source_updated_at = at
        return self

    def build(self, aircraft_id: int = 1) -> Aircraft:
        return Aircraft(
            aircraft_id=aircraft_id, tail_number=self.tail, serial_number="SN", aircraft_type=self.type,
            base_code="CEN", status=self.status, manufacture_date=date(2015, 1, 1), last_flight_at=self.last_flight,
            source_updated_at=self.source_updated_at,
            counters={u: Counter(u, v, T0) for u, v in self.counters.items()},
            inspection_limits=tuple(self.limits), inspection_dues=tuple(self.dues), permits=tuple(self.permits),
            maintenance_actions=tuple(self.mas), qualifications=tuple(self.quals),
            commitments=tuple(self.commitments))


def an_aircraft(type_code: str = "TP-200") -> AircraftBuilder:
    return AircraftBuilder(type_code)


def ctx(now: datetime = T0, load: dict[str, datetime] | None = None) -> EvaluationContext:
    feeds = load if load is not None else dict.fromkeys(EXPECTED_FEEDS, now)
    return EvaluationContext(now=now, required_permit_types=REQUIRED_PERMITS, data_load_status=feeds)


def a_mission(**kw: object) -> MissionRequest:
    base = MissionRequest(
        name="test", mission_type=MissionType.TRANSPORT, required_count=1, start_at=T0 + timedelta(days=1),
        duration_hours=4.0, landings=2, touch_and_goes=0, engine_starts=1, departure_base_code="CEN",
        required_qualifications=frozenset(), engine_requirement=EngineRequirement.ANY)
    return replace(base, **kw)  # type: ignore[arg-type]
