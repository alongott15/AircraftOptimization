"""Loads one consistent view of the fleet for a request: snapshot + commitments + policy + clock."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from sqlalchemy.orm import Session

from app.datasources.base import FleetSnapshot
from app.domain.compliance.status import AircraftStatus, aircraft_status
from app.domain.models import Aircraft, EvaluationContext
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


def load_context(c: Container, session: Session, policy: PlanningPolicy | None = None) -> FleetContext:
    now = c.clock.now()
    snapshot = c.ds.load_snapshot()
    commitments = store.active_commitments(session)
    aircraft = tuple(replace(a, commitments=tuple(commitments.get(a.aircraft_id, ()))) for a in snapshot.aircraft)
    base = store.load_policy(session)
    eval_ctx = EvaluationContext(now=now, required_permit_types=snapshot.required_permit_types,
                                 data_load_status=dict(snapshot.data_load_status), commitment_policy=base)
    return FleetContext(now, policy or base, snapshot, aircraft, eval_ctx)
