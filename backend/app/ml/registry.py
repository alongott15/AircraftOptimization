"""Model artifacts + registry with SHA-256 verification (SECURITY.md §8).

joblib/pickle loading executes code, so an artifact is loaded only if it lives under APP_MODELS_DIR and its
SHA-256 matches the registry row.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import joblib
from sqlalchemy.orm import Session

from app.db import orm
from app.repositories import store


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save_model(session: Session, models_dir: Path, name: str, payload: dict[str, Any], metrics: dict[str, Any],
               data_hash: str, trained_at: datetime, card: dict[str, Any]) -> orm.MlModelRow:
    day = trained_at.strftime("%Y.%m.%d")
    existing = {r.version for r in store.list_models(session) if r.model_name == name}
    seq = 1
    while f"{day}-{seq}" in existing or (models_dir / name / f"{day}-{seq}").exists():
        seq += 1
    version = f"{day}-{seq}"
    folder = models_dir / name / version
    folder.mkdir(parents=True, exist_ok=True)
    artifact = folder / "model.joblib"
    joblib.dump(payload, artifact)
    (folder / "model_card.json").write_text(
        json.dumps({**card, "model_name": name, "version": version, "metrics": metrics, "data_hash": data_hash},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    row = orm.MlModelRow(model_name=name, version=version, status="ACTIVE", trained_at=trained_at,
                         metrics_json=json.dumps(metrics), artifact_path=str(artifact.relative_to(models_dir)),
                         data_hash=data_hash, artifact_sha256=sha256_file(artifact))
    store.register_model(session, row)
    return row


class ArtifactIntegrityError(RuntimeError):
    pass


def load_artifact(row: orm.MlModelRow, models_dir: Path) -> dict[str, Any]:
    root = models_dir.resolve()
    path = (root / row.artifact_path).resolve()
    if root not in path.parents:
        raise ArtifactIntegrityError("artifact path escapes models directory")
    if not path.exists():
        raise ArtifactIntegrityError("artifact missing")
    data = path.read_bytes()  # hash and deserialize the same bytes (no swap between check and load)
    if hashlib.sha256(data).hexdigest() != row.artifact_sha256:
        raise ArtifactIntegrityError("artifact SHA-256 mismatch")
    payload = joblib.load(io.BytesIO(data))
    if not isinstance(payload, dict):
        raise ArtifactIntegrityError("unexpected artifact format")
    return payload
