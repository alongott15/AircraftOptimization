"""Loads one consistent view of the fleet for a request: snapshot + commitments + policy + clock."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime

from sqlalchemy.orm import Session

from app.datasources.base import FleetSnapshot
from app.domain.compliance.status import AircraftStatus, aircraft_status
from app.domain.models import Aircraft, EvaluationContext, InspectionLimit
from app.domain.policy import PlanningPolicy
from app.repositories import store
from app.services.container import Container


@dataclass
class FleetContext:
    now: datetime
    policy: PlanningPolicy
    snapshot: FleetSnapshot
    aircraft: tuple[Aircraft, ...]
    eval_ctx: EvaluationContext
    _statuses: dict[int, AircraftStatus] = field(default_factory=dict)

    def status(self, ac: Aircraft) -> AircraftStatus:
        if ac.aircraft_id not in self._statuses:
            self._statuses[ac.aircraft_id] = aircraft_status(ac, self.policy, self.eval_ctx)
        return self._statuses[ac.aircraft_id]

    def by_tail(self) -> dict[str, Aircraft]:
        return {a.tail_number: a for a in self.aircraft}

    def by_id(self) -> dict[int, Aircraft]:
        return {a.aircraft_id: a for a in self.aircraft}


TG_OVERRIDE_MAX = 5.0


def _with_tg_overrides(a: Aircraft, overrides: dict[tuple[str, str, str], float]) -> Aircraft:
    """Apply admin touch-and-go weights to *planned* use only (FR-60, BR-03).

    The effective planning weight is max(lake weight, override): an override can make planning more conservative,
    never less (a lower weight would under-project against the due value the maintenance system computes). A stale
    override below a later-raised lake weight is therefore ignored. A non-finite/out-of-range stored value makes the
    limit DATA_MISSING (S1).
    """
    if not overrides:
        return a
    limits: list[InspectionLimit] = []
    issues = list(a.data_issues)
    for lim in a.inspection_limits:
        override = overrides.get((a.aircraft_type.type_code, lim.inspection_type_code, str(lim.unit)))
        if override is None:
            limits.append(lim)
        elif not (math.isfinite(override) and 0 <= override <= TG_OVERRIDE_MAX):
            issues.append(("INSPECTION_LIMIT", lim.inspection_type_code))
            limits.append(lim)
        else:
            limits.append(replace(lim, projection_tg_weight=max(lim.tg_weight, override)))
    return replace(a, inspection_limits=tuple(limits), data_issues=tuple(issues))


def load_context(c: Container, session: Session, policy: PlanningPolicy | None = None) -> FleetContext:
    now = c.clock.now()
    snapshot = c.ds.load_snapshot()
    commitments = store.active_commitments(session)
    overrides = store.tg_overrides(session)
    aircraft = tuple(
        _with_tg_overrides(replace(a, commitments=tuple(commitments.get(a.aircraft_id, ()))), overrides)
        for a in snapshot.aircraft)
    base = store.load_policy(session)
    eval_ctx = EvaluationContext(now=now, required_permit_types=snapshot.required_permit_types,
                                 data_load_status=dict(snapshot.data_load_status), commitment_policy=base)
    return FleetContext(now, policy or base, snapshot, aircraft, eval_ctx)
