"""Runtime configuration. Values come from environment variables or a local .env file.

No secret has a usable default: anything sensitive must be supplied by the operator.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"  # development | test | production
    app_version: str = "0.2.0"
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

    # First-run setup: the person who knows this key can create the organisation
    # and become its account holder. Empty disables the setup screen.
    setup_key: str = ""

    # Key for encrypting sensitive fields at rest. Generate with `python -m app.seed generate-key`.
    encryption_key: str = ""

    # Outbound messages. "on" (the default) locks safe mode on for every organisation: nothing is
    # sent, whatever is saved in the portal. Set to "off" to let each organisation's own switch decide.
    comms_safe_mode: str = "on"
    # Seconds to wait for a provider before giving up.
    provider_timeout_seconds: float = 15.0

    # Where uploaded files are stored (a Docker volume in production).
    files_dir: str = "./data/files"
    max_upload_mb: int = 50


@lru_cache
def get_settings() -> Settings:
    return Settings()
