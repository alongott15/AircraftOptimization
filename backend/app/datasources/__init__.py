"""Fleet data sources (read-only). The services never know whether SQLite or Vertica is active."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.datasources.base import FleetDataSource


def make_datasource(settings: Settings, session_factory: sessionmaker[Session]) -> FleetDataSource:
    if settings.data_source == "vertica":
        from app.datasources.vertica import VerticaSource

        if not settings.vertica_dsn:
            raise RuntimeError("APP_VERTICA_DSN is required when APP_DATA_SOURCE=vertica")
        return VerticaSource(settings.vertica_dsn, require_verified_tls=settings.env == "prod")
    from app.datasources.poc_sqlite import PocSqliteSource

    return PocSqliteSource(session_factory)
