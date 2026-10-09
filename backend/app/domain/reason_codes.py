"""Reason / warning / unblock codes — single source of truth is docs/API.md §5.

The frontend mirrors these in frontend/src/i18n/reasonCodes.ts; a contract test compares the sets.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

ParamValue = str | int | float | None


class ReasonCode(StrEnum):
    AIRCRAFT_NOT_OPERATIONAL = "AIRCRAFT_NOT_OPERATIONAL"
    ALREADY_ASSIGNED = "ALREADY_ASSIGNED"
    ENGINE_REQUIREMENT_NOT_MET = "ENGINE_REQUIREMENT_NOT_MET"
    ENDURANCE_EXCEEDED = "ENDURANCE_EXCEEDED"
    GROUNDING_MA_OPEN = "GROUNDING_MA_OPEN"
    INSPECTION_EXPIRED = "INSPECTION_EXPIRED"
    INSPECTION_DUE_BEFORE_MISSION_END = "INSPECTION_DUE_BEFORE_MISSION_END"
    PERMIT_EXPIRED = "PERMIT_EXPIRED"
    PERMIT_EXPIRES_BEFORE_MISSION_END = "PERMIT_EXPIRES_BEFORE_MISSION_END"
    DEFERRAL_EXPIRED = "DEFERRAL_EXPIRED"
    DEFERRAL_EXPIRES_BEFORE_MISSION_END = "DEFERRAL_EXPIRES_BEFORE_MISSION_END"
    QUALIFICATION_MISSING = "QUALIFICATION_MISSING"
    QUALIFICATION_EXPIRED = "QUALIFICATION_EXPIRED"
    QUALIFICATION_EXPIRES_BEFORE_MISSION_END = "QUALIFICATION_EXPIRES_BEFORE_MISSION_END"
    QUALIFICATION_REVOKED_BY_MA = "QUALIFICATION_REVOKED_BY_MA"
    QUALIFICATION_REVOKED_BY_PERMIT = "QUALIFICATION_REVOKED_BY_PERMIT"
    PERMIT_RESTRICTION_CONFLICT = "PERMIT_RESTRICTION_CONFLICT"
    IDLE_LIMIT_EXCEEDED = "IDLE_LIMIT_EXCEEDED"
    DATA_MISSING = "DATA_MISSING"
    DATA_STALE = "DATA_STALE"
    MANUALLY_EXCLUDED = "MANUALLY_EXCLUDED"
    JEOPARDIZES_LATER_ASSIGNMENT = "JEOPARDIZES_LATER_ASSIGNMENT"


class WarningCode(StrEnum):
    LOW_MARGIN_AFTER_MISSION = "LOW_MARGIN_AFTER_MISSION"
    EXPIRES_SOON_AFTER_MISSION = "EXPIRES_SOON_AFTER_MISSION"
    HIGH_DEFECT_RISK = "HIGH_DEFECT_RISK"
    TOLERANCE_AVAILABLE = "TOLERANCE_AVAILABLE"
    IDLE_CHECK_REQUIRED = "IDLE_CHECK_REQUIRED"
    ML_MODEL_UNAVAILABLE = "ML_MODEL_UNAVAILABLE"
    NON_GROUNDING_PERMIT_INVALID = "NON_GROUNDING_PERMIT_INVALID"
    EXPIRES_WITHIN_WINDOW = "EXPIRES_WITHIN_WINDOW"


class UnblockCode(StrEnum):
    UNBLOCK_MAX_DURATION = "UNBLOCK_MAX_DURATION"
    UNBLOCK_MAX_LANDINGS = "UNBLOCK_MAX_LANDINGS"
    UNBLOCK_LATEST_END = "UNBLOCK_LATEST_END"
    UNBLOCK_PERFORM_INSPECTION = "UNBLOCK_PERFORM_INSPECTION"
    UNBLOCK_RENEW_PERMIT = "UNBLOCK_RENEW_PERMIT"
    UNBLOCK_RENEW_QUALIFICATION = "UNBLOCK_RENEW_QUALIFICATION"
    UNBLOCK_CLOSE_MA = "UNBLOCK_CLOSE_MA"
    UNBLOCK_IDLE_CHECK = "UNBLOCK_IDLE_CHECK"


Code = ReasonCode | WarningCode | UnblockCode


@dataclass(frozen=True, slots=True)
class Reason:
    """A machine-readable explanation. The UI renders Hebrew from (code, params)."""

    code: Code
    params: Mapping[str, ParamValue] = field(default_factory=dict)

    def key(self) -> tuple[str, tuple[tuple[str, ParamValue], ...]]:
        return (str(self.code), tuple(sorted(self.params.items())))

    def to_dict(self) -> dict[str, object]:
        return {"code": str(self.code), "params": dict(self.params)}


def dedupe(reasons: list[Reason]) -> tuple[Reason, ...]:
    """Drop exact duplicates while keeping first-seen order (order = severity order of rules)."""
    seen: set[tuple[str, tuple[tuple[str, ParamValue], ...]]] = set()
    out: list[Reason] = []
    for reason in reasons:
        k = reason.key()
        if k not in seen:
            seen.add(k)
            out.append(reason)
    return tuple(out)
