"""`python -m app.manage` — local account management (SECURITY.md §3.1)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.clock import FrozenClock
from app.config import Settings
from app.main import create_app
from app.manage import main

NEW_PASSWORD = "cli-test-password-42"  # test-only value


def answers(*values: str) -> Iterator[str]:
    yield from values


def run(settings: Settings, argv: list[str], prompts: list[str], secrets: list[str]) -> int:
    plain, hidden = answers(*prompts), answers(*secrets)
    return main(argv, read=lambda _: next(plain), read_secret=lambda _: next(hidden), url=settings.database_url)


def test_create_admin_and_sign_in(settings: Settings, clock: FrozenClock) -> None:
    assert run(settings, ["create-user"], ["Dana.Admin", "דנה"], [NEW_PASSWORD, NEW_PASSWORD]) == 0
    app = create_app(settings, clock=clock)
    with TestClient(app) as c:
        r = c.post("/api/v1/auth/login", json={"username": "dana.admin", "password": NEW_PASSWORD})
        assert r.status_code == 200 and r.json()["role"] == "ADMIN" and r.json()["display_name"] == "דנה"
    with app.state.container.sf() as s:
        row = s.execute(text("SELECT details_json FROM audit_log WHERE action = 'USER_CREATE'")).scalar_one()
        assert "dana.admin" in row and NEW_PASSWORD not in row


@pytest.mark.parametrize(("argv", "prompts", "secrets", "why"), [
    (["create-user", "--username", "x2"], [""], [NEW_PASSWORD, NEW_PASSWORD], "username too short"),
    (["create-user", "--username", "dana2"], [""], ["short", "short"], "password too short"),
    (["create-user", "--username", "dana3"], [""], [NEW_PASSWORD, NEW_PASSWORD + "x"], "passwords differ"),
    (["create-user", "--username", "admin"], [""], [NEW_PASSWORD, NEW_PASSWORD], "username exists"),
    (["set-password", "--username", "nobody"], [], [NEW_PASSWORD, NEW_PASSWORD], "unknown user"),
])
def test_rejected_inputs(settings: Settings, argv: list[str], prompts: list[str], secrets: list[str],
                         why: str) -> None:
    assert run(settings, argv, prompts, secrets) == 1, why


def test_set_password(settings: Settings, clock: FrozenClock) -> None:
    assert run(settings, ["set-password", "--username", "planner"], [], [NEW_PASSWORD, NEW_PASSWORD]) == 0
    with TestClient(create_app(settings, clock=clock)) as c:
        assert c.post("/api/v1/auth/login", json={"username": "planner", "password": NEW_PASSWORD}).status_code == 200
