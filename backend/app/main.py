"""FastAPI app factory: middleware (request id, security headers, size limit), errors, routes."""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from structlog.typing import EventDict, WrappedLogger

from app.api.errors import install_error_handlers, problem
from app.api.v1 import router
from app.clock import Clock
from app.config import Settings, get_settings
from app.services.container import Container, build_container

MAX_BODY_BYTES = 1_000_000
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_SECRET_KEY = re.compile(r"password|secret|token|dsn|cookie|authorization", re.IGNORECASE)


def _redact(_logger: WrappedLogger, _method: str, event: EventDict) -> EventDict:
    """SECURITY.md §4 — never log secrets, even by accident."""
    return {k: ("[REDACTED]" if _SECRET_KEY.search(k) else v) for k, v in event.items()}


SECURITY_HEADERS = {
    "Content-Security-Policy": ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self'; connect-src 'self'; worker-src 'self' blob:; "
                                "frame-ancestors 'none'"),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
}


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO), format="%(message)s")
    structlog.configure(processors=[
        structlog.processors.add_log_level,
        _redact,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.JSONRenderer(ensure_ascii=False),
    ])


def create_app(settings: Settings | None = None, clock: Clock | None = None,
               container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_prod()
    _configure_logging(settings.log_level)
    log = structlog.get_logger()
    app = FastAPI(title="Fleet Usability Optimizer API", version="0.1.0", openapi_url="/api/v1/openapi.json",
                  docs_url="/api/v1/docs" if settings.env != "prod" else None, redoc_url=None)
    app.state.container = container or build_container(settings, clock)
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                       allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"])

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _UUID.fullmatch(supplied) else str(uuid.uuid4())
        request.state.request_id = request_id
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return problem(request, 413, "BAD_REQUEST", "Payload too large", "Request body exceeds 1 MB")
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if settings.cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        log.info("http.request", request_id=request_id, method=request.method, route=request.url.path,
                 status=response.status_code, duration_ms=round((time.perf_counter() - started) * 1000, 1),
                 user_id=getattr(request.state, "user_id", None))
        return response

    app.include_router(router)
    return app


app = create_app()
