"""The extended demo profile (`make demo`, DATABASE.md §6.6): golden data untouched, EX-01…EX-20 as intended, a
pre-filled maintenance schedule that attaches to the right task cycles, and missions that exercise recommendations."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.clock import FrozenClock
from app.config import Settings
from app.datasources.poc_sqlite import PocSqliteSource
from app.db.session import session_factory
from app.domain.compliance.status import aircraft_status
from app.domain.models import EvaluationContext
from app.main import create_app
from app.seed.generator import EXTENDED_FLEET, EXTENDED_PLANS, EXTENDED_VERIFIED, generate
from tests.conftest import T0, TEST_PASSWORD, Fleet, login

EXT_TAILS = {tail for tail, *_ in EXTENDED_FLEET}
# EX-01…EX-20: every open maintenance task of the extended aircraft — (status, blocking)
EXPECTED = {
    ("4X-S09", "PERFORM_INSPECTION", "ANNUAL"): ("EXPIRED", True),
    ("4X-S10", "PERFORM_INSPECTION", "I50H"): ("EXPIRING_SOON", False),
    ("4X-S11", "CLOSE_MA", "MA-2026-00431"): ("EXPIRED", True),
    ("4X-S12", "IDLE_CHECK", "IDLE_CHECK"): ("EXPIRED", True),
    ("4X-S13", "RENEW_PERMIT", "COA"): ("EXPIRING_SOON", False),
    ("4X-P11", "PERFORM_INSPECTION", "A_CHECK"): ("EXPIRING_SOON", False),
    ("4X-P12", "RENEW_PERMIT", "INSURANCE"): ("EXPIRED", True),
    ("4X-P13", "CLOSE_MA", "MA-2026-00432"): ("EXPIRING_SOON", False),
    ("4X-P14", "RETURN_TO_SERVICE", "IN_MAINTENANCE"): ("EXPIRED", True),
    ("4X-P15", "PERFORM_INSPECTION", "LG_INSP"): ("EXPIRING_SOON", False),
    ("4X-P16", "FIX_DATA", "STALE:DATA"): ("UNKNOWN", True),
    ("4X-J07", "PERFORM_INSPECTION", "C_CHECK"): ("EXPIRING_SOON", False),
    ("4X-J09", "IDLE_CHECK", "IDLE_CHECK"): ("EXPIRED", True),
    ("4X-F07", "PERFORM_INSPECTION", "PHASE"): ("EXPIRED", True),
    ("4X-F08", "CLOSE_MA", "MA-2026-00433"): ("EXPIRED", True),
    ("4X-F09", "PERFORM_INSPECTION", "ANNUAL"): ("EXPIRING_SOON", False),
    ("4X-F10", "PERFORM_INSPECTION", "ENG_BORESCOPE"): ("EXPIRING_SOON", False),
}


@pytest.fixture(scope="module")
def ext_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("ext") / "extended.sqlite"
    generate(path, T0, 42, TEST_PASSWORD, "extended")
    return path


@pytest.fixture(scope="module")
def ext(ext_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    settings = Settings(sqlite_path=str(ext_db), models_dir=str(tmp_path_factory.mktemp("models")),
                        demo_password=TEST_PASSWORD, log_level="WARNING", frozen_now=None, env="test")
    with TestClient(create_app(settings, clock=FrozenClock(T0))) as c:
        yield c


@pytest.fixture(scope="module")
def board(ext: TestClient) -> list[dict[str, Any]]:
    headers = login(ext, "maint")
    items: list[dict[str, Any]] = ext.get("/api/v1/maintenance/board", headers=headers).json()["items"]
    return items


def test_golden_fleet_is_untouched(ext_db: Path, fleet: Fleet) -> None:
    snap = PocSqliteSource(session_factory(f"sqlite:///{ext_db}")).load_snapshot()
    ctx = EvaluationContext(now=T0, required_permit_types=snap.required_permit_types,
                            data_load_status=dict(snap.data_load_status))
    extended = {a.tail_number: a for a in snap.aircraft}
    assert set(extended) == set(fleet.aircraft) | EXT_TAILS and len(extended) == 54
    for tail, golden in fleet.aircraft.items():
        assert aircraft_status(extended[tail], fleet.policy, ctx) == aircraft_status(golden, fleet.policy, fleet.ctx)


def test_every_extended_scenario_is_on_the_board(board: list[dict[str, Any]]) -> None:
    open_cards = {(i["tail_number"], i["action"], i["ref"]): (i["compliance_status"], i["blocking"])
                  for i in board if i["status"] != "VERIFIED" and i["tail_number"] in EXT_TAILS}
    assert open_cards == EXPECTED


def test_prefilled_schedule_attaches_to_the_current_cycles(board: list[dict[str, Any]]) -> None:
    by_column: dict[str, list[str]] = {}
    for i in board:
        by_column.setdefault(i["status"], []).append(f'{i["tail_number"]}:{i["ref"]}')
    assert {c: len(v) for c, v in by_column.items() if c != "REQUIRED"} == {
        "SCHEDULED": 5, "IN_PROGRESS": 4, "DONE": 3, "VERIFIED": 3}
    assert len(EXTENDED_PLANS) + len(EXTENDED_VERIFIED) == 5 + 4 + 3 + 3  # no plan was orphaned
    assert sorted(by_column["VERIFIED"]) == ["4X-F11:MA-2026-00434", "4X-J08:RADIO", "4X-S14:ANNUAL"]
    verified = [i for i in board if i["status"] == "VERIFIED"]
    assert all(i["compliance_status"] == "OK" and not i["blocking"] for i in verified)
    # "done" on the board is not a fact: the aircraft are still not compliant
    done = {(i["tail_number"], i["compliance_status"]) for i in board if i["status"] == "DONE"}
    assert done == {("4X-P05", "EXPIRED"), ("4X-S07", "EXPIRED"), ("4X-F08", "EXPIRED")}


def test_missions_short_of_aircraft_get_recommendations(ext: TestClient) -> None:
    headers = login(ext, "planner")
    recs = {r["name"]: r for r in ext.get("/api/v1/maintenance/recommendations", headers=headers).json()}

    def tasks(name: str) -> set[tuple[str, str, str]]:
        return {(c["tail_number"], t["action"], t["ref"]) for c in recs[name]["candidates"] for t in c["tasks"]}

    night = recs["תרגיל אימון לילה — דרום"]
    assert (night["shortfall"], len(night["candidates"])) == (5, 7)  # every verified option, not just enough
    assert {("4X-S09", "PERFORM_INSPECTION", "ANNUAL"), ("4X-S11", "CLOSE_MA", "MA-2026-00431"),
            ("4X-S12", "IDLE_CHECK", "IDLE_CHECK")} <= tasks("תרגיל אימון לילה — דרום")
    assert recs["הטסת צוותים רפואיים"]["shortfall"] > 0
    assert {("4X-P12", "RENEW_PERMIT", "INSURANCE"), ("4X-P14", "RETURN_TO_SERVICE", "IN_MAINTENANCE")} <= tasks(
        "הטסת צוותים רפואיים")
    assert "4X-P16" not in {c["tail_number"] for c in recs["הטסת צוותים רפואיים"]["candidates"]}  # stale data
    assert recs["גיחות אימון קרב — שבוע הבא"]["shortfall"] > 0
    assert {("4X-F07", "PERFORM_INSPECTION", "PHASE"), ("4X-F08", "CLOSE_MA", "MA-2026-00433")} <= tasks(
        "גיחות אימון קרב — שבוע הבא")
    for fine in ("טיסת מעבר לבסיס דרום", "הובלת מטען לבסיס צפון"):  # not short ⇒ nothing computed
        assert (recs[fine]["shortfall"], recs[fine]["candidates"]) == (0, [])


def test_assignment_that_lost_eligibility_is_alerted(ext: TestClient) -> None:
    headers = login(ext, "ops")
    alerts = ext.get("/api/v1/fleet/alerts", headers=headers).json()
    at_risk = {(a["tail_number"], a["ref"]) for a in alerts if a["source"] == "ASSIGNMENT"}
    assert at_risk == {("4X-F09", "תרגיל משותף — טייסת 43")}  # 4X-F11's MA was closed, so it is fine


def test_demo_script_still_holds(ext: TestClient) -> None:
    """PRD §11 step 5 on the demo data: 4X-P01 is the best fit, 4X-P02 / 4X-P03 excluded."""
    headers = login(ext, "planner")
    body = {"name": "הובלת נוסעים", "mission_type": "TRANSPORT", "required_aircraft_count": 3,
            "start_at": "2026-01-16T08:00:00Z", "est_duration_hours": 4.0, "est_landings": 2,
            "departure_base_code": "CEN", "required_qualifications": ["PAX", "IFR"], "engine_requirement": "MIN_2"}
    mid = ext.post("/api/v1/missions", headers=headers, json=body).json()["mission_id"]
    run = ext.post(f"/api/v1/missions/{mid}/optimize", headers=headers, json={}).json()
    tails = {c["aircraft_id"]: c["tail_number"] for c in run["candidates"]}
    assert "4X-P01" in {tails[a] for a in run["sets"][0]["aircraft_ids"]}
    excluded = {c["tail_number"] for c in run["candidates"] if not c["eligible"]}
    assert {"4X-P02", "4X-P03"} <= excluded
