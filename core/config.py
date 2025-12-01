# core/config.py
import os

STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "memory")  # memory | sqlite | redis
SQLITE_PATH = os.environ.get("SQLITE_PATH", "data/macrs.sqlite3")
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Full Pydantic v2-native settings model.
    Automatically loads environment variables from .env and OS env.
    Uses strict validation and forbids unknown settings.
    """

    # ----------------------------
    # Model configuration (Pydantic v2 syntax)
    # ----------------------------
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",          # no unknown env vars allowed
        validate_default=True,
        case_sensitive=False,    # ENV vars can be uppercase/lowercase
    )

    # ----------------------------
    # Application info
    # ----------------------------
    app_name: str = "MACRS"
    app_env: str = "development"           # development | production
    api_port: int = 8000

    # ----------------------------
    # Postgres
    # ----------------------------
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = "macrs"
    postgres_password: str = "macrs_pass"
    postgres_db: str = "macrs_db"

    # ----------------------------
    # Redis
    # ----------------------------
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    # ----------------------------
    # Worker / Queue (RQ)
    # ----------------------------
    broker_url: str = "redis://redis:6379/0"


# Global singleton settings instance
settings = Settings()
