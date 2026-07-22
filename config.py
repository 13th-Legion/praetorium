"""Application configuration — loaded from environment / .env file."""

from pydantic_settings import BaseSettings
from pydantic import field_validator
from functools import lru_cache

_PLACEHOLDER_SECRETS = {
    "CHANGE-ME-IN-PRODUCTION",
    "CHANGE-ME",
    "changeme",
    "",
}


class Settings(BaseSettings):
    # App
    app_name: str = "Project Praetorium"
    app_url: str = "https://portal.13thlegion.org"
    debug: bool = False
    secret_key: str = "CHANGE-ME-IN-PRODUCTION"

    @field_validator("secret_key")
    @classmethod
    def _reject_weak_secret(cls, v: str, info) -> str:
        """Fail hard rather than silently signing sessions + QR check-in HMACs
        with a publicly known constant. Only tolerated when debug=True (local).
        """
        # `debug` may or may not be parsed yet depending on field order; read
        # the raw env as the authoritative signal for local dev.
        import os
        debug_env = os.getenv("DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
        if debug_env:
            return v
        if v in _PLACEHOLDER_SECRETS or len(v) < 32:
            raise ValueError(
                "SECRET_KEY is unset, a known placeholder, or shorter than 32 "
                "chars. Set a strong SECRET_KEY (e.g. `python -c \"import "
                "secrets; print(secrets.token_urlsafe(48))\"`) in the "
                "environment before starting in production."
            )
        return v

    # Database
    database_url: str = "postgresql+asyncpg://praetorium:praetorium@db:5432/praetorium"
    database_url_sync: str = "postgresql+psycopg2://praetorium:praetorium@db:5432/praetorium"

    # Nextcloud OAuth2
    nc_url: str = "https://cloud.13thlegion.org"
    nc_client_id: str = ""
    nc_client_secret: str = ""

    # Nextcloud API (service account for group sync, user provisioning)
    nc_api_user: str = "portal-svc"
    nc_api_password: str = ""

    # Session
    session_max_age: int = 86400  # 24 hours

    # Google Maps Geocoding

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
