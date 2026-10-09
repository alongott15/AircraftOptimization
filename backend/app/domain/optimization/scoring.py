"""Score terms for eligible aircraft (OPTIMIZATION_ENGINE.md §3). Never changes eligibility (BR-14)."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.compliance.evaluator import EligibilityResult
from app.domain.models import Unit
from app.domain.numeric import r2
from app.domain.optimization.expiry_window import NO_WINDOWS, WindowResult
from app.domain.policy import SCORE_TERMS, PlanningPolicy
from app.domain.reason_codes import Reason, WarningCode

# Horizon per unit for best-fit normalization (OPT §3).
BEST_FIT_HORIZON: Mapping[str, float] = {
    str(Unit.FH): 50.0, str(Unit.LDG): 100.0, str(Unit.CYC): 100.0, str(Unit.TG): 100.0, str(Unit.ENG_START): 100.0,
}


@dataclass(frozen=True, slots=True)
class ScoringInputs:
    """Per-aircraft inputs from services/ML. All optional ML values have neutral fallbacks."""

    type_code: str
    base_code: str
    p_defect: float | None
    fh_last_30d: float
    type_mean_fh30: float
    type_sd_fh30: float
    week_before: str | None = None
    week_after: str | None = None
    hangar_load_after: int = 0
    hangar_cap_after: int = 0
    windows: WindowResult = NO_WINDOWS  # planner expiration windows (OPT §3.4)


@dataclass(frozen=True, slots=True)
class TermScore:
    value: float
    weight: float
    contribution: float

    def to_dict(self) -> dict[str, float]:
        return {"value": r2(self.value), "weight": self.weight, "contribution": r2(self.contribution)}


@dataclass(frozen=True, slots=True)
class ScoredCandidate:
    result: EligibilityResult
    inputs: ScoringInputs
    score: float
    breakdown: Mapping[str, TermScore]
    warnings: tuple[Reason, ...]

    @property
    def aircraft_id(self) -> int:
        return self.result.aircraft_id


def best_fit(result: EligibilityResult) -> float:
    """Tight fit of the binding usage limit is preferred (classic best-fit bin packing)."""
    binding: tuple[float, float, str] | None = None
    for p in result.projections:
        if p.unit not in BEST_FIT_HORIZON or not p.interval or p.remaining_after is None or p.outcome != "OK":
            continue
        normalized = p.remaining_after / p.interval
        if binding is None or normalized < binding[0]:
            binding = (normalized, p.remaining_after, p.unit)
    if binding is None:
        return 0.5
    horizon = BEST_FIT_HORIZON[binding[2]]
    return 1.0 - min(max(binding[1], 0.0), horizon) / horizon


def balance(inputs: ScoringInputs) -> float:
    if inputs.type_sd_fh30 <= 0:
        return 0.5
    z = (inputs.type_mean_fh30 - inputs.fh_last_30d) / inputs.type_sd_fh30
    return 1.0 / (1.0 + math.exp(-z))


def capacity(inputs: ScoringInputs) -> float:
    if inputs.week_after is None or inputs.hangar_cap_after <= 0:
        return 1.0
    return inputs.hangar_cap_after / max(inputs.hangar_cap_after, inputs.hangar_load_after + 1)


def score_candidate(
    result: EligibilityResult, inputs: ScoringInputs, departure_base_code: str, policy: PlanningPolicy
) -> ScoredCandidate:
    if not result.eligible:
        raise ValueError("Only eligible aircraft can be scored (BR-14)")
    warnings = list(result.warnings)
    if inputs.p_defect is not None and inputs.p_defect >= policy.risk_high_threshold:
        warnings.append(Reason(WarningCode.HIGH_DEFECT_RISK, {"probability": round(inputs.p_defect * 100)}))
    values = {
        "risk": 1.0 - inputs.p_defect if inputs.p_defect is not None else 0.5,
        "best_fit": best_fit(result),
        "balance": balance(inputs),
        "capacity": capacity(inputs),
        "base_match": 1.0 if inputs.base_code == departure_base_code else 0.0,
        "warnings": 1.0 - min(1.0, 0.25 * len(warnings)),  # window hits have their own term — not counted twice
        "expiry_window": inputs.windows.value,
    }
    warnings.extend(inputs.windows.warnings)
    weights = dict(policy.weights)
    if inputs.windows.total == 0:
        weights["expiry_window"] = 0.0  # inactive term: renormalization keeps the other terms' scores unchanged
    total_weight = sum(weights.get(t, 0.0) for t in SCORE_TERMS) or 1.0
    breakdown = {
        t: TermScore(values[t], weights.get(t, 0.0), 100.0 * weights.get(t, 0.0) * values[t] / total_weight)
        for t in SCORE_TERMS
    }
    score = sum(b.contribution for b in breakdown.values())
    return ScoredCandidate(result, inputs, r2(score), breakdown, tuple(warnings))
