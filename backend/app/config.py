"""Runtime configuration. Values come from environment variables or a local .env file.

No secret has a usable default: anything sensitive must be supplied by the operator.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"  # development | test | production
    app_version: str = "0.1.0-stage1"
    database_url: str = "postgresql+psycopg://portal:portal@localhost:5432/portal"

    # Session policy
    session_ttl_hours: int = 12
    remember_me_ttl_days: int = 30
    idle_timeout_minutes: int = 240  # applies to sessions without remember-me

    # Login throttling
    login_max_failures: int = 5
    login_lock_minutes: int = 5

    # One-time links
    invite_ttl_hours: int = 72
    reset_ttl_minutes: int = 60

    # Used to build links in invitations. Empty means "derive from the request".
    public_base_url: str = ""

    # Payroll owner is configured, never hardcoded (spec section 14).
    payroll_owner_email: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
