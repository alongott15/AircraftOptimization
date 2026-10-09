"""Alembic manages exactly the app-store schema and stays in sync with the ORM (DATABASE.md §1)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app.db.orm import AppBase, FleetBase

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.attributes["url"] = url
    return cfg


def test_upgrade_creates_only_app_store_tables(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'store.sqlite'}"
    command.upgrade(_config(url), "head")
    tables = set(inspect(create_engine(url)).get_table_names()) - {"alembic_version"}
    assert tables == set(AppBase.metadata.tables)
    assert not tables & set(FleetBase.metadata.tables)


def test_migrations_match_orm(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'store.sqlite'}"
    command.upgrade(_config(url), "head")
    with create_engine(url).connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), AppBase.metadata)
    assert diff == [], f"ORM and migrations drifted — run `make migration`: {diff}"


def test_downgrade_to_base_is_clean(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'store.sqlite'}"
    cfg = _config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    assert set(inspect(create_engine(url)).get_table_names()) <= {"alembic_version"}


def test_app_store_has_no_foreign_keys_into_fleet_tables() -> None:
    fleet = set(FleetBase.metadata.tables)
    for table in AppBase.metadata.tables.values():
        for fk in table.foreign_keys:
            assert fk.column.table.name not in fleet, f"{table.name} → {fk.column.table.name}"
