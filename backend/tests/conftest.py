"""Shared fixtures: deterministic seeded DB at T0, frozen clock, API client (TESTING.md §2)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.clock import FrozenClock, parse_utc
from app.config import Settings
from app.datasources.poc_sqlite import PocSqliteSource
from app.db.session import session_factory
from app.domain.models import Aircraft, EvaluationContext
from app.domain.policy import PlanningPolicy
from app.main import create_app
from app.repositories.store import active_commitments
from app.seed.generator import generate

T0 = parse_utc("2026-01-15T08:00:00Z")
TEST_PASSWORD = "test-only-password-123"


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("db") / "golden.sqlite"
    generate(path, T0, 42, TEST_PASSWORD)
    return path


@dataclass
class Fleet:
    aircraft: dict[str, Aircraft]
    ctx: EvaluationContext
    policy: PlanningPolicy


@pytest.fixture(scope="session")
def fleet(seeded_db: Path) -> Fleet:
    sf = session_factory(f"sqlite:///{seeded_db}")
    snap = PocSqliteSource(sf).load_snapshot()
    with sf() as s:
        commitments = active_commitments(s)
    aircraft = {a.tail_number: replace(a, commitments=tuple(commitments.get(a.aircraft_id, ())))
                for a in snap.aircraft}
    ctx = EvaluationContext(now=T0, required_permit_types=snap.required_permit_types,
                            data_load_status=dict(snap.data_load_status))
    return Fleet(aircraft, ctx, PlanningPolicy())


@pytest.fixture
def db_copy(seeded_db: Path, tmp_path: Path) -> Path:
    target = tmp_path / "work.sqlite"
    shutil.copy(seeded_db, target)
    return target


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(T0)


@pytest.fixture
def settings(db_copy: Path, tmp_path: Path) -> Settings:
    return Settings(sqlite_path=str(db_copy), models_dir=str(tmp_path / "models"), demo_password=TEST_PASSWORD,
                    log_level="WARNING", frozen_now=None, env="test")


@pytest.fixture
def client(settings: Settings, clock: FrozenClock) -> Iterator[TestClient]:
    app = create_app(settings, clock=clock)
    with TestClient(app) as c:
        yield c


def login(c: TestClient, username: str) -> dict[str, str]:
    r = c.post("/api/v1/auth/login", json={"username": username, "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": c.cookies.get("ao_csrf") or ""}
