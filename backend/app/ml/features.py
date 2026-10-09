"""Feature builders for the defect-risk and duration-buffer models (OPTIMIZATION_ENGINE.md §4.1, §4.3).

Leakage rule: features at time t use only events strictly before t.
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.datasources.base import FlightRecord, InspectionEventRecord, MaRecord

TYPES = ("SE-100", "TP-200", "JT-300")
BASES = ("NTH", "CEN", "STH")
MISSION_TYPES = ("TRAINING", "TRANSPORT", "FERRY", "OTHER")
HORIZON_FH = 50.0
HORIZON_DAYS = 30

RISK_FEATURES = (
    "type_SE-100", "type_TP-200", "type_JT-300", "base_NTH", "base_CEN", "base_STH",
    "age_years", "fh_total", "fh_since_major", "days_since_major", "open_deferred", "defects_90d",
    "grounding_365d", "defects_365d", "defects_per_100fh_365d", "fh_30d", "ldg_30d", "tg_ratio_30d", "idle_days",
    "mean_sortie_30d",
)
DURATION_FEATURES = (
    "mt_TRAINING", "mt_TRANSPORT", "mt_FERRY", "mt_OTHER", "type_SE-100", "type_TP-200", "type_JT-300",
    "planned_hours", "landings", "touch_and_goes", "month_sin", "month_cos",
)


@dataclass(frozen=True, slots=True)
class AircraftMeta:
    aircraft_id: int
    type_code: str
    base_code: str
    manufacture_date: date
    fh_now: float | None


@dataclass
class History:
    meta: dict[int, AircraftMeta]
    flights: dict[int, list[FlightRecord]] = field(default_factory=dict)
    landing_times: dict[int, list[float]] = field(default_factory=dict)
    cum_fh: dict[int, list[float]] = field(default_factory=dict)
    mas: dict[int, list[MaRecord]] = field(default_factory=dict)
    events: dict[int, list[InspectionEventRecord]] = field(default_factory=dict)

    @staticmethod
    def build(meta: dict[int, AircraftMeta], flights: list[FlightRecord], mas: list[MaRecord],
              events: list[InspectionEventRecord]) -> History:
        h = History(meta)
        by_ac: dict[int, list[FlightRecord]] = defaultdict(list)
        for f in flights:
            by_ac[f.aircraft_id].append(f)
        for a, fl in by_ac.items():
            fl.sort(key=lambda f: f.landing_at)
            h.flights[a] = fl
            h.landing_times[a] = [f.landing_at.timestamp() for f in fl]
            acc, cum = 0.0, []
            for f in fl:
                acc += f.flight_hours
                cum.append(acc)
            h.cum_fh[a] = cum
        mas_by: dict[int, list[MaRecord]] = defaultdict(list)
        for m in mas:
            mas_by[m.aircraft_id].append(m)
        h.mas = dict(mas_by)
        ev_by: dict[int, list[InspectionEventRecord]] = defaultdict(list)
        for e in events:
            ev_by[e.aircraft_id].append(e)
        h.events = {a: sorted(v, key=lambda e: e.performed_at) for a, v in ev_by.items()}
        return h

    def _idx(self, a: int, t: datetime) -> int:
        """Number of flights that landed strictly before t."""
        return bisect.bisect_left(self.landing_times.get(a, []), t.timestamp())

    def fh_flown_between(self, a: int, t0: datetime, t1: datetime) -> float:
        cum = self.cum_fh.get(a, [])
        i0, i1 = self._idx(a, t0), self._idx(a, t1)
        before0 = cum[i0 - 1] if i0 > 0 else 0.0
        before1 = cum[i1 - 1] if i1 > 0 else 0.0
        return before1 - before0


def risk_features(h: History, a: int, t: datetime) -> list[float]:
    m = h.meta[a]
    nan = float("nan")
    fl = h.flights.get(a, [])
    idx = h._idx(a, t)
    total_flown = h.cum_fh[a][-1] if h.cum_fh.get(a) else 0.0
    flown_before = h.cum_fh[a][idx - 1] if idx > 0 else 0.0
    fh_total = (m.fh_now - (total_flown - flown_before)) if m.fh_now is not None else nan
    past_events = [e for e in h.events.get(a, []) if e.performed_at < t]
    if past_events and not math.isnan(fh_total):
        last = past_events[-1]
        fh_since_major = max(fh_total - last.fh_at, 0.0)
        days_since_major = (t - last.performed_at) / timedelta(days=1)
    else:
        fh_since_major, days_since_major = nan, nan
    mas = h.mas.get(a, [])
    open_deferred = sum(1 for x in mas if x.severity == "DEFERRED" and x.opened_at < t
                        and (x.closed_at is None or x.closed_at > t))
    defects_90d = sum(1 for x in mas if t - timedelta(days=90) <= x.opened_at < t)
    grounding_365d = sum(1 for x in mas if x.severity == "GROUNDING" and t - timedelta(days=365) <= x.opened_at < t)
    defects_365d = sum(1 for x in mas if t - timedelta(days=365) <= x.opened_at < t)
    fh_365d = h.fh_flown_between(a, t - timedelta(days=365), t)
    defects_per_100fh = 100.0 * defects_365d / fh_365d if fh_365d > 20 else nan
    recent = [f for f in fl[:idx] if f.takeoff_at >= t - timedelta(days=30)]
    fh_30d = sum(f.flight_hours for f in recent)
    ldg_30d = float(sum(f.landings for f in recent))
    tg_30d = float(sum(f.touch_and_goes for f in recent))
    tg_ratio = tg_30d / (ldg_30d + tg_30d) if (ldg_30d + tg_30d) > 0 else 0.0
    idle_days = (t - fl[idx - 1].landing_at) / timedelta(days=1) if idx > 0 else nan
    mean_sortie = fh_30d / len(recent) if recent else 0.0
    age_years = (t.date() - m.manufacture_date).days / 365.25
    return [
        *(1.0 if m.type_code == x else 0.0 for x in TYPES),
        *(1.0 if m.base_code == x else 0.0 for x in BASES),
        age_years, fh_total, fh_since_major, days_since_major, float(open_deferred), float(defects_90d),
        float(grounding_365d), float(defects_365d), defects_per_100fh, fh_30d, ldg_30d, tg_ratio, idle_days,
        mean_sortie,
    ]


def risk_label(h: History, a: int, t: datetime) -> int | None:
    """1 if a grounding MA opens before 50 FH are flown after t (or within 30 days). None if not observable."""
    fl = h.flights.get(a, [])
    idx = h._idx(a, t)
    end = t + timedelta(days=HORIZON_DAYS)
    flown = 0.0
    for f in fl[idx:]:
        flown += f.flight_hours
        if flown >= HORIZON_FH:
            end = min(end, f.landing_at)
            break
    return int(any(x.severity == "GROUNDING" and t < x.opened_at <= end for x in h.mas.get(a, [])))


def duration_features(mission_type: str, type_code: str, planned_hours: float, landings: int,
                      touch_and_goes: int, month: int) -> list[float]:
    angle = 2 * math.pi * (month - 1) / 12
    return [
        *(1.0 if mission_type == x else 0.0 for x in MISSION_TYPES),
        *(1.0 if type_code == x else 0.0 for x in TYPES),
        planned_hours, float(landings), float(touch_and_goes), math.sin(angle), math.cos(angle),
    ]
