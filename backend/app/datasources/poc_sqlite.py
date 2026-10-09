"""POC data source: reads the example DB tables (same schema as the Vertica canonical views)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.datasources.base import FleetSnapshot, FlightRecord, InspectionEventRecord, MaRecord
from app.datasources.sql_views import SqlAlchemyRunner, SqlViewSource
from app.db import orm


class PocSqliteSource:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def load_snapshot(self) -> FleetSnapshot:
        """Same defensive parser as production (sql_views.py), run over the POC tables — one code path, so a bad
        POC row fails safe exactly like a bad lake row (DATABASE.md §7)."""
        engine = self._sf.kw["bind"]
        return SqlViewSource(SqlAlchemyRunner(engine), schema="", view_prefix="").load_snapshot()

    def load_reference(self) -> dict[str, Any]:
        with self._sf() as s:
            limits: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for r in s.scalars(select(orm.InspectionLimitRow)):
                limits[r.inspection_type_code].append({
                    "type_code": r.type_code, "unit": r.unit_code, "interval": r.interval_value,
                    "tolerance": r.tolerance_value, "tg_weight": r.tg_weight})
            return {
                "aircraft_types": [
                    {"type_code": r.type_code, "name_he": r.name_he, "engine_count": r.engine_count,
                     "max_endurance_hours": r.max_endurance_hours, "idle_limit_days": r.idle_limit_days,
                     "model_shape": r.model_shape}
                    for r in s.scalars(select(orm.AircraftTypeRow).order_by(orm.AircraftTypeRow.type_code))],
                "bases": [{"base_code": r.base_code, "name_he": r.name_he}
                          for r in s.scalars(select(orm.BaseRow).order_by(orm.BaseRow.base_code))],
                "qualifications": [{"qualification_code": r.qualification_code, "name_he": r.name_he}
                                   for r in s.scalars(select(orm.QualificationRow))],
                "activity_units": [{"unit_code": r.unit_code, "name_he": r.name_he, "short_he": r.short_he,
                                    "kind": r.kind} for r in s.scalars(select(orm.ActivityUnitRow))],
                "inspection_types": [
                    {"inspection_type_code": r.inspection_type_code, "name_he": r.name_he, "zone_code": r.zone_code,
                     "limits": limits[r.inspection_type_code]}
                    for r in s.scalars(select(orm.InspectionTypeRow))],
                "permit_types": [{"permit_type_code": r.permit_type_code, "name_he": r.name_he,
                                  "is_grounding": r.is_grounding, "is_required": r.is_required}
                                 for r in s.scalars(select(orm.PermitTypeRow))],
                "zones": [{"zone_code": r.zone_code, "name_he": r.name_he} for r in s.scalars(select(orm.ZoneRow))],
            }

    def flights_since(self, since: datetime) -> list[FlightRecord]:
        with self._sf() as s:
            rows = s.scalars(select(orm.FlightLogRow).where(orm.FlightLogRow.takeoff_at >= since)
                             .order_by(orm.FlightLogRow.aircraft_id, orm.FlightLogRow.takeoff_at))
            return [FlightRecord(r.aircraft_id, r.mission_type, r.planned_duration_hours, r.takeoff_at, r.landing_at,
                                 r.flight_hours, r.landings, r.touch_and_goes, r.engine_starts) for r in rows]

    def maintenance_history(self) -> list[MaRecord]:
        with self._sf() as s:
            rows = s.scalars(select(orm.MaintenanceActionRow).order_by(orm.MaintenanceActionRow.opened_at))
            return [MaRecord(r.aircraft_id, r.severity, r.opened_at, r.closed_at) for r in rows]

    def inspection_events(self) -> list[InspectionEventRecord]:
        with self._sf() as s:
            rows = s.scalars(select(orm.InspectionEventRow).order_by(orm.InspectionEventRow.performed_at))
            return [InspectionEventRecord(r.aircraft_id, r.inspection_type_code, r.performed_at, r.fh_at)
                    for r in rows]
