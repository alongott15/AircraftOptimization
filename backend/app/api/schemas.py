"""Pydantic DTOs — the typed contract of API.md (OpenAPI → frontend/src/api/schema.d.ts)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

T = TypeVar("T")
Status = Literal["EXPIRED", "EXPIRING_SOON", "OK", "UNKNOWN"]
MissionTypeLit = Literal["TRAINING", "TRANSPORT", "FERRY", "OTHER"]
EngineReqLit = Literal["ANY", "SINGLE", "TWIN", "MIN_2"]
PresetLit = Literal["BALANCED", "MAX_SAFETY", "MAX_AVAILABILITY"]
ParamValue = str | int | float | None


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Out(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Page(Out, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


# ───────── requests ─────────
class LoginIn(In):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class MissionOverridesIn(In):
    """Per-mission planner choices. Calendar/landing buffers are admin policy only (FR-34 replaced them in the form);
    `duration_buffer_pct` (from the ML suggestion, FR-52) can only make the FH buffer stricter than the policy."""

    duration_buffer_pct: float | None = Field(default=None, ge=0, le=100)
    weights_preset: PresetLit | None = None


WindowUnitLit = Literal["FH", "LDG", "TG", "CYC", "ENG_START", "CAL_DAY"]


class ExpiryWindowIn(In):
    """"Avoid band" תוך … ועד … in one activity unit (days for CAL_DAY), counted after the mission. Ranking only."""

    unit: WindowUnitLit
    within: float = Field(default=0, ge=0, le=100_000, allow_inf_nan=False)
    until: float = Field(gt=0, le=100_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def _range(self) -> ExpiryWindowIn:
        if self.within >= self.until:
            raise ValueError("WINDOW_RANGE")
        return self


class MissionIn(In):
    name: str = Field(min_length=1, max_length=120)
    mission_type: MissionTypeLit
    required_aircraft_count: int = Field(ge=1, le=20)
    start_at: datetime
    est_duration_hours: float = Field(ge=0.1, le=24)
    est_landings: int = Field(ge=1, le=50)
    est_touch_and_goes: int = Field(default=0, ge=0, le=100)
    est_engine_starts: int = Field(default=1, ge=1, le=20)
    departure_base_code: str = Field(min_length=1, max_length=8)
    required_qualifications: list[str] = Field(default_factory=list, max_length=10)
    engine_requirement: EngineReqLit = "ANY"
    same_type_required: bool = False
    priority: int = Field(default=3, ge=1, le=5)
    notes: str | None = Field(default=None, max_length=1000)
    force_include: list[str] = Field(default_factory=list, max_length=20)
    force_exclude: list[str] = Field(default_factory=list, max_length=50)
    overrides: MissionOverridesIn = Field(default_factory=MissionOverridesIn)
    expiry_windows: list[ExpiryWindowIn] = Field(default_factory=list, max_length=6)

    @field_validator("start_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("INVALID_VALUE")
        return v

    @model_validator(mode="after")
    def _forced(self) -> MissionIn:
        if set(self.force_include) & set(self.force_exclude):
            raise ValueError("TAIL_FORCED_BOTH_WAYS")
        units = [w.unit for w in self.expiry_windows]
        if len(units) != len(set(units)):
            raise ValueError("DUPLICATE_UNIT")
        return self


class OptimizeIn(In):
    weights_preset: PresetLit | None = None


class AssignIn(In):
    run_id: str = Field(min_length=1, max_length=36)
    set_index: int = Field(ge=0, le=3)


class EligibilityCheckIn(In):
    mission: MissionIn
    tail_numbers: list[str] | None = None


class HorizonIn(In):
    mission_ids: list[int] | None = Field(default=None, max_length=50)
    date_from: datetime | None = None
    date_to: datetime | None = None
    weights_preset: PresetLit | None = None


class HorizonAssignmentIn(In):
    mission_id: int
    aircraft_ids: list[int] = Field(max_length=20)


class HorizonApplyIn(In):
    assignments: list[HorizonAssignmentIn] = Field(min_length=1, max_length=50)


class PolicyUpdateIn(In):
    duration_buffer_pct: float | None = Field(default=None, ge=0, le=100)
    min_duration_buffer_hours: float | None = Field(default=None, ge=0, le=5)
    calendar_buffer_hours: float | None = Field(default=None, ge=0, le=72)
    landing_buffer: int | None = Field(default=None, ge=0, le=10)
    turnaround_hours: float | None = Field(default=None, ge=0, le=24)
    idle_policy: Literal["EXCLUDE", "WARN"] | None = None
    tolerance_policy: Literal["WARN", "NEVER"] | None = None
    expiring_soon_days: float | None = Field(default=None, ge=0, le=90)
    expiring_soon_pct: float | None = Field(default=None, ge=0, le=50)
    expiring_soon_fh: float | None = Field(default=None, ge=0, le=100)
    expiring_soon_ldg: float | None = Field(default=None, ge=0, le=200)
    low_margin_pct: float | None = Field(default=None, ge=0, le=50)
    max_staleness_hours: float | None = Field(default=None, ge=1, le=168)
    risk_high_threshold: float | None = Field(default=None, ge=0, le=1)
    weights_preset: PresetLit | None = None


TermLit = Literal["risk", "best_fit", "balance", "capacity", "base_match", "warnings", "expiry_window"]


Weight = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class WeightsIn(In):
    presets: dict[PresetLit, dict[TermLit, Weight]] = Field(min_length=1)


class TgWeightIn(In):
    type_code: str = Field(min_length=1, max_length=16)
    inspection_type_code: str = Field(min_length=1, max_length=24)
    unit: Literal["LDG", "CYC"]
    tg_weight: float | None = Field(default=None, ge=0, le=5, allow_inf_nan=False)  # None ⇒ remove the override


class HangarCapacityIn(In):
    base_code: str = Field(min_length=1, max_length=8)
    iso_week: str = Field(pattern=r"^\d{4}-W\d{2}$")
    slots: int = Field(ge=0, le=50)


# ───────── responses ─────────
class WeightsOut(Out):
    presets: dict[str, dict[str, float]]


class TgWeightOut(Out):
    type_code: str
    inspection_type_code: str
    unit: str
    reference_tg_weight: float
    override_tg_weight: float | None
    effective_tg_weight: float


class UserOut(Out):
    user_id: int
    username: str
    display_name: str
    role: str


class AuthConfigOut(Out):
    mode: Literal["local", "oidc"]


class ReasonOut(Out):
    code: str
    params: dict[str, ParamValue] = Field(default_factory=dict)


class NextDueOut(Out):
    kind: str
    code: str
    unit: str
    due_at: str | None
    remaining: float | None
    status: Status


class AircraftSummaryOut(Out):
    aircraft_id: int
    tail_number: str
    type_code: str
    engine_count: int
    base_code: str
    operational_status: str
    derived_status: str
    compliance_status: Status
    expired_count: int
    expiring_soon_count: int
    open_ma_count: int
    grounding_ma_count: int
    available: bool
    risk_band: str
    risk_probability: float | None
    next_due: NextDueOut | None


class LimitOut(Out):
    unit: str
    due_value: float | None
    due_at: str | None
    remaining: float | None
    interval: float | None
    status: Status


class InspectionOut(Out):
    inspection_code: str
    zone_code: str | None
    status: Status
    limits: list[LimitOut]
    predicted_due_at: str | None


class PermitOut(Out):
    permit_id: int | None
    permit_type_code: str
    permit_number: str | None
    issued_at: str | None
    expires_at: str | None
    restrictions: list[str]
    status: Status
    remaining_days: float | None


class DeferralOut(Out):
    expires_at: str | None
    limit_unit: str | None
    limit_value: float | None
    status: Status
    remaining_days: float | None


class MaintenanceActionOut(Out):
    ma_number: str
    title: str
    description: str | None
    ata_chapter: int | None
    zone_code: str | None
    severity: Literal["GROUNDING", "DEFERRED", "MONITOR"]
    mel_category: str | None
    opened_at: str | None
    deferral: DeferralOut | None
    affects_qualification_code: str | None
    status: str


class RevokedByOut(Out):
    kind: Literal["MA", "PERMIT"]
    ref: str
    restriction: str | None = None


class QualificationOut(Out):
    qualification_code: str
    valid_to: str | None
    effective: bool
    revoked_by: RevokedByOut | None
    status: Status


class CounterOut(Out):
    unit: str
    value: float
    as_of: str | None


class IdleOut(Out):
    idle_days: float | None
    limit_days: float | None
    status: Status


class RiskFactorOut(Out):
    feature_code: str
    direction: Literal["HIGH", "LOW"]


class RiskOut(Out):
    probability: float | None
    band: Literal["LOW", "MEDIUM", "HIGH", "UNAVAILABLE"]
    model_version: str | None
    top_factors: list[RiskFactorOut]


class ModelShapeOut(Out):
    model_shape: str
    engine_count: int
    zones: list[str]


class FreshnessOut(Out):
    oldest_source_updated_at: str | None
    is_stale: bool


class AircraftDetailOut(AircraftSummaryOut):
    serial_number: str
    manufacture_date: str
    type_name_he: str
    last_flight_at: str | None
    max_endurance_hours: float
    model: ModelShapeOut
    counters: list[CounterOut]
    inspections: list[InspectionOut]
    permits: list[PermitOut]
    maintenance_actions: list[MaintenanceActionOut]
    qualifications: list[QualificationOut]
    idle: IdleOut | None
    risk: RiskOut
    data_freshness: FreshnessOut


class TypeCountOut(Out):
    type_code: str
    total: int
    available: int


class FleetSummaryOut(Out):
    as_of: str | None
    total_aircraft: int
    available_now: int
    availability_pct: float
    aircraft_with_expired_items: int
    items_expiring_7d: int
    aircraft_unknown: int
    open_grounding_mas: int
    in_maintenance: int
    by_type: list[TypeCountOut]


class AlertOut(Out):
    aircraft_id: int
    tail_number: str
    source: str
    ref: str
    unit: str | None
    status: Status
    remaining: float | None
    due_at: str | None
    missing_kind: str | None
    missing_code: str | None


class TimelineItemOut(Out):
    aircraft_id: int
    tail_number: str
    source: str
    ref: str
    unit: str
    status: Status
    date: str
    predicted: bool


class SourceFreshnessOut(Out):
    source_name: str
    loaded_at: str | None
    is_stale: bool


class AssignmentOut(Out):
    aircraft_id: int
    tail_number: str | None
    window_start: str | None
    window_end: str | None
    status: str
    run_id: str | None
    set_index: int | None


class ExpiryWindowOut(Out):
    unit: str
    within: float
    until: float


class MissionOut(Out):
    mission_id: int
    name: str
    mission_type: MissionTypeLit
    status: Literal["DRAFT", "OPTIMIZED", "ASSIGNED", "CANCELLED", "COMPLETED"]
    required_aircraft_count: int
    start_at: str
    end_at: str
    est_duration_hours: float
    est_landings: int
    est_touch_and_goes: int
    est_engine_starts: int
    departure_base_code: str
    required_qualifications: list[str]
    engine_requirement: EngineReqLit
    same_type_required: bool
    priority: int
    notes: str | None
    force_include: list[str]
    force_exclude: list[str]
    overrides: dict[str, Any]
    expiry_windows: list[ExpiryWindowOut]
    created_by: int
    created_at: str | None
    updated_at: str | None
    latest_run_id: str | None
    assignments: list[AssignmentOut]


class TermOut(Out):
    value: float
    weight: float
    contribution: float


class ProjectionOut(Out):
    source: str
    ref: str
    unit: str
    interval: float | None
    remaining_now: float | None
    consumption: float | None
    remaining_after: float | None
    due_at: str | None
    outcome: Literal["OK", "EXPIRED", "DUE_BEFORE_END", "MISSING"]


class CandidateOut(Out):
    aircraft_id: int
    tail_number: str
    type_code: str
    base_code: str
    eligible: bool
    score: float | None
    rank: int | None
    score_breakdown: dict[str, TermOut] | None
    reasons: list[ReasonOut]
    warnings: list[ReasonOut]
    unblock_hints: list[ReasonOut]
    projections: list[ProjectionOut]
    risk_probability: float | None
    week_before: str | None = None
    week_after: str | None = None


class SetOut(Out):
    set_index: int
    objective: float
    aircraft_ids: list[int]
    tail_numbers: list[str]


class OptimizationRunOut(Out):
    run_id: str
    mission_id: int
    requested_at: str
    clock_now: str
    solver_status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "SKIPPED"]
    duration_ms: int
    weights_preset: str
    model_versions: dict[str, str | None]
    required: int
    n_aircraft: int
    n_eligible: int
    shortfall: int
    sets: list[SetOut]
    candidates: list[CandidateOut]
    nearest_to_eligible: list[int]
    run_warnings: list[ReasonOut]
    policy: dict[str, Any]


class RunSummaryOut(Out):
    run_id: str
    requested_at: str
    solver_status: str
    n_eligible: int
    shortfall: int
    duration_ms: int


class ModelOut(Out):
    model_name: str
    version: str
    status: str
    trained_at: str
    metrics: dict[str, Any]


class DurationSuggestionOut(Out):
    suggested_buffer_pct: float
    p50_ratio: float
    p90_ratio: float
    model_version: str


class AuditOut(Out):
    audit_id: int
    at: str
    user_id: int | None
    username: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    details: dict[str, Any] | None
    request_id: str | None


class HangarCapacityOut(Out):
    base_code: str
    iso_week: str
    slots: int


class CodeList(Out):
    reasons: list[str]
    warnings: list[str]
    unblock: list[str]


class ReferenceOut(Out):
    aircraft_types: list[dict[str, Any]]
    bases: list[dict[str, Any]]
    qualifications: list[dict[str, Any]]
    activity_units: list[dict[str, Any]]
    inspection_types: list[dict[str, Any]]
    permit_types: list[dict[str, Any]]
    zones: list[dict[str, Any]]
    restrictions: list[str]
    weight_presets: dict[str, dict[str, float]]
    mission_types: list[str]
    engine_requirements: list[str]
    codes: CodeList


class HorizonSeatOut(Out):
    aircraft_id: int
    tail_number: str
    score: float | None


class HorizonMissionOut(Out):
    mission_id: int
    name: str
    start_at: str | None
    end_at: str | None
    required: int
    eligible_count: int
    assigned: list[HorizonSeatOut]
    shortfall: int
    overlaps_with: list[int]


class HorizonAssignmentOut(Out):
    mission_id: int
    aircraft_ids: list[int]


class HorizonPlanOut(Out):
    solver_status: Literal["OPTIMAL", "FEASIBLE", "INFEASIBLE", "SKIPPED"]
    objective: float
    duration_ms: int
    clock_now: str | None
    missions: list[HorizonMissionOut]
    assignments: list[HorizonAssignmentOut]


class HorizonAppliedOut(Out):
    mission_id: int
    tail_numbers: list[str]


class HorizonApplyOut(Out):
    applied: list[HorizonAppliedOut]


class HealthOut(Out):
    status: str


class ReadyOut(Out):
    db: str
    data_source: str
    models: dict[str, str]
