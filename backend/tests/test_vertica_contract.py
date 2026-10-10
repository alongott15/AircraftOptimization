"""Canonical view contract (DATABASE.md §7).

1. On SQLite: `ao_v_*` views over the golden DB → SqlViewSource must return exactly what PocSqliteSource returns.
2. On a real Vertica (only when APP_VERTICA_DSN is set): every view exists with the expected columns.
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect, text

from app.datasources.poc_sqlite import PocSqliteSource
from app.datasources.sql_views import VIEWS, SqlAlchemyRunner, SqlViewSource
from app.db.orm import FleetBase
from app.db.session import session_factory
from app.domain.compliance.evaluator import evaluate_eligibility
from app.domain.models import EvaluationContext, MissionRequest, MissionType, OperationalStatus, Severity, Unit
from app.domain.policy import PlanningPolicy
from tests.conftest import T0


@pytest.fixture(scope="module")
def view_db(seeded_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("views") / "views.sqlite"
    shutil.copy(seeded_db, path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        for view in VIEWS:
            conn.execute(text(f"CREATE VIEW ao_v_{view} AS SELECT * FROM {view}"))
    engine.dispose()
    return path


@pytest.fixture(scope="module")
def sources(view_db: Path) -> tuple[PocSqliteSource, SqlViewSource]:
    url = f"sqlite:///{view_db}"
    return PocSqliteSource(session_factory(url)), SqlViewSource(SqlAlchemyRunner(create_engine(url)), schema="")


def test_views_cover_every_fleet_table() -> None:
    assert set(VIEWS) == set(FleetBase.metadata.tables)


def test_snapshot_is_identical(sources: tuple[PocSqliteSource, SqlViewSource]) -> None:
    poc, views = sources
    assert views.load_snapshot() == poc.load_snapshot()


def test_reference_and_history_are_identical(sources: tuple[PocSqliteSource, SqlViewSource]) -> None:
    poc, views = sources
    assert views.load_reference() == poc.load_reference()
    since = T0.replace(month=1, day=1)
    assert views.flights_since(since) == poc.flights_since(since)
    assert views.maintenance_history() == poc.maintenance_history()
    assert views.inspection_events() == poc.inspection_events()


def test_unknown_view_is_rejected(sources: tuple[PocSqliteSource, SqlViewSource]) -> None:
    with pytest.raises(ValueError, match="unknown view"):
        sources[1]._v("app_user")  # app-store tables are never read through the lake adapter


@pytest.mark.skipif(not os.environ.get("APP_VERTICA_DSN"), reason="needs APP_VERTICA_DSN (real Vertica)")
def test_real_vertica_views_have_expected_columns() -> None:
    from app.datasources.vertica import VerticaSource

    source = VerticaSource(os.environ["APP_VERTICA_DSN"])
    expected = {name: {c.name for c in table.columns} for name, table in FleetBase.metadata.tables.items()}
    for view in VIEWS:
        cols = set(source.view_columns(view))
        assert expected[view] <= cols, f"ao_v_{view} missing {expected[view] - cols}"


def test_sqlite_inspector_sees_views(view_db: Path) -> None:
    names = set(inspect(create_engine(f"sqlite:///{view_db}")).get_view_names())
    assert {f"ao_v_{v}" for v in VIEWS} <= names


# ── defensive parsing: a lake row can be NULL/NaN/garbage; it must never read as "OK" (S1) ────────────────
class MutatingRunner:
    """Wraps a real runner and lets a test corrupt the rows of individual views."""

    def __init__(self, inner: SqlAlchemyRunner, mutate: dict[str, Callable[[list[dict[str, Any]]], None]]) -> None:
        self._inner, self._mutate = inner, mutate

    def fetch(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        rows = self._inner.fetch(sql, params)
        view = re.search(r"ao_v_(\w+)", sql)
        if view and view.group(1) in self._mutate:
            self._mutate[view.group(1)](rows)
        return rows

    def bind_datetime(self, value: datetime) -> Any:
        return self._inner.bind_datetime(value)


@pytest.fixture(scope="module")
def dirty_db(view_db: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Views with an MA whose status is NULL and severity unknown (NOT NULL constraints don't bind a view)."""
    path = tmp_path_factory.mktemp("dirty") / "dirty.sqlite"
    shutil.copy(view_db, path)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE ma_extra AS SELECT * FROM maintenance_action WHERE 0"))
        conn.execute(text(
            "INSERT INTO ma_extra (ma_id, ma_number, aircraft_id, title, severity, status, opened_at, "
            "source_updated_at) SELECT ma.ma_id + 100000, 'MA-NULL-STATUS', ma.aircraft_id, 'x', 'BOGUS', NULL, "
            "ma.opened_at, ma.source_updated_at "
            "FROM maintenance_action ma JOIN aircraft a ON a.aircraft_id = ma.aircraft_id "
            "WHERE a.tail_number = '4X-S01' LIMIT 1"))
        conn.execute(text("DROP VIEW ao_v_maintenance_action"))
        conn.execute(text("CREATE VIEW ao_v_maintenance_action AS SELECT * FROM maintenance_action "
                          "UNION ALL SELECT * FROM ma_extra"))
    engine.dispose()
    return path


def _set(view_rows: list[dict[str, Any]], match: dict[str, Any], **changes: Any) -> None:
    for row in view_rows:
        if all(row.get(k) == v for k, v in match.items()):
            row.update(changes)


def test_dirty_lake_rows_fail_safe(dirty_db: Path) -> None:
    engine = create_engine(f"sqlite:///{dirty_db}")
    tails = {r["tail_number"]: r["aircraft_id"] for r in SqlAlchemyRunner(engine).fetch("SELECT * FROM aircraft")}
    def add_apu_limit(rows: list[dict[str, Any]]) -> None:
        rows.append({**next(r for r in rows if r["type_code"] == "TP-200"), "inspection_type_code": "APU500",
                     "unit_code": "APU_HR"})

    def duplicate_counter(rows: list[dict[str, Any]]) -> None:
        rows.append(dict(next(r for r in rows if r["aircraft_id"] == tails["4X-P01"] and r["unit_code"] == "FH")))

    runner = MutatingRunner(SqlAlchemyRunner(engine), {
        "aircraft_type": lambda rows: _set(rows, {"type_code": "JT-300"}, engine_count=0),
        "inspection_limit": add_apu_limit,
        "aircraft_permit": lambda rows: _set(rows, {"aircraft_id": tails["4X-P02"], "permit_type_code": "COA"},
                                             status="SUSPENDED"),
        "permit_type": lambda rows: _set(rows, {"permit_type_code": "INSURANCE"}, is_grounding=None, is_required=None),
        "aircraft": lambda rows: (_set(rows, {"tail_number": "4X-S02"}, type_code="ZZ-999"),
                                  _set(rows, {"tail_number": "4X-S04"}, status="bogus"),
                                  _set(rows, {"tail_number": "4X-S01"}, squadron_code=" SQ-11 "),
                                  _set(rows, {"tail_number": "4X-S06"}, squadron_code="  ")),
        "usage_counter": lambda rows: (_set(rows, {"aircraft_id": tails["4X-S06"], "unit_code": "FH"},
                                            value=float("nan")), duplicate_counter(rows)),
        "data_load_status": lambda rows: rows.__setitem__(slice(None), [r for r in rows
                                                                       if r["source_name"] != "permits"]),
    })
    snap = SqlViewSource(runner, schema="").load_snapshot()
    by_tail = {a.tail_number: a for a in snap.aircraft}
    assert "INSURANCE" in snap.required_permit_types
    assert len(snap.aircraft) == len(tails)  # an unknown type never makes an aircraft vanish
    assert ("AIRCRAFT_TYPE", "ZZ-999") in by_tail["4X-S02"].data_issues
    assert by_tail["4X-S04"].status == OperationalStatus.GROUNDED
    assert ("AIRCRAFT_STATUS", "bogus") in by_tail["4X-S04"].data_issues
    assert Unit.FH not in by_tail["4X-S06"].counters
    assert by_tail["4X-S01"].squadron_code == "SQ-11" and by_tail["4X-S06"].squadron_code is None  # trimmed / blank
    assert ("INSPECTION_LIMIT", "APU500") in by_tail["4X-P03"].data_issues  # unknown unit: flagged, not dropped
    assert ("COUNTER", "FH") in by_tail["4X-P01"].data_issues  # duplicate rows: no "last one wins"
    assert ("PERMIT", "COA") in by_tail["4X-P02"].data_issues  # unknown permit status
    assert ("AIRCRAFT_TYPE", "JT-300") in by_tail["4X-J01"].data_issues  # engine_count 0 is invalid
    null_ma = next(m for m in by_tail["4X-S01"].maintenance_actions if m.ma_number == "MA-NULL-STATUS")
    assert null_ma.is_open and null_ma.severity == Severity.GROUNDING

    ctx = EvaluationContext(now=T0, required_permit_types=snap.required_permit_types,
                            data_load_status=snap.data_load_status)
    mission = MissionRequest(name="t", mission_type=MissionType.TRAINING, required_count=1,
                             start_at=T0 + timedelta(hours=2), duration_hours=1.0, landings=1, touch_and_goes=0,
                             engine_starts=1, departure_base_code="CEN")
    results = {t: evaluate_eligibility(by_tail[t], mission, PlanningPolicy(), ctx)
               for t in ("4X-S01", "4X-S02", "4X-S04", "4X-S06")}
    assert not any(r.eligible for r in results.values())
    assert "GROUNDING_MA_OPEN" in {str(r.code) for r in results["4X-S01"].reasons}
    assert all("DATA_STALE" in {str(r.code) for r in r.reasons} for r in results.values())  # permits feed missing
