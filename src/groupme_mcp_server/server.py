"""The FastMCP server instance.

Prefect Horizon is configured to load this module and use the ``mcp`` object as
its entrypoint (``src/groupme_mcp_server/server.py:mcp``).
"""

from __future__ import annotations

from fastmcp import FastMCP

from groupme_mcp_server.observability import configure_observability
from groupme_mcp_server.settings import get_settings

configure_observability(get_settings())

INSTRUCTIONS = """\
Tools for reading and sending GroupMe messages via the GroupMe API v3.
"""

mcp: FastMCP = FastMCP(name="groupme-mcp-server", instructions=INSTRUCTIONS)
