"""Aircraft-set selection with OR-Tools CP-SAT (OPTIMIZATION_ENGINE.md §5).

Only eligible candidates enter the model, so the chosen set can never contain an ineligible aircraft.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass

from ortools.sat.python import cp_model

SCALE = 1000
OVERFLOW_PENALTY = 15.0  # score points per aircraft-week of hangar overflow (λ)


@dataclass(frozen=True, slots=True)
class SelectionCandidate:
    aircraft_id: int
    type_code: str
    base_code: str
    score: float
    week_before: str | None = None
    week_after: str | None = None


@dataclass(frozen=True, slots=True)
class SelectionSet:
    set_index: int
    aircraft_ids: tuple[int, ...]
    objective: float


@dataclass(frozen=True, slots=True)
class SelectionResult:
    status: str  # OPTIMAL | FEASIBLE | INFEASIBLE | SKIPPED
    sets: tuple[SelectionSet, ...]
    shortfall: int


class ForcedTailConflictError(ValueError):
    """Force-included tails cannot be satisfied together (e.g. more than N, or mixed types with same-type)."""


def select(
    candidates: list[SelectionCandidate],
    required: int,
    *,
    force_include_ids: frozenset[int] = frozenset(),
    same_type: bool = False,
    base_load: Mapping[tuple[str, str], int] | None = None,
    capacity: Mapping[tuple[str, str], int] | None = None,
    max_alternatives: int = 3,
    alternative_ratio: float = 0.85,
    time_limit_s: float = 2.0,
    workers: int = 1,
) -> SelectionResult:
    cands = sorted(candidates, key=lambda c: c.aircraft_id)
    if not cands:
        return SelectionResult("SKIPPED", (), required)
    forced = sorted(force_include_ids & {c.aircraft_id for c in cands})
    if len(forced) > required:
        raise ForcedTailConflictError("more forced aircraft than required")

    by_type: dict[str, int] = defaultdict(int)
    for c in cands:
        by_type[c.type_code] += 1
    available = max(by_type.values()) if same_type else len(cands)
    target = min(required, available)
    shortfall = required - target

    model = cp_model.CpModel()
    x = {c.aircraft_id: model.new_bool_var(f"x_{c.aircraft_id}") for c in cands}
    model.add(sum(x.values()) == target)
    for a in forced:
        model.add(x[a] == 1)
    if same_type:
        y = {t: model.new_bool_var(f"y_{t}") for t in sorted(by_type)}
        model.add(sum(y.values()) == 1)
        for c in cands:
            model.add(x[c.aircraft_id] <= y[c.type_code])

    overflow_terms = _hangar_overflow(model, cands, x, base_load or {}, capacity or {})
    score_expr = sum(round(c.score * SCALE) * x[c.aircraft_id] for c in cands)
    penalty = round(OVERFLOW_PENALTY * SCALE)
    model.maximize(score_expr - penalty * sum(overflow_terms))

    sets: list[SelectionSet] = []
    status_name = "INFEASIBLE"
    for idx in range(max_alternatives + 1):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_limit_s
        solver.parameters.num_workers = workers
        solver.parameters.random_seed = 42
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            if idx == 0:
                raise ForcedTailConflictError("no feasible selection with the forced tails")
            break
        chosen = tuple(a for a in sorted(x) if solver.value(x[a]) == 1)
        objective = solver.objective_value / SCALE
        if idx == 0:
            status_name = "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE"
        elif sets and sets[0].objective > 0 and objective < alternative_ratio * sets[0].objective:
            break
        sets.append(SelectionSet(idx, chosen, round(objective, 2)))
        if not chosen:
            break
        model.add(sum(x[a] for a in chosen) <= len(chosen) - 1)  # no-good cut → next alternative
    return SelectionResult(status_name, tuple(sets), shortfall)


def _hangar_overflow(
    model: cp_model.CpModel,
    cands: list[SelectionCandidate],
    x: Mapping[int, cp_model.IntVar],
    base_load: Mapping[tuple[str, str], int],
    capacity: Mapping[tuple[str, str], int],
) -> list[cp_model.IntVar]:
    """o_w ≥ base_load + Σ moved-in − Σ moved-out − cap, for each (base, week) a candidate can touch."""
    keys: set[tuple[str, str]] = set()
    for c in cands:
        if c.week_before:
            keys.add((c.base_code, c.week_before))
        if c.week_after:
            keys.add((c.base_code, c.week_after))
    out: list[cp_model.IntVar] = []
    for base, week in sorted(keys):
        cap = capacity.get((base, week))
        if cap is None:
            continue
        moved_in = [x[c.aircraft_id] for c in cands
                    if c.base_code == base and c.week_after == week and c.week_before != week]
        moved_out = [x[c.aircraft_id] for c in cands
                     if c.base_code == base and c.week_before == week and c.week_after != week]
        if not moved_in and not moved_out:
            continue
        o = model.new_int_var(0, len(cands) + base_load.get((base, week), 0), f"o_{base}_{week}")
        model.add(o >= base_load.get((base, week), 0) + sum(moved_in) - sum(moved_out) - cap)
        out.append(o)
    return out
