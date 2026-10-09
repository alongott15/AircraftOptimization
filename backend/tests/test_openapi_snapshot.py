"""The committed OpenAPI document (source of the frontend's generated types) must match the live API (TESTING.md §3.5).

If this fails after an intentional DTO change, run `make gen-api` and commit frontend/openapi.json + schema.d.ts.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.main import create_app

SNAPSHOT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def test_openapi_matches_committed_snapshot() -> None:
    live = create_app(Settings(log_level="WARNING")).openapi()
    committed = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert live == committed, "OpenAPI changed — run `make gen-api` to regenerate frontend types"
