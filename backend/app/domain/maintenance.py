"""Maintenance planning (FR-90…FR-92, OPTIMIZATION_ENGINE.md §2.9).

* `aircraft_requirements` — the maintenance work an aircraft needs, derived from its fleet compliance status: perform an
  inspection, renew a permit, close a maintenance action, renew a qualification, idle check, return to service, or
  complete missing source data. One task per (aircraft, action, ref); a new due date is a new cycle (`fingerprint`).
* `recommend` — for a mission short of aircraft, which tasks would make which excluded aircraft eligible. Each
  candidate is *verified*: the tasks are applied to a copy of the aircraft (`complete_tasks`) and the unchanged
  compliance engine re-evaluates it. Only aircraft the engine then finds eligible are recommended.

Planning only: nothing here changes eligibility. A task is done when the source data says so (S1, golden rule 7);
missing data is never simulated away (FIX_DATA cannot be verified).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.domain.compliance.evaluator import EligibilityResult, evaluate_eligibility
from app.domain.compliance.status import AircraftStatus, ItemStatus
from app.domain.models import (
    COUNTER_UNITS,
    STATUS_SEVERITY,
    TG_WEIGHTED_UNITS,
    Aircraft,
    ComplianceStatus,
    EvaluationContext,
    InspectionDue,
    MissionRequest,
    OperationalStatus,
    Permit,
    Severity,
    Unit,
)
from app.domain.policy import PlanningPolicy
from app.domain.reason_codes import Reason, ReasonCode

# What-if assumptions for verifying recommendations only (never eligibility) — OPTIMIZATION_ENGINE.md §2.9, PRD §12:
# an idle check is closed by a recorded check flight using this much of each counter (engine starts: one per engine).
CHECK_FLIGHT_USE: dict[Unit, float] = {Unit.FH: 1.0, Unit.LDG: 1.0, Unit.CYC: 1.0}


class TaskAction(StrEnum):
    PERFORM_INSPECTION = "PERFORM_INSPECTION"
    RENEW_PERMIT = "RENEW_PERMIT"
    CLOSE_MA = "CLOSE_MA"
    RENEW_QUALIFICATION = "RENEW_QUALIFICATION"
    IDLE_CHECK = "IDLE_CHECK"
    RETURN_TO_SERVICE = "RETURN_TO_SERVICE"
    FIX_DATA = "FIX_DATA"


Task = tuple[TaskAction, str]  # (action, ref)


def task_key(aircraft_id: int, action: TaskAction | str, ref: str) -> str:
    return f"{aircraft_id}:{action}:{ref}"


@dataclass(frozen=True, slots=True)
class Requirement:
    key: str
    aircraft_id: int
    action: TaskAction
    ref: str  # inspection code | permit type | MA number | qualification code | IDLE_CHECK | status | KIND:CODE
    status: ComplianceStatus  # EXPIRED | EXPIRING_SOON | UNKNOWN (an open grounding MA / not operational = EXPIRED)
    blocking: bool  # the aircraft cannot fly until this is done
    due_at: datetime | None
    remaining: float | None
    unit: str | None
    zone_code: str | None
    fingerprint: str  # identifies the due cycle; a plan made for an earlier cycle does not carry over


_ITEM_ACTION = {
    "INSPECTION": TaskAction.PERFORM_INSPECTION,
    "PERMIT": TaskAction.RENEW_PERMIT,
    "DEFERRAL": TaskAction.CLOSE_MA,
    "QUALIFICATION": TaskAction.RENEW_QUALIFICATION,
    "IDLE": TaskAction.IDLE_CHECK,
}


def _iso(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else "-"


def _normalized(remaining: float | None, interval: float | None) -> float:
    if remaining is None:
        return float("-inf")
    return remaining / interval if interval else remaining / 365.0


def _urgency(r: Requirement) -> tuple[bool, int, float, str]:
    return (not r.blocking, -STATUS_SEVERITY[r.status], r.due_at.timestamp() if r.due_at else float("inf"), r.key)


def aircraft_requirements(ac: Aircraft, st: AircraftStatus) -> tuple[Requirement, ...]:
    """Every task this aircraft needs now or soon (EXPIRED, EXPIRING_SOON or UNKNOWN items), most urgent first."""
    found: dict[str, Requirement] = {}
    ranks: dict[str, float] = {}

    def add(req: Requirement, rank: float = 0.0) -> None:
        old = found.get(req.key)
        if old is None:
            found[req.key], ranks[req.key] = req, rank
            return
        worse = (STATUS_SEVERITY[req.status], -rank) > (STATUS_SEVERITY[old.status], -ranks[req.key])
        found[req.key] = replace(req if worse else old, blocking=old.blocking or req.blocking)
        ranks[req.key] = rank if worse else ranks[req.key]

    if ac.status != OperationalStatus.OPERATIONAL:
        ref = str(ac.status)
        add(Requirement(task_key(ac.aircraft_id, TaskAction.RETURN_TO_SERVICE, ref), ac.aircraft_id,
                        TaskAction.RETURN_TO_SERVICE, ref, ComplianceStatus.EXPIRED, True, None, None, None, None,
                        task_cycle(ac, st, TaskAction.RETURN_TO_SERVICE, ref)))
    for ma in ac.open_mas:
        if ma.severity == Severity.GROUNDING:
            add(Requirement(task_key(ac.aircraft_id, TaskAction.CLOSE_MA, ma.ma_number), ac.aircraft_id,
                            TaskAction.CLOSE_MA, ma.ma_number, ComplianceStatus.EXPIRED, True, None, None, None,
                            ma.zone_code, task_cycle(ac, st, TaskAction.CLOSE_MA, ma.ma_number)))
    for item in st.items:
        if item.status == ComplianceStatus.OK:
            continue
        if item.status == ComplianceStatus.UNKNOWN:
            # missing/unreadable source data: only the data owner can fix it — never assumed fine (S1)
            ref = f"{item.missing_kind or item.source}:{item.missing_code or item.ref}"
            add(Requirement(task_key(ac.aircraft_id, TaskAction.FIX_DATA, ref), ac.aircraft_id, TaskAction.FIX_DATA,
                            ref, ComplianceStatus.UNKNOWN, item.blocking, None, None, None, item.zone_code,
                            task_cycle(ac, st, TaskAction.FIX_DATA, ref)))
            continue
        action = _ITEM_ACTION.get(item.source)
        if action is None:
            continue
        ref = "IDLE_CHECK" if action == TaskAction.IDLE_CHECK else item.ref
        blocking = (item.blocking and item.status == ComplianceStatus.EXPIRED
                    and action != TaskAction.RENEW_QUALIFICATION)  # a qualification only matters to some missions
        add(Requirement(task_key(ac.aircraft_id, action, ref), ac.aircraft_id, action, ref, item.status, blocking,
                        item.due_at, item.remaining, item.unit or None, item.zone_code,
                        task_cycle(ac, st, action, ref)),
            _normalized(item.remaining, item.interval))
    # unreadable rows and stale feeds make the aircraft UNKNOWN even without an item — they need the data owner too
    gaps = [f"{kind}:{code}" for kind, code in st.data_issues] + (["STALE:DATA"] if st.stale else [])
    for ref in gaps:
        add(Requirement(task_key(ac.aircraft_id, TaskAction.FIX_DATA, ref), ac.aircraft_id, TaskAction.FIX_DATA, ref,
                        ComplianceStatus.UNKNOWN, True, None, None, None, None,
                        task_cycle(ac, st, TaskAction.FIX_DATA, ref)))
    return tuple(sorted(found.values(), key=_urgency))


_ACTION_SOURCE = {TaskAction.PERFORM_INSPECTION: "INSPECTION", TaskAction.RENEW_PERMIT: "PERMIT",
                  TaskAction.RENEW_QUALIFICATION: "QUALIFICATION", TaskAction.CLOSE_MA: "DEFERRAL",
                  TaskAction.IDLE_CHECK: "IDLE"}


def _all_task_items(st: AircraftStatus, action: TaskAction, ref: str) -> list[ItemStatus]:
    source = _ACTION_SOURCE.get(action)
    return [i for i in st.items if i.source == source and (action == TaskAction.IDLE_CHECK or i.ref == ref)]


def _task_items(st: AircraftStatus, action: TaskAction, ref: str) -> list[ItemStatus]:
    return [i for i in _all_task_items(st, action, ref) if i.status != ComplianceStatus.UNKNOWN]


def _point(value: str) -> float | None:
    """A due point from a cycle fingerprint: epoch seconds for a date, else a counter value (None if unreadable)."""
    try:
        if "T" in value:
            return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp()
        point = float(value)
    except ValueError:
        return None
    return point if math.isfinite(point) else None  # NaN / inf are unreadable


def _item_point(i: ItemStatus) -> float | None:
    return i.due_at.timestamp() if i.due_at else (None if i.due_value is None else _point(str(i.due_value)))


def _dues_moved_on(ac: Aircraft, st: AircraftStatus, action: TaskAction, ref: str, fingerprint: str) -> bool:
    """Every limit of the planned cycle is present, readable and now due strictly later (work recorded, S1)."""
    if action == TaskAction.IDLE_CHECK:
        last = _point(fingerprint)
        return (last is not None and ac.last_flight_at is not None and ac.last_flight_at.timestamp() > last
                and all(i.status != ComplianceStatus.UNKNOWN for i in _all_task_items(st, action, ref)))
    items = _all_task_items(st, action, ref)
    if not items or any(i.status == ComplianceStatus.UNKNOWN for i in items):
        return False
    old: dict[str, list[float | None]] = {}
    for part in fingerprint.split("|"):
        unit, _, value = part.partition(":")
        old.setdefault(unit, []).append(_point(value))
    new: dict[str, list[float | None]] = {}
    for i in items:
        new.setdefault(i.unit, []).append(_item_point(i))
    for unit, before in old.items():
        after = new.get(unit)
        if not after or None in before or None in after:
            return False
        if not min(p for p in after if p is not None) > max(p for p in before if p is not None):
            return False
    return bool(old)


def task_cycle(ac: Aircraft, st: AircraftStatus, action: TaskAction, ref: str) -> str:
    """Identifies the occurrence (due cycle) of a task — it changes when the work is recorded in the source data.

    A plan belongs to one cycle: an MA occurrence is its number + opening time; a grounding or a data gap is tied to
    the last flight, so a new one after the aircraft flew again starts unplanned (never inherits an old "done").
    """
    if action == TaskAction.IDLE_CHECK:
        return _iso(ac.last_flight_at)
    if action == TaskAction.CLOSE_MA:
        opened = [m.opened_at for m in ac.open_mas if m.ma_number == ref]
        return f"{ref}@{_iso(min(opened))}" if opened else ref
    if action in (TaskAction.RETURN_TO_SERVICE, TaskAction.FIX_DATA):
        return f"{ref}@{_iso(ac.last_flight_at)}"
    parts = sorted(f"{i.unit}:{_iso(i.due_at) if i.due_at else i.due_value}" for i in _task_items(st, action, ref))
    return "|".join(parts) or ref


def task_done(ac: Aircraft, st: AircraftStatus, action: TaskAction, ref: str, fingerprint: str) -> bool:
    """True once the source data shows the task of cycle `fingerprint` done (never because a planner marked it done).

    Fail-safe (S1): data that disappears, becomes unreadable or moves *earlier* is never "done".
    """
    if action == TaskAction.CLOSE_MA:  # that occurrence is closed in readable data (a re-opened MA is a new one)
        unreadable = ("MAINTENANCE_ACTION", ref) in ac.data_issues or any(
            i.status == ComplianceStatus.UNKNOWN for i in _all_task_items(st, action, ref))
        return not unreadable and task_cycle(ac, st, action, ref) != fingerprint
    if action == TaskAction.RETURN_TO_SERVICE:  # operational again, or it flew since (so it was released)
        if any(kind == "AIRCRAFT_STATUS" for kind, _ in ac.data_issues):
            return False
        flown = _point(fingerprint.partition("@")[2])
        return ac.status == OperationalStatus.OPERATIONAL or (
            flown is not None and ac.last_flight_at is not None and ac.last_flight_at.timestamp() > flown)
    if action == TaskAction.FIX_DATA:  # only the data itself counts — flying with the gap open does not close it
        return not any(r.action == TaskAction.FIX_DATA and r.ref == ref for r in aircraft_requirements(ac, st))
    return _dues_moved_on(ac, st, action, ref, fingerprint)


def _task_status(ac: Aircraft, st: AircraftStatus, action: TaskAction, ref: str) -> ComplianceStatus:
    """Current status of a task's items — UNKNOWN when they are missing (never a default OK, S1)."""
    if action == TaskAction.RETURN_TO_SERVICE:
        return ComplianceStatus.OK if ac.status == OperationalStatus.OPERATIONAL else ComplianceStatus.EXPIRED
    if action == TaskAction.FIX_DATA:
        return ComplianceStatus.UNKNOWN if not task_done(ac, st, action, ref, "") else ComplianceStatus.OK
    items = _all_task_items(st, action, ref)
    if action == TaskAction.CLOSE_MA:
        if ("MAINTENANCE_ACTION", ref) in ac.data_issues:
            return ComplianceStatus.UNKNOWN
        if any(m.ma_number == ref and m.severity == Severity.GROUNDING for m in ac.open_mas):
            return ComplianceStatus.EXPIRED
        if not items:  # an open item without a deferral limit (e.g. it revokes a qualification): nothing is due
            return ComplianceStatus.OK
    if not items:
        return ComplianceStatus.UNKNOWN
    return max((i.status for i in items), key=lambda s: STATUS_SEVERITY[s])


def requirement_for(ac: Aircraft, st: AircraftStatus, action: TaskAction, ref: str) -> Requirement:
    """The task as a board item even when not yet due by fleet thresholds (needed for a mission, or planned)."""
    key = task_key(ac.aircraft_id, action, ref)
    for r in aircraft_requirements(ac, st):
        if r.key == key:
            return r
    items = sorted(_task_items(st, action, ref), key=lambda i: _normalized(i.remaining, i.interval))
    first = items[0] if items else None
    return Requirement(key, ac.aircraft_id, action, ref, _task_status(ac, st, action, ref), False,
                       first.due_at if first else None, first.remaining if first else None,
                       (first.unit or None) if first else None, first.zone_code if first else None,
                       task_cycle(ac, st, action, ref))


# ── what-if: the aircraft as it would be once the tasks are done ─────────────────────────────────────────────────
def _counter_value(ac: Aircraft, unit: Unit, tg_weight: float) -> float | None:
    base = ac.counter(unit)
    if base is None:
        return None
    value = base.value
    if unit in TG_WEIGHTED_UNITS and tg_weight > 0:
        tg = ac.counter(Unit.TG)
        if tg is None:
            return None
        value += tg_weight * tg.value
    return value


def _perform_inspection(ac: Aircraft, code: str, at: datetime) -> Aircraft | None:
    limits = [lim for lim in ac.inspection_limits if lim.inspection_type_code == code and lim.unit != Unit.IDLE_DAY]
    if not limits:
        return None  # no programme to reset against — cannot be shown to help
    dues = {(d.inspection_type_code, d.unit): d for d in ac.inspection_dues}
    for lim in limits:
        old = dues.get((code, lim.unit))
        # An overflown limit restarts from where it was due (tolerance rule) — the earlier point is always safe.
        if lim.unit == Unit.CAL_DAY:
            start = min(at, old.due_at) if old and old.due_at else at
            dues[(code, lim.unit)] = InspectionDue(code, lim.unit, None, start + timedelta(days=lim.interval_value),
                                                   last_done_at=at)
        elif lim.unit in COUNTER_UNITS:
            current = _counter_value(ac, lim.unit, lim.tg_weight)
            if current is None:
                return None
            point = min(current, old.due_value) if old and old.due_value is not None else current
            dues[(code, lim.unit)] = InspectionDue(code, lim.unit, point + lim.interval_value, None,
                                                   last_done_value=point, last_done_at=at)
    return replace(ac, inspection_dues=tuple(dues[k] for k in sorted(dues)))


def _renew_permit(ac: Aircraft, type_code: str, at: datetime) -> Aircraft | None:
    same = [p for p in ac.permits if p.permit_type_code == type_code]
    if not same or any(p.limit_unit is not None for p in same):
        return None  # absent (a data question) or usage-limited (renewal limit unknown)
    dated = [p for p in same if p.is_active and p.expires_at is not None and p.issued_at <= at
             and p.expires_at > p.issued_at]
    if not dated:
        return None  # no previous validity to repeat — the renewal period would be invented
    latest = max(dated, key=lambda p: (p.issued_at, p.permit_id))
    assert latest.expires_at is not None
    # Same validity length as the last permit; every restriction any permit of this type carried is kept.
    restrictions = tuple(sorted({r for p in same for r in p.restrictions}))
    renewed = Permit(permit_id=max(p.permit_id for p in ac.permits) + 1, permit_type_code=type_code,
                     permit_number=f"{latest.permit_number}-R", issued_at=at,
                     expires_at=at + (latest.expires_at - latest.issued_at), limit_unit=None, limit_value=None,
                     restrictions=restrictions, status="ACTIVE", is_grounding=any(p.is_grounding for p in same))
    others = tuple(p for p in ac.permits if p.permit_type_code != type_code)
    return replace(ac, permits=(*others, renewed))


def _close_ma(ac: Aircraft, ma_number: str, at: datetime) -> Aircraft | None:
    if not any(m.ma_number == ma_number and m.is_open for m in ac.maintenance_actions):
        return None
    return replace(ac, maintenance_actions=tuple(
        replace(m, is_open=False, closed_at=at) if m.ma_number == ma_number else m for m in ac.maintenance_actions))


def _renew_qualification(ac: Aircraft, code: str, at: datetime) -> Aircraft | None:
    same = [q for q in ac.qualifications if q.qualification_code == code]
    if not same or any(q.valid_to is None or q.valid_to <= q.valid_from for q in same):
        return None  # no previous validity to repeat
    return replace(ac, qualifications=tuple(
        replace(q, valid_from=at, valid_to=at + (q.valid_to - q.valid_from))
        if q.qualification_code == code and q.valid_to is not None else q
        for q in ac.qualifications))


def _check_flight(ac: Aircraft, at: datetime) -> Aircraft | None:
    """Idle check = a recorded check flight at `at`, with its counter use (CHECK_FLIGHT_USE)."""
    use = {**CHECK_FLIGHT_USE, Unit.ENG_START: float(ac.aircraft_type.engine_count)}
    counters = dict(ac.counters)
    for unit, amount in use.items():
        c = counters.get(unit)
        if c is None:
            return None
        counters[unit] = replace(c, value=c.value + amount)
    return replace(ac, last_flight_at=at, counters=counters)


def complete_tasks(ac: Aircraft, tasks: Sequence[Task], at: datetime) -> Aircraft | None:
    """The aircraft once `tasks` are done at `at`; None if any task cannot be simulated (never assume success)."""
    try:
        return _complete(ac, tasks, at)
    except OverflowError:  # absurd intervals or dates in the source — cannot be shown to help
        return None


def _complete(ac: Aircraft, tasks: Sequence[Task], at: datetime) -> Aircraft | None:
    out: Aircraft | None = ac
    for action, ref in tasks:
        if out is None:
            return None
        if action == TaskAction.PERFORM_INSPECTION:
            out = _perform_inspection(out, ref, at)
        elif action == TaskAction.RENEW_PERMIT:
            out = _renew_permit(out, ref, at)
        elif action == TaskAction.CLOSE_MA:
            out = _close_ma(out, ref, at)
        elif action == TaskAction.RENEW_QUALIFICATION:
            out = _renew_qualification(out, ref, at)
        elif action == TaskAction.IDLE_CHECK:
            out = _check_flight(out, at)
        elif action == TaskAction.RETURN_TO_SERVICE:
            out = replace(out, status=OperationalStatus.OPERATIONAL)
        else:  # FIX_DATA: only the data owner can resolve it
            return None
    return out


# ── recommendations ─────────────────────────────────────────────────────────────────────────────────────────────
_SOURCE_ACTION = {"INSPECTION": TaskAction.PERFORM_INSPECTION, "PERMIT": TaskAction.RENEW_PERMIT,
                  "DEFERRAL": TaskAction.CLOSE_MA}


def tasks_for_reasons(reasons: Sequence[Reason]) -> tuple[Task, ...] | None:
    """Maintenance tasks that address every reason; None if any reason is not a maintenance matter.

    Not maintenance: engine/endurance/scope/qualification-missing mismatches, overlapping assignments, manual
    exclusion, permit-restriction conflicts and missing or stale data (data problems are never "fixed" by a plan).
    """
    tasks: list[Task] = []
    for r in reasons:
        p = r.params
        code = r.code
        if code in (ReasonCode.INSPECTION_EXPIRED, ReasonCode.INSPECTION_DUE_BEFORE_MISSION_END):
            tasks.append((TaskAction.PERFORM_INSPECTION, str(p["inspection_code"])))
        elif code in (ReasonCode.PERMIT_EXPIRED, ReasonCode.PERMIT_EXPIRES_BEFORE_MISSION_END):
            tasks.append((TaskAction.RENEW_PERMIT, str(p["permit_type_code"])))
        elif code in (ReasonCode.DEFERRAL_EXPIRED, ReasonCode.DEFERRAL_EXPIRES_BEFORE_MISSION_END,
                      ReasonCode.GROUNDING_MA_OPEN, ReasonCode.QUALIFICATION_REVOKED_BY_MA):
            tasks.append((TaskAction.CLOSE_MA, str(p["ma_number"])))
        elif code in (ReasonCode.QUALIFICATION_EXPIRED, ReasonCode.QUALIFICATION_EXPIRES_BEFORE_MISSION_END):
            tasks.append((TaskAction.RENEW_QUALIFICATION, str(p["qualification_code"])))
        elif code == ReasonCode.IDLE_LIMIT_EXCEEDED:
            tasks.append((TaskAction.IDLE_CHECK, "IDLE_CHECK"))
        elif code == ReasonCode.AIRCRAFT_NOT_OPERATIONAL:
            tasks.append((TaskAction.RETURN_TO_SERVICE, str(p["status"])))
        elif code == ReasonCode.JEOPARDIZES_LATER_ASSIGNMENT and str(p.get("source")) in _SOURCE_ACTION:
            tasks.append((_SOURCE_ACTION[str(p["source"])], str(p["ref"])))
        else:
            return None
    return tuple(sorted(set(tasks)))


@dataclass(frozen=True, slots=True)
class Candidate:
    aircraft_id: int
    tail_number: str
    tasks: tuple[Task, ...]


@dataclass(frozen=True, slots=True)
class MissionRecommendation:
    eligible_count: int
    shortfall: int
    complete_by: datetime  # every task must be finished before the mission starts
    candidates: tuple[Candidate, ...]  # every verified option (none when not short); fewest tasks first


def recommend(aircraft: Sequence[Aircraft], results: Sequence[EligibilityResult], mission: MissionRequest,
              policy: PlanningPolicy, ctx: EvaluationContext) -> MissionRecommendation:
    """Excluded aircraft that maintenance would make eligible for `mission`, each verified by re-evaluation.

    All verified options are returned (the planner chooses); nothing is computed when the mission is not short.
    """
    by_id = {a.aircraft_id: a for a in aircraft}
    eligible = sum(1 for r in results if r.eligible)
    shortfall = max(0, mission.required_count - eligible)
    found: list[Candidate] = []
    if shortfall == 0:
        return MissionRecommendation(eligible, 0, mission.start_at, ())
    for r in sorted(results, key=lambda x: x.aircraft_id):
        ac = by_id.get(r.aircraft_id)
        if r.eligible or ac is None:
            continue
        tasks = tasks_for_reasons(r.reasons)
        if not tasks:
            continue
        done = complete_tasks(ac, tasks, ctx.now)
        if done is None or not evaluate_eligibility(done, mission, policy, ctx).eligible:
            continue  # the tasks alone would not be enough — not recommended
        found.append(Candidate(ac.aircraft_id, ac.tail_number, tasks))
    found.sort(key=lambda c: (len(c.tasks), c.tail_number))
    return MissionRecommendation(eligible, shortfall, mission.start_at, tuple(found))
