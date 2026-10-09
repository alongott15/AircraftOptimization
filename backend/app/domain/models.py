"""Immutable domain models (DATABASE.md §3 mapped to frozen dataclasses).

Counter semantics: LDG and CYC counters count full-stop events only; touch-and-goes are in TG.
For an LDG/CYC limit the current value is `counter + tg_weight × TG` (BR-03).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.policy import PlanningPolicy


class Unit(StrEnum):
    FH = "FH"
    LDG = "LDG"
    TG = "TG"
    CYC = "CYC"
    ENG_START = "ENG_START"
    CAL_DAY = "CAL_DAY"
    IDLE_DAY = "IDLE_DAY"


# Source feeds that must appear in data_load_status; a feed that never loaded is stale, not fine (FR-71).
EXPECTED_FEEDS = frozenset({"flight_ops", "mro", "permits"})

COUNTER_UNITS = frozenset({Unit.FH, Unit.LDG, Unit.TG, Unit.CYC, Unit.ENG_START})
TG_WEIGHTED_UNITS = frozenset({Unit.LDG, Unit.CYC})


class OperationalStatus(StrEnum):
    OPERATIONAL = "OPERATIONAL"
    IN_MAINTENANCE = "IN_MAINTENANCE"
    GROUNDED = "GROUNDED"
    STORED = "STORED"


class Severity(StrEnum):
    GROUNDING = "GROUNDING"
    DEFERRED = "DEFERRED"
    MONITOR = "MONITOR"


class MissionType(StrEnum):
    TRAINING = "TRAINING"
    TRANSPORT = "TRANSPORT"
    FERRY = "FERRY"
    OTHER = "OTHER"


class EngineRequirement(StrEnum):
    ANY = "ANY"
    SINGLE = "SINGLE"
    TWIN = "TWIN"
    MIN_2 = "MIN_2"


class ComplianceStatus(StrEnum):
    EXPIRED = "EXPIRED"
    EXPIRING_SOON = "EXPIRING_SOON"
    OK = "OK"
    UNKNOWN = "UNKNOWN"


# Worst first — used to compute an aircraft's overall status.
STATUS_SEVERITY: Mapping[ComplianceStatus, int] = {
    ComplianceStatus.EXPIRED: 3,
    ComplianceStatus.UNKNOWN: 2,
    ComplianceStatus.EXPIRING_SOON: 1,
    ComplianceStatus.OK: 0,
}


class ItemSource(StrEnum):
    INSPECTION = "INSPECTION"
    PERMIT = "PERMIT"
    DEFERRAL = "DEFERRAL"
    QUALIFICATION = "QUALIFICATION"
    IDLE = "IDLE"


# Permit restriction → qualification it revokes (OPT §2.6, BR-08).
RESTRICTION_REVOKES: Mapping[str, str] = {
    "DAY_ONLY": "NIGHT",
    "VFR_ONLY": "IFR",
    "NO_PAX": "PAX",
    "NO_OVERWATER": "OVERWATER",
}
KNOWN_RESTRICTIONS = frozenset({*RESTRICTION_REVOKES, "FERRY_ONLY"})


@dataclass(frozen=True, slots=True)
class AircraftType:
    type_code: str
    name_he: str
    engine_count: int
    max_endurance_hours: float
    idle_limit_days: int
    model_shape: str


@dataclass(frozen=True, slots=True)
class InspectionLimit:
    inspection_type_code: str
    unit: Unit
    interval_value: float
    tolerance_value: float = 0.0
    tg_weight: float = 1.0
    zone_code: str | None = None


@dataclass(frozen=True, slots=True)
class InspectionDue:
    inspection_type_code: str
    unit: Unit
    due_value: float | None
    due_at: datetime | None
    last_done_value: float | None = None
    last_done_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class Counter:
    unit: Unit
    value: float
    as_of: datetime


@dataclass(frozen=True, slots=True)
class Permit:
    permit_id: int
    permit_type_code: str
    permit_number: str
    issued_at: datetime
    expires_at: datetime | None
    limit_unit: Unit | None
    limit_value: float | None
    restrictions: tuple[str, ...]
    status: str  # ACTIVE | REVOKED
    is_grounding: bool = True

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"


@dataclass(frozen=True, slots=True)
class MaintenanceAction:
    ma_number: str
    title: str
    severity: Severity
    is_open: bool
    opened_at: datetime
    ata_chapter: int | None = None
    zone_code: str | None = None
    mel_category: str | None = None
    description: str | None = None
    deferral_expires_at: datetime | None = None
    deferral_limit_unit: Unit | None = None
    deferral_limit_value: float | None = None
    affects_qualification_code: str | None = None
    closed_at: datetime | None = None

    @property
    def has_deferral(self) -> bool:
        return self.deferral_expires_at is not None or self.deferral_limit_unit is not None


@dataclass(frozen=True, slots=True)
class AircraftQualification:
    qualification_code: str
    valid_from: datetime
    valid_to: datetime | None


@dataclass(frozen=True, slots=True)
class CommittedMission:
    """An already-assigned mission of this tail (used for overlap and committed consumption)."""

    mission_id: int
    mission_name: str
    start_at: datetime
    duration_hours: float
    landings: int
    touch_and_goes: int
    engine_starts: int
    window_start: datetime
    window_end: datetime


@dataclass(frozen=True, slots=True)
class Aircraft:
    aircraft_id: int
    tail_number: str
    serial_number: str
    aircraft_type: AircraftType
    base_code: str
    status: OperationalStatus
    manufacture_date: date
    last_flight_at: datetime | None
    source_updated_at: datetime
    counters: Mapping[Unit, Counter] = field(default_factory=dict)
    inspection_limits: tuple[InspectionLimit, ...] = ()
    inspection_dues: tuple[InspectionDue, ...] = ()
    permits: tuple[Permit, ...] = ()
    maintenance_actions: tuple[MaintenanceAction, ...] = ()
    qualifications: tuple[AircraftQualification, ...] = ()
    commitments: tuple[CommittedMission, ...] = ()
    # (item_kind, item_code) rows the adapter could not read (unknown type, bad enum …) ⇒ DATA_MISSING (S1)
    data_issues: tuple[tuple[str, str], ...] = ()

    def counter(self, unit: Unit) -> Counter | None:
        return self.counters.get(unit)

    def due(self, inspection_type_code: str, unit: Unit) -> InspectionDue | None:
        for d in self.inspection_dues:
            if d.inspection_type_code == inspection_type_code and d.unit == unit:
                return d
        return None

    @property
    def open_mas(self) -> tuple[MaintenanceAction, ...]:
        return tuple(sorted((m for m in self.maintenance_actions if m.is_open), key=lambda m: m.ma_number))

    @property
    def has_open_grounding_ma(self) -> bool:
        return any(m.severity == Severity.GROUNDING for m in self.open_mas)


@dataclass(frozen=True, slots=True)
class MissionRequest:
    name: str
    mission_type: MissionType
    required_count: int
    start_at: datetime
    duration_hours: float
    landings: int
    touch_and_goes: int
    engine_starts: int
    departure_base_code: str
    required_qualifications: frozenset[str] = frozenset()
    engine_requirement: EngineRequirement = EngineRequirement.ANY
    same_type_required: bool = False
    force_include: frozenset[str] = frozenset()
    force_exclude: frozenset[str] = frozenset()
    mission_id: int | None = None


@dataclass(frozen=True, slots=True)
class EvaluationContext:
    """Everything the compliance engine needs besides the aircraft and the mission."""

    now: datetime
    required_permit_types: frozenset[str]
    data_load_status: Mapping[str, datetime] = field(default_factory=dict)
    expected_feeds: frozenset[str] = field(default_factory=lambda: EXPECTED_FEEDS)
    # Policy used to cost *other* (already-assigned) flights: the base policy, so a mission's own overrides
    # (e.g. 0 % buffer) never shrink the buffers of flights that are already committed.
    commitment_policy: PlanningPolicy | None = None
