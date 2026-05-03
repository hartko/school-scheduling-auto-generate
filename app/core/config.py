"""
Application configuration loaded from environment variables or a .env file.
All settings have sensible defaults so the app runs without a .env file in development.
"""

from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Ignore extra env vars that don't match any field
        extra="ignore",
    )

    # ── NestJS backend ────────────────────────────────────────────────────────
    # Base URL of the NestJS school-scheduling API
    nestjs_base_url: AnyHttpUrl = "http://localhost:4000"  # type: ignore[assignment]

    # Number of records to request per page when fetching paginated NestJS data
    nestjs_page_size: int = 100

    # Seconds before an individual HTTP request to NestJS is considered timed out
    nestjs_timeout_seconds: float = 30.0

    # How many times to retry a failed NestJS request (uses exponential back-off)
    nestjs_max_retries: int = 3

    # ── Solver ────────────────────────────────────────────────────────────────
    # Default wall-clock limit for the CP-SAT solver (Stage 1).
    # When the limit is reached the solver returns the best solution found so far.
    default_solver_time_limit_seconds: int = 300

    # ── API ───────────────────────────────────────────────────────────────────
    api_prefix: str = "/api/v1"

    # ── Logging ───────────────────────────────────────────────────────────────
    log_level: str = "INFO"


# Single shared instance imported everywhere else
settings = Settings()
