"""RFC 9457 problem+json for every error (API.md §2). No stack traces ever leave the server."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.errors import ServiceError

log = structlog.get_logger()

_HTTP_CODES = {400: "BAD_REQUEST", 401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND",
               405: "BAD_REQUEST", 413: "BAD_REQUEST", 429: "RATE_LIMITED"}

_PYDANTIC_CODES = {
    "missing": "FIELD_REQUIRED",
    "greater_than_equal": "VALUE_TOO_SMALL", "greater_than": "VALUE_TOO_SMALL",
    "less_than_equal": "VALUE_TOO_LARGE", "less_than": "VALUE_TOO_LARGE",
    "string_too_short": "FIELD_REQUIRED", "string_too_long": "VALUE_TOO_LARGE",
    "too_short": "VALUE_TOO_SMALL", "too_long": "VALUE_TOO_LARGE",
}


def problem(request: Request, status: int, code: str, title: str, detail: str,
            errors: list[dict[str, Any]] | None = None, extra: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://aircraft-opt/errors/{code.lower()}", "title": title, "status": status, "code": code,
        "detail": detail, "errors": errors or [], "request_id": getattr(request.state, "request_id", None),
    }
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def _field_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    out = []
    for err in exc.errors():
        loc = [str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path")]
        ctx = err.get("ctx") or {}
        params: dict[str, Any] = {}
        for key in ("ge", "gt", "min_length"):
            if key in ctx:
                params["min"] = ctx[key]
        for key in ("le", "lt", "max_length"):
            if key in ctx:
                params["max"] = ctx[key]
        code = _PYDANTIC_CODES.get(err.get("type", ""), "INVALID_VALUE")
        msg = str(err.get("msg", ""))
        if err.get("type") == "value_error" and msg.startswith("Value error, "):
            code = msg.removeprefix("Value error, ")
        out.append({"field": ".".join(loc), "code": code, "params": {k: v for k, v in params.items()
                                                                     if isinstance(v, int | float | str)}})
    return out


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def _service(request: Request, exc: ServiceError) -> JSONResponse:
        return problem(request, exc.status, exc.code, exc.code.replace("_", " ").title(), exc.detail,
                       exc.errors, exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(request, 422, "VALIDATION_ERROR", "Validation failed", "Request is invalid",
                       _field_errors(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "BAD_REQUEST")
        return problem(request, exc.status_code, code, code.replace("_", " ").title(), str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("request.unhandled_error", error_type=type(exc).__name__)
        return problem(request, 500, "INTERNAL_ERROR", "Internal error", "Unexpected error")
