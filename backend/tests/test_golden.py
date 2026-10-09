"""Golden scenarios on the seeded example DB (TESTING.md §4, DATABASE.md §6)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.domain.compliance.evaluator import EligibilityResult, evaluate_eligibility
from app.domain.compliance.status import aircraft_status
from app.domain.models import EngineRequirement, MissionRequest, MissionType
from app.domain.optimization.cpsat_selector import SelectionCandidate, select
from app.domain.optimization.scoring import ScoringInputs, score_candidate
from tests.conftest import T0, Fleet

M_A = MissionRequest("אימון לילה", MissionType.TRAINING, 2, T0 + timedelta(hours=2), 2.5, 1, 6, 1, "NTH",
                     frozenset({"NIGHT"}), EngineRequirement.SINGLE)
M_B = MissionRequest("הובלת נוסעים", MissionType.TRANSPORT, 3, T0 + timedelta(days=1), 4.0, 2, 0, 1, "CEN",
                     frozenset({"PAX", "IFR"}), EngineRequirement.MIN_2)
M_C = MissionRequest("טיסת מעבר ארוכה", MissionType.FERRY, 1, T0 + timedelta(hours=3), 7.0, 1, 0, 1, "CEN",
                     frozenset({"OVERWATER"}), EngineRequirement.MIN_2)

SE = [f"4X-S0{i}" for i in range(1, 9)]
TP = [f"4X-P{i:02d}" for i in range(1, 11)]
JT = [f"4X-J0{i}" for i in range(1, 7)]


def run(fleet: Fleet, m: MissionRequest) -> dict[str, EligibilityResult]:
    return {t: evaluate_eligibility(a, m, fleet.policy, fleet.ctx) for t, a in fleet.aircraft.items()}


def has(result: EligibilityResult, code: str, **params: Any) -> bool:
    for r in (*result.reasons, *result.warnings, *result.unblock_hints):
        if str(r.code) == code and all(r.params.get(k) == v for k, v in params.items()):
            return True
    return False


# ── §4.1 fleet status at T0 ────────────────────────────────────────────
@pytest.mark.parametrize(("tail", "status"), [
    *[(t, "EXPIRED") for t in ("4X-S03", "4X-P05", "4X-S07", "4X-J02")],
    ("4X-P04", "UNKNOWN"),
    *[(t, "EXPIRING_SOON") for t in ("4X-P02", "4X-S05", "4X-P01", "4X-P03", "4X-J06", "4X-P06", "4X-P08")],
    *[(t, "OK") for t in ("4X-S01", "4X-S02", "4X-S04", "4X-S06", "4X-S08", "4X-P07", "4X-P09", "4X-P10",
                          "4X-J01", "4X-J05", "4X-J03", "4X-J04")],
])
def test_fleet_status_at_t0(fleet: Fleet, tail: str, status: str) -> None:
    assert str(aircraft_status(fleet.aircraft[tail], fleet.policy, fleet.ctx).compliance_status) == status


def test_derived_statuses(fleet: Fleet) -> None:
    assert aircraft_status(fleet.aircraft["4X-J04"], fleet.policy, fleet.ctx).derived_status == "GROUNDED"
    assert aircraft_status(fleet.aircraft["4X-J03"], fleet.policy, fleet.ctx).derived_status == "IN_MAINTENANCE"


# ── §4.2 M-A ────────────────────────────────────────────────────────────
def test_mission_a(fleet: Fleet) -> None:
    r = run(fleet, M_A)
    assert {t for t, x in r.items() if x.eligible} == {"4X-S01", "4X-S02", "4X-S04", "4X-S06"}
    assert has(r["4X-S03"], "INSPECTION_EXPIRED", inspection_code="ANNUAL")
    assert has(r["4X-S05"], "INSPECTION_DUE_BEFORE_MISSION_END", inspection_code="LG_INSP", unit="LDG",
               remaining=5.0, required=8.0)
    assert has(r["4X-S05"], "UNBLOCK_MAX_LANDINGS", max_landings_plus_tg=4)
    assert has(r["4X-S07"], "IDLE_LIMIT_EXCEEDED", limit_days=30.0)
    assert has(r["4X-S08"], "ALREADY_ASSIGNED")
    for t in TP + JT:
        assert has(r[t], "ENGINE_REQUIREMENT_NOT_MET")


def test_mission_a_ranking_with_stub_risk(fleet: Fleet) -> None:
    r = run(fleet, M_A)
    risk = {"4X-S02": 0.45}
    scored = [score_candidate(x, ScoringInputs("SE-100", fleet.aircraft[t].base_code, risk.get(t, 0.05), 20, 20, 5),
                              "NTH", fleet.policy) for t, x in r.items() if x.eligible]
    s02 = next(s for s in scored if s.result.tail_number == "4X-S02")
    assert "HIGH_DEFECT_RISK" in {str(w.code) for w in s02.warnings}
    sel = select([SelectionCandidate(s.aircraft_id, "SE-100", s.inputs.base_code, s.score) for s in scored], 2)
    tails = {fleet.aircraft[t].aircraft_id: t for t in fleet.aircraft}
    chosen = {tails[a] for a in sel.sets[0].aircraft_ids}
    assert sel.status == "OPTIMAL" and len(sel.sets) >= 2
    assert chosen <= {"4X-S01", "4X-S04", "4X-S06"}


# ── §4.3 M-B ────────────────────────────────────────────────────────────
def test_mission_b(fleet: Fleet) -> None:
    r = run(fleet, M_B)
    eligible = {t for t, x in r.items() if x.eligible}
    assert eligible == {"4X-P01", "4X-P06", "4X-P07", "4X-P10", "4X-J01", "4X-J02", "4X-J05"}
    assert has(r["4X-P01"], "LOW_MARGIN_AFTER_MISSION", ref="A_CHECK", unit="FH", remaining_after=0.4)
    assert has(r["4X-P06"], "EXPIRES_SOON_AFTER_MISSION", ref="ANNUAL")
    assert has(r["4X-P02"], "INSPECTION_DUE_BEFORE_MISSION_END", inspection_code="I100H", unit="FH", remaining=1.5,
               required=4.6)
    assert has(r["4X-P02"], "UNBLOCK_MAX_DURATION", max_hours=1.2)
    assert has(r["4X-P03"], "INSPECTION_DUE_BEFORE_MISSION_END", inspection_code="A_CHECK", remaining=4.4,
               required=4.6)
    assert has(r["4X-P03"], "UNBLOCK_MAX_DURATION", max_hours=3.82)
    assert has(r["4X-P04"], "DATA_MISSING", item_kind="COUNTER", item_code="LDG")
    assert has(r["4X-P05"], "PERMIT_EXPIRED", permit_type_code="COA")
    assert has(r["4X-P08"], "QUALIFICATION_REVOKED_BY_MA", qualification_code="IFR", ma_number="MA-2026-00398")
    assert has(r["4X-P09"], "QUALIFICATION_REVOKED_BY_PERMIT", qualification_code="PAX",
               permit_type_code="SPECIAL_FLIGHT", restriction="NO_PAX")
    assert has(r["4X-J03"], "AIRCRAFT_NOT_OPERATIONAL", status="IN_MAINTENANCE")
    assert has(r["4X-J04"], "GROUNDING_MA_OPEN", ma_number="MA-2026-00412", zone_code="ENGINE_2")
    assert has(r["4X-J06"], "PERMIT_EXPIRES_BEFORE_MISSION_END", permit_type_code="INSURANCE")
    for t in SE:
        assert has(r[t], "ENGINE_REQUIREMENT_NOT_MET")


def test_mission_b_best_fit_favours_p01(fleet: Fleet) -> None:
    from app.domain.optimization.scoring import best_fit

    r = run(fleet, M_B)
    fits = {t: best_fit(x) for t, x in r.items() if x.eligible}
    assert max(fits, key=lambda t: fits[t]) == "4X-P01"


# ── §4.4 M-C ────────────────────────────────────────────────────────────
def test_mission_c(fleet: Fleet) -> None:
    r = run(fleet, M_C)
    assert {t for t, x in r.items() if x.eligible} == {"4X-J01", "4X-J05"}
    for t in TP:
        assert has(r[t], "ENDURANCE_EXCEEDED", required_hours=8.05, max_hours=6.0)
    assert has(r["4X-J02"], "QUALIFICATION_EXPIRED", qualification_code="OVERWATER")
    assert has(r["4X-J03"], "AIRCRAFT_NOT_OPERATIONAL")
    assert has(r["4X-J04"], "GROUNDING_MA_OPEN")
    assert has(r["4X-J06"], "PERMIT_EXPIRES_BEFORE_MISSION_END", permit_type_code="INSURANCE")
    assert has(r["4X-J06"], "UNBLOCK_RENEW_PERMIT")
    assert not has(r["4X-J06"], "UNBLOCK_LATEST_END")
    for t in SE:
        assert has(r[t], "ENGINE_REQUIREMENT_NOT_MET")
        assert has(r[t], "ENDURANCE_EXCEEDED")
        assert has(r[t], "QUALIFICATION_MISSING", qualification_code="OVERWATER")
