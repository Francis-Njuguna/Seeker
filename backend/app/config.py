"""Application configuration, loaded from environment variables."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://seeker:seeker@db:5432/seeker"

    # Auth
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 12

    # Storage
    upload_dir: str = "/data/uploads"

    # OCR
    tesseract_cmd: str = "tesseract"
    ocr_languages: str = "eng"

    # Email notifications. When smtp_host is empty, emails are written to the
    # application log instead of being sent. This keeps the MVP runnable with
    # zero external configuration.
    smtp_host: str = ""
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    email_from: str = "no-reply@seeker.local"

    # Abuse control. Searching by ID number is an enumeration vector, so it is
    # rate limited per authenticated user.
    search_rate_limit: int = 30
    search_rate_window_seconds: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
