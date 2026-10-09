"""Mission consumption per activity unit (OPTIMIZATION_ENGINE.md §2.1, BR-02/03)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.models import CommittedMission, MissionRequest, Unit
from app.domain.policy import PlanningPolicy


@dataclass(frozen=True, slots=True)
class FlightLoad:
    duration_hours: float
    landings: int
    touch_and_goes: int
    engine_starts: int

    @staticmethod
    def of_mission(m: MissionRequest) -> FlightLoad:
        return FlightLoad(m.duration_hours, m.landings, m.touch_and_goes, m.engine_starts)

    @staticmethod
    def of_commitment(c: CommittedMission) -> FlightLoad:
        return FlightLoad(c.duration_hours, c.landings, c.touch_and_goes, c.engine_starts)


def buffered_hours(duration_hours: float, policy: PlanningPolicy) -> float:
    """FH consumption = D + max(D × pct, floor). Also used for endurance (R4)."""
    if duration_hours <= 0:
        return 0.0
    buffer = max(duration_hours * policy.duration_buffer_pct / 100.0, policy.min_duration_buffer_hours)
    return duration_hours + buffer


def consumption(unit: Unit, load: FlightLoad, policy: PlanningPolicy, engine_count: int, tg_weight: float) -> float:
    """Projected consumption of one flight for a limit in `unit` (tg_weight from that limit)."""
    if unit == Unit.FH:
        return buffered_hours(load.duration_hours, policy)
    if unit in (Unit.LDG, Unit.CYC):
        # BR-03: full-stop landings + landing buffer + weighted touch-and-goes
        return load.landings + policy.landing_buffer + tg_weight * load.touch_and_goes
    if unit == Unit.TG:
        return float(load.touch_and_goes)
    if unit == Unit.ENG_START:
        return float(load.engine_starts * engine_count)
    return 0.0


def mission_end(start_at: datetime, duration_hours: float) -> datetime:
    return start_at + timedelta(hours=duration_hours)


def calendar_deadline(start_at: datetime, duration_hours: float, policy: PlanningPolicy) -> datetime:
    """Calendar items must be valid until here (BR-04)."""
    return start_at + timedelta(hours=duration_hours + policy.calendar_buffer_hours)


def mission_window(m: MissionRequest, policy: PlanningPolicy) -> tuple[datetime, datetime]:
    """Occupied window for overlap checks: start → start + buffered duration + turnaround."""
    end = m.start_at + timedelta(hours=buffered_hours(m.duration_hours, policy) + policy.turnaround_hours)
    return m.start_at, end
