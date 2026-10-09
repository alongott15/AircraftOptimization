"""OIDC SSO flow against a mocked identity provider (SECURITY.md §3.1)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.clock import FrozenClock
from app.config import Settings
from app.db import orm
from app.main import create_app
from app.security.auth import decode_session_token
from app.security.oidc import OidcProvider, parse_role_map
from app.services.container import build_container

ISSUER = "https://idp.test/realms/ao"
CLIENT_ID = "aircraft-opt"


class FakeIdp:
    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.nonce = ""
        self.groups: list[str] = ["AO-Planners"]
        self.audience: str | list[str] = CLIENT_ID
        self.azp: str | None = None
        self.sub = "u-123"
        self.username = "dana"
        self.tamper = False

    def jwks(self) -> dict[str, Any]:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": "k1", "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={
                "issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/auth", "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/certs"})
        if path.endswith("/certs"):
            return httpx.Response(200, json=self.jwks())
        if path.endswith("/token"):
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"] and form["code_verifier"][0]
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": self.audience, "sub": self.sub, "iat": now, "exp": now + 300,
                      "nonce": self.nonce, "preferred_username": self.username, "name": "Dana", "groups": self.groups}
            if self.azp:
                claims["azp"] = self.azp
            token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})
            if self.tamper:
                token = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
            return httpx.Response(200, json={"id_token": token, "access_token": "x"})
        return httpx.Response(404)


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp()


@pytest.fixture
def sso_client(settings: Settings, clock: FrozenClock, idp: FakeIdp) -> Iterator[TestClient]:
    s = settings.model_copy(update={
        "auth_mode": "oidc", "oidc_issuer": ISSUER, "oidc_client_id": CLIENT_ID, "oidc_client_secret": "test-secret",
        "oidc_redirect_uri": "http://testserver/api/v1/auth/oidc/callback",
        "oidc_role_map": "AO-Admins=ADMIN,AO-Planners=PLANNER,AO-Viewers=VIEWER"})
    container = build_container(s, clock)
    container.oidc = OidcProvider(s, http=httpx.Client(transport=httpx.MockTransport(idp.handler)))
    with TestClient(create_app(s, container=container)) as c:
        yield c


def _login(c: TestClient, idp: FakeIdp, state_override: str | None = None) -> httpx.Response:
    start = c.get("/api/v1/auth/oidc/login", follow_redirects=False)
    assert start.status_code == 302
    params = parse_qs(urlparse(start.headers["location"]).query)
    assert params["code_challenge_method"] == ["S256"] and params["client_id"] == [CLIENT_ID]
    idp.nonce = params["nonce"][0]
    state = state_override or params["state"][0]
    return c.get(f"/api/v1/auth/oidc/callback?code=abc&state={state}", follow_redirects=False)


def test_config_reports_mode(sso_client: TestClient) -> None:
    assert sso_client.get("/api/v1/auth/config").json() == {"mode": "oidc"}


def test_sso_login_maps_group_to_role(sso_client: TestClient, idp: FakeIdp) -> None:
    done = _login(sso_client, idp)
    assert done.status_code == 302 and done.headers["location"] == "/"
    me = sso_client.get("/api/v1/auth/me").json()
    assert me["username"] == "dana" and me["role"] == "PLANNER"


def test_local_login_disabled_in_sso_mode(sso_client: TestClient) -> None:
    r = sso_client.post("/api/v1/auth/login", json={"username": "admin", "password": "x"})
    assert r.status_code == 404


@pytest.mark.parametrize(("setup", "error"), [
    ("no_group", "FORBIDDEN"),
    ("bad_state", "UNAUTHENTICATED"),
    ("bad_audience", "UNAUTHENTICATED"),
    ("tampered", "UNAUTHENTICATED"),
])
def test_sso_rejections(sso_client: TestClient, idp: FakeIdp, setup: str, error: str) -> None:
    if setup == "no_group":
        idp.groups = ["Someone-Else"]
    if setup == "bad_audience":
        idp.audience = "another-app"
    if setup == "tampered":
        idp.tamper = True
    done = _login(sso_client, idp, state_override="forged" if setup == "bad_state" else None)
    assert done.status_code == 302 and done.headers["location"] == f"/?sso_error={error}"
    assert sso_client.get("/api/v1/auth/me").status_code == 401


def test_role_map_parsing() -> None:
    assert parse_role_map("A=ADMIN, B = PLANNER,bad,C=ROOT") == {"A": "ADMIN", "B": "PLANNER"}


# ── identity linking (security review 2026-10-09) ────────────────────────
def test_idp_user_named_like_a_local_account_cannot_take_it_over(sso_client: TestClient, idp: FakeIdp) -> None:
    idp.username, idp.groups = "admin", ["AO-Viewers"]
    done = _login(sso_client, idp)
    assert done.headers["location"] == "/?sso_error=FORBIDDEN"
    c: Any = sso_client.app.state.container  # type: ignore[attr-defined]
    with c.sf() as s:
        local = s.scalars(select(orm.AppUserRow).where(orm.AppUserRow.username == "admin")).one()
        assert local.role == "ADMIN" and local.oidc_subject is None


def test_sso_user_is_keyed_by_subject_not_username(sso_client: TestClient, idp: FakeIdp) -> None:
    _login(sso_client, idp)
    first = sso_client.get("/api/v1/auth/me").json()
    idp.username, idp.groups = "dana.renamed", ["AO-Admins"]
    _login(sso_client, idp)
    again = sso_client.get("/api/v1/auth/me").json()
    assert again["user_id"] == first["user_id"] and again["role"] == "ADMIN"


def test_disabled_sso_user_is_rejected_before_role_refresh(sso_client: TestClient, idp: FakeIdp) -> None:
    _login(sso_client, idp)
    c: Any = sso_client.app.state.container  # type: ignore[attr-defined]
    with c.sf() as s:
        row = s.scalars(select(orm.AppUserRow).where(orm.AppUserRow.username == "dana")).one()
        row.is_active = False
        s.commit()
    idp.groups = ["AO-Admins"]
    assert _login(sso_client, idp).headers["location"] == "/?sso_error=FORBIDDEN"
    with c.sf() as s:
        assert s.scalars(select(orm.AppUserRow).where(orm.AppUserRow.username == "dana")).one().role == "PLANNER"


@pytest.mark.parametrize(("setup", "error"), [("long_username", "FORBIDDEN"), ("azp_mismatch", "UNAUTHENTICATED")])
def test_sso_claim_checks(sso_client: TestClient, idp: FakeIdp, setup: str, error: str) -> None:
    if setup == "long_username":
        idp.username = "x" * 51
    else:
        idp.audience, idp.azp = [CLIENT_ID, "other-app"], "other-app"
    assert _login(sso_client, idp).headers["location"] == f"/?sso_error={error}"


def test_state_cookie_is_not_a_session(settings: Settings) -> None:
    state = jwt.encode({"typ": "oidc_state", "sub": "1", "iat": int(time.time()), "exp": int(time.time()) + 60,
                        "jti": "x"}, settings.jwt_secret, algorithm="HS256")
    assert decode_session_token(state, settings.jwt_secret) is None
