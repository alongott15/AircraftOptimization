"""ML tests: leakage, metrics, registry integrity, fallback (TESTING.md §3.4)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

from app.datasources.base import FlightRecord
from app.datasources.poc_sqlite import PocSqliteSource
from app.db.session import session_factory, session_scope
from app.ml.features import RISK_FEATURES, History, risk_features
from app.ml.provider import MlProvider
from app.ml.registry import ArtifactIntegrityError, load_artifact, save_model
from app.ml.train import build_history, train_defect_risk, train_duration_buffer
from app.ml.utilization import compute_utilization, ewma
from app.repositories import store
from tests.conftest import T0


@pytest.fixture(scope="module")
def history(seeded_db: Path) -> History:
    return build_history(PocSqliteSource(session_factory(f"sqlite:///{seeded_db}")), T0)


def test_features_ignore_future_events(history: History) -> None:
    aid = 1
    t = T0 - timedelta(days=120)
    before = risk_features(history, aid, t)
    future = FlightRecord(aid, "TRAINING", 1.0, t + timedelta(days=1), t + timedelta(days=1, hours=2), 2.0, 1, 5, 1)
    flights = [*history.flights[aid], future]
    meta = {aid: replace(history.meta[aid], fh_now=(history.meta[aid].fh_now or 0) + 2.0)}
    h2 = History.build(meta, flights, history.mas.get(aid, []), history.events.get(aid, []))
    after = risk_features(h2, aid, t)
    assert len(before) == len(RISK_FEATURES)
    assert [round(x, 6) if x == x else None for x in before] == [round(x, 6) if x == x else None for x in after]


def test_defect_risk_meets_acceptance(history: History) -> None:
    _, metrics, _, _ = train_defect_risk(history, T0)
    assert metrics["roc_auc"] >= 0.70
    assert metrics["brier"] <= 0.20


def test_duration_buffer_p90_coverage(history: History) -> None:
    _, metrics, _, _ = train_duration_buffer(history, T0)
    assert 0.85 <= metrics["p90_coverage"] <= 0.95


def test_ewma_and_shrinkage() -> None:
    assert ewma([1.0] * 10) == pytest.approx(1.0)
    assert ewma([0.0] * 9 + [10.0]) > ewma([10.0] + [0.0] * 9)
    util = compute_utilization([], T0, {1: "SE-100", 2: "SE-100"}, {1: 1, 2: 1})
    assert util.rates[1]["FH"] == 0.0


def test_registry_rejects_tampered_artifact(seeded_db: Path, tmp_path: Path, history: History) -> None:
    payload, metrics, data_hash, card = train_duration_buffer(history, T0)
    url = f"sqlite:///{seeded_db}"
    with session_scope(url) as s:
        row = save_model(s, tmp_path, "duration_buffer", payload, metrics, data_hash, T0, card)
        s.flush()
        assert load_artifact(row, tmp_path)["feature_names"]
        (tmp_path / row.artifact_path).write_bytes(b"tampered")
        with pytest.raises(ArtifactIntegrityError):
            load_artifact(row, tmp_path)
        s.rollback()


def test_provider_falls_back_without_models(seeded_db: Path, tmp_path: Path) -> None:
    sf = session_factory(f"sqlite:///{seeded_db}")
    with sf() as s:
        assert store.active_model(s, "defect_risk") is None
    provider = MlProvider(PocSqliteSource(sf), sf, tmp_path)
    snap = PocSqliteSource(sf).load_snapshot()
    assert provider.predict_risk(snap.aircraft, T0) is None
    assert provider.model_versions()["defect_risk"] is None
