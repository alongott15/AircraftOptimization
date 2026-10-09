"""Scoring and CP-SAT selector tests (TESTING.md §3.3)."""

from __future__ import annotations

import time

import pytest

from app.domain.compliance.evaluator import evaluate_eligibility
from app.domain.models import Unit
from app.domain.optimization.cpsat_selector import ForcedTailConflictError, SelectionCandidate, select
from app.domain.optimization.scoring import ScoringInputs, best_fit, score_candidate
from app.domain.policy import PlanningPolicy
from tests.builders import a_mission, an_aircraft, ctx

P = PlanningPolicy()


def C(aid: int, score: float, type_code: str = "TP-200", base: str = "CEN", wb: str | None = None,
      wa: str | None = None) -> SelectionCandidate:
    return SelectionCandidate(aid, type_code, base, score, wb, wa)


def test_best_fit_prefers_tight_remaining() -> None:
    tight = evaluate_eligibility(an_aircraft().remaining("A_CHECK", Unit.FH, 5.0, interval=500).build(),
                                 a_mission(), P, ctx())
    loose = evaluate_eligibility(an_aircraft().remaining("A_CHECK", Unit.FH, 200, interval=500).build(),
                                 a_mission(), P, ctx())
    assert best_fit(tight) > best_fit(loose)
    assert best_fit(tight) == pytest.approx(1 - 0.4 / 50)


def test_score_weights_and_high_risk_warning() -> None:
    r = evaluate_eligibility(an_aircraft().build(), a_mission(), P, ctx())
    sc = score_candidate(r, ScoringInputs("TP-200", "CEN", 0.45, 10, 10, 2), "CEN", P)
    assert sum(t.contribution for t in sc.breakdown.values()) == pytest.approx(sc.score, abs=0.01)
    assert "HIGH_DEFECT_RISK" in {str(w.code) for w in sc.warnings}
    with pytest.raises(ValueError, match="eligible"):
        bad = evaluate_eligibility(an_aircraft().remaining("I100H", Unit.FH, -1).build(), a_mission(), P, ctx())
        score_candidate(bad, ScoringInputs("TP-200", "CEN", 0.1, 10, 10, 2), "CEN", P)


def test_selector_picks_best_and_alternatives_are_distinct() -> None:
    res = select([C(1, 90), C(2, 80), C(3, 70), C(4, 10)], 2)
    assert res.status == "OPTIMAL"
    assert res.sets[0].aircraft_ids == (1, 2)
    assert len({s.aircraft_ids for s in res.sets}) == len(res.sets)
    assert all(s.objective >= 0.85 * res.sets[0].objective for s in res.sets)


def test_selector_same_type_and_shortfall() -> None:
    res = select([C(1, 90, "A"), C(2, 80, "B"), C(3, 70, "B")], 3, same_type=True)
    assert res.shortfall == 1
    assert set(res.sets[0].aircraft_ids) == {2, 3}


def test_selector_force_include() -> None:
    res = select([C(1, 90), C(2, 80), C(3, 10)], 2, force_include_ids=frozenset({3}))
    assert 3 in res.sets[0].aircraft_ids
    with pytest.raises(ForcedTailConflictError):
        select([C(1, 90), C(2, 80)], 1, force_include_ids=frozenset({1, 2}))


def test_hangar_overflow_changes_choice() -> None:
    cands = [C(1, 80, wb="2026-W05", wa="2026-W04"), C(2, 75)]
    res = select(cands, 1, base_load={("CEN", "2026-W04"): 2}, capacity={("CEN", "2026-W04"): 2})
    assert res.sets[0].aircraft_ids == (2,)  # moving 1 into a full week costs 15 points


@pytest.mark.perf
def test_perf_200_aircraft_under_two_seconds() -> None:
    cands = [C(i, (i * 37) % 100, type_code=("A", "B", "C")[i % 3]) for i in range(1, 201)]
    started = time.perf_counter()
    select(cands, 6, same_type=True)
    assert time.perf_counter() - started < 2.0
