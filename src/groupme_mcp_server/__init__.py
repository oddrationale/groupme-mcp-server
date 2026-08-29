"""A FastMCP server that exposes the GroupMe API v3 to MCP clients."""

from __future__ import annotations

from importlib.metadata import version

from groupme_mcp_server.client import GroupMeClient
from groupme_mcp_server.errors import (
    GroupMeApiError,
    GroupMeAuthError,
    GroupMeNotFoundError,
    GroupMeRateLimitError,
)
from groupme_mcp_server.server import mcp
from groupme_mcp_server.settings import Settings, get_settings

__version__ = version("groupme-mcp-server")

__all__ = [
    "GroupMeApiError",
    "GroupMeAuthError",
    "GroupMeClient",
    "GroupMeNotFoundError",
    "GroupMeRateLimitError",
    "Settings",
    "__version__",
    "get_settings",
    "mcp",
]
