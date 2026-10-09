"""Runtime ML access with neutral fallbacks (FR-53). Never raises into the optimization path."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import structlog
from sqlalchemy.orm import Session, sessionmaker

from app.datasources.base import FleetDataSource
from app.domain.models import Aircraft, Unit
from app.ml.features import RISK_FEATURES, AircraftMeta, History, duration_features, risk_features
from app.ml.registry import ArtifactIntegrityError, load_artifact
from app.ml.utilization import Utilization, compute_utilization
from app.repositories import store

log = structlog.get_logger()
HISTORY_TTL_S = 60.0


@dataclass(frozen=True, slots=True)
class RiskInfo:
    probability: float
    band: str
    top_factors: tuple[dict[str, str], ...]


RISK_BAND_LOW, RISK_BAND_HIGH = 0.15, 0.30  # relative to the ~20 % fleet base rate (OPT §4.1)


def risk_band(p: float) -> str:
    return "LOW" if p < RISK_BAND_LOW else "MEDIUM" if p < RISK_BAND_HIGH else "HIGH"


class MlProvider:
    def __init__(self, ds: FleetDataSource, sf: sessionmaker[Session], models_dir: Path) -> None:
        self._ds, self._sf, self._dir = ds, sf, models_dir
        self._lock = threading.Lock()
        self._models: dict[str, tuple[str, dict[str, Any]] | None] = {}
        self._history: tuple[float, History] | None = None

    # ── models ──────────────────────────────────────────────────────────
    def _model(self, name: str) -> tuple[str, dict[str, Any]] | None:
        with self._sf() as s:
            row = store.active_model(s, name)
        if row is None:
            return None
        cached = self._models.get(name)
        if cached is not None and cached[0] == row.version:
            return cached
        try:
            payload = load_artifact(row, self._dir)
        except (ArtifactIntegrityError, OSError, ValueError) as exc:
            log.warning("ml.model_unavailable", model=name, error=str(exc))
            self._models[name] = None
            return None
        self._models[name] = (row.version, payload)
        return self._models[name]

    def model_versions(self) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        for name in ("defect_risk", "duration_buffer"):
            m = self._model(name)
            out[name] = m[0] if m else None
        out["utilization"] = "ewma-online"
        return out

    def invalidate(self) -> None:
        with self._lock:
            self._models.clear()
            self._history = None

    # ── history (cached) ───────────────────────────────────────────────
    def _history_for(self, aircraft: tuple[Aircraft, ...], now: datetime) -> History:
        with self._lock:
            if self._history and time.monotonic() - self._history[0] < HISTORY_TTL_S:
                return self._history[1]
        meta = {}
        for a in aircraft:
            fh = a.counter(Unit.FH)
            meta[a.aircraft_id] = AircraftMeta(a.aircraft_id, a.aircraft_type.type_code, a.base_code,
                                               a.manufacture_date, fh.value if fh else None)
        history = History.build(meta, self._ds.flights_since(now - timedelta(days=400)),
                                self._ds.maintenance_history(), self._ds.inspection_events())
        with self._lock:
            self._history = (time.monotonic(), history)
        return history

    # ── predictions ────────────────────────────────────────────────────
    def predict_risk(self, aircraft: tuple[Aircraft, ...], now: datetime) -> dict[int, RiskInfo] | None:
        model = self._model("defect_risk")
        if model is None:
            return None
        payload = model[1]
        try:
            h = self._history_for(aircraft, now)
            ids = [a.aircraft_id for a in aircraft]
            x = np.array([risk_features(h, a, now) for a in ids])
            probs = payload["model"].predict_proba(x)[:, 1]
        except Exception as exc:
            log.warning("ml.predict_failed", model="defect_risk", error=str(exc))
            return None
        means, stds = payload["train_means"], payload["train_stds"]
        importances: dict[str, float] = payload["importances"]
        out: dict[int, RiskInfo] = {}
        for i, aid in enumerate(ids):
            p = float(probs[i])
            if math.isnan(p):
                continue
            p = min(max(p, 0.0), 1.0)
            factors = []
            for j, name in enumerate(RISK_FEATURES):
                v = x[i, j]
                if math.isnan(v) or not stds[j] or name.startswith(("type_", "base_")):
                    continue
                z = (v - means[j]) / stds[j]
                factors.append((max(importances.get(name, 0.0), 0.0) * abs(z), name, "HIGH" if z > 0 else "LOW"))
            factors.sort(reverse=True)
            top = tuple({"feature_code": n, "direction": d} for w, n, d in factors[:3] if w > 0)
            out[aid] = RiskInfo(round(p, 4), risk_band(p), top)
        return out

    def utilization(self, aircraft: tuple[Aircraft, ...], now: datetime) -> Utilization:
        flights = self._ds.flights_since(now - timedelta(days=90))
        types = {a.aircraft_id: a.aircraft_type.type_code for a in aircraft}
        engines = {a.aircraft_id: a.aircraft_type.engine_count for a in aircraft}
        return compute_utilization(flights, now, types, engines)

    def duration_suggestion(self, mission_type: str, type_code: str, duration_hours: float, landings: int,
                            touch_and_goes: int, month: int, policy_pct: float) -> dict[str, Any] | None:
        model = self._model("duration_buffer")
        if model is None:
            return None
        version, payload = model
        x = np.array([duration_features(mission_type, type_code, duration_hours, landings, touch_and_goes, month)])
        p50 = float(payload["p50"].predict(x)[0])
        p90 = float(payload["p90"].predict(x)[0])
        suggested = max(policy_pct, (p90 - 1.0) * 100.0)
        return {"suggested_buffer_pct": round(suggested, 1), "p50_ratio": round(p50, 3), "p90_ratio": round(p90, 3),
                "model_version": version}
