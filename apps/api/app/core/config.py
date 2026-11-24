from __future__ import annotations

from functools import lru_cache
from typing import Any, List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "OpenKhazana API"
    app_version: str = "0.6.0"
    environment: str = "development"
    auth_mode: str = ""  # "local" (zero-login self-hosted) | "multiuser" (cloud/team auth) | "" (auto)

    database_url: str = "postgresql+asyncpg://openkhazana:openkhazana_dev_pw@localhost:5432/openkhazana"
    redis_url: str = "redis://localhost:6379/0"

    secret_key: str = "dev-only-insecure-change-me-in-production-32chars"
    admin_api_key: str = ""
    demo_ttl_hours: int = 24
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080  # 7 days
    cookie_name: str = "okz_session"
    cookie_secure: bool = False

    cors_origins: str = "http://localhost:3000"

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:3000/api/auth/google/callback"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "alerts@openkhazana.local"
    telegram_bot_token: str = ""

    scrapers_enabled: bool = True
    seed_demo_on_empty_db: bool = True
    psx_intraday_interval_seconds: int = 300
    scraper_dlq_dir: str = "/tmp/scraper_dlq"

    @field_validator("*", mode="before")
    @classmethod
    def _strip_string_env_values(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    @property
    def effective_auth_mode(self) -> str:
        """
        Resolve effective auth mode:
        - Explicit AUTH_MODE=local or AUTH_MODE=multiuser always wins.
        - Otherwise: 'multiuser' in production & test environments, 'local' for self-hosted development.
        """
        explicit = (self.auth_mode or "").strip().lower()
        if explicit in {"local", "single", "none", "selfhosted"}:
            return "local"
        if explicit in {"multiuser", "multi", "cloud", "jwt"}:
            return "multiuser"
        env = (self.environment or "").strip().lower()
        if env in {"production", "prod", "test", "testing"}:
            return "multiuser"
        return "local"

    @property
    def is_local_auth_mode(self) -> bool:
        return self.effective_auth_mode == "local"

    @property
    def cors_origin_list(self) -> List[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def validate_production_security(self) -> None:
        """Fail fast if running in production with an insecure default SECRET_KEY."""
        if self.environment.strip().lower() == "production" and not self.is_local_auth_mode:
            insecure_prefixes = ("dev-only", "change-this")
            if len(self.secret_key.strip()) < 32 or self.secret_key.strip().startswith(insecure_prefixes):
                raise RuntimeError(
                    "Insecure SECRET_KEY configured for ENVIRONMENT=production. "
                    "Set a cryptographically random SECRET_KEY (>= 32 chars)."
                )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
