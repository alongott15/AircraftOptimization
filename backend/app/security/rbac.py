"""Role → permission matrix (SECURITY.md §3.3). Enforced server-side on every route."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum


class Permission(StrEnum):
    VIEW = "VIEW"  # dashboard, fleet, aircraft, missions, runs, reference
    PLAN = "PLAN"  # create/edit/optimize/assign/cancel missions
    WHAT_IF = "WHAT_IF"  # stateless eligibility check
    AUDIT = "AUDIT"  # view audit log
    ADMIN = "ADMIN"  # policy, weights, hangar capacity, retrain models


ROLES = ("VIEWER", "PLANNER", "MAINTENANCE_CONTROLLER", "ADMIN")

ROLE_PERMISSIONS: Mapping[str, frozenset[Permission]] = {
    "VIEWER": frozenset({Permission.VIEW}),
    "PLANNER": frozenset({Permission.VIEW, Permission.PLAN, Permission.WHAT_IF}),
    "MAINTENANCE_CONTROLLER": frozenset({Permission.VIEW, Permission.WHAT_IF, Permission.AUDIT}),
    "ADMIN": frozenset(Permission),
}


def has_permission(role: str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
