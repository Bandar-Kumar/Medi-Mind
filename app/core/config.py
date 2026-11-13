"""Application configuration loaded from environment variables and ``.env``.

Precedence (highest first): real environment variables, then ``.env``, then the defaults below.
Every variable uses the ``MEDIMIND_`` prefix, e.g. ``MEDIMIND_LOG_LEVEL=DEBUG``.

Security rule: secret settings (added in later phases, e.g. a database URL or an API key) must use
``pydantic.SecretStr`` and must never be added to ``SAFE_TO_DISPLAY``.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.errors import ConfigurationError

ENV_PREFIX = "MEDIMIND_"
ENV_FILE = Path(".env")

# Only these fields may ever be printed or logged. Secrets (db_password) are never added here.
SAFE_TO_DISPLAY: tuple[str, ...] = (
    "app_env",
    "log_level",
    "log_format",
    "data_dir",
    "db_host",
    "db_port",
    "db_name",
    "db_user",
)


class Settings(BaseSettings):
    """Typed, validated application settings."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",  # ignore unrelated MEDIMIND_* or .env entries instead of failing
    )

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    # Required application data directory; data/synthetic inside it is optional demo data.
    data_dir: Path = Path("data")

    # PostgreSQL (Docker Compose, Task 1.5). docker-compose.yml reads the same MEDIMIND_DB_*
    # variables from .env. 127.0.0.1 rather than "localhost": the container port is bound to IPv4
    # loopback only, and "localhost" can resolve to IPv6 (::1) first on Windows.
    db_host: str = "127.0.0.1"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = "medimind"
    db_user: str = "medimind"
    # Secret: never displayed or logged. Optional here so the app still starts without it; the
    # health check warns, and Docker Compose refuses to start the database without it.
    db_password: SecretStr | None = None

    def safe_summary(self) -> dict[str, Any]:
        """Return only the allow-listed, non-secret settings, for display or logging."""
        return {name: getattr(self, name) for name in SAFE_TO_DISPLAY}


def _describe(exc: ValidationError) -> str:
    """Turn a pydantic error into one readable line per invalid environment variable."""
    lines = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error["loc"])
        lines.append(f"  {ENV_PREFIX}{field.upper()}: {error['msg']}")
    return "Invalid configuration:\n" + "\n".join(lines)


@lru_cache
def get_settings() -> Settings:
    """Load settings once and return the same object on every later call.

    Raises:
        ConfigurationError: if any setting is invalid.
    """
    try:
        return Settings()
    except ValidationError as exc:
        # Pydantic's error includes the rejected input value, so we keep only a sanitized
        # message that names the setting, never its value.
        message = _describe(exc)
    # Raised outside the `except` block on purpose: the ConfigurationError then carries no
    # __cause__ or __context__, so no traceback can ever print the original pydantic error (and
    # the value inside it). `raise ... from None` would only hide that error, not remove it.
    raise ConfigurationError(message)
