"""Production data source: read-only Vertica canonical views `<schema>.ao_v_*` (DATABASE.md §7, SECURITY.md §7).

DSN: vertica://<user>:<password>@<host>:5433/<database>?schema=aircraft_opt&tlsmode=require
Install the driver with `uv sync --extra vertica`.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from app.datasources.base import FleetSnapshot, FlightRecord, InspectionEventRecord, MaRecord
from app.datasources.sql_views import SqlViewSource, column_names

SESSION_SETUP = (
    "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY",
    "SET SESSION RUNTIMECAP '30 seconds'",
)


def parse_dsn(dsn: str, require_verified_tls: bool = False) -> tuple[dict[str, Any], str]:
    url = urlparse(dsn)
    if url.scheme != "vertica":
        raise ValueError("APP_VERTICA_DSN must start with vertica://")
    query = {k: v[-1] for k, v in parse_qs(url.query).items()}
    options: dict[str, Any] = {
        "host": url.hostname or "localhost",
        "port": url.port or 5433,
        "user": unquote(url.username or ""),
        "password": unquote(url.password or ""),
        "database": url.path.lstrip("/"),
        "session_label": "aircraft_opt",
        "connection_timeout": int(query.get("connect_timeout", "10")),
        "autocommit": True,
    }
    tlsmode = query.get("tlsmode", "")
    if require_verified_tls and tlsmode not in ("verify-ca", "verify-full"):
        raise ValueError("production requires tlsmode=verify-ca or verify-full in APP_VERTICA_DSN")
    if tlsmode:
        options["tlsmode"] = tlsmode
    if query.get("tls_cafile"):
        options["tls_cafile"] = query["tls_cafile"]
    return options, query.get("schema", "aircraft_opt")


class VerticaRunner:
    """One shared read-only connection; reconnects once on failure. Never logs the DSN (it contains a secret)."""

    def __init__(self, options: Mapping[str, Any]) -> None:
        self._options = dict(options)
        self._conn: Any = None
        self._lock = threading.Lock()

    def _connect(self) -> Any:
        import vertica_python  # optional extra: uv sync --extra vertica

        conn = vertica_python.connect(**self._options)
        cur = conn.cursor()
        for statement in SESSION_SETUP:
            cur.execute(statement)
        return conn

    def fetch(self, sql: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None:
                        self._conn = self._connect()
                    cur = self._conn.cursor("dict")
                    cur.execute(sql, dict(params or {}))
                    return list(cur.fetchall())
                except Exception:
                    self._close()
                    if attempt == 2:
                        raise
        return []

    def bind_datetime(self, value: datetime) -> Any:
        return value

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None


class VerticaSource:
    def __init__(self, dsn: str, require_verified_tls: bool = False) -> None:
        options, schema = parse_dsn(dsn, require_verified_tls)
        self._runner = VerticaRunner(options)
        self._schema = schema
        self._views = SqlViewSource(self._runner, schema=schema)

    def load_snapshot(self) -> FleetSnapshot:
        return self._views.load_snapshot()

    def load_reference(self) -> dict[str, Any]:
        return self._views.load_reference()

    def flights_since(self, since: datetime) -> list[FlightRecord]:
        return self._views.flights_since(since)

    def maintenance_history(self) -> list[MaRecord]:
        return self._views.maintenance_history()

    def inspection_events(self) -> list[InspectionEventRecord]:
        return self._views.inspection_events()

    def view_columns(self, view: str) -> Sequence[str]:
        """Used by the contract test against a real Vertica."""
        return column_names(self._runner, self._schema, view)
