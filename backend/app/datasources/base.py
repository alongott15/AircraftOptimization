"""FleetDataSource protocol + the snapshot it returns (ARCHITECTURE.md §4)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from app.domain.models import Aircraft


@dataclass(frozen=True, slots=True)
class FleetSnapshot:
    aircraft: tuple[Aircraft, ...]
    required_permit_types: frozenset[str]
    data_load_status: Mapping[str, datetime] = field(default_factory=dict)

    def by_tail(self) -> dict[str, Aircraft]:
        return {a.tail_number: a for a in self.aircraft}

    def by_id(self) -> dict[int, Aircraft]:
        return {a.aircraft_id: a for a in self.aircraft}


@dataclass(frozen=True, slots=True)
class FlightRecord:
    aircraft_id: int
    mission_type: str
    planned_duration_hours: float | None
    takeoff_at: datetime
    landing_at: datetime
    flight_hours: float
    landings: int
    touch_and_goes: int
    engine_starts: int


@dataclass(frozen=True, slots=True)
class MaRecord:
    aircraft_id: int
    severity: str
    opened_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class InspectionEventRecord:
    aircraft_id: int
    inspection_type_code: str
    performed_at: datetime
    fh_at: float


class FleetDataSource(Protocol):
    def load_snapshot(self) -> FleetSnapshot: ...

    def load_reference(self) -> dict[str, Any]: ...

    def flights_since(self, since: datetime) -> list[FlightRecord]: ...

    def maintenance_history(self) -> list[MaRecord]: ...

    def inspection_events(self) -> list[InspectionEventRecord]: ...
