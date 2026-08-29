"""Runtime configuration, loaded from the environment.

Values are read from ``GROUPME_*`` environment variables (Prefect Horizon
injects these at runtime) or from a local ``.env`` file during development:

- ``GROUPME_ACCESS_TOKEN`` — the GroupMe API token (optional at startup so
  ``fastmcp inspect`` works; required when a tool actually calls the API).
- ``GROUPME_LOG_LEVEL`` — verbosity of the server's own logging.
- ``GROUPME_API_BASE_URL`` / ``GROUPME_IMAGE_API_BASE_URL`` — endpoint
  overrides, mainly for testing.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from groupme_mcp_server.errors import GroupMeAuthError

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

_CONTROL_CHAR_MAX = 0x20  # everything below U+0020 is a C0 control character
_DELETE_CHAR = 0x7F


class Settings(BaseSettings):
    """Configuration for the GroupMe MCP server."""

    model_config = SettingsConfigDict(
        env_prefix="GROUPME_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    access_token: SecretStr | None = None
    """GroupMe API access token; ``None`` until configured."""

    log_level: LogLevel = "INFO"
    """Verbosity of the server's own logging."""

    api_base_url: str = "https://api.groupme.com/v3"
    """Base URL of the GroupMe REST API."""

    image_api_base_url: str = "https://image.groupme.com"
    """Base URL of the GroupMe image-upload service."""

    def require_access_token(self) -> SecretStr:
        """Return the access token, or fail with actionable guidance.

        Called at API-call time so the server can still start (and be
        inspected) without a token. Tokens containing control characters are
        rejected here so they can never reach the HTTP layer, whose header
        errors would echo the value back.

        Returns:
            The configured token.

        Raises:
            GroupMeAuthError: If no token is configured, or the token
                contains control characters (the message never includes the
                token itself).
        """
        if self.access_token is None:
            msg = (
                "GroupMe access token is not configured. "
                "Set GROUPME_ACCESS_TOKEN — get a token at https://dev.groupme.com"
            )
            raise GroupMeAuthError(msg)
        token = self.access_token.get_secret_value()
        if any(ord(ch) < _CONTROL_CHAR_MAX or ord(ch) == _DELETE_CHAR for ch in token):
            msg = (
                "GroupMe access token contains control characters and cannot be "
                "sent as a header. Set GROUPME_ACCESS_TOKEN to the exact token "
                "from https://dev.groupme.com"
            )
            raise GroupMeAuthError(msg)
        return self.access_token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, constructing them on first use.

    Returns:
        The cached [`Settings`][groupme_mcp_server.settings.Settings] instance.
    """
    return Settings()
