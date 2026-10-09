"""Training entry point: `python -m app.ml.train --all` (OPTIMIZATION_ENGINE.md §4.4)."""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingRegressor, HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import brier_score_loss, roc_auc_score

from app.clock import SystemClock, parse_utc
from app.config import get_settings
from app.datasources.base import FleetDataSource
from app.datasources.poc_sqlite import PocSqliteSource
from app.db.session import session_factory, session_scope
from app.domain.models import Unit
from app.ml.features import (
    DURATION_FEATURES,
    RISK_FEATURES,
    AircraftMeta,
    History,
    duration_features,
    risk_features,
    risk_label,
)
from app.ml.registry import save_model

RANDOM_STATE = 42
TRAIN_FRACTION = 14 / 18  # time-based split: first 14 months train, last 4 validate


def build_history(ds: FleetDataSource, now: datetime) -> History:
    snapshot = ds.load_snapshot()
    meta = {}
    for a in snapshot.aircraft:
        fh = a.counter(Unit.FH)
        meta[a.aircraft_id] = AircraftMeta(a.aircraft_id, a.aircraft_type.type_code, a.base_code,
                                           a.manufacture_date, fh.value if fh else None)
    flights = ds.flights_since(now - timedelta(days=800))
    return History.build(meta, flights, ds.maintenance_history(), ds.inspection_events())


def _data_hash(rows: list[list[float]], labels: list[float]) -> str:
    digest = hashlib.sha256()
    digest.update(np.asarray(rows, dtype=float).tobytes())
    digest.update(np.asarray(labels, dtype=float).tobytes())
    return digest.hexdigest()


def train_defect_risk(h: History, now: datetime) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    first = min((fl[0].takeoff_at for fl in h.flights.values() if fl), default=now - timedelta(days=548))
    start, stop = first + timedelta(days=60), now - timedelta(days=30)
    split = first + (now - first) * TRAIN_FRACTION
    x_train: list[list[float]] = []
    x_val: list[list[float]] = []
    y_train: list[int] = []
    y_val: list[int] = []
    t = start
    while t <= stop:
        for a in sorted(h.meta):
            label = risk_label(h, a, t)
            if label is None:
                continue
            feats = risk_features(h, a, t)
            (x_train if t < split else x_val).append(feats)
            (y_train if t < split else y_val).append(label)
        t += timedelta(days=3)
    xt, yt, xv, yv = np.array(x_train), np.array(y_train), np.array(x_val), np.array(y_val)
    base = HistGradientBoostingClassifier(random_state=RANDOM_STATE, max_iter=200, learning_rate=0.05,
                                          max_leaf_nodes=15, l2_regularization=1.0)
    model = CalibratedClassifierCV(base, method="isotonic", cv=3)
    model.fit(xt, yt)
    pv = model.predict_proba(xv)[:, 1]
    auc = float(roc_auc_score(yv, pv)) if len(set(yv.tolist())) > 1 else float("nan")
    brier = float(brier_score_loss(yv, pv))
    imp = permutation_importance(model, xv, yv, scoring="roc_auc", n_repeats=5, random_state=RANDOM_STATE)
    importances = {name: float(v) for name, v in zip(RISK_FEATURES, imp.importances_mean, strict=True)}
    means = np.nanmean(xt, axis=0)
    stds = np.nanstd(xt, axis=0)
    payload = {"model": model, "feature_names": list(RISK_FEATURES), "importances": importances,
               "train_means": means.tolist(), "train_stds": stds.tolist()}
    metrics = {"roc_auc": round(auc, 4), "brier": round(brier, 4), "n_train": len(yt), "n_val": len(yv),
               "positive_rate_train": round(float(yt.mean()), 4), "positive_rate_val": round(float(yv.mean()), 4)}
    card = {"target": "grounding MA within next 50 FH (or 30 days)", "features": list(RISK_FEATURES),
            "train_window": [start.isoformat(), split.isoformat()], "validation_window": [split.isoformat(),
                                                                                          stop.isoformat()]}
    return payload, metrics, _data_hash(x_train + x_val, [*y_train, *y_val]), card


def train_duration_buffer(h: History, now: datetime) -> tuple[dict[str, Any], dict[str, Any], str, dict[str, Any]]:
    first = min((fl[0].takeoff_at for fl in h.flights.values() if fl), default=now - timedelta(days=548))
    split = first + (now - first) * TRAIN_FRACTION
    xs_t: list[list[float]] = []
    xs_v: list[list[float]] = []
    ys_t: list[float] = []
    ys_v: list[float] = []
    for a, flights in sorted(h.flights.items()):
        type_code = h.meta[a].type_code
        for f in flights:
            if not f.planned_duration_hours or f.planned_duration_hours <= 0:
                continue
            feats = duration_features(f.mission_type, type_code, f.planned_duration_hours, f.landings,
                                      f.touch_and_goes, f.takeoff_at.month)
            ratio = f.flight_hours / f.planned_duration_hours
            (xs_t if f.takeoff_at < split else xs_v).append(feats)
            (ys_t if f.takeoff_at < split else ys_v).append(ratio)
    xt, yt, xv, yv = np.array(xs_t), np.array(ys_t), np.array(xs_v), np.array(ys_v)
    models = {}
    for name, alpha in (("p50", 0.5), ("p90", 0.9)):
        m = GradientBoostingRegressor(loss="quantile", alpha=alpha, n_estimators=150, max_depth=3,
                                      learning_rate=0.05, random_state=RANDOM_STATE)
        m.fit(xt, yt)
        models[name] = m
    p90 = models["p90"].predict(xv)
    coverage = float(np.mean(yv <= p90)) if len(yv) else float("nan")
    payload = {"p50": models["p50"], "p90": models["p90"], "feature_names": list(DURATION_FEATURES)}
    metrics = {"p90_coverage": round(coverage, 4), "n_train": len(yt), "n_val": len(yv),
               "mean_ratio_val": round(float(yv.mean()), 4) if len(yv) else None}
    card = {"target": "actual / planned flight duration", "features": list(DURATION_FEATURES)}
    return payload, metrics, _data_hash(xs_t + xs_v, ys_t + ys_v), card


def main() -> None:
    parser = argparse.ArgumentParser(description="Train ML models on the example DB history")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--model", choices=["defect_risk", "duration_buffer"])
    args = parser.parse_args()
    settings = get_settings()
    now = parse_utc(settings.frozen_now) if settings.frozen_now else SystemClock().now()
    ds = PocSqliteSource(session_factory())
    # Use the data's own "now" so a DB seeded earlier still trains on its full history.
    load = ds.load_snapshot().data_load_status
    if load:
        now = min(now, max(load.values()))
    history = build_history(ds, now)
    targets = ["defect_risk", "duration_buffer"] if args.all or not args.model else [args.model]
    models_dir = Path(settings.models_dir)
    for name in targets:
        trainer = train_defect_risk if name == "defect_risk" else train_duration_buffer
        payload, metrics, data_hash, card = trainer(history, now)
        with session_scope() as s:
            row = save_model(s, models_dir, name, payload, metrics, data_hash, now, card)
        print(f"{name} {row.version}: {metrics}")


if __name__ == "__main__":
    main()
