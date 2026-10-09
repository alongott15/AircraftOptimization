"""Service-level errors, mapped to RFC 9457 problem+json by the API layer."""

from __future__ import annotations

from typing import Any


class ServiceError(Exception):
    def __init__(self, status: int, code: str, detail: str, *, errors: list[dict[str, Any]] | None = None,
                 extra: dict[str, Any] | None = None) -> None:
        super().__init__(detail)
        self.status, self.code, self.detail = status, code, detail
        self.errors = errors or []
        self.extra = extra or {}


def not_found(what: str) -> ServiceError:
    return ServiceError(404, "NOT_FOUND", f"{what} not found")


def validation(field: str, code: str, **params: Any) -> ServiceError:
    return ServiceError(422, "VALIDATION_ERROR", "Request is invalid",
                        errors=[{"field": field, "code": code, "params": params}])
