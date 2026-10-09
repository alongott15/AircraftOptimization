"""Multi-mission horizon planner: domain model + API (FR-46)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain.optimization.horizon import HorizonMission, HorizonOption, check_plan, plan_horizon
from tests.conftest import login

KEY = "INSPECTION:I100H:FH"


def opt(aid: int, mid: int, score: float = 50.0, type_code: str = "TP") -> HorizonOption:
    return HorizonOption(aid, mid, score, type_code)


def test_overlapping_missions_never_share_an_aircraft() -> None:
    missions = [HorizonMission(1, 1), HorizonMission(2, 1)]
    options = [opt(10, 1, 90), opt(10, 2, 90), opt(11, 1, 40), opt(11, 2, 40)]
    plan = plan_horizon(missions, options, frozenset({(1, 2)}), {}, {})
    assert plan.status == "OPTIMAL"
    assert set(plan.assignments[1]) | set(plan.assignments[2]) == {10, 11}
    assert not set(plan.assignments[1]) & set(plan.assignments[2])


def test_cumulative_usage_respects_limits_across_missions() -> None:
    # aircraft 10 has 5 FH before its 100 h inspection; each mission consumes 2.9 FH → it can fly only one
    missions = [HorizonMission(1, 1), HorizonMission(2, 1)]
    options = [opt(10, 1, 95), opt(10, 2, 95), opt(11, 2, 30)]
    room = {(10, KEY): 5.0}
    use = {(10, 1, KEY): 2.9, (10, 2, KEY): 2.9}
    plan = plan_horizon(missions, options, frozenset(), room, use)
    assert plan.assignments[1] == (10,) and plan.assignments[2] == (11,)
    assert check_plan(plan.assignments, {1: 1, 2: 1}, frozenset({(10, 1), (10, 2), (11, 2)}), frozenset(), room, use) == []


def test_shortfall_is_reported_not_padded() -> None:
    plan = plan_horizon([HorizonMission(1, 3)], [opt(10, 1)], frozenset(), {}, {})
    assert plan.assignments[1] == (10,) and plan.shortfall[1] == 2


def test_same_type_per_mission() -> None:
    options = [opt(10, 1, 90, "A"), opt(11, 1, 80, "B"), opt(12, 1, 70, "B")]
    plan = plan_horizon([HorizonMission(1, 2, same_type=True)], options, frozenset(), {}, {})
    assert set(plan.assignments[1]) == {11, 12}


def test_check_plan_catches_every_violation_kind() -> None:
    room = {(10, KEY): 3.0}
    use = {(10, 1, KEY): 2.0, (10, 2, KEY): 2.0}
    violations = check_plan({1: [10, 10], 2: [10, 99]}, {1: 1, 2: 1}, frozenset({(10, 1), (10, 2)}),
                            frozenset({(1, 2)}), room, use)
    assert {v.code for v in violations} == {"DUPLICATE", "OVER_REQUIRED", "NOT_ELIGIBLE", "OVERLAP", "CUMULATIVE_LIMIT"}


MISSION = {"mission_type": "TRAINING", "required_aircraft_count": 2, "est_duration_hours": 2.5, "est_landings": 1,
           "est_touch_and_goes": 2, "departure_base_code": "NTH", "required_qualifications": ["NIGHT"],
           "engine_requirement": "SINGLE"}


def test_horizon_api_plans_and_applies_two_overlapping_missions(client: TestClient) -> None:
    headers = login(client, "planner")
    a = client.post("/api/v1/missions", json={**MISSION, "name": "א", "start_at": "2026-01-15T10:00:00Z"},
                    headers=headers).json()["mission_id"]
    b = client.post("/api/v1/missions", json={**MISSION, "name": "ב", "start_at": "2026-01-15T11:00:00Z"},
                    headers=headers).json()["mission_id"]
    plan = client.post("/api/v1/planning/horizon", json={"mission_ids": [a, b]}, headers=headers).json()
    assert plan["solver_status"] in ("OPTIMAL", "FEASIBLE")
    by_mission = {m["mission_id"]: m for m in plan["missions"]}
    tails_a = {x["tail_number"] for x in by_mission[a]["assigned"]}
    tails_b = {x["tail_number"] for x in by_mission[b]["assigned"]}
    assert not tails_a & tails_b  # overlapping windows
    assert len(tails_a) + len(tails_b) == 4 - by_mission[a]["shortfall"] - by_mission[b]["shortfall"]
    # SE fleet eligible for NIGHT single-engine at T0+2h: S01, S02, S04, S06 → 4 seats, no shortfall
    assert by_mission[a]["shortfall"] == 0 and by_mission[b]["shortfall"] == 0
    applied = client.post("/api/v1/planning/horizon/apply", json={"assignments": plan["assignments"]}, headers=headers)
    assert applied.status_code == 200
    assert client.get(f"/api/v1/missions/{a}").json()["status"] == "ASSIGNED"


def test_horizon_apply_rejects_unsafe_plan(client: TestClient) -> None:
    headers = login(client, "planner")
    a = client.post("/api/v1/missions", json={**MISSION, "name": "א", "start_at": "2026-01-15T10:00:00Z"},
                    headers=headers).json()["mission_id"]
    b = client.post("/api/v1/missions", json={**MISSION, "name": "ב", "start_at": "2026-01-15T11:00:00Z"},
                    headers=headers).json()["mission_id"]
    # 4X-S01 = aircraft 1 on both overlapping missions, plus 4X-S03 (expired annual inspection) = aircraft 3
    bad = {"assignments": [{"mission_id": a, "aircraft_ids": [1, 3]}, {"mission_id": b, "aircraft_ids": [1]}]}
    r = client.post("/api/v1/planning/horizon/apply", json=bad, headers=headers)
    assert r.status_code == 409 and r.json()["code"] == "PLAN_CONFLICT"
    codes = {v["code"] for v in r.json()["violations"]}
    assert {"OVERLAP", "NOT_ELIGIBLE"} <= codes


def test_horizon_requires_plan_permission(client: TestClient) -> None:
    headers = login(client, "ops")
    assert client.post("/api/v1/planning/horizon", json={}, headers=headers).status_code == 403


def test_horizon_rejects_ineligible_forced_tail(client: TestClient) -> None:
    headers = login(client, "planner")
    a = client.post("/api/v1/missions", json={**MISSION, "name": "א", "start_at": "2026-01-15T10:00:00Z",
                                               "force_include": ["4X-S03"]}, headers=headers).json()["mission_id"]
    r = client.post("/api/v1/planning/horizon", json={"mission_ids": [a]}, headers=headers)
    assert r.status_code == 422 and r.json()["code"] == "FORCE_INCLUDE_INELIGIBLE"
    assert r.json()["params"] == {"tail_number": "4X-S03", "mission_id": a}
    assert "INSPECTION_EXPIRED" in {x["code"] for x in r.json()["reasons"][str(a)]["4X-S03"]}


def test_bands_never_change_horizon_eligibility(client: TestClient) -> None:
    headers = login(client, "planner")
    bands = [{"unit": "FH", "until": 100000}, {"unit": "CAL_DAY", "until": 3650}]
    a = client.post("/api/v1/missions", json={**MISSION, "name": "א", "start_at": "2026-01-15T10:00:00Z"},
                    headers=headers).json()["mission_id"]
    b = client.post("/api/v1/missions", json={**MISSION, "name": "ב", "start_at": "2026-01-15T10:00:00Z",
                                               "expiry_windows": bands}, headers=headers).json()["mission_id"]
    plan = client.post("/api/v1/planning/horizon", json={"mission_ids": [a, b]}, headers=headers).json()
    counts = {m["mission_id"]: m["eligible_count"] for m in plan["missions"]}
    assert counts[a] == counts[b]  # identical missions, one with bands: same eligibility
