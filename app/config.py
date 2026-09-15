"""Application configuration.

Every value can be overridden through the environment (systemd EnvironmentFile).
Defaults are chosen so the app boots on a developer machine without any .env.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LOYOLA_", env_file=".env", extra="ignore")

    # --- identity -----------------------------------------------------------
    app_name: str = "Loyola Networking"
    campus_name: str = "Loyola Academy"
    base_url: str = "http://127.0.0.1:8011"
    debug: bool = False

    # --- database -----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://loyola_user:loyola@127.0.0.1:5433/loyola_db"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # --- crypto -------------------------------------------------------------
    # secret_key signs session cookies and media URLs.
    secret_key: str = "dev-only-insecure-secret-change-me"
    # media_key is a urlsafe base64 32-byte Fernet key encrypting verification artifacts.
    media_key: str = ""

    session_cookie: str = "loyola_session"
    session_days: int = 30

    # --- storage ------------------------------------------------------------
    media_root: Path = Path("./var/media")
    verification_root: Path = Path("./var/verification")
    max_upload_mb: int = 12

    # --- verification -------------------------------------------------------
    # Regex the OCR'd roll number must satisfy. Overridable at runtime from the
    # admin console (settings table wins over this default) so the pattern can be
    # corrected against real cards without a redeploy.
    roll_number_regex: str = r"^[A-Z0-9][A-Z0-9\-/]{4,19}$"
    ocr_autopass_confidence: float = 0.72
    verification_attempts_per_week: int = 3
    id_image_retention_days: int = 30
    provisional_tier_hours: int = 72

    # --- reputation ---------------------------------------------------------
    provisional_settle_hours: int = 48
    decay_percent_per_idle_month: float = 2.0

    # --- misc ---------------------------------------------------------------
    counsellor_contact: str = "Campus counselling desk — Room 104, Admin Block"
    grievance_officer: str = "Grievance Officer, Loyola Networking"
    grievance_email: str = "grievance@example.invalid"
    enable_scheduler: bool = True

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.media_root.mkdir(parents=True, exist_ok=True)
    s.verification_root.mkdir(parents=True, exist_ok=True)
    return s


settings = get_settings()
