"""Application configuration — loaded from environment / .env file."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    app_name: str = "Project Praetorium"
    app_url: str = "https://portal.13thlegion.org"
    debug: bool = False
    secret_key: str = "CHANGE-ME-IN-PRODUCTION"

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
