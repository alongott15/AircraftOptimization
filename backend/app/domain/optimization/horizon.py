"""Multi-mission horizon planner (FR-46, OPTIMIZATION_ENGINE.md §5.4).

Assigns aircraft to several missions at once. Only (aircraft, mission) pairs that passed the compliance engine enter
the model. On top of per-mission eligibility it enforces what single-mission optimization cannot see:
  * no aircraft on two overlapping missions;
  * cumulative usage — the consumption of every mission an aircraft flies in the plan must fit under each of its
    counter limits (inspections, usage-limited permits, deferrals) at the same time.
Shortfall is allowed but heavily penalized, so the solver fills as many seats as possible before optimizing scores.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ortools.sat.python import cp_model

SCALE = 1000
USAGE_SCALE = 100  # usage in 1/100 units; consumption rounded UP and room rounded DOWN (fail-safe)
SHORTFALL_PENALTY = 1000.0  # score points per unfilled seat — dominates any score difference

LimitKey = str  # "<source>:<ref>:<unit>"


@dataclass(frozen=True, slots=True)
class HorizonMission:
    mission_id: int
    required: int
    same_type: bool = False
    forced: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class HorizonOption:
    """An eligible (aircraft, mission) pair with its score."""

    aircraft_id: int
    mission_id: int
    score: float
    type_code: str


@dataclass(frozen=True, slots=True)
class HorizonPlan:
    status: str  # OPTIMAL | FEASIBLE | INFEASIBLE | SKIPPED
    assignments: Mapping[int, tuple[int, ...]]
    shortfall: Mapping[int, int]
    objective: float


@dataclass(frozen=True, slots=True)
class Violation:
    code: str  # NOT_ELIGIBLE | OVERLAP | CUMULATIVE_LIMIT | OVER_REQUIRED | DUPLICATE
    mission_id: int
    aircraft_id: int | None = None
    detail: str = ""


def _scaled_use(value: float) -> int:
    return math.ceil(max(value, 0.0) * USAGE_SCALE - 1e-9)


def _scaled_room(value: float) -> int:
    return math.floor(max(value, 0.0) * USAGE_SCALE + 1e-9)


def plan_horizon(
    missions: Sequence[HorizonMission],
    options: Sequence[HorizonOption],
    overlaps: frozenset[tuple[int, int]],
    room: Mapping[tuple[int, LimitKey], float],
    use: Mapping[tuple[int, int, LimitKey], float],
    *,
    time_limit_s: float = 5.0,
    workers: int = 1,
) -> HorizonPlan:
    if not missions:
        return HorizonPlan("SKIPPED", {}, {}, 0.0)
    opts = sorted(options, key=lambda o: (o.mission_id, o.aircraft_id))
    model = cp_model.CpModel()
    x = {(o.aircraft_id, o.mission_id): model.new_bool_var(f"x_{o.aircraft_id}_{o.mission_id}") for o in opts}
    by_mission: dict[int, list[HorizonOption]] = defaultdict(list)
    by_aircraft: dict[int, list[HorizonOption]] = defaultdict(list)
    for o in opts:
        by_mission[o.mission_id].append(o)
        by_aircraft[o.aircraft_id].append(o)

    shortfall_vars = {}
    for m in sorted(missions, key=lambda m: m.mission_id):
        seats = [x[(o.aircraft_id, m.mission_id)] for o in by_mission[m.mission_id]]
        short = model.new_int_var(0, m.required, f"short_{m.mission_id}")
        model.add(sum(seats) + short == m.required)
        shortfall_vars[m.mission_id] = short
        for aid in sorted(m.forced):
            if (aid, m.mission_id) in x:
                model.add(x[(aid, m.mission_id)] == 1)
        if m.same_type:
            types = sorted({o.type_code for o in by_mission[m.mission_id]})
            if types:
                y = {t: model.new_bool_var(f"y_{m.mission_id}_{t}") for t in types}
                model.add(sum(y.values()) <= 1)
                for o in by_mission[m.mission_id]:
                    model.add(x[(o.aircraft_id, m.mission_id)] <= y[o.type_code])

    for aid, aircraft_opts in sorted(by_aircraft.items()):
        missions_of = {o.mission_id for o in aircraft_opts}
        for m1, m2 in sorted(overlaps):
            if m1 in missions_of and m2 in missions_of:
                model.add(x[(aid, m1)] + x[(aid, m2)] <= 1)
        keys = sorted({k for (a, _m, k) in use if a == aid})
        for key in keys:
            terms = [(_scaled_use(use.get((aid, o.mission_id, key), 0.0)), x[(aid, o.mission_id)])
                     for o in aircraft_opts]
            total = sum(w for w, _ in terms)
            cap = _scaled_room(room.get((aid, key), 0.0))
            if total > cap:  # constraint can bind
                model.add(sum(w * v for w, v in terms) <= cap)

    score_expr = sum(round(o.score * SCALE) * x[(o.aircraft_id, o.mission_id)] for o in opts)
    model.maximize(score_expr - round(SHORTFALL_PENALTY * SCALE) * sum(shortfall_vars.values()))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = 42
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return HorizonPlan("INFEASIBLE", {}, {m.mission_id: m.required for m in missions}, 0.0)
    assignments = {
        m.mission_id: tuple(sorted(o.aircraft_id for o in by_mission[m.mission_id]
                                   if solver.value(x[(o.aircraft_id, m.mission_id)]) == 1))
        for m in missions
    }
    shortfall = {mid: int(solver.value(v)) for mid, v in shortfall_vars.items()}
    status_name = "OPTIMAL" if status == cp_model.OPTIMAL else "FEASIBLE"
    return HorizonPlan(status_name, assignments, shortfall, round(solver.objective_value / SCALE, 2))


def check_plan(
    assignments: Mapping[int, Sequence[int]],
    required: Mapping[int, int],
    eligible_pairs: frozenset[tuple[int, int]],
    overlaps: frozenset[tuple[int, int]],
    room: Mapping[tuple[int, LimitKey], float],
    use: Mapping[tuple[int, int, LimitKey], float],
) -> list[Violation]:
    """Independent verification of a plan (used before committing). Empty list ⇒ the plan is safe to apply."""
    out: list[Violation] = []
    flown: dict[int, list[int]] = defaultdict(list)
    for mid, aids in sorted(assignments.items()):
        if len(set(aids)) != len(aids):
            out.append(Violation("DUPLICATE", mid))
        if len(set(aids)) > required.get(mid, 0):
            out.append(Violation("OVER_REQUIRED", mid))
        for aid in sorted(set(aids)):
            if (aid, mid) not in eligible_pairs:
                out.append(Violation("NOT_ELIGIBLE", mid, aid))
            flown[aid].append(mid)
    for aid, mids in sorted(flown.items()):
        for m1, m2 in sorted(overlaps):
            if m1 in mids and m2 in mids:
                out.append(Violation("OVERLAP", m1, aid, str(m2)))
        for key in sorted({k for (a, _m, k) in use if a == aid}):
            total = sum(_scaled_use(use.get((aid, mid, key), 0.0)) for mid in mids)
            if total > _scaled_room(room.get((aid, key), 0.0)):
                out.append(Violation("CUMULATIVE_LIMIT", mids[0], aid, key))
    return out
