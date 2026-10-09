"""FastAPI dependencies: container, DB session, current user, permission checks, CSRF."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.security.auth import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, SessionUser, decode_session_token
from app.security.rbac import Permission, has_permission
from app.services import admin
from app.services.container import Container
from app.services.errors import ServiceError

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_session(c: ContainerDep) -> Iterator[Session]:
    session = c.sf()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


def current_user(request: Request, c: ContainerDep, s: SessionDep) -> SessionUser:
    token = request.cookies.get(SESSION_COOKIE)
    claims = decode_session_token(token, c.settings.jwt_secret) if token else None
    if not claims:
        raise ServiceError(401, "UNAUTHENTICATED", "Not signed in")
    user = admin.active_user(s, int(claims["sub"]))
    if user is None:
        raise ServiceError(401, "UNAUTHENTICATED", "User inactive")
    if request.method not in SAFE_METHODS:
        header, cookie = request.headers.get(CSRF_HEADER), request.cookies.get(CSRF_COOKIE)
        if not header or not cookie or not secrets.compare_digest(header, cookie):
            raise ServiceError(403, "FORBIDDEN", "CSRF token missing or invalid")
    request.state.user_id = user.user_id
    return user


UserDep = Annotated[SessionUser, Depends(current_user)]


def require(permission: Permission) -> Callable[[SessionUser], SessionUser]:
    def _check(user: UserDep) -> SessionUser:
        if not has_permission(user.role, permission):
            raise ServiceError(403, "FORBIDDEN", "Role not allowed")
        return user

    return _check


Viewer = Annotated[SessionUser, Depends(require(Permission.VIEW))]
Planner = Annotated[SessionUser, Depends(require(Permission.PLAN))]
WhatIf = Annotated[SessionUser, Depends(require(Permission.WHAT_IF))]
Auditor = Annotated[SessionUser, Depends(require(Permission.AUDIT))]
Admin = Annotated[SessionUser, Depends(require(Permission.ADMIN))]
