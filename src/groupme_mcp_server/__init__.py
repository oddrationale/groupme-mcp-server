"""A FastMCP server that exposes the GroupMe API v3 to MCP clients."""

from __future__ import annotations

from importlib.metadata import version

from groupme_mcp_server.server import mcp
from groupme_mcp_server.settings import Settings, get_settings

__version__ = version("groupme-mcp-server")

__all__ = ["Settings", "__version__", "get_settings", "mcp"]
