"""Compliance engine unit tests (TESTING.md §3.1)."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import timedelta

import pytest

from app.domain.compliance.consumption import FlightLoad, buffered_hours, consumption
from app.domain.compliance.evaluator import evaluate_eligibility
from app.domain.compliance.status import aircraft_status
from app.domain.compliance.unblock import max_duration_for_remaining
from app.domain.models import (
    ComplianceStatus,
    Counter,
    EngineRequirement,
    InspectionDue,
    MissionType,
    OperationalStatus,
    Permit,
    Severity,
    Unit,
)
from app.domain.policy import PlanningPolicy
from tests.builders import T0, a_mission, an_aircraft, ctx

P = PlanningPolicy()


def codes(result) -> set[str]:  # type: ignore[no-untyped-def]
    return {str(r.code) for r in result.reasons}


def reason(result, code: str):  # type: ignore[no-untyped-def]
    return next(r for r in result.reasons if str(r.code) == code)


# ── consumption (OPT §2.1) ─────────────────────────────────────────────
@pytest.mark.parametrize(("hours", "expected"), [(1.0, 1.3), (4.0, 4.6), (2.0, 2.3), (0.0, 0.0)])
def test_fh_buffer_uses_max_of_pct_and_floor(hours: float, expected: float) -> None:
    assert buffered_hours(hours, P) == pytest.approx(expected)


@pytest.mark.parametrize(("tg_weight", "expected"), [(1.0, 8.0), (0.5, 5.0), (0.0, 2.0)])
def test_landing_consumption_weights_touch_and_goes(tg_weight: float, expected: float) -> None:
    load = FlightLoad(2.5, 1, 6, 1)
    assert consumption(Unit.LDG, load, P, 1, tg_weight) == pytest.approx(expected)


def test_engine_starts_multiply_by_engine_count() -> None:
    assert consumption(Unit.ENG_START, FlightLoad(1, 1, 0, 2), P, 2, 1.0) == 4.0


# ── usage limits (OPT §2.3) ────────────────────────────────────────────
def test_reaching_limit_exactly_is_allowed() -> None:
    ac = an_aircraft().remaining("I100H", Unit.FH, 4.6).build()
    assert evaluate_eligibility(ac, a_mission(duration_hours=4.0), P, ctx()).eligible


def test_limit_exceeded_during_mission_is_ineligible_with_numbers() -> None:
    ac = an_aircraft().remaining("I100H", Unit.FH, 1.5).build()
    r = evaluate_eligibility(ac, a_mission(duration_hours=4.0), P, ctx())
    assert not r.eligible
    params = reason(r, "INSPECTION_DUE_BEFORE_MISSION_END").params
    assert params == {"inspection_code": "I100H", "unit": "FH", "remaining": 1.5, "required": 4.6}


def test_limit_already_exceeded_is_expired() -> None:
    ac = an_aircraft().remaining("I100H", Unit.FH, -2.0).build()
    r = evaluate_eligibility(ac, a_mission(), P, ctx())
    assert reason(r, "INSPECTION_EXPIRED").params["over_by"] == 2.0


def test_touch_and_goes_count_toward_landing_limit() -> None:
    ac = an_aircraft("SE-100").remaining("LG_INSP", Unit.LDG, 5, interval=300).build()
    training = a_mission(duration_hours=2.5, landings=1, touch_and_goes=6)
    assert "INSPECTION_DUE_BEFORE_MISSION_END" in codes(evaluate_eligibility(ac, training, P, ctx()))
    short = a_mission(duration_hours=2.5, landings=1, touch_and_goes=3)
    assert evaluate_eligibility(ac, short, P, ctx()).eligible


def test_tg_weight_zero_ignores_touch_and_goes() -> None:
    ac = an_aircraft().remaining("LG_INSP", Unit.LDG, 4, interval=800, tg_weight=0.0).build()
    assert evaluate_eligibility(ac, a_mission(landings=1, touch_and_goes=50), P, ctx()).eligible


# ── calendar items (OPT §2.4) ──────────────────────────────────────────
def test_calendar_due_exactly_at_deadline_is_ok() -> None:
    m = a_mission(duration_hours=4.0)
    deadline = m.start_at + timedelta(hours=4.0 + P.calendar_buffer_hours)
    ac = an_aircraft().limit("ANNUAL", Unit.CAL_DAY, 365, due_at=deadline).build()
    assert evaluate_eligibility(ac, m, P, ctx()).eligible


def test_calendar_due_one_second_before_deadline_fails() -> None:
    m = a_mission(duration_hours=4.0)
    deadline = m.start_at + timedelta(hours=4.0 + P.calendar_buffer_hours)
    ac = an_aircraft().limit("ANNUAL", Unit.CAL_DAY, 365, due_at=deadline - timedelta(seconds=1)).build()
    assert "INSPECTION_DUE_BEFORE_MISSION_END" in codes(evaluate_eligibility(ac, m, P, ctx()))


def test_permit_expiring_mid_flight_blocks() -> None:
    m = a_mission(start_at=T0 + timedelta(hours=3), duration_hours=7.0)
    ac = an_aircraft().permit("INSURANCE", T0 + timedelta(hours=5)).build()
    r = evaluate_eligibility(ac, m, P, ctx())
    assert "PERMIT_EXPIRES_BEFORE_MISSION_END" in codes(r)
    assert "UNBLOCK_LATEST_END" not in {str(h.code) for h in r.unblock_hints}


def test_permit_expired_now() -> None:
    ac = an_aircraft().permit("COA", T0 - timedelta(days=1)).build()
    assert "PERMIT_EXPIRED" in codes(evaluate_eligibility(ac, a_mission(), P, ctx()))


# ── fail-safe (S1) ─────────────────────────────────────────────────────
def test_missing_counter_is_data_missing() -> None:
    ac = an_aircraft().remaining("LG_INSP", Unit.LDG, 100, interval=600).counter(Unit.LDG, None).build()
    r = evaluate_eligibility(ac, a_mission(), P, ctx())
    assert reason(r, "DATA_MISSING").params == {"item_kind": "COUNTER", "item_code": "LDG"}


def test_missing_due_row_is_data_missing() -> None:
    ac = an_aircraft().limit("ANNUAL", Unit.CAL_DAY, 365, add_due=False).build()
    assert "DATA_MISSING" in codes(evaluate_eligibility(ac, a_mission(), P, ctx()))


def test_missing_required_permit_is_data_missing() -> None:
    ac = an_aircraft().no_permit("COA").build()
    r = evaluate_eligibility(ac, a_mission(), P, ctx())
    assert reason(r, "DATA_MISSING").params == {"item_kind": "PERMIT", "item_code": "COA"}


def test_missing_last_flight_is_data_missing() -> None:
    ac = an_aircraft().last_flight_at(None).build()
    assert "DATA_MISSING" in codes(evaluate_eligibility(ac, a_mission(), P, ctx()))


def test_stale_data_blocks() -> None:
    ac = an_aircraft().updated_at(T0 - timedelta(hours=30)).build()
    assert "DATA_STALE" in codes(evaluate_eligibility(ac, a_mission(), P, ctx()))
    assert aircraft_status(ac, P, ctx()).compliance_status == ComplianceStatus.UNKNOWN


# ── qualifications (OPT §2.6) ──────────────────────────────────────────
def test_qualification_revoked_by_ma() -> None:
    ac = an_aircraft().ma("MA-1", affects_qualification_code="IFR").build()
    r = evaluate_eligibility(ac, a_mission(required_qualifications=frozenset({"IFR"})), P, ctx())
    assert reason(r, "QUALIFICATION_REVOKED_BY_MA").params == {"qualification_code": "IFR", "ma_number": "MA-1"}


def test_qualification_revoked_by_permit_restriction() -> None:
    ac = an_aircraft().permit("SPECIAL_FLIGHT", T0 + timedelta(days=20), ("DAY_ONLY", "NO_PAX")).build()
    r = evaluate_eligibility(ac, a_mission(required_qualifications=frozenset({"NIGHT", "PAX"})), P, ctx())
    assert {str(x.params["qualification_code"]) for x in r.reasons
            if str(x.code) == "QUALIFICATION_REVOKED_BY_PERMIT"} == {"NIGHT", "PAX"}


def test_qualification_expired_and_expiring() -> None:
    expired = an_aircraft().qual("OVERWATER", T0 - timedelta(days=1)).build()
    m = a_mission(required_qualifications=frozenset({"OVERWATER"}))
    assert "QUALIFICATION_EXPIRED" in codes(evaluate_eligibility(expired, m, P, ctx()))
    expiring = an_aircraft().qual("OVERWATER", m.start_at + timedelta(hours=1)).build()
    assert "QUALIFICATION_EXPIRES_BEFORE_MISSION_END" in codes(evaluate_eligibility(expiring, m, P, ctx()))


def test_qualification_missing() -> None:
    r = evaluate_eligibility(an_aircraft().build(), a_mission(required_qualifications=frozenset({"MEDEVAC"})), P,
                             ctx())
    assert "QUALIFICATION_MISSING" in codes(r)


def test_unrequired_expired_qualification_does_not_block() -> None:
    ac = an_aircraft().qual("OVERWATER", T0 - timedelta(days=1)).build()
    assert evaluate_eligibility(ac, a_mission(), P, ctx()).eligible
    assert aircraft_status(ac, P, ctx()).compliance_status == ComplianceStatus.EXPIRED


# ── other rules ────────────────────────────────────────────────────────
def test_idle_exclude_vs_warn() -> None:
    ac = an_aircraft("SE-100").last_flight_at(T0 - timedelta(days=40)).build()
    m = a_mission(duration_hours=1.0)
    assert "IDLE_LIMIT_EXCEEDED" in codes(evaluate_eligibility(ac, m, P, ctx()))
    warn = evaluate_eligibility(ac, m, P.with_overrides({"idle_policy": "WARN"}), ctx())
    assert warn.eligible and "IDLE_CHECK_REQUIRED" in {str(w.code) for w in warn.warnings}


def test_committed_flight_before_start_resets_idle_and_consumes_hours() -> None:
    m = a_mission(start_at=T0 + timedelta(days=2), duration_hours=2.0)
    ac = (an_aircraft().remaining("I100H", Unit.FH, 5.0)
          .commitment(99, T0 + timedelta(days=1), 2.0).build())
    # committed 2.3 FH + mission 2.3 FH = 4.6 ≤ 5.0
    assert evaluate_eligibility(ac, m, P, ctx()).eligible
    ac2 = an_aircraft().remaining("I100H", Unit.FH, 4.0).commitment(99, T0 + timedelta(days=1), 2.0).build()
    assert "INSPECTION_DUE_BEFORE_MISSION_END" in codes(evaluate_eligibility(ac2, m, P, ctx()))


def test_overlapping_assignment_blocks() -> None:
    m = a_mission(start_at=T0 + timedelta(hours=2), duration_hours=2.0)
    ac = an_aircraft().commitment(7, T0 + timedelta(hours=1), 1.7, window_end=T0 + timedelta(hours=4)).build()
    assert "ALREADY_ASSIGNED" in codes(evaluate_eligibility(ac, m, P, ctx()))


def test_engine_and_endurance_rules() -> None:
    ac = an_aircraft("SE-100").build()
    r = evaluate_eligibility(ac, a_mission(engine_requirement=EngineRequirement.MIN_2, duration_hours=7), P, ctx())
    assert {"ENGINE_REQUIREMENT_NOT_MET", "ENDURANCE_EXCEEDED"} <= codes(r)
    assert reason(r, "ENDURANCE_EXCEEDED").params == {"required_hours": 8.05, "max_hours": 4.5}


def test_grounding_ma_and_status() -> None:
    ac = an_aircraft().ma("MA-9", Severity.GROUNDING, zone_code="ENGINE_2").with_status(
        OperationalStatus.IN_MAINTENANCE).build()
    assert {"GROUNDING_MA_OPEN", "AIRCRAFT_NOT_OPERATIONAL"} <= codes(evaluate_eligibility(ac, a_mission(), P, ctx()))


def test_all_reasons_are_collected() -> None:
    ac = (an_aircraft().remaining("I100H", Unit.FH, -1).permit("COA", T0 - timedelta(days=1))
          .ma("MA-2", Severity.GROUNDING).build())
    assert {"INSPECTION_EXPIRED", "PERMIT_EXPIRED", "GROUNDING_MA_OPEN"} <= codes(
        evaluate_eligibility(ac, a_mission(), P, ctx()))


def test_tolerance_never_grants_eligibility() -> None:
    ac = an_aircraft().remaining("I100H", Unit.FH, 1.5, tolerance=10).build()
    r = evaluate_eligibility(ac, a_mission(), P, ctx())
    assert not r.eligible
    assert "TOLERANCE_AVAILABLE" in {str(w.code) for w in r.warnings}


def test_ferry_only_and_unknown_restrictions_conflict() -> None:
    ac = an_aircraft().permit("SPECIAL_FLIGHT", T0 + timedelta(days=9), ("FERRY_ONLY",)).build()
    assert "PERMIT_RESTRICTION_CONFLICT" in codes(evaluate_eligibility(ac, a_mission(), P, ctx()))
    assert evaluate_eligibility(ac, a_mission(mission_type=MissionType.FERRY), P, ctx()).eligible
    weird = an_aircraft().permit("SPECIAL_FLIGHT", T0 + timedelta(days=9), ("SOMETHING_NEW",)).build()
    assert "PERMIT_RESTRICTION_CONFLICT" in codes(
        evaluate_eligibility(weird, a_mission(mission_type=MissionType.FERRY), P, ctx()))


def test_manual_exclusion() -> None:
    ac = an_aircraft().build()
    assert "MANUALLY_EXCLUDED" in codes(evaluate_eligibility(ac, a_mission(force_exclude=frozenset({"4X-T01"})),
                                                             P, ctx()))


# ── unblock hints (OPT §2.7) ───────────────────────────────────────────
@pytest.mark.parametrize(("remaining", "expected"), [(1.5, 1.2), (4.4, 3.82), (0.2, 0.0)])
def test_max_duration_hint(remaining: float, expected: float) -> None:
    assert max_duration_for_remaining(remaining, P) == expected


def test_max_landings_hint() -> None:
    ac = an_aircraft("SE-100").remaining("LG_INSP", Unit.LDG, 5, interval=300).build()
    r = evaluate_eligibility(ac, a_mission(duration_hours=2.5, landings=1, touch_and_goes=6), P, ctx())
    hint = next(h for h in r.unblock_hints if str(h.code) == "UNBLOCK_MAX_LANDINGS")
    assert hint.params == {"max_landings_plus_tg": 4}


def test_latest_end_hint_when_room_exists() -> None:
    m = a_mission(start_at=T0 + timedelta(hours=1), duration_hours=6.0)
    ac = an_aircraft().permit("INSURANCE", T0 + timedelta(hours=6)).build()
    r = evaluate_eligibility(ac, m, P, ctx())
    hint = next(h for h in r.unblock_hints if str(h.code) == "UNBLOCK_LATEST_END")
    assert hint.params == {"latest_end": "2026-01-15T12:00:00Z"}


# ── fleet status (OPT §2.8) ────────────────────────────────────────────
def test_status_thresholds() -> None:
    assert aircraft_status(an_aircraft().remaining("A_CHECK", Unit.FH, 5.0, interval=500).build(), P,
                           ctx()).compliance_status == ComplianceStatus.EXPIRING_SOON
    assert aircraft_status(an_aircraft().remaining("A_CHECK", Unit.FH, 200, interval=500).build(), P,
                           ctx()).compliance_status == ComplianceStatus.OK
    assert aircraft_status(an_aircraft().counter(Unit.FH, None).remaining("I50H", Unit.LDG, 50).build(), P,
                           ctx()).compliance_status == ComplianceStatus.OK


# ── compliance-safety review fixes (2026-10-09) ─────────────────────────
def _params(result, code: str):  # type: ignore[no-untyped-def]
    return [dict(r.params) for r in (*result.reasons, *result.warnings) if str(r.code) == code]


def test_flight_after_counter_snapshot_but_before_now_is_counted() -> None:
    # counters as_of T0; "now" is 12 h later; an assigned 3 h flight flew in between → not yet in the counters
    now = T0 + timedelta(hours=12)
    b = an_aircraft().remaining("I100H", Unit.FH, 4.0).commitment(7, T0 + timedelta(hours=2), 3.0)
    r = evaluate_eligibility(b.build(), a_mission(duration_hours=1.0), P, ctx(now=now))
    assert reason(r, "INSPECTION_DUE_BEFORE_MISSION_END").params["required"] == pytest.approx(3.45 + 1.3)


def test_committed_buffers_apply_per_flight() -> None:
    # 3 × 1 h commitments = 3 × 1.3 FH (not 3 h + one 0.45 buffer); + this 1 h mission 1.3 → 5.2 > 5.0
    b = an_aircraft().remaining("I100H", Unit.FH, 5.0)
    for i, h in enumerate((2, 5, 8)):
        b.commitment(10 + i, T0 + timedelta(hours=h), 1.0)
    r = evaluate_eligibility(b.build(), a_mission(duration_hours=1.0), P, ctx())
    assert reason(r, "INSPECTION_DUE_BEFORE_MISSION_END").params["required"] == pytest.approx(5.2)


def test_later_assignment_is_protected() -> None:
    m = a_mission(duration_hours=2.0)  # 2.3 FH
    later = T0 + timedelta(days=3)
    ok = an_aircraft().remaining("I100H", Unit.FH, 6.0).commitment(9, later, 3.0).build()  # 2.3 + 3.45 ≤ 6
    assert evaluate_eligibility(ok, m, P, ctx()).eligible
    tight = an_aircraft().remaining("I100H", Unit.FH, 5.0).commitment(9, later, 3.0).build()
    r = evaluate_eligibility(tight, m, P, ctx())
    assert not r.eligible
    assert reason(r, "JEOPARDIZES_LATER_ASSIGNMENT").params == {
        "mission_id": 9, "mission_name": "M9", "source": "INSPECTION", "ref": "I100H", "unit": "FH", "over_by": 0.75}


def test_nan_and_inf_values_are_missing_not_ok() -> None:
    nan_counter = an_aircraft().limit("I100H", Unit.FH, 100, due_value=1100).counter(Unit.FH, math.nan).build()
    r = evaluate_eligibility(nan_counter, a_mission(), P, ctx())
    assert {"item_kind": "COUNTER", "item_code": "FH"} in _params(r, "DATA_MISSING")
    inf_due = an_aircraft().limit("I100H", Unit.FH, 100, due_value=math.inf).build()
    r = evaluate_eligibility(inf_due, a_mission(), P, ctx())
    assert {"item_kind": "INSPECTION", "item_code": "I100H"} in _params(r, "DATA_MISSING")
    ac = an_aircraft().build()
    nan_type = replace(ac, aircraft_type=replace(ac.aircraft_type, max_endurance_hours=math.nan))
    r = evaluate_eligibility(nan_type, a_mission(), P, ctx())
    assert {"item_kind": "AIRCRAFT_TYPE", "item_code": "TP-200"} in _params(r, "DATA_MISSING")


def test_inspection_programme_gaps_are_missing() -> None:
    b = an_aircraft()
    b.dues.append(InspectionDue("ORPHAN", Unit.FH, 2000.0, None))
    r = evaluate_eligibility(b.build(), a_mission(), P, ctx())
    assert {"item_kind": "INSPECTION_LIMIT", "item_code": "ORPHAN"} in _params(r, "DATA_MISSING")
    bare = an_aircraft()
    bare.limits = [lim for lim in bare.limits if lim.unit == Unit.IDLE_DAY]
    bare.dues = []
    ac = bare.build()
    r = evaluate_eligibility(ac, a_mission(), P, ctx())
    assert {"item_kind": "INSPECTION_LIMIT", "item_code": "TP-200"} in _params(r, "DATA_MISSING")
    assert aircraft_status(ac, P, ctx()).compliance_status == ComplianceStatus.UNKNOWN


def test_idle_rule_falls_back_to_type_limit() -> None:
    b = an_aircraft().last_flight_at(T0 - timedelta(days=50))  # TP-200 idle limit 45 days
    b.limits = [lim for lim in b.limits if lim.unit != Unit.IDLE_DAY]
    r = evaluate_eligibility(b.build(), a_mission(), P, ctx())
    assert "IDLE_LIMIT_EXCEEDED" in codes(r)


def test_deferred_ma_without_usable_limit_is_missing() -> None:
    no_limit = an_aircraft().ma("MA-1", Severity.DEFERRED).build()
    r = evaluate_eligibility(no_limit, a_mission(), P, ctx())
    assert {"item_kind": "DEFERRAL", "item_code": "MA-1"} in _params(r, "DATA_MISSING")
    no_unit = an_aircraft().ma("MA-2", Severity.DEFERRED, deferral_expires_at=T0 + timedelta(days=9),
                               deferral_limit_value=10.0).build()
    r = evaluate_eligibility(no_unit, a_mission(), P, ctx())
    assert {"item_kind": "DEFERRAL", "item_code": "MA-2"} in _params(r, "DATA_MISSING")


def test_undated_permit_is_missing_and_never_supersedes_a_dated_one() -> None:
    r = evaluate_eligibility(an_aircraft().permit("COA", None).build(), a_mission(), P, ctx())
    assert {"item_kind": "PERMIT", "item_code": "COA"} in _params(r, "DATA_MISSING")
    b = an_aircraft().permit("COA", T0 - timedelta(days=1))
    b.permits.append(Permit(99, "COA", "COA-U", T0 - timedelta(days=10), None, None, None, (), "ACTIVE"))
    assert "PERMIT_EXPIRED" in codes(evaluate_eligibility(b.build(), a_mission(), P, ctx()))


def test_missing_feed_and_stale_counter_are_stale() -> None:
    ac = an_aircraft().build()
    r = evaluate_eligibility(ac, a_mission(), P, ctx(load={"mro": T0, "flight_ops": T0}))
    assert {"source_name": "permits", "age_hours": None} in _params(r, "DATA_STALE")
    old = replace(ac, counters={**ac.counters, Unit.FH: Counter(Unit.FH, 1000.0, T0 - timedelta(days=3))})
    r = evaluate_eligibility(old, a_mission(), P, ctx())
    assert {"source_name": "usage_counter", "unit": "FH", "age_hours": 72.0} in _params(r, "DATA_STALE")
    assert aircraft_status(old, P, ctx()).compliance_status == ComplianceStatus.UNKNOWN


def test_non_grounding_permit_failure_is_a_warning() -> None:
    b = an_aircraft()
    b.permits.append(Permit(77, "NOISE", "N-1", T0 - timedelta(days=100), T0 - timedelta(days=1), None, None, (),
                            "ACTIVE", is_grounding=False))
    r = evaluate_eligibility(b.build(), a_mission(), P, ctx())
    assert r.eligible
    assert _params(r, "NON_GROUNDING_PERMIT_INVALID") == [{"permit_type_code": "NOISE", "outcome": "EXPIRED"}]


def test_adapter_data_issues_block_and_show_unknown() -> None:
    ac = replace(an_aircraft().build(), data_issues=(("MAINTENANCE_ACTION", "MA-9"),))
    r = evaluate_eligibility(ac, a_mission(), P, ctx())
    assert {"item_kind": "MAINTENANCE_ACTION", "item_code": "MA-9"} in _params(r, "DATA_MISSING")
    assert aircraft_status(ac, P, ctx()).compliance_status == ComplianceStatus.UNKNOWN


# ── follow-up safety review (2026-10-09, second pass) ───────────────────
def test_future_dated_snapshot_never_drops_commitments_and_is_stale() -> None:
    b = an_aircraft().remaining("I100H", Unit.FH, 5.0).commitment(7, T0 + timedelta(hours=1), 3.0)
    ac = b.build()
    future = T0 + timedelta(hours=5)  # e.g. local time read as UTC
    ac = replace(ac, counters={u: replace(cnt, as_of=future) for u, cnt in ac.counters.items()},
                 last_flight_at=future)
    r = evaluate_eligibility(ac, a_mission(duration_hours=2.0), P, ctx())
    assert reason(r, "INSPECTION_DUE_BEFORE_MISSION_END").params["required"] == pytest.approx(3.45 + 2.3)
    stale = _params(r, "DATA_STALE")
    assert {"source_name": "usage_counter", "unit": "FH", "age_hours": -5.0} in stale
    assert {"source_name": "last_flight", "age_hours": -5.0} in stale


def test_flight_landing_after_snapshot_is_counted_by_its_window() -> None:
    # estimated end T0−1h is before the T0 snapshot, but the assignment window (late landing) runs past it
    b = an_aircraft().remaining("I100H", Unit.FH, 3.0).commitment(7, T0 - timedelta(hours=3), 2.0)
    r = evaluate_eligibility(b.build(), a_mission(duration_hours=1.0), P, ctx())
    assert reason(r, "INSPECTION_DUE_BEFORE_MISSION_END").params["required"] == pytest.approx(2.3 + 1.3)


def test_permit_is_valid_only_once_issued() -> None:
    b = an_aircraft().permit("COA", T0 + timedelta(hours=10))
    b.permits.append(Permit(98, "COA", "COA-R", T0 + timedelta(days=5), T0 + timedelta(days=400), None, None, (),
                            "ACTIVE"))
    r = evaluate_eligibility(b.build(), a_mission(), P, ctx())
    assert "PERMIT_EXPIRES_BEFORE_MISSION_END" in codes(r)


def test_required_non_grounding_permit_still_blocks() -> None:
    b = an_aircraft().no_permit("INSURANCE")
    b.permits.append(Permit(97, "INSURANCE", "I-1", T0 - timedelta(days=300), T0 - timedelta(days=1), None, None, (),
                            "ACTIVE", is_grounding=False))
    assert "PERMIT_EXPIRED" in codes(evaluate_eligibility(b.build(), a_mission(), P, ctx()))


def test_mission_overrides_never_shrink_buffers_of_other_flights() -> None:
    later = T0 + timedelta(days=3)
    ac = an_aircraft().remaining("I100H", Unit.FH, 8.8).commitment(9, later, 4.0).build()  # later flight: 4.6 FH
    no_buffer = replace(P, duration_buffer_pct=0.0, min_duration_buffer_hours=0.0)
    m = a_mission(duration_hours=4.4)  # 4.4 FH with the override
    base_ctx = replace(ctx(), commitment_policy=P)
    r = evaluate_eligibility(ac, m, no_buffer, base_ctx)
    assert "JEOPARDIZES_LATER_ASSIGNMENT" in codes(r)  # 4.4 + 4.6 > 8.8 (not 4.4 + 4.0)


def test_later_conflict_unblock_hint_accounts_for_later_flights() -> None:
    ac = an_aircraft().remaining("I100H", Unit.FH, 5.0).commitment(9, T0 + timedelta(days=3), 3.0).build()
    r = evaluate_eligibility(ac, a_mission(duration_hours=2.0), P, ctx())
    hints = {str(h.code): dict(h.params) for h in r.unblock_hints}
    assert hints["UNBLOCK_MAX_DURATION"] == {"max_hours": 1.25}  # room 5 − 3.45 = 1.55 ⇒ 1.25 h + 0.3 h buffer


def test_data_issues_make_aircraft_unavailable_with_visible_items() -> None:
    ac = replace(an_aircraft().build(), data_issues=(("MAINTENANCE_ACTION", "MA-9"),))
    st = aircraft_status(ac, P, ctx())
    assert not st.available
    assert any(i.missing_kind == "MAINTENANCE_ACTION" and i.status == ComplianceStatus.UNKNOWN for i in st.items)
