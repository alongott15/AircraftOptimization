"""Settings from environment (APP_* — ARCHITECTURE.md §7)."""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
# Secrets that were ever published in .env.example — never accepted for signing.
PUBLIC_SECRETS = frozenset({"dev-only-insecure-secret-change-me-0123456789"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=(str(REPO_ROOT / ".env"),), env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "dev"
    data_source: str = "poc"
    sqlite_path: str = str(REPO_ROOT / "data" / "poc.sqlite")
    store_url: str | None = None
    vertica_dsn: str | None = None
    frozen_now: str | None = None
    auth_mode: str = "local"
    jwt_secret: str = ""  # empty/public default ⇒ random per process outside prod; rejected in prod
    session_ttl_hours: float = 8.0
    login_rate_per_min: int = 5  # SECURITY.md §3.1; raised only for the E2E suite
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173"
    models_dir: str = str(REPO_ROOT / "data" / "models")
    demo_password: str | None = None
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str | None = None
    oidc_redirect_uri: str = ""
    oidc_scopes: str = "openid profile email"
    oidc_role_claim: str = "groups"
    oidc_role_map: str = "AO-Viewers=VIEWER,AO-Planners=PLANNER,AO-MaintControl=MAINTENANCE_CONTROLLER,AO-Admins=ADMIN"
    oidc_username_claim: str = "preferred_username"
    log_level: str = "INFO"
    snapshot_ttl_seconds: int = 60

    @model_validator(mode="after")
    def _no_public_secret(self) -> Settings:
        """SECURITY.md §5 — a secret published in .env.example can forge sessions; never sign with it."""
        if self.env != "prod" and (not self.jwt_secret or self.jwt_secret in PUBLIC_SECRETS):
            self.jwt_secret = secrets.token_urlsafe(48)
        return self

    @property
    def database_url(self) -> str:
        return self.store_url or f"sqlite:///{self.sqlite_path}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_for_prod(self) -> None:
        """SECURITY.md §5 — refuse insecure production configuration."""
        if self.env != "prod":
            return
        problems = []
        if len(self.jwt_secret) < 32 or "insecure" in self.jwt_secret or self.jwt_secret in PUBLIC_SECRETS:
            problems.append("APP_JWT_SECRET is default or too short")
        if self.auth_mode == "local":
            problems.append("APP_AUTH_MODE=local is not allowed in prod")
        if "*" in self.cors_origin_list:
            problems.append("CORS must not allow *")
        if not self.cookie_secure:
            problems.append("APP_COOKIE_SECURE must be true in prod")
        if self.auth_mode == "oidc" and not (self.oidc_issuer and self.oidc_client_id and self.oidc_redirect_uri):
            problems.append("APP_OIDC_ISSUER / APP_OIDC_CLIENT_ID / APP_OIDC_REDIRECT_URI are required for OIDC")
        if problems:
            raise RuntimeError("Insecure production configuration: " + "; ".join(problems))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
