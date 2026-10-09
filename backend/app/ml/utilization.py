"""Utilization forecast: EWMA daily rates with shrinkage to the type mean (OPTIMIZATION_ENGINE.md §4.2)."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.datasources.base import FlightRecord

WINDOW_DAYS = 90
HALF_LIFE_DAYS = 14.0
SHRINK_K = 10.0
UNITS = ("FH", "LDG", "TG", "CYC", "ENG_START")


@dataclass(frozen=True, slots=True)
class Utilization:
    rates: Mapping[int, Mapping[str, float]]  # aircraft_id → unit → per-day rate
    fh_last_30d: Mapping[int, float]
    type_fh30: Mapping[str, tuple[float, float]]  # type → (mean, sd)


def ewma(values: list[float], half_life: float = HALF_LIFE_DAYS) -> float:
    """Exponentially weighted mean; values[-1] is the most recent day."""
    if not values:
        return 0.0
    decay = math.log(2) / half_life
    weights = [math.exp(-decay * (len(values) - 1 - i)) for i in range(len(values))]
    return sum(w * v for w, v in zip(weights, values, strict=True)) / sum(weights)


def compute_utilization(
    flights: list[FlightRecord], now: datetime, aircraft_types: Mapping[int, str], engine_counts: Mapping[int, int]
) -> Utilization:
    start = now - timedelta(days=WINDOW_DAYS)
    daily: dict[int, dict[str, list[float]]] = {
        a: {u: [0.0] * WINDOW_DAYS for u in UNITS} for a in aircraft_types
    }
    flying_days: dict[int, set[int]] = defaultdict(set)
    fh30: dict[int, float] = dict.fromkeys(aircraft_types, 0.0)
    for f in flights:
        if f.aircraft_id not in daily or f.takeoff_at < start or f.takeoff_at >= now:
            continue
        day = int((f.takeoff_at - start) / timedelta(days=1))
        d = daily[f.aircraft_id]
        d["FH"][day] += f.flight_hours
        d["LDG"][day] += f.landings
        d["TG"][day] += f.touch_and_goes
        d["CYC"][day] += f.landings
        d["ENG_START"][day] += f.engine_starts * engine_counts.get(f.aircraft_id, 1)
        flying_days[f.aircraft_id].add(day)
        if f.takeoff_at >= now - timedelta(days=30):
            fh30[f.aircraft_id] += f.flight_hours
    own = {a: {u: ewma(series) for u, series in units.items()} for a, units in daily.items()}
    by_type: dict[str, list[int]] = defaultdict(list)
    for a, t in aircraft_types.items():
        by_type[t].append(a)
    rates: dict[int, dict[str, float]] = {}
    for a, t in aircraft_types.items():
        peers = by_type[t]
        n = len(flying_days[a])
        weight = n / (n + SHRINK_K)
        rates[a] = {}
        for u in UNITS:
            type_mean = sum(own[p][u] for p in peers) / len(peers)
            rates[a][u] = weight * own[a][u] + (1 - weight) * type_mean
    type_fh30: dict[str, tuple[float, float]] = {}
    for t, peers in by_type.items():
        values = [fh30[p] for p in peers]
        mean = sum(values) / len(values)
        sd = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        type_fh30[t] = (mean, sd)
    return Utilization(rates, fh30, type_fh30)
