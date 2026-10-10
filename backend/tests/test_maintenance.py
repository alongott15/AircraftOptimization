"""Maintenance board, scheduling and recommendations (FR-90…FR-92) on the golden fleet.

Safety properties: recommendations are verified by the unchanged compliance engine; missing data is never simulated
away; a plan never changes eligibility; a task is done only when the source data says so — and data that disappears,
becomes unreadable or moves earlier is never "done"; a plan belongs to one task cycle and never carries over.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.domain.compliance.evaluator import evaluate_eligibility
from app.domain.compliance.status import aircraft_status
from app.domain.maintenance import (
    TaskAction,
    aircraft_requirements,
    complete_tasks,
    recommend,
    requirement_for,
    task_cycle,
    task_done,
    tasks_for_reasons,
)
from app.domain.models import Aircraft, ComplianceStatus, Permit, Unit
from app.domain.reason_codes import Reason, ReasonCode
from tests.conftest import T0, Fleet, login
from tests.test_golden import M_B, M_C

A = TaskAction


def _reqs(fleet: Fleet, ac: Aircraft) -> dict[tuple[str, str], tuple[str, bool]]:
    return {(str(r.action), r.ref): (str(r.status), r.blocking)
            for r in aircraft_requirements(ac, aircraft_status(ac, fleet.policy, fleet.ctx))}


def _done(fleet: Fleet, ac: Aircraft, action: TaskAction, ref: str, fingerprint: str) -> bool:
    return task_done(ac, aircraft_status(ac, fleet.policy, fleet.ctx), action, ref, fingerprint)


def _cycle(fleet: Fleet, ac: Aircraft, action: TaskAction, ref: str) -> str:
    return task_cycle(ac, aircraft_status(ac, fleet.policy, fleet.ctx), action, ref)


def _with_due(ac: Aircraft, code: str, **changes: Any) -> Aircraft:
    return replace(ac, inspection_dues=tuple(replace(d, **changes) if d.inspection_type_code == code else d
                                             for d in ac.inspection_dues))


def test_requirements_follow_the_fleet_status(fleet: Fleet) -> None:
    ac = fleet.aircraft
    assert _reqs(fleet, ac["4X-S03"]) == {("PERFORM_INSPECTION", "ANNUAL"): ("EXPIRED", True)}
    assert _reqs(fleet, ac["4X-J04"]) == {("CLOSE_MA", "MA-2026-00412"): ("EXPIRED", True)}
    assert _reqs(fleet, ac["4X-J03"]) == {("RETURN_TO_SERVICE", "IN_MAINTENANCE"): ("EXPIRED", True)}
    assert _reqs(fleet, ac["4X-S07"]) == {("IDLE_CHECK", "IDLE_CHECK"): ("EXPIRED", True)}
    assert _reqs(fleet, ac["4X-P04"]) == {("FIX_DATA", "COUNTER:LDG"): ("UNKNOWN", True)}
    assert _reqs(fleet, ac["4X-J06"]) == {("RENEW_PERMIT", "INSURANCE"): ("EXPIRING_SOON", False)}
    assert _reqs(fleet, ac["4X-J02"]) == {("RENEW_QUALIFICATION", "OVERWATER"): ("EXPIRED", False)}  # per mission
    assert _reqs(fleet, ac["4X-P08"]) == {("CLOSE_MA", "MA-2026-00398"): ("EXPIRING_SOON", False)}
    assert _reqs(fleet, ac["4X-S01"]) == {}
    assert sum(len(_reqs(fleet, a)) for a in ac.values()) == 14


def test_unreadable_rows_and_stale_feeds_need_the_data_owner(fleet: Fleet) -> None:
    broken = replace(fleet.aircraft["4X-S01"], data_issues=(("AIRCRAFT_STATUS", "BROKEN"),))
    assert _reqs(fleet, broken)[("FIX_DATA", "AIRCRAFT_STATUS:BROKEN")] == ("UNKNOWN", True)
    old_feeds = replace(fleet.ctx, data_load_status={k: T0 - timedelta(hours=48) for k in fleet.ctx.data_load_status})
    s01 = fleet.aircraft["4X-S01"]
    stale = aircraft_requirements(s01, aircraft_status(s01, fleet.policy, old_feeds))
    assert [(str(r.action), r.ref, r.blocking) for r in stale] == [("FIX_DATA", "STALE:DATA", True)]


def test_recommendations_are_verified_by_the_engine(fleet: Fleet) -> None:
    mission = replace(M_B, required_count=9)  # 7 eligible ⇒ 2 short
    aircraft = list(fleet.aircraft.values())
    results = [evaluate_eligibility(a, mission, fleet.policy, fleet.ctx) for a in aircraft]
    rec = recommend(aircraft, results, mission, fleet.policy, fleet.ctx)
    assert (rec.eligible_count, rec.shortfall, rec.complete_by) == (7, 2, mission.start_at)
    tasks = {c.tail_number: [(str(a), r) for a, r in c.tasks] for c in rec.candidates}
    assert tasks["4X-P02"] == [("PERFORM_INSPECTION", "I100H")]
    assert tasks["4X-J04"] == [("CLOSE_MA", "MA-2026-00412")]
    assert tasks["4X-J03"] == [("RETURN_TO_SERVICE", "IN_MAINTENANCE")]
    # never recommended: missing data (P04) and a permit restriction (P09) are not maintenance matters
    assert "4X-P04" not in tasks and "4X-P09" not in tasks
    for cand in rec.candidates:
        ac = fleet.aircraft[cand.tail_number]
        done = complete_tasks(ac, cand.tasks, fleet.ctx.now)
        assert done is not None and evaluate_eligibility(done, mission, fleet.policy, fleet.ctx).eligible
        # the real aircraft is untouched: still excluded until the source data changes
        assert not evaluate_eligibility(ac, mission, fleet.policy, fleet.ctx).eligible


def test_qualification_renewal_is_recommended_for_overwater(fleet: Fleet) -> None:
    mission = replace(M_C, required_count=3)
    aircraft = list(fleet.aircraft.values())
    results = [evaluate_eligibility(a, mission, fleet.policy, fleet.ctx) for a in aircraft]
    rec = recommend(aircraft, results, mission, fleet.policy, fleet.ctx)
    assert rec.shortfall == 1
    by_tail = {c.tail_number: list(c.tasks) for c in rec.candidates}
    assert by_tail["4X-J02"] == [(A.RENEW_QUALIFICATION, "OVERWATER")]


def test_missing_data_is_never_simulated(fleet: Fleet) -> None:
    assert complete_tasks(fleet.aircraft["4X-P04"], [(A.FIX_DATA, "COUNTER:LDG")], T0) is None
    assert tasks_for_reasons([Reason(ReasonCode.DATA_MISSING, {"item_kind": "COUNTER", "item_code": "LDG"})]) is None
    assert tasks_for_reasons([Reason(ReasonCode.ENGINE_REQUIREMENT_NOT_MET, {"required": "TWIN", "actual": 1}),
                              Reason(ReasonCode.GROUNDING_MA_OPEN, {"ma_number": "X"})]) is None


# ── task cycles: done only from readable source data ────────────────────────────────────────────────────────────
def test_inspection_is_done_only_when_every_due_moves_later(fleet: Fleet) -> None:
    s03 = fleet.aircraft["4X-S03"]
    cycle = _cycle(fleet, s03, A.PERFORM_INSPECTION, "ANNUAL")
    assert not _done(fleet, s03, A.PERFORM_INSPECTION, "ANNUAL", cycle)
    performed = complete_tasks(s03, [(A.PERFORM_INSPECTION, "ANNUAL")], T0)
    assert performed is not None and _done(fleet, performed, A.PERFORM_INSPECTION, "ANNUAL", cycle)
    # fail-safe: a due that disappears or moves *earlier* is a data problem, never "done"
    gone = replace(s03, inspection_dues=tuple(d for d in s03.inspection_dues if d.inspection_type_code != "ANNUAL"))
    assert not _done(fleet, gone, A.PERFORM_INSPECTION, "ANNUAL", cycle)
    st_gone = aircraft_status(gone, fleet.policy, fleet.ctx)
    assert requirement_for(gone, st_gone, A.PERFORM_INSPECTION, "ANNUAL").status == ComplianceStatus.UNKNOWN
    earlier = _with_due(s03, "ANNUAL", due_at=T0 - timedelta(days=30))
    assert not _done(fleet, earlier, A.PERFORM_INSPECTION, "ANNUAL", cycle)


def test_ma_occurrence_and_unreadable_ma(fleet: Fleet) -> None:
    j04 = fleet.aircraft["4X-J04"]
    cycle = _cycle(fleet, j04, A.CLOSE_MA, "MA-2026-00412")
    closed = complete_tasks(j04, [(A.CLOSE_MA, "MA-2026-00412")], T0)
    assert closed is not None and _done(fleet, closed, A.CLOSE_MA, "MA-2026-00412", cycle)
    # an MA row the adapter cannot read leaves the open list — that is not "closed" (S1)
    unreadable = replace(j04, data_issues=(("MAINTENANCE_ACTION", "MA-2026-00412"),), maintenance_actions=tuple(
        m for m in j04.maintenance_actions if m.ma_number != "MA-2026-00412"))
    assert not _done(fleet, unreadable, A.CLOSE_MA, "MA-2026-00412", cycle)
    # re-opened later: a new occurrence with its own cycle, so an old plan cannot attach to it
    reopened = replace(j04, maintenance_actions=tuple(
        replace(m, opened_at=T0 - timedelta(hours=1)) if m.ma_number == "MA-2026-00412" else m
        for m in j04.maintenance_actions))
    assert _cycle(fleet, reopened, A.CLOSE_MA, "MA-2026-00412") != cycle


def test_return_to_service_cycle_follows_the_last_flight(fleet: Fleet) -> None:
    j03 = fleet.aircraft["4X-J03"]
    cycle = _cycle(fleet, j03, A.RETURN_TO_SERVICE, "IN_MAINTENANCE")
    assert not _done(fleet, j03, A.RETURN_TO_SERVICE, "IN_MAINTENANCE", cycle)
    flew = replace(j03, last_flight_at=T0 - timedelta(minutes=30))  # released, flew, grounded again
    assert _done(fleet, flew, A.RETURN_TO_SERVICE, "IN_MAINTENANCE", cycle)
    assert _cycle(fleet, flew, A.RETURN_TO_SERVICE, "IN_MAINTENANCE") != cycle  # the new grounding starts unplanned
    unreadable = replace(j03, data_issues=(("AIRCRAFT_STATUS", "??"),))
    assert not _done(fleet, unreadable, A.RETURN_TO_SERVICE, "IN_MAINTENANCE", cycle)


# ── what-if simulation is conservative ──────────────────────────────────────────────────────────────────────────
def test_overflown_inspection_restarts_from_its_due_point(fleet: Fleet) -> None:
    s01 = fleet.aircraft["4X-S01"]
    counter = s01.counter(Unit.FH)
    assert counter is not None
    over = _with_due(s01, "I50H", due_value=counter.value - 4)  # flown 4 FH past the limit (within tolerance)
    done = complete_tasks(over, [(A.PERFORM_INSPECTION, "I50H")], T0)
    assert done is not None
    due = done.due("I50H", Unit.FH)
    assert due is not None and due.due_value == pytest.approx(counter.value - 4 + 50)


def test_renewals_repeat_the_previous_validity_and_keep_restrictions(fleet: Fleet) -> None:
    p09 = complete_tasks(fleet.aircraft["4X-P09"], [(A.RENEW_PERMIT, "SPECIAL_FLIGHT")], T0)
    assert p09 is not None
    sp = next(p for p in p09.permits if p.permit_type_code == "SPECIAL_FLIGHT")
    assert sp.expires_at == T0 + timedelta(days=30) and {"DAY_ONLY", "NO_PAX"} <= set(sp.restrictions)
    s01 = fleet.aircraft["4X-S01"]
    revoked = Permit(9999, "COA", "COA-X", T0 - timedelta(days=1), T0 + timedelta(days=300), None, None,
                     ("DAY_ONLY",), "REVOKED")
    renewed = complete_tasks(replace(s01, permits=(*s01.permits, revoked)), [(A.RENEW_PERMIT, "COA")], T0)
    assert renewed is not None
    assert "DAY_ONLY" in next(p for p in renewed.permits if p.permit_type_code == "COA").restrictions
    limited = replace(s01, permits=tuple(replace(p, limit_unit=Unit.FH, limit_value=10.0)
                                         if p.permit_type_code == "COA" else p for p in s01.permits))
    assert complete_tasks(limited, [(A.RENEW_PERMIT, "COA")], T0) is None  # usage-limited: renewal unknown
    assert complete_tasks(s01, [(A.RENEW_PERMIT, "NOPE")], T0) is None  # absent: a data question
    j02 = complete_tasks(fleet.aircraft["4X-J02"], [(A.RENEW_QUALIFICATION, "OVERWATER")], T0)
    assert j02 is not None
    q = next(q for q in j02.qualifications if q.qualification_code == "OVERWATER")
    assert (q.valid_from, q.valid_to) == (T0, T0 + timedelta(days=990))  # 1000 days ago … 10 days ago


def test_idle_check_is_a_recorded_check_flight(fleet: Fleet) -> None:
    s07 = fleet.aircraft["4X-S07"]
    done = complete_tasks(s07, [(A.IDLE_CHECK, "IDLE_CHECK")], T0)
    assert done is not None and done.last_flight_at == T0
    for unit, used in ((Unit.FH, 1.0), (Unit.LDG, 1.0), (Unit.ENG_START, 1.0)):
        before, after = s07.counter(unit), done.counter(unit)
        assert before is not None and after is not None and after.value == pytest.approx(before.value + used)


def test_absurd_source_values_cannot_be_simulated(fleet: Fleet) -> None:
    s03 = fleet.aircraft["4X-S03"]
    huge = replace(s03, inspection_limits=tuple(replace(lim, interval_value=1e12) if lim.inspection_type_code
                                                == "ANNUAL" else lim for lim in s03.inspection_limits))
    assert complete_tasks(huge, [(A.PERFORM_INSPECTION, "ANNUAL")], T0) is None


# ── API ───────────────────────────────────────────────────────────────
def _board(client: TestClient, headers: dict[str, str]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = client.get("/api/v1/maintenance/board", headers=headers).json()["items"]
    assert len({i["card_id"] for i in items}) == len(items)  # every card is unique
    return items


def _key(items: list[dict[str, Any]], tail: str, ref: str) -> str:
    return str(next(i["item_key"] for i in items if i["tail_number"] == tail and i["ref"] == ref))


def _sql(client: TestClient, statement: str, **params: object) -> None:
    c = client.app.state.container  # type: ignore[attr-defined]
    with c.sf() as s:
        s.execute(text(statement), params)
        s.commit()


def test_board_and_manual_scheduling(client: TestClient) -> None:
    viewer = login(client, "ops")
    items = _board(client, viewer)
    assert len(items) == 14 and all(i["status"] == "REQUIRED" for i in items)
    assert items[0]["blocking"]  # blocking work first
    key = _key(items, "4X-S03", "ANNUAL")
    assert client.put("/api/v1/maintenance/tasks", headers=viewer,
                      json={"item_key": key, "status": "SCHEDULED"}).status_code == 403

    maint = login(client, "maint")
    r = client.put("/api/v1/maintenance/tasks", headers=maint, json={"item_key": key, "status": "SCHEDULED"})
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "FIELD_REQUIRED"
    r = client.put("/api/v1/maintenance/tasks", headers=maint, json={
        "item_key": key, "status": "SCHEDULED", "planned_start": "2026-01-16T06:00:00Z",
        "planned_end": "2026-01-15T06:00:00Z"})
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "planned_end"
    r = client.put("/api/v1/maintenance/tasks", headers=maint, json={
        "item_key": key, "status": "SCHEDULED", "planned_start": "2026-01-16T06:00:00Z", "assignee": " צוות א ",
        "notes": "האנגר 2"})
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["planned_start"], r.json()["assignee"]) == (
        "SCHEDULED", "2026-01-16T06:00:00Z", "צוות א")
    item = next(i for i in _board(client, maint) if i["item_key"] == key)
    assert item["status"] == "SCHEDULED" and item["notes"] == "האנגר 2"

    # DONE on the board never makes the aircraft compliant — only the source data does
    r = client.put("/api/v1/maintenance/tasks", headers=maint, json={
        "item_key": key, "status": "DONE", "planned_start": "2026-01-16T06:00:00Z"})
    assert r.status_code == 200 and r.json()["status"] == "DONE"
    assert client.get("/api/v1/aircraft/4X-S03", headers=maint).json()["compliance_status"] == "EXPIRED"

    assert client.put("/api/v1/maintenance/tasks", headers=maint, json={
        "item_key": "1:PERFORM_INSPECTION:NOPE", "status": "REQUIRED"}).status_code == 404
    admin = login(client, "admin")
    audit = client.get("/api/v1/audit", params={"action": "MAINT_TASK_UPDATE"}, headers=admin).json()["items"]
    assert len(audit) == 2


def test_next_cycle_is_planned_afresh_and_the_old_plan_verified(client: TestClient) -> None:
    maint = login(client, "maint")
    key = _key(_board(client, maint), "4X-S03", "ANNUAL")
    body = {"item_key": key, "status": "SCHEDULED", "planned_start": "2026-01-16T06:00:00Z", "notes": "מחזור 1"}
    assert client.put("/api/v1/maintenance/tasks", headers=maint, json=body).status_code == 200
    # the maintenance system records the inspection; the next due (in 3 days) is already within the warning band
    _sql(client, "UPDATE aircraft_inspection_due SET due_at = '2026-01-18T08:00:00Z' WHERE inspection_type_code = "
         "'ANNUAL' AND aircraft_id = (SELECT aircraft_id FROM aircraft WHERE tail_number = '4X-S03')")
    cards = [i for i in _board(client, maint) if i["item_key"] == key]
    assert sorted((c["status"], c["compliance_status"], c["notes"]) for c in cards) == [
        ("REQUIRED", "EXPIRING_SOON", None), ("VERIFIED", "EXPIRING_SOON", "מחזור 1")]
    r = client.put("/api/v1/maintenance/tasks", headers=maint, json={**body, "notes": "מחזור 2"})
    assert r.status_code == 200, r.text  # a new cycle gets its own plan (no clash with the old one)
    cards = [i for i in _board(client, maint) if i["item_key"] == key]
    assert sorted((c["status"], c["notes"]) for c in cards) == [("SCHEDULED", "מחזור 2"), ("VERIFIED", "מחזור 1")]


def test_a_new_grounding_never_inherits_an_old_done(client: TestClient) -> None:
    maint = login(client, "maint")
    key = _key(_board(client, maint), "4X-J03", "IN_MAINTENANCE")
    assert client.put("/api/v1/maintenance/tasks", headers=maint,
                      json={"item_key": key, "status": "DONE", "notes": "שוחרר"}).status_code == 200
    _sql(client, "UPDATE aircraft SET status = 'OPERATIONAL', last_flight_at = '2026-01-15T07:30:00Z' "
         "WHERE tail_number = '4X-J03'")
    assert [i["status"] for i in _board(client, maint) if i["item_key"] == key] == ["VERIFIED"]
    _sql(client, "UPDATE aircraft SET status = 'IN_MAINTENANCE' WHERE tail_number = '4X-J03'")
    cards = sorted((i["status"], i["notes"]) for i in _board(client, maint) if i["item_key"] == key)
    assert cards == [("REQUIRED", None), ("VERIFIED", "שוחרר")]


def test_data_gaps_never_show_as_verified(client: TestClient) -> None:
    maint = login(client, "maint")
    key = _key(_board(client, maint), "4X-S03", "ANNUAL")
    body = {"item_key": key, "status": "IN_PROGRESS", "planned_start": "2026-01-15T06:00:00Z"}
    assert client.put("/api/v1/maintenance/tasks", headers=maint, json=body).status_code == 200
    _sql(client, "UPDATE aircraft_inspection_due SET due_at = NULL WHERE inspection_type_code = 'ANNUAL' "
         "AND aircraft_id = (SELECT aircraft_id FROM aircraft WHERE tail_number = '4X-S03')")
    s03 = [i for i in _board(client, maint) if i["tail_number"] == "4X-S03"]
    assert all(i["status"] != "VERIFIED" for i in s03)
    assert any(i["action"] == "FIX_DATA" for i in s03)


@pytest.mark.parametrize(("patch", "field"), [
    ({"planned_start": "0001-01-01T00:00:00+05:00"}, "planned_start"),
    ({"planned_start": "1900-01-01T00:00:00Z"}, "planned_start"),
    ({"planned_start": "2029-06-01T00:00:00Z"}, "planned_start"),  # beyond the planning window around now
    ({"planned_end": "9999-12-31T23:00:00-05:00"}, "planned_end"),
    ({"assignee": "a\x00b"}, "assignee"),
    ({"notes": "x\u202ey"}, "notes"),
    ({"item_key": "not a key"}, "item_key"),
])
def test_schedule_input_is_bounded(client: TestClient, patch: dict[str, str], field: str) -> None:
    maint = login(client, "maint")
    key = _key(_board(client, maint), "4X-S03", "ANNUAL")
    body = {"item_key": key, "status": "SCHEDULED", "planned_start": "2026-01-16T06:00:00Z", **patch}
    r = client.put("/api/v1/maintenance/tasks", headers=maint, json=body)
    assert r.status_code == 422, r.text
    assert any(e["field"] == field and e["code"] in ("INVALID_VALUE", "PATTERN") for e in r.json()["errors"])


def test_ids_beyond_32_bits_are_rejected(client: TestClient) -> None:
    viewer = login(client, "ops")
    assert client.get("/api/v1/maintenance/recommendations", params={"mission_id": 2**70},
                      headers=viewer).status_code == 422
    assert client.get(f"/api/v1/missions/{2**70}", headers=viewer).status_code == 422


def test_recommendations_for_a_mission_short_of_aircraft(client: TestClient) -> None:
    planner = login(client, "planner")
    body = {"name": "הובלת נוסעים", "mission_type": "TRANSPORT", "required_aircraft_count": 9,
            "start_at": "2026-01-16T08:00:00Z", "est_duration_hours": 4.0, "est_landings": 2,
            "departure_base_code": "CEN", "required_qualifications": ["PAX", "IFR"], "engine_requirement": "MIN_2"}
    r = client.post("/api/v1/missions", headers=planner, json=body)
    assert r.status_code == 201, r.text
    mid = r.json()["mission_id"]
    recs = client.get("/api/v1/maintenance/recommendations", params={"mission_id": mid}, headers=planner).json()
    assert len(recs) == 1 and (recs[0]["shortfall"], recs[0]["eligible_count"]) == (2, 7)
    p02 = next(c for c in recs[0]["candidates"] if c["tail_number"] == "4X-P02")
    assert [(t["action"], t["ref"]) for t in p02["tasks"]] == [("PERFORM_INSPECTION", "I100H")]
    # the board shows which mission each recommended task unblocks
    card = next(i for i in _board(client, planner) if i["item_key"] == p02["tasks"][0]["item_key"])
    assert [u["mission_id"] for u in card["unblocks"]] == [mid]
    # without a mission id: the open missions in the next 14 days
    fleetwide = client.get("/api/v1/maintenance/recommendations", headers=planner).json()
    assert any(rec["mission_id"] == mid for rec in fleetwide)
    assert client.get("/api/v1/maintenance/recommendations", params={"mission_id": 99999},
                      headers=planner).status_code == 404
