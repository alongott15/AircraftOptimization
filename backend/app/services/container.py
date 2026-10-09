"""Wiring of settings, clock, data source, ML provider and limiters (one per app instance)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.clock import Clock, FrozenClock, SystemClock, parse_utc
from app.config import Settings
from app.datasources import make_datasource
from app.datasources.base import FleetDataSource
from app.db.session import session_factory
from app.ml.provider import MlProvider
from app.security.oidc import OidcProvider
from app.security.ratelimit import LoginLockout, RateLimiter


@dataclass
class Container:
    settings: Settings
    clock: Clock
    sf: sessionmaker[Session]
    ds: FleetDataSource
    ml: MlProvider
    login_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(5, 60))
    optimize_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(10, 60))
    lockout: LoginLockout = field(default_factory=LoginLockout)
    oidc: OidcProvider | None = None
    ready_cache: tuple[float, dict[str, Any]] | None = None  # (monotonic time, /ready payload)
    # Serializes "re-validate eligibility → write assignments" so two concurrent assigns on the same tail cannot
    # both pass the cumulative/later-flight checks (single worker; production needs a DB-level lock as well).
    assign_lock: threading.Lock = field(default_factory=threading.Lock)


def build_container(settings: Settings, clock: Clock | None = None) -> Container:
    if clock is None:
        clock = FrozenClock(parse_utc(settings.frozen_now)) if settings.frozen_now else SystemClock()
    sf = session_factory(settings.database_url)
    ds = make_datasource(settings, sf)
    ml = MlProvider(ds, sf, Path(settings.models_dir))
    oidc = OidcProvider(settings) if settings.auth_mode == "oidc" else None
    return Container(settings=settings, clock=clock, sf=sf, ds=ds, ml=ml, oidc=oidc,
                     login_limiter=RateLimiter(max(settings.login_rate_per_min, 1), 60))
