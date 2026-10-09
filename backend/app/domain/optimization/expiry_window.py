"""Planner expiration "avoid band" per activity unit (OPTIMIZATION_ENGINE.md §3.4) — ranking only (BR-14).

A mission may list several bands, at most one per activity unit. Each band is "תוך X ועד Y" in that unit: an
eligible aircraft *hits* the band when any of its items in the unit would expire between X and Y after the mission
(remaining after the mission for usage units; days from mission end to expiry for CAL_DAY). Hits lower the score
(term `expiry_window`) and add a warning; they never change eligibility.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.domain.compliance.evaluator import EligibilityResult, LimitProjection
from app.domain.models import ExpiryWindow, Unit
from app.domain.numeric import r2
from app.domain.reason_codes import Reason, WarningCode

DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class WindowResult:
    total: int
    hit: int
    warnings: tuple[Reason, ...]

    @property
    def value(self) -> float:
        """Score term value: share of bands the aircraft stays out of (1.0 when there are no bands)."""
        return 1.0 if self.total == 0 else 1.0 - self.hit / self.total


NO_WINDOWS = WindowResult(0, 0, ())


def _margin(p: LimitProjection, unit: Unit, mission_end: datetime) -> float | None:
    """What remains of this item after the mission, in the band's unit (days for calendar items)."""
    if unit == Unit.CAL_DAY:
        if not p.due_at:
            return None
        due = datetime.strptime(p.due_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        return (due - mission_end) / DAY
    return p.remaining_after


def evaluate_windows(result: EligibilityResult, windows: Sequence[ExpiryWindow],
                     mission_end: datetime) -> WindowResult:
    """Which of the planner's bands this (eligible) aircraft falls into, with one warning per item."""
    warnings: list[Reason] = []
    hit = 0
    for w in windows:
        found = []
        for p in result.projections:
            if p.unit != str(w.unit) or p.source == "IDLE" or p.outcome != "OK":
                continue
            margin = _margin(p, w.unit, mission_end)
            if margin is not None and w.within <= margin <= w.until:
                found.append(Reason(WarningCode.EXPIRES_WITHIN_WINDOW, {
                    "source": p.source, "ref": p.ref, "unit": p.unit, "margin": r2(margin),
                    "within": w.within, "until": w.until}))
        if found:
            hit += 1
            warnings.extend(found)
    return WindowResult(total=len(windows), hit=hit, warnings=tuple(warnings))
