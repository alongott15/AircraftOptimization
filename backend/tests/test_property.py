"""Safety invariants P1–P9 with hypothesis (TESTING.md §3.2)."""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from app.domain.compliance.evaluator import evaluate_eligibility
from app.domain.compliance.status import aircraft_status
from app.domain.models import STATUS_SEVERITY, ComplianceStatus, Unit
from app.domain.optimization.cpsat_selector import SelectionCandidate, select
from app.domain.optimization.scoring import ScoringInputs, score_candidate
from app.domain.policy import WEIGHT_PRESETS, PlanningPolicy
from tests.builders import T0, a_mission, an_aircraft, ctx

P = PlanningPolicy()
remaining_fh = st.floats(min_value=-5, max_value=60, allow_nan=False)
remaining_ldg = st.integers(min_value=-3, max_value=40)
hours = st.floats(min_value=0.2, max_value=8, allow_nan=False)
small = st.integers(min_value=0, max_value=10)
permit_days = st.floats(min_value=-2, max_value=10, allow_nan=False)


def build(rfh: float, rldg: int, permit_in_days: float, tg_weight: float = 1.0):  # type: ignore[no-untyped-def]
    return (an_aircraft().remaining("I100H", Unit.FH, rfh)
            .remaining("LG_INSP", Unit.LDG, rldg, interval=600, tg_weight=tg_weight)
            .permit("INSURANCE", T0 + timedelta(days=permit_in_days)).build())


@settings(max_examples=150, deadline=None)
@given(remaining_fh, remaining_ldg, permit_days, hours, small, small, hours, small, small)
def test_p1_more_consumption_never_helps(rfh, rldg, pdays, d, landings, tgs, dd, dl, dt) -> None:  # type: ignore[no-untyped-def]
    ac = build(rfh, rldg, pdays)
    base = evaluate_eligibility(ac, a_mission(duration_hours=d, landings=landings + 1, touch_and_goes=tgs), P, ctx())
    bigger = evaluate_eligibility(
        ac, a_mission(duration_hours=d + dd, landings=landings + 1 + dl, touch_and_goes=tgs + dt), P, ctx())
    if not base.eligible:
        assert not bigger.eligible


@settings(max_examples=100, deadline=None)
@given(remaining_fh, remaining_ldg, permit_days, st.sampled_from([Unit.FH, Unit.LDG, Unit.TG]))
def test_p2_removing_data_never_helps(rfh, rldg, pdays, unit) -> None:  # type: ignore[no-untyped-def]
    ac = build(rfh, rldg, pdays)
    stripped = replace(ac, counters={u: c for u, c in ac.counters.items() if u != unit})
    full = evaluate_eligibility(ac, a_mission(), P, ctx())
    partial = evaluate_eligibility(stripped, a_mission(), P, ctx())
    assert not partial.eligible
    if not full.eligible:
        assert not partial.eligible


@settings(max_examples=60, deadline=None)
@given(st.lists(st.tuples(remaining_fh, remaining_ldg, permit_days), min_size=2, max_size=8),
       st.lists(st.floats(min_value=0, max_value=1), min_size=8, max_size=8),
       st.sampled_from(sorted(WEIGHT_PRESETS)), st.integers(min_value=1, max_value=4))
def test_p3_p7_ml_and_weights_never_change_eligibility_and_selection_is_subset(specs, probs, preset, n) -> None:  # type: ignore[no-untyped-def]
    policy = P.with_overrides({"weights_preset": preset})
    m = a_mission()
    results = [evaluate_eligibility(replace(build(*s), aircraft_id=i + 1, tail_number=f"4X-T{i:02d}"), m, policy,
                                    ctx()) for i, s in enumerate(specs)]
    baseline = [evaluate_eligibility(replace(build(*s), aircraft_id=i + 1, tail_number=f"4X-T{i:02d}"), m, P,
                                     ctx()).eligible for i, s in enumerate(specs)]
    assert [r.eligible for r in results] == baseline  # P3
    eligible = [r for r in results if r.eligible]
    scored = [score_candidate(r, ScoringInputs("TP-200", "CEN", probs[i], 10, 10, 2), "CEN", policy)
              for i, r in enumerate(eligible)]
    sel = select([SelectionCandidate(sc.aircraft_id, "TP-200", "CEN", sc.score) for sc in scored], n)
    if not eligible:
        assert sel.status == "SKIPPED"
        return
    chosen = set(sel.sets[0].aircraft_ids)
    assert chosen <= {r.aircraft_id for r in eligible}  # P7
    assert len(chosen) == min(n, len(eligible))
    for alt in sel.sets[1:]:
        assert alt.objective <= sel.sets[0].objective + 1e-6


@settings(max_examples=150, deadline=None)
@given(remaining_fh, remaining_ldg, permit_days, hours, small, small)
def test_p4_eligible_means_every_limit_holds(rfh, rldg, pdays, d, landings, tgs) -> None:  # type: ignore[no-untyped-def]
    m = a_mission(duration_hours=d, landings=landings + 1, touch_and_goes=tgs)
    r = evaluate_eligibility(build(rfh, rldg, pdays), m, P, ctx())
    if r.eligible:
        for p in r.projections:
            assert p.outcome == "OK"
            if p.remaining_after is not None and p.source in ("INSPECTION", "PERMIT", "DEFERRAL"):
                assert p.remaining_after >= -1e-6


@settings(max_examples=40, deadline=None)
@given(st.lists(st.tuples(remaining_fh, remaining_ldg, permit_days), min_size=2, max_size=6), st.randoms())
def test_p6_order_does_not_matter(specs, rnd) -> None:  # type: ignore[no-untyped-def]
    fleet = [replace(build(*s), aircraft_id=i + 1, tail_number=f"4X-T{i:02d}") for i, s in enumerate(specs)]
    shuffled = fleet[:]
    random.Random(rnd.random()).shuffle(shuffled)
    m = a_mission()
    a = {x.aircraft_id: evaluate_eligibility(x, m, P, ctx()) for x in fleet}
    b = {x.aircraft_id: evaluate_eligibility(x, m, P, ctx()) for x in shuffled}
    assert a == b


@settings(max_examples=80, deadline=None)
@given(remaining_ldg, st.integers(min_value=0, max_value=60))
def test_p8_zero_tg_weight_ignores_touch_and_goes(rldg, tgs) -> None:  # type: ignore[no-untyped-def]
    ac = build(50, rldg, 30, tg_weight=0.0)
    a = evaluate_eligibility(ac, a_mission(touch_and_goes=0), P, ctx()).eligible
    b = evaluate_eligibility(ac, a_mission(touch_and_goes=tgs), P, ctx()).eligible
    assert a == b


@settings(max_examples=80, deadline=None)
@given(permit_days, st.floats(min_value=0, max_value=30))
def test_p9_time_never_unexpires(pdays, advance) -> None:  # type: ignore[no-untyped-def]
    ac = an_aircraft().permit("COA", T0 + timedelta(days=pdays)).build()
    now_status = aircraft_status(ac, P, ctx()).compliance_status
    later = T0 + timedelta(days=advance)
    later_status = aircraft_status(ac, P, ctx(now=later, load={"mro": later})).compliance_status
    if now_status == ComplianceStatus.EXPIRED:
        assert later_status == ComplianceStatus.EXPIRED
    assert STATUS_SEVERITY[later_status] >= STATUS_SEVERITY[now_status] or now_status == ComplianceStatus.UNKNOWN
