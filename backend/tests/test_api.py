"""API contract, validation, authorization matrix and assignment safety (TESTING.md §3.5)."""

from __future__ import annotations

import re
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clock import FrozenClock
from app.config import Settings
from app.datasources.sql_views import SqlAlchemyRunner, SqlViewSource
from app.domain.reason_codes import ReasonCode, UnblockCode, WarningCode
from tests.conftest import login

M_A = {"name": "אימון לילה", "mission_type": "TRAINING", "required_aircraft_count": 2,
       "start_at": "2026-01-15T10:00:00Z", "est_duration_hours": 2.5, "est_landings": 1, "est_touch_and_goes": 6,
       "departure_base_code": "NTH", "required_qualifications": ["NIGHT"], "engine_requirement": "SINGLE"}

# (method, path, allowed roles) — SECURITY.md §3.3
ALL = {"ops", "planner", "maint", "admin"}
PLAN = {"planner", "admin"}
# Every authenticated route (test_every_route_is_in_the_matrix keeps this list complete).
ROUTES = [
    ("GET", "/api/v1/auth/me", ALL),
    ("POST", "/api/v1/auth/logout", ALL),
    ("GET", "/api/v1/reference", ALL),
    ("GET", "/api/v1/fleet/summary", ALL),
    ("GET", "/api/v1/fleet/alerts", ALL),
    ("GET", "/api/v1/fleet/timeline", ALL),
    ("GET", "/api/v1/fleet/data-freshness", ALL),
    ("GET", "/api/v1/aircraft", ALL),
    ("GET", "/api/v1/aircraft/4X-J04", ALL),
    ("GET", "/api/v1/aircraft/4X-J04/flights", ALL),
    ("GET", "/api/v1/aircraft/4X-J04/risk", ALL),
    ("POST", "/api/v1/missions", PLAN),
    ("GET", "/api/v1/missions", ALL),
    ("GET", "/api/v1/missions/1", ALL),
    ("PATCH", "/api/v1/missions/1", PLAN),
    ("POST", "/api/v1/missions/1/duplicate", PLAN),
    ("POST", "/api/v1/missions/1/cancel", PLAN),
    ("POST", "/api/v1/missions/1/optimize", PLAN),
    ("GET", "/api/v1/missions/1/runs", ALL),
    ("GET", "/api/v1/optimization-runs/00000000-0000-0000-0000-000000000000", ALL),
    ("POST", "/api/v1/missions/1/assign", PLAN),
    ("POST", "/api/v1/missions/1/unassign", PLAN),
    ("POST", "/api/v1/eligibility/check", {"planner", "maint", "admin"}),
    ("POST", "/api/v1/planning/horizon", PLAN),
    ("POST", "/api/v1/planning/horizon/apply", PLAN),
    ("GET", "/api/v1/ml/models", ALL),
    ("GET", "/api/v1/ml/duration-buffer", PLAN),
    ("POST", "/api/v1/ml/models/no_such_model/retrain", {"admin"}),
    ("GET", "/api/v1/ml/jobs/no-such-job", {"admin"}),
    ("GET", "/api/v1/settings/policy", ALL),
    ("PUT", "/api/v1/settings/policy", {"admin"}),
    ("GET", "/api/v1/settings/hangar-capacity", ALL),
    ("GET", "/api/v1/settings/weights", ALL),
    ("PUT", "/api/v1/settings/weights", {"admin"}),
    ("GET", "/api/v1/settings/tg-weights", ALL),
    ("PUT", "/api/v1/settings/tg-weights", {"admin"}),
    ("PUT", "/api/v1/settings/hangar-capacity", {"admin"}),
    ("GET", "/api/v1/audit", {"maint", "admin"}),
]
PUBLIC = {"/api/v1/health", "/api/v1/ready", "/api/v1/auth/login", "/api/v1/auth/config",
          "/api/v1/auth/oidc/login", "/api/v1/auth/oidc/callback"}
BODIES = {"/api/v1/missions": M_A, "/api/v1/eligibility/check": {"mission": M_A},
          "/api/v1/settings/policy": {"landing_buffer": 1}, "/api/v1/settings/hangar-capacity": [],
          "/api/v1/missions/1": {"notes": "x"}, "/api/v1/planning/horizon": {},
          "/api/v1/planning/horizon/apply": {"assignments": []},
          "/api/v1/settings/weights": {"presets": {"BALANCED": {"risk": 0.3, "best_fit": 0.25, "balance": 0.15,
                                                                "capacity": 0.15, "base_match": 0.1, "warnings": 0.05,
                                                                "expiry_window": 0.1}}},
          "/api/v1/settings/tg-weights": []}


def test_every_route_is_in_the_matrix(client: TestClient) -> None:
    covered = {(m, re.sub(r"/(4X-J04|1|no_such_model|no-such-job|0{8}-[0-9-]+)(?=/|$)", "/{}", p))
               for m, p, _ in ROUTES}
    for route in client.app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1/") or path in PUBLIC or "openapi" in path or "docs" in path:
            continue
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            assert (method, re.sub(r"\{[^}]+\}", "{}", path)) in covered, f"{method} {path} not in ROUTES"


def test_health_and_ready(client: TestClient) -> None:
    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/ready").json()["db"] == "ok"


@pytest.mark.parametrize(("method", "path", "allowed"), ROUTES)
def test_anonymous_is_rejected(client: TestClient, method: str, path: str, allowed: set[str]) -> None:
    r = client.request(method, path, json=BODIES.get(path))
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert r.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("role", ["ops", "planner", "maint", "admin"])
@pytest.mark.parametrize(("method", "path", "allowed"), ROUTES)
def test_authorization_matrix(client: TestClient, role: str, method: str, path: str, allowed: set[str]) -> None:
    headers = login(client, role)
    r = client.request(method, path, json=BODIES.get(path), headers=headers)
    if role in allowed:
        assert r.status_code not in (401, 403), r.text
    else:
        assert r.status_code == 403
        assert r.json()["code"] == "FORBIDDEN"


def test_csrf_required_for_state_changes(client: TestClient) -> None:
    login(client, "planner")
    r = client.post("/api/v1/missions", json=M_A)
    assert r.status_code == 403


def test_login_failure_and_audit(client: TestClient) -> None:
    r = client.post("/api/v1/auth/login", json={"username": "planner", "password": "wrong"})
    assert r.status_code == 401
    headers = login(client, "admin")
    actions = {a["action"] for a in client.get("/api/v1/audit", headers=headers).json()["items"]}
    assert {"LOGIN_FAILED", "LOGIN"} <= actions


@pytest.mark.parametrize(("patch", "field", "code"), [
    ({"start_at": "2025-01-01T00:00:00Z"}, "start_at", "START_IN_PAST"),
    ({"required_aircraft_count": 0}, "required_aircraft_count", "VALUE_TOO_SMALL"),
    ({"est_duration_hours": 30}, "est_duration_hours", "VALUE_TOO_LARGE"),
    ({"name": ""}, "name", "FIELD_REQUIRED"),
    ({"required_qualifications": ["XYZ"]}, "required_qualifications", "UNKNOWN_QUALIFICATION"),
    ({"force_include": ["4X-S01"], "force_exclude": ["4X-S01"]}, "", "TAIL_FORCED_BOTH_WAYS"),
])
def test_mission_validation_codes(client: TestClient, patch: dict[str, object], field: str, code: str) -> None:
    headers = login(client, "planner")
    r = client.post("/api/v1/missions", json={**M_A, **patch}, headers=headers)
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert any(e["code"] == code and e["field"] == field for e in errors), errors


def test_dashboard_summary(client: TestClient) -> None:
    login(client, "ops")
    s = client.get("/api/v1/fleet/summary").json()
    assert (s["aircraft_with_expired_items"], s["aircraft_unknown"], s["open_grounding_mas"], s["in_maintenance"]) \
        == (4, 1, 1, 1)
    expired = client.get("/api/v1/aircraft", params={"compliance_status": "EXPIRED"}).json()["items"]
    assert {a["tail_number"] for a in expired} == {"4X-S03", "4X-P05", "4X-S07", "4X-J02"}


def test_aircraft_detail_for_3d(client: TestClient) -> None:
    login(client, "ops")
    d = client.get("/api/v1/aircraft/4X-J04").json()
    assert d["derived_status"] == "GROUNDED"
    assert d["model"]["engine_count"] == 2 and "ENGINE_2" in d["model"]["zones"]
    assert d["maintenance_actions"][0]["zone_code"] == "ENGINE_2"
    assert client.get("/api/v1/aircraft/4X-ZZZ").status_code == 404


def _optimize(client: TestClient, headers: dict[str, str]) -> tuple[int, dict[str, object]]:
    mid = client.post("/api/v1/missions", json=M_A, headers=headers).json()["mission_id"]
    run = client.post(f"/api/v1/missions/{mid}/optimize", json={}, headers=headers).json()
    return mid, run


def test_optimize_and_assign_flow(client: TestClient) -> None:
    headers = login(client, "planner")
    mid, run = _optimize(client, headers)
    assert run["solver_status"] == "OPTIMAL" and run["n_eligible"] == 4
    assert run["run_warnings"][0]["code"] == "ML_MODEL_UNAVAILABLE"  # no models in the test models dir
    r = client.post(f"/api/v1/missions/{mid}/assign", json={"run_id": run["run_id"], "set_index": 0},
                    headers=headers)
    assert r.status_code == 200 and len(r.json()) == 2
    assert client.get(f"/api/v1/missions/{mid}").json()["status"] == "ASSIGNED"
    assert client.get(f"/api/v1/optimization-runs/{run['run_id']}").json()["run_id"] == run["run_id"]


def test_assign_rejects_stale_run(client: TestClient, clock: FrozenClock) -> None:
    headers = login(client, "planner")
    mid, run = _optimize(client, headers)
    clock.advance(timedelta(minutes=31))
    r = client.post(f"/api/v1/missions/{mid}/assign", json={"run_id": run["run_id"], "set_index": 0},
                    headers=headers)
    assert r.status_code == 409 and r.json()["code"] == "RUN_STALE"


def test_assign_revalidates_eligibility(client: TestClient, db_copy: Path) -> None:
    headers = login(client, "planner")
    mid, run = _optimize(client, headers)
    chosen = run["sets"][0]["aircraft_ids"]  # type: ignore[index]
    with sqlite3.connect(db_copy) as con:
        con.execute("UPDATE aircraft_permit SET expires_at='2026-01-14T00:00:00Z' WHERE aircraft_id=? "
                    "AND permit_type_code='COA'", (chosen[0],))
    r = client.post(f"/api/v1/missions/{mid}/assign", json={"run_id": run["run_id"], "set_index": 0},
                    headers=headers)
    assert r.status_code == 409 and r.json()["code"] == "ELIGIBILITY_CHANGED"


def test_force_include_ineligible_is_rejected(client: TestClient) -> None:
    headers = login(client, "planner")
    mid = client.post("/api/v1/missions", json={**M_A, "force_include": ["4X-S03"]}, headers=headers).json()[
        "mission_id"]
    r = client.post(f"/api/v1/missions/{mid}/optimize", json={}, headers=headers)
    assert r.status_code == 422 and r.json()["code"] == "FORCE_INCLUDE_INELIGIBLE"


def test_policy_update_is_audited(client: TestClient) -> None:
    headers = login(client, "admin")
    r = client.put("/api/v1/settings/policy", json={"duration_buffer_pct": 20}, headers=headers)
    assert r.status_code == 200 and r.json()["duration_buffer_pct"] == 20
    assert client.put("/api/v1/settings/policy", json={"duration_buffer_pct": 500}, headers=headers).status_code == 422
    items = client.get("/api/v1/audit", params={"action": "POLICY_UPDATE"}, headers=headers).json()["items"]
    assert items[0]["details"]["after"] == {"duration_buffer_pct": 20}


def test_reference_exposes_all_codes(client: TestClient) -> None:
    login(client, "ops")
    codes = client.get("/api/v1/reference").json()["codes"]
    assert set(codes["reasons"]) == {str(c) for c in ReasonCode}
    assert set(codes["warnings"]) == {str(c) for c in WarningCode}
    assert set(codes["unblock"]) == {str(c) for c in UnblockCode}


def test_frontend_has_hebrew_template_for_every_code() -> None:
    path = Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n" / "reasonCodes.ts"
    if not path.exists():
        pytest.skip("frontend not built yet")
    text = path.read_text(encoding="utf-8")
    missing = [str(c) for c in (*ReasonCode, *WarningCode, *UnblockCode) if f"{c}:" not in text]
    assert not missing, f"missing Hebrew templates: {missing}"


def test_logout_revokes_the_session_token(client: TestClient) -> None:
    headers = login(client, "planner")
    token = client.cookies.get("ao_session")
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    client.cookies.set("ao_session", token or "")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_untrusted_request_id_is_replaced(client: TestClient) -> None:
    r = client.get("/api/v1/health", headers={"X-Request-ID": "x" * 200})
    assert re.fullmatch(r"[0-9a-f-]{36}", r.headers["X-Request-ID"])
    good = "123e4567-e89b-12d3-a456-426614174000"
    assert client.get("/api/v1/health", headers={"X-Request-ID": good}).headers["X-Request-ID"] == good


def test_audit_log_is_append_only_in_the_database(client: TestClient) -> None:
    login(client, "admin")
    c = client.app.state.container  # type: ignore[attr-defined]
    with c.sf() as s, pytest.raises(Exception, match="append-only"):
        s.execute(text("UPDATE audit_log SET action = 'X'"))


def test_public_jwt_secret_is_never_used_and_prod_config_is_strict() -> None:
    public = "dev-only-insecure-secret-change-me-0123456789"
    dev = Settings(_env_file=None, jwt_secret=public)  # type: ignore[call-arg]
    assert dev.jwt_secret != public and len(dev.jwt_secret) >= 32
    assert Settings(_env_file=None).jwt_secret  # type: ignore[call-arg]
    prod = Settings(_env_file=None, env="prod", jwt_secret=public, auth_mode="local")  # type: ignore[call-arg]
    with pytest.raises(RuntimeError, match=r"APP_JWT_SECRET.*APP_AUTH_MODE=local.*APP_COOKIE_SECURE"):
        prod.validate_for_prod()


def test_vertica_requires_verified_tls_in_prod() -> None:
    from app.datasources.vertica import parse_dsn

    dsn = "vertica://u:p@lake:5433/db?schema=aircraft_opt&tlsmode=require"
    assert parse_dsn(dsn)[0]["tlsmode"] == "require"
    with pytest.raises(ValueError, match="verify-ca or verify-full"):
        parse_dsn(dsn, require_verified_tls=True)
    assert parse_dsn(dsn.replace("require", "verify-full"), require_verified_tls=True)[1] == "aircraft_opt"
    with pytest.raises(ValueError, match="invalid schema"):
        SqlViewSource(SqlAlchemyRunner(None), schema="x; DROP TABLE y")


def test_assigned_mission_that_lost_eligibility_is_alerted(client: TestClient) -> None:
    headers = login(client, "planner")
    mid = client.post("/api/v1/missions", json=M_A, headers=headers).json()["mission_id"]
    c = client.app.state.container  # type: ignore[attr-defined]
    with c.sf() as s:  # 4X-S03 has an expired annual inspection (SC-01) — as if it expired after assignment
        s3 = s.execute(text("SELECT aircraft_id FROM aircraft WHERE tail_number = '4X-S03'")).scalar_one()
        s.execute(text("UPDATE mission SET status = 'ASSIGNED' WHERE mission_id = :m"), {"m": mid})
        s.execute(text(
            "INSERT INTO mission_assignment (mission_id, aircraft_id, window_start, window_end, status, assigned_by, "
            "assigned_at) VALUES (:m, :a, '2026-01-15T10:00:00Z', '2026-01-15T14:00:00Z', 'ACTIVE', 1, "
            "'2026-01-15T08:00:00Z')"), {"m": mid, "a": s3})
        s.commit()
    alerts = client.get("/api/v1/fleet/alerts", headers=headers).json()
    assert {"tail_number": "4X-S03", "source": "ASSIGNMENT", "ref": M_A["name"], "status": "EXPIRED"}.items() <= next(
        a for a in alerts if a["source"] == "ASSIGNMENT").items()


# ── settings: scoring weights (FR-45), touch-and-go weights (FR-60) ─────
BALANCED = {"risk": 3, "best_fit": 2.5, "balance": 1.5, "capacity": 1.5, "base_match": 1, "warnings": 0.5,
            "expiry_window": 0}


def test_admin_edits_preset_weights_normalized_and_audited(client: TestClient) -> None:
    headers = login(client, "admin")
    r = client.put("/api/v1/settings/weights", json={"presets": {"BALANCED": BALANCED}}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["presets"]["BALANCED"] == pytest.approx(
        {"risk": 0.3, "best_fit": 0.25, "balance": 0.15, "capacity": 0.15, "base_match": 0.1, "warnings": 0.05,
         "expiry_window": 0.0})
    bad = client.put("/api/v1/settings/weights", json={"presets": {"BALANCED": {**BALANCED, "risk": -1}}},
                     headers=headers)
    assert bad.status_code == 422
    missing = client.put("/api/v1/settings/weights", json={"presets": {"BALANCED": {"risk": 1}}}, headers=headers)
    assert missing.status_code == 422
    nan = client.put("/api/v1/settings/weights", content='{"presets": {"BALANCED": {"risk": NaN, "best_fit": 1, '
                     '"balance": 1, "capacity": 1, "base_match": 1, "warnings": 1, "expiry_window": 1}}}',
                     headers={**headers, "Content-Type": "application/json"})
    assert nan.status_code == 422
    actions = {a["action"] for a in client.get("/api/v1/audit", headers=headers).json()["items"]}
    assert "WEIGHTS_UPDATE" in actions


def test_preset_weights_drive_the_score(client: TestClient) -> None:
    headers = login(client, "admin")
    only_risk = {**dict.fromkeys(BALANCED, 0), "risk": 1}
    client.put("/api/v1/settings/weights", json={"presets": {"BALANCED": only_risk}}, headers=headers)
    planner = login(client, "planner")
    mid = client.post("/api/v1/missions", json=M_A, headers=planner).json()["mission_id"]
    run = client.post(f"/api/v1/missions/{mid}/optimize", json={}, headers=planner).json()
    scored = [c for c in run["candidates"] if c["eligible"] and c["score_breakdown"]]
    assert scored and all(set(c["score_breakdown"]) >= {"risk"} for c in scored)
    for c in scored:
        weighted = {k: v["weight"] for k, v in c["score_breakdown"].items()}
        assert weighted["risk"] == pytest.approx(1.0) and weighted["best_fit"] == pytest.approx(0.0)


def test_tg_weight_override_changes_eligibility_and_is_audited(client: TestClient) -> None:
    headers = login(client, "admin")
    type_code = client.get("/api/v1/aircraft/4X-S05", headers=headers).json()["type_code"]
    rows = client.get("/api/v1/settings/tg-weights", headers=headers).json()
    lg = next(r for r in rows if r["type_code"] == type_code and r["inspection_type_code"] == "LG_INSP")
    assert lg["override_tg_weight"] is None and lg["effective_tg_weight"] == lg["reference_tg_weight"]

    def lg_reason() -> dict[str, object]:
        cand = client.post("/api/v1/eligibility/check", json={"mission": M_A, "tail_numbers": ["4X-S05"]},
                           headers=headers).json()[0]
        return next(x["params"] for x in cand["reasons"] if x["params"].get("inspection_code") == "LG_INSP")

    before = lg_reason()  # SC-04: 5 weighted landings left; 1 landing + 1 buffer + 6 TG × 1.0 = 8
    assert before["remaining"] == 5.0 and before["required"] == 8.0
    body = [{"type_code": type_code, "inspection_type_code": "LG_INSP", "unit": lg["unit"], "tg_weight": 2.0}]
    updated = client.put("/api/v1/settings/tg-weights", json=body, headers=headers)
    assert updated.status_code == 200, updated.text
    assert next(r for r in updated.json() if r["inspection_type_code"] == "LG_INSP"
                and r["type_code"] == type_code)["effective_tg_weight"] == 2.0
    after = lg_reason()
    assert after["remaining"] == 5.0  # the flown history is never re-weighted (lake due_value space)
    assert after["required"] == 14.0  # only the planned TGs use the override: 1 + 1 + 6 × 2.0
    lower = client.put("/api/v1/settings/tg-weights", json=[{**body[0], "tg_weight": 0}], headers=headers)
    assert lower.status_code == 422 and lower.json()["errors"][0]["code"] == "VALUE_TOO_SMALL"
    client.put("/api/v1/settings/tg-weights", json=[{**body[0], "tg_weight": None}], headers=headers)
    assert lg_reason()["required"] == 8.0  # removing the override restores the reference weight
    unknown = client.put("/api/v1/settings/tg-weights", json=[{**body[0], "inspection_type_code": "NOPE"}],
                         headers=headers)
    assert unknown.status_code == 422
    audit = client.get("/api/v1/audit", headers=headers, params={"action": "TG_WEIGHTS_UPDATE"}).json()["items"]
    assert any(a["details"]["changes"][0]["before"] == lg["reference_tg_weight"] for a in audit)


def test_session_commits_before_the_response_is_sent() -> None:
    """Read-your-writes: a client must be able to GET what a POST just returned (no commit-after-response race)."""
    from typing import get_args

    from app.api.deps import SessionDep

    assert get_args(SessionDep)[1].scope == "function"


def test_created_mission_is_immediately_readable(client: TestClient) -> None:
    headers = login(client, "planner")
    created = client.post("/api/v1/missions", json=M_A, headers=headers)
    assert created.status_code == 201
    assert client.get(f"/api/v1/missions/{created.json()['mission_id']}", headers=headers).status_code == 200



# ── expiration "avoid bands" (ranking, FR-34) and fleet expiry filters (FR-14) ──
def test_mission_bands_are_stored_validated_and_rank_only(client: TestClient) -> None:
    headers = login(client, "planner")
    bands = [{"unit": "FH", "within": 0, "until": 100000}, {"unit": "CAL_DAY", "within": 0, "until": 3650}]
    plain = client.post("/api/v1/missions", json=M_A, headers=headers).json()
    mission = client.post("/api/v1/missions", json={**M_A, "expiry_windows": bands}, headers=headers).json()
    assert mission["expiry_windows"] == [{"unit": "FH", "within": 0, "until": 100000},
                                         {"unit": "CAL_DAY", "within": 0, "until": 3650}]
    assert "expiry_windows" not in mission["overrides"]
    with_w = client.post(f"/api/v1/missions/{mission['mission_id']}/optimize", json={}, headers=headers).json()
    without = client.post(f"/api/v1/missions/{plain['mission_id']}/optimize", json={}, headers=headers).json()
    # BR-14: bands never change eligibility — only ranking and warnings
    assert {c["tail_number"] for c in with_w["candidates"] if c["eligible"]} == \
        {c["tail_number"] for c in without["candidates"] if c["eligible"]}
    eligible = [c for c in with_w["candidates"] if c["eligible"]]
    assert all(c["score_breakdown"]["expiry_window"]["weight"] > 0 for c in eligible)
    assert all(c["score_breakdown"]["expiry_window"]["value"] == 0 for c in eligible)  # every band is hit
    assert all("EXPIRES_WITHIN_WINDOW" in {w["code"] for w in c["warnings"]} for c in eligible)
    assert all(c["score_breakdown"]["expiry_window"]["weight"] == 0
               for c in without["candidates"] if c["eligible"])


@pytest.mark.parametrize(("windows", "code"), [
    ([{"unit": "FH", "until": 5}, {"unit": "FH", "until": 9}], "DUPLICATE_UNIT"),
    ([{"unit": "LDG", "within": 10, "until": 5}], "WINDOW_RANGE"),
    ([{"unit": "LDG", "within": 3}], None),  # ועד is required
    ([{"unit": "IDLE_DAY", "until": 3}], None),
])
def test_band_validation(client: TestClient, windows: list[dict[str, object]], code: str | None) -> None:
    headers = login(client, "planner")
    r = client.post("/api/v1/missions", json={**M_A, "expiry_windows": windows}, headers=headers)
    assert r.status_code == 422
    if code:
        assert code in {e["code"] for e in r.json()["errors"]}


def test_unreadable_stored_band_never_breaks_safety_paths(client: TestClient) -> None:
    headers = login(client, "planner")
    mid = client.post("/api/v1/missions", json={**M_A, "expiry_windows": [{"unit": "FH", "until": 5}]},
                      headers=headers).json()["mission_id"]
    c = client.app.state.container  # type: ignore[attr-defined]
    huge = "1" + "0" * 400  # float() overflows
    with c.sf() as s:
        s.execute(text("UPDATE mission SET overrides_json = :j, status = 'ASSIGNED' WHERE mission_id = :m"),
                  {"j": '{"expiry_windows": [{"unit": "NOPE", "until": "x"}, 7, {"unit": "FH", "within": 0, '
                        f'"until": {huge}}}]}}', "m": mid})
        s.execute(text(  # an ACTIVE assignment so the at-risk re-check really parses this mission
            "INSERT INTO mission_assignment (mission_id, aircraft_id, window_start, window_end, status, assigned_by, "
            "assigned_at) VALUES (:m, 1, '2026-01-15T10:00:00Z', '2026-01-15T14:00:00Z', 'ACTIVE', 1, "
            "'2026-01-15T08:00:00Z')"), {"m": mid})
        s.commit()
    assert client.get(f"/api/v1/missions/{mid}", headers=headers).json()["expiry_windows"] == []
    assert client.get("/api/v1/fleet/alerts", headers=headers).status_code == 200


def test_fleet_views_filter_by_units_and_bands(client: TestClient) -> None:
    headers = login(client, "maint")
    cal = client.get("/api/v1/fleet/timeline", params={"days": 365, "units": "CAL_DAY"}, headers=headers).json()
    assert cal and {x["unit"] for x in cal} == {"CAL_DAY"}
    both = client.get("/api/v1/fleet/timeline", params={"days": 365, "units": "CAL_DAY,FH"}, headers=headers).json()
    assert {x["unit"] for x in both} <= {"CAL_DAY", "FH"} and len(both) >= len(cal)
    until = client.get("/api/v1/fleet/timeline", params={"until": "2026-01-20", "units": "CAL_DAY"},
                       headers=headers).json()
    assert all(x["date"] < "2026-01-21" for x in until) and len(until) <= len(cal)
    everyone = client.get("/api/v1/aircraft", params={"page_size": 200}, headers=headers).json()["total"]
    soon = client.get("/api/v1/aircraft", params={"page_size": 200, "expiry": "CAL_DAY:0:7"}, headers=headers).json()
    assert 0 < soon["total"] < everyone
    tails = {a["tail_number"] for a in soon["items"]}
    assert {"4X-P06", "4X-S03", "4X-P05"} <= tails  # due in 3 days; overdue items count as 0 remaining
    assert "4X-P04" in {a["tail_number"] for a in client.get(  # SC-11: unknown LDG counter is never hidden
        "/api/v1/aircraft", params={"page_size": 200, "expiry": "LDG:0:1"}, headers=headers).json()["items"]}
    for bad in ("IDLE_DAY:0:5", "FH:5:1", "FH", "FH:0:5,FH:1:9"):
        assert client.get("/api/v1/aircraft", params={"expiry": bad}, headers=headers).status_code == 422
    assert client.get("/api/v1/fleet/timeline", params={"units": "IDLE_DAY"}, headers=headers).status_code == 422
    # safety alerts without a unit (grounding MA, data issues, at-risk assignments) survive a unit filter
    alerts = client.get("/api/v1/fleet/alerts", params={"units": "CAL_DAY"}, headers=headers).json()
    assert any(a["source"] == "MAINTENANCE_ACTION" for a in alerts)


def test_timeline_never_hides_overdue_usage_items_without_forecast() -> None:
    from datetime import UTC, datetime

    from app.domain.compliance.status import ItemStatus
    from app.domain.models import ComplianceStatus
    from app.services.fleet import ExpiryFilter

    now = datetime(2026, 1, 15, 8, tzinfo=UTC)
    overdue = ItemStatus(source="INSPECTION", ref="I100H", unit="FH", kind="COUNTER", status=ComplianceStatus.EXPIRED,
                         remaining=-3.0, due_value=100.0, due_at=None, interval=100.0, zone_code=None, blocking=True)
    from tests.builders import an_aircraft

    window = ExpiryFilter(frozenset({"FH"}), now, rates={})
    assert window.item_date(an_aircraft().build(), overdue, now) == now


def test_missions_can_never_loosen_admin_buffers(client: TestClient) -> None:
    headers = login(client, "planner")
    r = client.post("/api/v1/missions", json={**M_A, "overrides": {"calendar_buffer_hours": 0}}, headers=headers)
    assert r.status_code == 422  # calendar/landing buffers are admin policy only
    plain = client.post("/api/v1/missions", json=M_A, headers=headers).json()["mission_id"]
    legacy = client.post("/api/v1/missions", json=M_A, headers=headers).json()["mission_id"]
    c = client.app.state.container  # type: ignore[attr-defined]
    with c.sf() as s:  # an older mission row that still stores loosened buffers
        s.execute(text("UPDATE mission SET overrides_json = :j WHERE mission_id = :m"), {
            "j": '{"calendar_buffer_hours": 0, "landing_buffer": 0, "duration_buffer_pct": 0}', "m": legacy})
        s.commit()

    def eligible(mid: int) -> list[tuple[str, bool]]:
        run = client.post(f"/api/v1/missions/{mid}/optimize", json={}, headers=headers).json()
        return sorted((c["tail_number"], c["eligible"]) for c in run["candidates"])

    assert eligible(legacy) == eligible(plain)
    assert client.get(f"/api/v1/missions/{legacy}", headers=headers).json()["overrides"] == {"duration_buffer_pct": 0}


def test_band_filter_never_hides_stale_unreadable_or_expired() -> None:
    from dataclasses import replace as rep
    from datetime import timedelta as td

    from app.domain.compliance.status import aircraft_status
    from app.domain.models import Unit
    from app.domain.policy import PlanningPolicy
    from app.services.fleet import BandFilter
    from tests.builders import T0, an_aircraft, ctx

    p, band = PlanningPolicy(), BandFilter((("FH", 0.0, 50.0),))
    healthy = an_aircraft().remaining("I100H", Unit.FH, 60.0).build()
    assert not band.matches(aircraft_status(healthy, p, ctx()))  # 60 FH left: outside 0–50
    stale = an_aircraft().remaining("I100H", Unit.FH, 60.0).updated_at(T0 - td(days=5)).build()
    assert band.matches(aircraft_status(stale, p, ctx()))
    unreadable = rep(healthy, data_issues=(("MAINTENANCE_ACTION", "MA-9"),))
    assert band.matches(aircraft_status(unreadable, p, ctx()))
    expired = an_aircraft().remaining("I100H", Unit.FH, -3.0).build()
    assert BandFilter((("FH", 10.0, 50.0),)).matches(aircraft_status(expired, p, ctx()))
