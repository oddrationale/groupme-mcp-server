"""End-to-end stdio test: the console script running as a real subprocess.

Spawns ``uv run groupme-mcp-server`` with a deliberately fake access token
and drives it over the real stdio MCP protocol. This proves the packaging
(console script), server wiring, and stderr-only logging end to end — a
stray print or a crash on an API error would corrupt or kill the protocol.
No real credentials are required.

Run with: ``uv run pytest -m integration --no-cov``
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport
from fastmcp.exceptions import ToolError

pytestmark = pytest.mark.integration

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EXPECTED_TOOLS = frozenset(
    {
        "get_conversation_context",
        "get_highlights",
        "list_conversations",
        "react_to_message",
        "read_messages",
        "search_messages",
        "send_message",
    }
)
# Deliberately fake: GroupMe rejects it with 401, which must surface as an
# actionable ToolError, not a server crash.
_DUMMY_TOKEN = "invalid-integration-test-token"  # noqa: S105 - not a real credential


async def test_stdio_server_lists_tools_and_survives_auth_errors(tmp_path: Path) -> None:
    # The subprocess needs the parent environment (PATH etc. for uv) plus the
    # dummy token; the conftest has already scrubbed real GROUPME_*/OTEL_*
    # variables from os.environ. Its working directory is an empty temp dir
    # so the repository's real .env can never be picked up.
    env = {**os.environ, "GROUPME_ACCESS_TOKEN": _DUMMY_TOKEN}
    transport = StdioTransport(
        command="uv",
        args=["run", "--project", str(_PROJECT_ROOT), "groupme-mcp-server"],
        env=env,
        cwd=str(tmp_path),
        keep_alive=False,
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == _EXPECTED_TOOLS
        with pytest.raises(ToolError, match="GROUPME_ACCESS_TOKEN"):
            await client.call_tool("list_conversations", {"limit": 1})
        # The auth failure must not have killed the subprocess: the same
        # session keeps answering requests.
        tools_again = await client.list_tools()
        assert {tool.name for tool in tools_again} == _EXPECTED_TOOLS
