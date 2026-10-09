"""Alembic environment — manages ONLY the app-store tables (AppBase). Fleet data is read-only (Vertica views)."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from app.config import get_settings
from app.db.orm import AppBase

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url = config.attributes.get("url") or get_settings().database_url
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
target_metadata = AppBase.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if isinstance(supplied, Connection):
        _run(supplied)
        return
    engine = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
