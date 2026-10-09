"""OIDC single sign-on: authorization code + PKCE (SECURITY.md §3.1).

State, nonce and the PKCE verifier travel in a short-lived signed cookie (`ao_oidc`, SameSite=Lax so it survives the
IdP redirect). The ID token is verified against the IdP JWKS (signature, issuer, audience, expiry, nonce); IdP groups
are mapped to app roles via APP_OIDC_ROLE_MAP. No group mapping ⇒ access denied.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.config import Settings

STATE_COOKIE = "ao_oidc"
STATE_TTL_S = 600
ROLE_PRIORITY = ("ADMIN", "PLANNER", "MAINTENANCE_CONTROLLER", "VIEWER")


class OidcError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code


@dataclass(frozen=True, slots=True)
class OidcIdentity:
    subject: str
    username: str
    display_name: str
    role: str


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def parse_role_map(spec: str) -> dict[str, str]:
    """'AO-Admins=ADMIN,AO-Planners=PLANNER' → {group: role}."""
    out: dict[str, str] = {}
    for pair in spec.split(","):
        if "=" in pair:
            group, role = (x.strip() for x in pair.split("=", 1))
            if role in ROLE_PRIORITY and group:
                out[group] = role
    return out


class OidcProvider:
    def __init__(self, settings: Settings, http: httpx.Client | None = None) -> None:
        if not (settings.oidc_issuer and settings.oidc_client_id and settings.oidc_redirect_uri):
            raise RuntimeError("APP_OIDC_ISSUER, APP_OIDC_CLIENT_ID and APP_OIDC_REDIRECT_URI are required")
        if settings.env == "prod" and not settings.oidc_issuer.startswith("https://"):
            raise RuntimeError("APP_OIDC_ISSUER must use https in production")
        self._s = settings
        self._http = http or httpx.Client(timeout=10.0)
        self._lock = threading.RLock()  # _keyset() calls discovery() while holding the lock
        self._discovery: dict[str, Any] | None = None
        self._jwks: jwt.PyJWKSet | None = None
        self._role_map = parse_role_map(settings.oidc_role_map)

    # ── metadata ────────────────────────────────────────────────────────
    def discovery(self) -> dict[str, Any]:
        with self._lock:
            if self._discovery is None:
                url = self._s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"
                resp = self._http.get(url)
                resp.raise_for_status()
                doc = resp.json()
                if doc.get("issuer", "").rstrip("/") != self._s.oidc_issuer.rstrip("/"):
                    raise OidcError("FORBIDDEN", "issuer mismatch in discovery document")
                if self._s.env == "prod":
                    endpoints = [doc.get(k, "") for k in ("authorization_endpoint", "token_endpoint", "jwks_uri")]
                    if not all(str(e).startswith("https://") for e in endpoints):
                        raise OidcError("FORBIDDEN", "IdP endpoints must use https in production")
                self._discovery = doc
            return self._discovery

    def _keyset(self, refresh: bool = False) -> jwt.PyJWKSet:
        with self._lock:
            if self._jwks is None or refresh:
                resp = self._http.get(self.discovery()["jwks_uri"])
                resp.raise_for_status()
                self._jwks = jwt.PyJWKSet.from_dict(resp.json())
            return self._jwks

    # ── flow ────────────────────────────────────────────────────────────
    def start(self) -> tuple[str, str]:
        """Returns (IdP authorization URL, signed state-cookie value)."""
        state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        params = {
            "response_type": "code", "client_id": self._s.oidc_client_id, "redirect_uri": self._s.oidc_redirect_uri,
            "scope": self._s.oidc_scopes, "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        payload = {"typ": "oidc_state", "state": state, "nonce": nonce, "verifier": verifier,
                   "exp": int(time.time()) + STATE_TTL_S}
        cookie = jwt.encode(payload, self._s.jwt_secret, algorithm="HS256")
        return f"{self.discovery()['authorization_endpoint']}?{urlencode(params)}", cookie

    def finish(self, code: str, state: str, cookie: str | None) -> OidcIdentity:
        if not cookie:
            raise OidcError("UNAUTHENTICATED", "missing SSO state")
        try:
            saved = jwt.decode(cookie, self._s.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "typ"]})
        except jwt.PyJWTError as exc:
            raise OidcError("UNAUTHENTICATED", "SSO state expired") from exc
        if saved.get("typ") != "oidc_state" or not saved.get("state") or not saved.get("verifier"):
            raise OidcError("UNAUTHENTICATED", "invalid SSO state")
        if not secrets.compare_digest(str(saved["state"]), state):
            raise OidcError("UNAUTHENTICATED", "SSO state mismatch")
        resp = self._http.post(self.discovery()["token_endpoint"], data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": self._s.oidc_redirect_uri,
            "client_id": self._s.oidc_client_id, "client_secret": self._s.oidc_client_secret or "",
            "code_verifier": saved["verifier"],
        })
        if resp.status_code != 200:
            raise OidcError("UNAUTHENTICATED", "token exchange failed")
        id_token = resp.json().get("id_token")
        if not id_token:
            raise OidcError("UNAUTHENTICATED", "no id_token")
        claims = self._verify(id_token)
        if not secrets.compare_digest(str(claims.get("nonce", "")), str(saved["nonce"])):
            raise OidcError("UNAUTHENTICATED", "nonce mismatch")
        role = self.map_role(claims)
        if role is None:
            raise OidcError("FORBIDDEN", "no application role for this user")
        username = str(claims.get(self._s.oidc_username_claim) or claims["sub"])
        if len(username) > 50:
            raise OidcError("FORBIDDEN", "username too long")
        return OidcIdentity(subject=f"{claims['iss']}|{claims['sub']}", username=username,
                            display_name=str(claims.get("name") or username)[:100], role=role)

    def _verify(self, token: str) -> dict[str, Any]:
        try:
            kid = str(jwt.get_unverified_header(token).get("kid") or "")
        except jwt.PyJWTError as exc:
            raise OidcError("UNAUTHENTICATED", "malformed id_token") from exc
        keyset = self._keyset()
        if kid not in {k.key_id for k in keyset.keys}:
            keyset = self._keyset(refresh=True)  # key rotation
        try:
            key = keyset[kid]
            claims: dict[str, Any] = jwt.decode(
                token, key.key, algorithms=["RS256", "ES256"], audience=self._s.oidc_client_id,
                issuer=self.discovery()["issuer"], options={"require": ["exp", "iat", "sub", "aud", "iss"]})
        except (jwt.PyJWTError, KeyError) as exc:
            raise OidcError("UNAUTHENTICATED", "invalid id_token") from exc
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != self._s.oidc_client_id:
            raise OidcError("UNAUTHENTICATED", "azp mismatch")
        return claims

    def map_role(self, claims: dict[str, Any]) -> str | None:
        raw = claims.get(self._s.oidc_role_claim, [])
        groups = {raw} if isinstance(raw, str) else set(raw or [])
        roles = {self._role_map[g] for g in groups if g in self._role_map}
        return next((r for r in ROLE_PRIORITY if r in roles), None)
