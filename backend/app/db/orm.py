"""SQLAlchemy ORM — source of truth for the schema (DATABASE.md §3–4).

POC note: counters are stored as FLOAT in SQLite (NUMERIC(12,2) in Vertica/Postgres). Datetimes are stored as
ISO-8601 UTC text and always returned timezone-aware.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    DDL,
    TIMESTAMP,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator, TypeEngine


class UTCDateTime(TypeDecorator[datetime]):
    """Aware UTC datetimes: TIMESTAMPTZ on PostgreSQL, 'YYYY-MM-DDTHH:MM:SSZ' text elsewhere. Rejects naive values."""

    impl = String(20)
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(TIMESTAMP(timezone=True))
        return dialect.type_descriptor(String(20))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime not allowed")
        utc = value.astimezone(UTC)
        return utc if dialect.name == "postgresql" else utc.strftime("%Y-%m-%dT%H:%M:%SZ")

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


class FleetBase(DeclarativeBase):
    """Fleet data — read-only for the app (tables in the POC, canonical Vertica views in production)."""


class AppBase(DeclarativeBase):
    """App store — missions, runs, assignments, policy, users, audit, ML registry. Managed by Alembic."""


ALL_METADATA = (FleetBase.metadata, AppBase.metadata)


# ───────────────────────── Fleet data (read-only for the app; Vertica views in prod) ─────────────────────────

class AircraftTypeRow(FleetBase):
    __tablename__ = "aircraft_type"
    type_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name_he: Mapped[str] = mapped_column(String(100))
    engine_count: Mapped[int] = mapped_column(Integer)
    max_endurance_hours: Mapped[float] = mapped_column(Float)
    idle_limit_days: Mapped[int] = mapped_column(Integer)
    model_shape: Mapped[str] = mapped_column(String(32))


class BaseRow(FleetBase):
    __tablename__ = "base"
    base_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    name_he: Mapped[str] = mapped_column(String(100))


class AircraftRow(FleetBase):
    __tablename__ = "aircraft"
    aircraft_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tail_number: Mapped[str] = mapped_column(String(16), unique=True)
    serial_number: Mapped[str] = mapped_column(String(32))
    type_code: Mapped[str] = mapped_column(ForeignKey("aircraft_type.type_code"))
    base_code: Mapped[str] = mapped_column(ForeignKey("base.base_code"))
    status: Mapped[str] = mapped_column(String(16))
    manufacture_date: Mapped[date] = mapped_column(Date)
    last_flight_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    source_updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ActivityUnitRow(FleetBase):
    __tablename__ = "activity_unit"
    unit_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name_he: Mapped[str] = mapped_column(String(50))
    short_he: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))


class UsageCounterRow(FleetBase):
    __tablename__ = "usage_counter"
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.aircraft_id"), primary_key=True)
    unit_code: Mapped[str] = mapped_column(ForeignKey("activity_unit.unit_code"), primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    as_of: Mapped[datetime] = mapped_column(UTCDateTime)
    source_updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class FlightLogRow(FleetBase):
    __tablename__ = "flight_log"
    flight_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.aircraft_id"))
    mission_type: Mapped[str] = mapped_column(String(16))
    planned_duration_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    takeoff_at: Mapped[datetime] = mapped_column(UTCDateTime)
    landing_at: Mapped[datetime] = mapped_column(UTCDateTime)
    flight_hours: Mapped[float] = mapped_column(Float)
    landings: Mapped[int] = mapped_column(Integer)
    touch_and_goes: Mapped[int] = mapped_column(Integer, default=0)
    engine_starts: Mapped[int] = mapped_column(Integer, default=1)
    source_updated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_flight_log_aircraft_time", "aircraft_id", "takeoff_at"),)


class QualificationRow(FleetBase):
    __tablename__ = "qualification"
    qualification_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name_he: Mapped[str] = mapped_column(String(100))


class AircraftQualificationRow(FleetBase):
    __tablename__ = "aircraft_qualification"
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.aircraft_id"), primary_key=True)
    qualification_code: Mapped[str] = mapped_column(ForeignKey("qualification.qualification_code"), primary_key=True)
    valid_from: Mapped[datetime] = mapped_column(UTCDateTime)
    valid_to: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    source_updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class ZoneRow(FleetBase):
    __tablename__ = "zone"
    zone_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name_he: Mapped[str] = mapped_column(String(50))


class AtaZoneMapRow(FleetBase):
    __tablename__ = "ata_zone_map"
    ata_from: Mapped[int] = mapped_column(Integer, primary_key=True)
    ata_to: Mapped[int] = mapped_column(Integer, primary_key=True)
    zone_code: Mapped[str] = mapped_column(ForeignKey("zone.zone_code"))


class InspectionTypeRow(FleetBase):
    __tablename__ = "inspection_type"
    inspection_type_code: Mapped[str] = mapped_column(String(24), primary_key=True)
    name_he: Mapped[str] = mapped_column(String(100))
    zone_code: Mapped[str | None] = mapped_column(ForeignKey("zone.zone_code"), nullable=True)
    is_grounding_if_overdue: Mapped[bool] = mapped_column(Boolean, default=True)


class InspectionLimitRow(FleetBase):
    __tablename__ = "inspection_limit"
    inspection_type_code: Mapped[str] = mapped_column(
        ForeignKey("inspection_type.inspection_type_code"), primary_key=True)
    type_code: Mapped[str] = mapped_column(ForeignKey("aircraft_type.type_code"), primary_key=True)
    unit_code: Mapped[str] = mapped_column(ForeignKey("activity_unit.unit_code"), primary_key=True)
    interval_value: Mapped[float] = mapped_column(Float)
    tolerance_value: Mapped[float] = mapped_column(Float, default=0.0)
    tg_weight: Mapped[float] = mapped_column(Float, default=1.0)


class AircraftInspectionDueRow(FleetBase):
    __tablename__ = "aircraft_inspection_due"
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.aircraft_id"), primary_key=True)
    inspection_type_code: Mapped[str] = mapped_column(
        ForeignKey("inspection_type.inspection_type_code"), primary_key=True)
    unit_code: Mapped[str] = mapped_column(ForeignKey("activity_unit.unit_code"), primary_key=True)
    last_done_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_done_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    due_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    source_updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class InspectionEventRow(FleetBase):
    """Performed inspections (maintenance history) — used for ML features."""

    __tablename__ = "inspection_event"
    event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.aircraft_id"))
    inspection_type_code: Mapped[str] = mapped_column(ForeignKey("inspection_type.inspection_type_code"))
    performed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    fh_at: Mapped[float] = mapped_column(Float)
    source_updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class PermitTypeRow(FleetBase):
    __tablename__ = "permit_type"
    permit_type_code: Mapped[str] = mapped_column(String(24), primary_key=True)
    name_he: Mapped[str] = mapped_column(String(100))
    is_grounding: Mapped[bool] = mapped_column(Boolean, default=True)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True)


class AircraftPermitRow(FleetBase):
    __tablename__ = "aircraft_permit"
    permit_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.aircraft_id"))
    permit_type_code: Mapped[str] = mapped_column(ForeignKey("permit_type.permit_type_code"))
    permit_number: Mapped[str] = mapped_column(String(32))
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    limit_unit_code: Mapped[str | None] = mapped_column(ForeignKey("activity_unit.unit_code"), nullable=True)
    limit_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    restrictions: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    source_updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class MaintenanceActionRow(FleetBase):
    __tablename__ = "maintenance_action"
    ma_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ma_number: Mapped[str] = mapped_column(String(24), unique=True)
    aircraft_id: Mapped[int] = mapped_column(ForeignKey("aircraft.aircraft_id"))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    ata_chapter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    zone_code: Mapped[str | None] = mapped_column(ForeignKey("zone.zone_code"), nullable=True)
    severity: Mapped[str] = mapped_column(String(16))
    mel_category: Mapped[str | None] = mapped_column(String(1), nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    deferral_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    deferral_limit_unit: Mapped[str | None] = mapped_column(ForeignKey("activity_unit.unit_code"), nullable=True)
    deferral_limit_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    affects_qualification_code: Mapped[str | None] = mapped_column(
        ForeignKey("qualification.qualification_code"), nullable=True)
    source_updated_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_ma_aircraft_status", "aircraft_id", "status"),)


class DataLoadStatusRow(FleetBase):
    __tablename__ = "data_load_status"
    source_name: Mapped[str] = mapped_column(String(50), primary_key=True)
    loaded_at: Mapped[datetime] = mapped_column(UTCDateTime)


# ───────────────────────── App store (read/write) ─────────────────────────

class AppUserRow(AppBase):
    __tablename__ = "app_user"
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    oidc_subject: Mapped[str | None] = mapped_column(String(300), nullable=True, unique=True, index=True)  # iss|sub
    role: Mapped[str] = mapped_column(String(24))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)


class PlanningPolicyRow(AppBase):
    __tablename__ = "planning_policy"
    policy_key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("app_user.user_id"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class WeightPresetRow(AppBase):
    __tablename__ = "weight_preset"
    preset_code: Mapped[str] = mapped_column(String(24), primary_key=True)
    weights_json: Mapped[str] = mapped_column(Text)


class HangarCapacityRow(AppBase):
    __tablename__ = "hangar_capacity"
    base_code: Mapped[str] = mapped_column(String(8), primary_key=True)
    iso_week: Mapped[str] = mapped_column(String(8), primary_key=True)
    slots: Mapped[int] = mapped_column(Integer)


class MissionRow(AppBase):
    __tablename__ = "mission"
    mission_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    mission_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    required_aircraft_count: Mapped[int] = mapped_column(Integer)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime)
    est_duration_hours: Mapped[float] = mapped_column(Float)
    est_landings: Mapped[int] = mapped_column(Integer)
    est_touch_and_goes: Mapped[int] = mapped_column(Integer, default=0)
    est_engine_starts: Mapped[int] = mapped_column(Integer, default=1)
    departure_base_code: Mapped[str] = mapped_column(String(8))
    engine_requirement: Mapped[str] = mapped_column(String(8))
    same_type_required: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    overrides_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("app_user.user_id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime)


class MissionQualificationRow(AppBase):
    __tablename__ = "mission_qualification"
    mission_id: Mapped[int] = mapped_column(ForeignKey("mission.mission_id", ondelete="CASCADE"), primary_key=True)
    qualification_code: Mapped[str] = mapped_column(String(16), primary_key=True)


class MissionForceRow(AppBase):
    __tablename__ = "mission_force"
    mission_id: Mapped[int] = mapped_column(ForeignKey("mission.mission_id", ondelete="CASCADE"), primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(8))


class OptimizationRunRow(AppBase):
    __tablename__ = "optimization_run"
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("mission.mission_id"))
    requested_by: Mapped[int] = mapped_column(ForeignKey("app_user.user_id"))
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime)
    clock_now: Mapped[datetime] = mapped_column(UTCDateTime)
    data_snapshot_at: Mapped[datetime] = mapped_column(UTCDateTime)
    policy_json: Mapped[str] = mapped_column(Text)
    weights_json: Mapped[str] = mapped_column(Text)
    model_versions_json: Mapped[str] = mapped_column(Text)
    solver_status: Mapped[str] = mapped_column(String(16))
    objective_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_aircraft: Mapped[int] = mapped_column(Integer)
    n_eligible: Mapped[int] = mapped_column(Integer)
    shortfall: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str] = mapped_column(Text)  # full OptimizationRun DTO for exact replay


class OptimizationSetRow(AppBase):
    __tablename__ = "optimization_set"
    run_id: Mapped[str] = mapped_column(ForeignKey("optimization_run.run_id", ondelete="CASCADE"), primary_key=True)
    set_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    objective_value: Mapped[float] = mapped_column(Float)
    aircraft_ids: Mapped[str] = mapped_column(Text)


class OptimizationCandidateRow(AppBase):
    __tablename__ = "optimization_candidate"
    run_id: Mapped[str] = mapped_column(ForeignKey("optimization_run.run_id", ondelete="CASCADE"), primary_key=True)
    aircraft_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    eligible: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasons_json: Mapped[str] = mapped_column(Text)
    warnings_json: Mapped[str] = mapped_column(Text)
    unblock_json: Mapped[str] = mapped_column(Text)
    projections_json: Mapped[str] = mapped_column(Text)


class MissionAssignmentRow(AppBase):
    __tablename__ = "mission_assignment"
    assignment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("mission.mission_id"))
    aircraft_id: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("optimization_run.run_id"), nullable=True)
    set_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    window_start: Mapped[datetime] = mapped_column(UTCDateTime)
    window_end: Mapped[datetime] = mapped_column(UTCDateTime)
    status: Mapped[str] = mapped_column(String(16))
    assigned_by: Mapped[int] = mapped_column(ForeignKey("app_user.user_id"))
    assigned_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (Index("ix_assignment_aircraft_window", "aircraft_id", "window_start", "window_end"),)


class AuditLogRow(AppBase):
    __tablename__ = "audit_log"
    audit_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("app_user.user_id"), nullable=True)
    action: Mapped[str] = mapped_column(String(50))
    entity_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)


# SECURITY.md §9 — the audit log is append-only, enforced by the database as well as by the code.
AUDIT_APPEND_ONLY_DDL = {
    "sqlite": (
        "CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END",
        "CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END",
    ),
    "postgresql": (
        "CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END $$",
        "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()",
    ),
}
for _dialect, _statements in AUDIT_APPEND_ONLY_DDL.items():
    for _statement in _statements:
        event.listen(AuditLogRow.__table__, "after_create",
                     DDL(_statement).execute_if(dialect=_dialect))  # type: ignore[no-untyped-call]


class MlModelRow(AppBase):
    __tablename__ = "ml_model"
    model_name: Mapped[str] = mapped_column(String(50), primary_key=True)
    version: Mapped[str] = mapped_column(String(20), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    trained_at: Mapped[datetime] = mapped_column(UTCDateTime)
    metrics_json: Mapped[str] = mapped_column(Text)
    artifact_path: Mapped[str] = mapped_column(String(300))
    data_hash: Mapped[str] = mapped_column(String(64))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
