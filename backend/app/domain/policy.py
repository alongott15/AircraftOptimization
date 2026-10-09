"""Planning policy and score weights (OPTIMIZATION_ENGINE.md §1.2, §3.3)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, replace
from typing import Any

SCORE_TERMS = ("risk", "best_fit", "balance", "capacity", "base_match", "warnings")

WEIGHT_PRESETS: Mapping[str, Mapping[str, float]] = {
    "BALANCED": {
        "risk": 0.30, "best_fit": 0.25, "balance": 0.15, "capacity": 0.15, "base_match": 0.10, "warnings": 0.05,
    },
    "MAX_SAFETY": {
        "risk": 0.55, "best_fit": 0.10, "balance": 0.10, "capacity": 0.10, "base_match": 0.05, "warnings": 0.10,
    },
    "MAX_AVAILABILITY": {
        "risk": 0.15, "best_fit": 0.35, "balance": 0.20, "capacity": 0.20, "base_match": 0.05, "warnings": 0.05,
    },
}


@dataclass(frozen=True, slots=True)
class PlanningPolicy:
    duration_buffer_pct: float = 15.0
    min_duration_buffer_hours: float = 0.3
    calendar_buffer_hours: float = 2.0
    landing_buffer: int = 1
    turnaround_hours: float = 1.0
    idle_policy: str = "EXCLUDE"  # EXCLUDE | WARN
    tolerance_policy: str = "WARN_ONLY"  # tolerances never grant eligibility in the POC (S6)
    expiring_soon_days: float = 7.0
    expiring_soon_pct: float = 10.0
    expiring_soon_fh: float = 5.0
    expiring_soon_ldg: float = 10.0
    low_margin_pct: float = 5.0
    max_staleness_hours: float = 24.0
    risk_high_threshold: float = 0.30
    weights_preset: str = "BALANCED"

    def with_overrides(self, overrides: Mapping[str, Any] | None) -> PlanningPolicy:
        if not overrides:
            return self
        names = {f.name for f in fields(self)}
        clean = {k: v for k, v in overrides.items() if k in names and v is not None}
        return replace(self, **clean)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def weights(self) -> Mapping[str, float]:
        return WEIGHT_PRESETS.get(self.weights_preset, WEIGHT_PRESETS["BALANCED"])


POLICY_RANGES: Mapping[str, tuple[float, float]] = {
    "duration_buffer_pct": (0, 100),
    "min_duration_buffer_hours": (0, 5),
    "calendar_buffer_hours": (0, 72),
    "landing_buffer": (0, 10),
    "turnaround_hours": (0, 24),
    "expiring_soon_days": (0, 90),
    "expiring_soon_pct": (0, 50),
    "expiring_soon_fh": (0, 100),
    "expiring_soon_ldg": (0, 200),
    "low_margin_pct": (0, 50),
    "max_staleness_hours": (1, 168),
    "risk_high_threshold": (0, 1),
}
