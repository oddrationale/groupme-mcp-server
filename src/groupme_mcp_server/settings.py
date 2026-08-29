"""Runtime configuration, loaded from the environment.

Values are read from ``GROUPME_MCP_*`` environment variables (Prefect Horizon
injects these at runtime) or from a local ``.env`` file during development.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Configuration for the GroupMe MCP server."""

    model_config = SettingsConfigDict(
        env_prefix="GROUPME_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    log_level: LogLevel = "INFO"
    """Verbosity of the server's own logging."""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, constructing them on first use.

    Returns:
        The cached [`Settings`][groupme_mcp_server.settings.Settings] instance.
    """
    return Settings()
