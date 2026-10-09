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
    ("PUT", "/api/v1/settings/hangar-capacity", {"admin"}),
    ("GET", "/api/v1/audit", {"maint", "admin"}),
]
PUBLIC = {"/api/v1/health", "/api/v1/ready", "/api/v1/auth/login", "/api/v1/auth/config",
          "/api/v1/auth/oidc/login", "/api/v1/auth/oidc/callback"}
BODIES = {"/api/v1/missions": M_A, "/api/v1/eligibility/check": {"mission": M_A},
          "/api/v1/settings/policy": {"landing_buffer": 1}, "/api/v1/settings/hangar-capacity": [],
          "/api/v1/missions/1": {"notes": "x"}, "/api/v1/planning/horizon": {},
          "/api/v1/planning/horizon/apply": {"assignments": []}}


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
