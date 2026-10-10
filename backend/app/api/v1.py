"""All /api/v1 routes (API.md §3). Routers parse → call a service → return a DTO."""

from __future__ import annotations

import math
import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.api.deps import Admin, Auditor, ContainerDep, Planner, SessionDep, UserDep, Viewer, WhatIf
from app.api.schemas import (
    AircraftDetailOut,
    AircraftSummaryOut,
    AlertOut,
    AssignIn,
    AssignmentOut,
    AuditOut,
    AuthConfigOut,
    CandidateOut,
    DurationSuggestionOut,
    EligibilityCheckIn,
    FleetSummaryOut,
    HangarCapacityIn,
    HangarCapacityOut,
    HealthOut,
    HorizonApplyIn,
    HorizonApplyOut,
    HorizonIn,
    HorizonPlanOut,
    LoginIn,
    MissionIn,
    MissionOut,
    ModelOut,
    OptimizationRunOut,
    OptimizeIn,
    Page,
    PolicyUpdateIn,
    ReadyOut,
    ReferenceOut,
    RiskOut,
    RunSummaryOut,
    SourceFreshnessOut,
    TgWeightIn,
    TgWeightOut,
    TimelineItemOut,
    UserOut,
    WeightsIn,
    WeightsOut,
)
from app.domain.models import EngineRequirement, MissionRequest, MissionType
from app.security.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    create_session_token,
    new_csrf_token,
    revoke_session_token,
)
from app.security.oidc import STATE_COOKIE, STATE_TTL_S, OidcError
from app.services import admin, fleet, horizon, missions, optimization
from app.services.context import load_context
from app.services.errors import ServiceError, validation

router = APIRouter(prefix="/api/v1")
ISRAEL = ZoneInfo("Asia/Jerusalem")
READY_TTL_S = 10.0


def _page(items: list[Any], page: int, page_size: int) -> dict[str, Any]:
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "total": len(items), "page": page, "page_size": page_size}


def _audit(request: Request, s: SessionDep, c: ContainerDep, user_id: int | None, action: str,
           entity_type: str | None = None, entity_id: str | None = None,
           details: dict[str, Any] | None = None) -> None:
    admin.audit(s, at=c.clock.now(), user_id=user_id, action=action, entity_type=entity_type, entity_id=entity_id,
                details=details, request_id=getattr(request.state, "request_id", None),
                client_ip=request.client.host if request.client else None)


# ── health & auth ──────────────────────────────────────────────────────
@router.get("/health", response_model=HealthOut, tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready", response_model=ReadyOut, tags=["health"])
def ready(c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    # Unauthenticated: cache the probe so it cannot be used to hammer the data lake (SECURITY.md §7).
    if c.ready_cache and time.monotonic() - c.ready_cache[0] < READY_TTL_S:
        return c.ready_cache[1]
    db = "ok" if admin.db_ok(s) else "error"
    try:
        c.ds.load_reference()
        source = "ok"
    except Exception:
        source = "error"
    versions = c.ml.model_versions()
    models = {k: ("ACTIVE" if v else "MISSING") for k, v in versions.items() if k != "utilization"}
    payload = {"db": db, "data_source": source, "models": models}
    c.ready_cache = (time.monotonic(), payload)
    return payload


@router.post("/auth/login", response_model=UserOut, tags=["auth"])
def login(body: LoginIn, request: Request, response: Response, c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    if c.settings.auth_mode != "local":
        raise ServiceError(404, "NOT_FOUND", "local login disabled")
    ip = request.client.host if request.client else "unknown"
    if not c.login_limiter.allow(ip) or c.lockout.is_locked(body.username):
        raise ServiceError(429, "RATE_LIMITED", "Too many attempts")
    user = admin.authenticate(s, body.username, body.password)
    if user is None:
        c.lockout.record_failure(body.username)
        _audit(request, s, c, None, "LOGIN_FAILED", "user", body.username[:50])
        s.commit()
        raise ServiceError(401, "UNAUTHENTICATED", "Invalid credentials")
    c.lockout.clear(body.username)
    _issue_session(response, c, user.user_id, user.role)
    _audit(request, s, c, user.user_id, "LOGIN", "user", str(user.user_id))
    return admin.user_dict(user)


def _issue_session(response: Response, c: ContainerDep, user_id: int, role: str) -> None:
    token = create_session_token(user_id, role, c.settings.jwt_secret, c.settings.session_ttl_hours)
    max_age = int(c.settings.session_ttl_hours * 3600)
    secure = c.settings.cookie_secure
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=secure, samesite="strict",
                        path="/")
    response.set_cookie(CSRF_COOKIE, new_csrf_token(), max_age=max_age, httponly=False, secure=secure,
                        samesite="strict", path="/")


@router.get("/auth/config", response_model=AuthConfigOut, tags=["auth"])
def auth_config(c: ContainerDep) -> dict[str, str]:
    return {"mode": c.settings.auth_mode}


@router.get("/auth/oidc/login", tags=["auth"], status_code=302)
def oidc_login(c: ContainerDep) -> Response:
    if c.oidc is None:
        raise ServiceError(404, "NOT_FOUND", "SSO is not enabled")
    url, state_cookie = c.oidc.start()
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(STATE_COOKIE, state_cookie, max_age=STATE_TTL_S, httponly=True,
                        secure=c.settings.cookie_secure, samesite="lax", path="/api/v1/auth/oidc")
    return response


@router.get("/auth/oidc/callback", tags=["auth"], status_code=302)
def oidc_callback(request: Request, c: ContainerDep, s: SessionDep,
                  code: Annotated[str, Query(max_length=2048)] = "",
                  state: Annotated[str, Query(max_length=256)] = "") -> Response:
    if c.oidc is None:
        raise ServiceError(404, "NOT_FOUND", "SSO is not enabled")
    try:
        identity = c.oidc.finish(code, state, request.cookies.get(STATE_COOKIE))
        user = admin.upsert_sso_user(s, identity.subject, identity.username, identity.display_name, identity.role,
                                     c.clock.now())
    except (OidcError, ServiceError) as exc:
        _audit(request, s, c, None, "LOGIN_FAILED", "user", "sso", {"reason": exc.code})
        s.commit()
        failed = RedirectResponse(f"/?sso_error={exc.code}", status_code=302)
        failed.delete_cookie(STATE_COOKIE, path="/api/v1/auth/oidc")
        return failed
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(STATE_COOKIE, path="/api/v1/auth/oidc")
    _issue_session(response, c, user.user_id, user.role)
    _audit(request, s, c, user.user_id, "LOGIN", "user", str(user.user_id), {"method": "oidc"})
    return response


@router.post("/auth/logout", status_code=204, tags=["auth"])
def logout(request: Request, response: Response, user: UserDep, c: ContainerDep, s: SessionDep) -> Response:
    _audit(request, s, c, user.user_id, "LOGOUT", "user", str(user.user_id))
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        revoke_session_token(token, c.settings.jwt_secret)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@router.get("/auth/me", response_model=UserOut, tags=["auth"])
def me(user: UserDep) -> dict[str, Any]:
    return admin.user_dict(user)


# ── reference ──────────────────────────────────────────────────────────
@router.get("/reference", response_model=ReferenceOut, tags=["reference"])
def reference(_: Viewer, c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    return admin.reference(c, s)


# ── fleet ──────────────────────────────────────────────────────────────
@router.get("/fleet/summary", response_model=FleetSummaryOut, tags=["fleet"])
def fleet_summary(_: Viewer, c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    return fleet.fleet_summary(load_context(c, s))


@router.get("/fleet/alerts", response_model=list[AlertOut], tags=["fleet"])
def fleet_alerts(_: Viewer, c: ContainerDep, s: SessionDep,
                 status: str = "EXPIRED,EXPIRING_SOON,UNKNOWN",
                 limit: Annotated[int, Query(ge=1, le=500)] = 100, units: str | None = None) -> list[dict[str, Any]]:
    fc = load_context(c, s)
    wanted = [x.strip() for x in status.split(",") if x.strip()]
    at_risk = optimization.assignments_at_risk(s, fc) if "EXPIRED" in wanted else []  # never filtered by unit
    return fleet.fleet_alerts(fc, wanted, limit, extra=at_risk, units=_units(units))


FILTER_UNITS = frozenset({"FH", "LDG", "TG", "CYC", "ENG_START", "CAL_DAY"})


def _units(raw: str | None) -> frozenset[str]:
    """Comma-separated activity units for the fleet expiry filters (FR-14); unknown codes are a 422."""
    units = frozenset(x.strip() for x in (raw or "").split(",") if x.strip())
    if units - FILTER_UNITS:
        raise validation("units", "INVALID_VALUE")
    return units


def _bands(raw: str) -> fleet.BandFilter:
    """`expiry=FH:0:50,CAL_DAY:0:7` — per unit "תוך:ועד" in that unit (FR-14). Malformed ⇒ 422."""
    bands: list[tuple[str, float, float]] = []
    for part in (x.strip() for x in raw.split(",") if x.strip()):
        try:
            unit, low, high = part.split(":")
            lo, hi = float(low), float(high)
        except ValueError:
            raise validation("expiry", "INVALID_VALUE") from None
        if unit not in FILTER_UNITS or not (math.isfinite(lo) and math.isfinite(hi) and 0 <= lo < hi):
            raise validation("expiry", "INVALID_VALUE")
        if any(b[0] == unit for b in bands):
            raise validation("expiry", "DUPLICATE_UNIT")
        bands.append((unit, lo, hi))
    return fleet.BandFilter(tuple(bands))


def _end_of_day(day: date | None) -> datetime | None:
    """`until` is a calendar day in Israel time; the window includes that whole day."""
    if day is None:
        return None
    try:
        return datetime.combine(day + timedelta(days=1), clock_time.min, tzinfo=ISRAEL).astimezone(UTC)
    except OverflowError:
        raise validation("until", "INVALID_VALUE") from None


@router.get("/fleet/timeline", response_model=list[TimelineItemOut], tags=["fleet"])
def fleet_timeline(_: Viewer, c: ContainerDep, s: SessionDep,
                   days: Annotated[int, Query(ge=1, le=365)] = 30, units: str | None = None,
                   until: date | None = None) -> list[dict[str, Any]]:
    fc = load_context(c, s)
    return fleet.fleet_timeline(fc, days, c.ml.utilization(fc.aircraft, fc.now), _units(units), _end_of_day(until))


@router.get("/fleet/data-freshness", response_model=list[SourceFreshnessOut], tags=["fleet"])
def fleet_freshness(_: Viewer, c: ContainerDep, s: SessionDep) -> list[dict[str, Any]]:
    return fleet.data_freshness(load_context(c, s))


# ── aircraft ───────────────────────────────────────────────────────────
@router.get("/aircraft", response_model=Page[AircraftSummaryOut], tags=["aircraft"])
def list_aircraft(_: Viewer, c: ContainerDep, s: SessionDep, type_code: str | None = None,
                  base_code: str | None = None, status: str | None = None, compliance_status: str | None = None,
                  qualification_code: str | None = None, q: Annotated[str | None, Query(max_length=16)] = None,
                  sort: str = "compliance_status", page: Annotated[int, Query(ge=1)] = 1,
                  page_size: Annotated[int, Query(ge=1, le=200)] = 50,
                  expiry: Annotated[str | None, Query(max_length=200)] = None,
                  squadron_code: str | None = None) -> dict[str, Any]:
    fc = load_context(c, s)
    risks = c.ml.predict_risk(fc.aircraft, fc.now)
    statuses = [x.strip() for x in compliance_status.split(",")] if compliance_status else None
    bands = _bands(expiry) if expiry else None  # FR-14: per-unit "תוך … ועד …" bands
    rows = fleet.list_aircraft(fc, risks, type_code=type_code, base_code=base_code, status=status,
                               compliance_status=statuses, qualification_code=qualification_code, q=q, sort=sort,
                               expiry=bands, squadron_code=squadron_code)
    return _page(rows, page, page_size)


@router.get("/aircraft/{tail_number}", response_model=AircraftDetailOut, tags=["aircraft"])
def aircraft_detail(tail_number: str, _: Viewer, c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    fc = load_context(c, s)
    risks = c.ml.predict_risk(fc.aircraft, fc.now)
    ac = fc.by_tail().get(tail_number)
    risk = risks.get(ac.aircraft_id) if (risks and ac) else None
    util = c.ml.utilization(fc.aircraft, fc.now)
    return fleet.aircraft_detail(fc, tail_number, risk, c.ml.model_versions().get("defect_risk"), util)


@router.get("/aircraft/{tail_number}/flights", tags=["aircraft"])
def aircraft_flights(tail_number: str, _: Viewer, c: ContainerDep, s: SessionDep,
                     page: Annotated[int, Query(ge=1)] = 1,
                     page_size: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    fc = load_context(c, s)
    return _page(admin.flights_for(c, fc, tail_number), page, page_size)


@router.get("/aircraft/{tail_number}/risk", response_model=RiskOut, tags=["aircraft"])
def aircraft_risk(tail_number: str, _: Viewer, c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    fc = load_context(c, s)
    ac = fc.by_tail().get(tail_number)
    if ac is None:
        raise ServiceError(404, "NOT_FOUND", "aircraft not found")
    risks = c.ml.predict_risk(fc.aircraft, fc.now)
    return fleet._risk_dict(risks.get(ac.aircraft_id) if risks else None,
                            c.ml.model_versions().get("defect_risk"))


# ── missions ───────────────────────────────────────────────────────────
def _mission_out(c: ContainerDep, s: SessionDep, m: Any) -> dict[str, Any]:
    tails = {a.aircraft_id: a.tail_number for a in c.ds.load_snapshot().aircraft}
    return missions.mission_out(s, m, tails)


@router.post("/missions", response_model=MissionOut, status_code=201, tags=["missions"])
def create_mission(body: MissionIn, request: Request, user: Planner, c: ContainerDep, s: SessionDep
                   ) -> dict[str, Any]:
    by_tail = {a.tail_number: a.aircraft_id for a in c.ds.load_snapshot().aircraft}
    m = missions.upsert_mission(s, body.model_dump(), user_id=user.user_id, now=c.clock.now(), by_tail=by_tail,
                                reference=c.ds.load_reference())
    _audit(request, s, c, user.user_id, "MISSION_CREATE", "mission", str(m.mission_id))
    return _mission_out(c, s, m)


@router.get("/missions", response_model=Page[MissionOut], tags=["missions"])
def list_missions(_: Viewer, c: ContainerDep, s: SessionDep, status: str | None = None,
                  date_from: Annotated[datetime | None, Query(alias="from")] = None,
                  date_to: Annotated[datetime | None, Query(alias="to")] = None,
                  page: Annotated[int, Query(ge=1)] = 1,
                  page_size: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    rows, total = admin.list_missions(s, page=page, page_size=page_size, status=status, date_from=date_from,
                                      date_to=date_to)
    tails = {a.aircraft_id: a.tail_number for a in c.ds.load_snapshot().aircraft}
    return {"items": [missions.mission_out(s, m, tails) for m in rows], "total": total, "page": page,
            "page_size": page_size}


@router.get("/missions/{mission_id}", response_model=MissionOut, tags=["missions"])
def get_mission(mission_id: int, _: Viewer, c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    return _mission_out(c, s, missions.get_mission_or_404(s, mission_id))


@router.patch("/missions/{mission_id}", response_model=MissionOut, tags=["missions"])
def update_mission(mission_id: int, body: MissionIn, request: Request, user: Planner, c: ContainerDep,
                   s: SessionDep) -> dict[str, Any]:
    m = missions.get_mission_or_404(s, mission_id)
    by_tail = {a.tail_number: a.aircraft_id for a in c.ds.load_snapshot().aircraft}
    missions.upsert_mission(s, body.model_dump(), user_id=user.user_id, now=c.clock.now(), by_tail=by_tail,
                            reference=c.ds.load_reference(), mission=m)
    _audit(request, s, c, user.user_id, "MISSION_UPDATE", "mission", str(mission_id))
    return _mission_out(c, s, m)


@router.post("/missions/{mission_id}/duplicate", response_model=MissionOut, status_code=201, tags=["missions"])
def duplicate_mission(mission_id: int, request: Request, user: Planner, c: ContainerDep, s: SessionDep
                      ) -> dict[str, Any]:
    m = missions.duplicate(s, missions.get_mission_or_404(s, mission_id), user.user_id, c.clock.now())
    _audit(request, s, c, user.user_id, "MISSION_CREATE", "mission", str(m.mission_id),
           {"duplicated_from": mission_id})
    return _mission_out(c, s, m)


@router.post("/missions/{mission_id}/cancel", response_model=MissionOut, tags=["missions"])
def cancel_mission(mission_id: int, request: Request, user: Planner, c: ContainerDep, s: SessionDep
                   ) -> dict[str, Any]:
    m = missions.get_mission_or_404(s, mission_id)
    missions.cancel(s, m, c.clock.now())
    _audit(request, s, c, user.user_id, "MISSION_CANCEL", "mission", str(mission_id))
    return _mission_out(c, s, m)


@router.post("/missions/{mission_id}/optimize", response_model=OptimizationRunOut, tags=["optimization"])
def optimize(mission_id: int, user: Planner, c: ContainerDep, s: SessionDep,
             body: OptimizeIn | None = None) -> dict[str, Any]:
    if not c.optimize_limiter.allow(str(user.user_id)):
        raise ServiceError(429, "RATE_LIMITED", "Too many optimization requests")
    m = missions.get_mission_or_404(s, mission_id)
    if m.status in ("CANCELLED", "COMPLETED"):
        raise ServiceError(409, "MISSION_NOT_EDITABLE", "mission is closed")
    return optimization.run_optimization(c, s, m, user.user_id, body.weights_preset if body else None)


@router.get("/missions/{mission_id}/runs", response_model=list[RunSummaryOut], tags=["optimization"])
def mission_runs(mission_id: int, _: Viewer, s: SessionDep) -> list[dict[str, Any]]:
    missions.get_mission_or_404(s, mission_id)
    return admin.run_summaries(s, mission_id)


@router.get("/optimization-runs/{run_id}", response_model=OptimizationRunOut, tags=["optimization"])
def get_run(run_id: str, _: Viewer, s: SessionDep) -> dict[str, Any]:
    return admin.run_detail(s, run_id)


@router.post("/missions/{mission_id}/assign", response_model=list[AssignmentOut], tags=["optimization"])
def assign(mission_id: int, body: AssignIn, user: Planner, c: ContainerDep, s: SessionDep) -> list[dict[str, Any]]:
    m = missions.get_mission_or_404(s, mission_id)
    return optimization.assign(c, s, m, body.run_id, body.set_index, user.user_id)


@router.post("/missions/{mission_id}/unassign", response_model=MissionOut, tags=["optimization"])
def unassign(mission_id: int, request: Request, user: Planner, c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    m = missions.get_mission_or_404(s, mission_id)
    admin.unassign(s, m, c.clock.now())
    _audit(request, s, c, user.user_id, "ASSIGN_CANCEL", "mission", str(mission_id))
    return _mission_out(c, s, m)


@router.post("/eligibility/check", response_model=list[CandidateOut], tags=["optimization"])
def eligibility_check(body: EligibilityCheckIn, _: WhatIf, c: ContainerDep, s: SessionDep) -> list[dict[str, Any]]:
    mi = body.mission
    missions.validate_codes(mi.model_dump(), c.ds.load_reference())  # same 422s as saving a mission
    mission = MissionRequest(
        name=mi.name, mission_type=MissionType(mi.mission_type), required_count=mi.required_aircraft_count,
        start_at=mi.start_at, duration_hours=mi.est_duration_hours, landings=mi.est_landings,
        touch_and_goes=mi.est_touch_and_goes, engine_starts=mi.est_engine_starts,
        departure_base_code=mi.departure_base_code, required_qualifications=frozenset(mi.required_qualifications),
        engine_requirement=EngineRequirement(mi.engine_requirement), same_type_required=mi.same_type_required,
        force_include=frozenset(mi.force_include), force_exclude=frozenset(mi.force_exclude),
        squadron_codes=frozenset(mi.squadron_codes), type_codes=frozenset(mi.aircraft_type_codes))
    return optimization.eligibility_check(c, s, mission, body.tail_numbers, mi.overrides.model_dump())


# ── horizon planning (FR-46) ───────────────────────────────────────────
@router.post("/planning/horizon", response_model=HorizonPlanOut, tags=["optimization"])
def horizon_plan(body: HorizonIn, user: Planner, c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    if not c.optimize_limiter.allow(str(user.user_id)):
        raise ServiceError(429, "RATE_LIMITED", "Too many optimization requests")
    return horizon.plan(c, s, user_id=user.user_id, mission_ids=body.mission_ids, date_from=body.date_from,
                        date_to=body.date_to, preset=body.weights_preset)


@router.post("/planning/horizon/apply", response_model=HorizonApplyOut, tags=["optimization"])
def horizon_apply(body: HorizonApplyIn, user: Planner, c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    return horizon.apply(c, s, user_id=user.user_id,
                         assignments=[(a.mission_id, a.aircraft_ids) for a in body.assignments])


# ── ML ─────────────────────────────────────────────────────────────────
@router.get("/ml/models", response_model=list[ModelOut], tags=["ml"])
def ml_models(_: Viewer, s: SessionDep) -> list[dict[str, Any]]:
    return admin.models(s)


@router.get("/ml/duration-buffer", response_model=DurationSuggestionOut, tags=["ml"])
def duration_buffer(_: Planner, c: ContainerDep, s: SessionDep, mission_type: str, type_code: str,
                    duration_hours: Annotated[float, Query(gt=0, le=24)],
                    landings: Annotated[int, Query(ge=1, le=50)] = 1,
                    touch_and_goes: Annotated[int, Query(ge=0, le=100)] = 0) -> dict[str, Any]:
    policy = admin.policy(s)
    out = c.ml.duration_suggestion(mission_type, type_code, duration_hours, landings, touch_and_goes,
                                   c.clock.now().month, policy["duration_buffer_pct"])
    if out is None:
        raise ServiceError(404, "NOT_FOUND", "duration_buffer model not available")
    return out


@router.post("/ml/models/{model_name}/retrain", status_code=202, tags=["ml"])
def retrain(model_name: str, request: Request, background: BackgroundTasks, user: Admin, c: ContainerDep,
            s: SessionDep) -> dict[str, str]:
    if model_name not in ("defect_risk", "duration_buffer"):
        raise ServiceError(404, "NOT_FOUND", "unknown model")
    job_id = admin.start_retrain(c, background, model_name)
    _audit(request, s, c, user.user_id, "MODEL_RETRAIN", "ml_model", model_name, {"job_id": job_id})
    return {"job_id": job_id}


@router.get("/ml/jobs/{job_id}", tags=["ml"])
def ml_job(job_id: str, _: Admin) -> dict[str, Any]:
    return admin.job_status(job_id)


# ── settings & audit ───────────────────────────────────────────────────
@router.get("/settings/policy", tags=["settings"])
def get_policy(_: Viewer, s: SessionDep) -> dict[str, Any]:
    return admin.policy(s)


@router.put("/settings/policy", tags=["settings"])
def put_policy(body: PolicyUpdateIn, request: Request, user: Admin, c: ContainerDep, s: SessionDep
               ) -> dict[str, Any]:
    before = admin.policy(s)
    after = admin.update_policy(s, body.model_dump(exclude_none=True), user.user_id, c.clock.now())
    _audit(request, s, c, user.user_id, "POLICY_UPDATE", "planning_policy", None,
           {"before": {k: before[k] for k in body.model_dump(exclude_none=True)},
            "after": body.model_dump(exclude_none=True)})
    return after


@router.get("/settings/weights", response_model=WeightsOut, tags=["settings"])
def get_weights(_: Viewer, s: SessionDep) -> dict[str, Any]:
    return {"presets": admin.weights(s)}


@router.put("/settings/weights", response_model=WeightsOut, tags=["settings"])
def put_weights(body: WeightsIn, request: Request, user: Admin, c: ContainerDep, s: SessionDep) -> dict[str, Any]:
    before = admin.weights(s)
    after = admin.update_weights(s, {str(k): {str(t): w for t, w in v.items()} for k, v in body.presets.items()})
    _audit(request, s, c, user.user_id, "WEIGHTS_UPDATE", "weight_preset", None,
           {"before": {k: before[k] for k in body.presets}, "after": {k: after[k] for k in body.presets}})
    return {"presets": after}


@router.get("/settings/tg-weights", response_model=list[TgWeightOut], tags=["settings"])
def get_tg_weights(_: Viewer, c: ContainerDep, s: SessionDep) -> list[dict[str, Any]]:
    return admin.tg_weights(c, s)


@router.put("/settings/tg-weights", response_model=list[TgWeightOut], tags=["settings"])
def put_tg_weights(body: list[TgWeightIn], request: Request, user: Admin, c: ContainerDep, s: SessionDep
                   ) -> list[dict[str, Any]]:
    before = {(r["type_code"], r["inspection_type_code"], r["unit"]): r["effective_tg_weight"]
              for r in admin.tg_weights(c, s)}
    after = admin.update_tg_weights(c, s, [(x.type_code, x.inspection_type_code, x.unit, x.tg_weight) for x in body],
                                    user.user_id, c.clock.now())
    _audit(request, s, c, user.user_id, "TG_WEIGHTS_UPDATE", "tg_weight_override", None, {"changes": [
        {**x.model_dump(), "before": before.get((x.type_code, x.inspection_type_code, x.unit))} for x in body]})
    return after


@router.get("/settings/hangar-capacity", response_model=list[HangarCapacityOut], tags=["settings"])
def get_hangar(_: Viewer, s: SessionDep, base_code: str | None = None) -> list[dict[str, Any]]:
    return admin.hangar(s, base_code)


@router.put("/settings/hangar-capacity", response_model=list[HangarCapacityOut], tags=["settings"])
def put_hangar(body: list[HangarCapacityIn], request: Request, user: Admin, c: ContainerDep, s: SessionDep
               ) -> list[dict[str, Any]]:
    admin.save_hangar(s, [(x.base_code, x.iso_week, x.slots) for x in body])
    _audit(request, s, c, user.user_id, "HANGAR_CAPACITY_UPDATE", "hangar_capacity", None,
           {"rows": [x.model_dump() for x in body]})
    return admin.hangar(s, None)


@router.get("/audit", response_model=Page[AuditOut], tags=["audit"])
def audit_log(_: Auditor, s: SessionDep, user_id: int | None = None, action: str | None = None,
              date_from: Annotated[datetime | None, Query(alias="from")] = None,
              date_to: Annotated[datetime | None, Query(alias="to")] = None,
              page: Annotated[int, Query(ge=1)] = 1,
              page_size: Annotated[int, Query(ge=1, le=200)] = 50) -> dict[str, Any]:
    return admin.audit_page(s, page=page, page_size=page_size, user_id=user_id, action=action,
                            date_from=date_from, date_to=date_to)
