"""Local auth (POC): argon2 password check, signed session JWT in an HttpOnly cookie, CSRF double-submit token.

Token expiry uses wall-clock time, independent of the business Clock (which may be frozen for demos).
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

SESSION_COOKIE = "ao_session"
CSRF_COOKIE = "ao_csrf"
CSRF_HEADER = "X-CSRF-Token"
ALGORITHM = "HS256"

_hasher = PasswordHasher()


@dataclass(frozen=True, slots=True)
class SessionUser:
    user_id: int
    username: str
    display_name: str
    role: str


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_session_token(user_id: int, role: str, secret: str, ttl_hours: float) -> str:
    now = int(time.time())
    claims = {"typ": "session", "sub": str(user_id), "role": role, "iat": now, "exp": now + int(ttl_hours * 3600),
              "jti": str(uuid.uuid4())}
    return jwt.encode(claims, secret, algorithm=ALGORITHM)


def decode_session_token(token: str, secret: str) -> dict[str, Any] | None:
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM],
                            options={"require": ["sub", "exp", "iat", "jti", "typ"]})
    except jwt.PyJWTError:
        return None
    if not isinstance(claims, dict) or claims.get("typ") != "session" or _REVOKED.is_revoked(str(claims["jti"])):
        return None
    return claims


class RevokedTokens:
    """jti denylist kept until the token would have expired anyway (logout). Single-process POC store."""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def revoke(self, jti: str, exp: float) -> None:
        with self._lock:
            now = time.time()
            self._until = {k: v for k, v in self._until.items() if v > now}
            self._until[jti] = exp

    def is_revoked(self, jti: str) -> bool:
        with self._lock:
            exp = self._until.get(jti)
            return exp is not None and exp > time.time()


_REVOKED = RevokedTokens()


def revoke_session_token(token: str, secret: str) -> None:
    claims = decode_session_token(token, secret)
    if claims:
        _REVOKED.revoke(str(claims["jti"]), float(claims["exp"]))


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
