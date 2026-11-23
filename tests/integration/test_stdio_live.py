"""Live stdio smoke test: the installed console script with a real token.

``test_stdio_e2e.py`` proves the console script starts and survives a bad
token. This test proves the same packaged server works for real: it spawns
``uv run groupme-mcp-server`` with the live access token and calls read-only
tools over stdio against the dedicated test group and topic, exactly as an
MCP client would. It skips without the token or the test ids (see
``conftest.py``) and never writes.

Run with: ``uv run pytest -m integration --no-cov``
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

if TYPE_CHECKING:
    from groupme_mcp_server.models import GroupId
    from groupme_mcp_server.settings import Settings

pytestmark = pytest.mark.integration

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


async def test_console_script_serves_live_data_over_stdio(
    tmp_path: Path, live_settings: Settings, live_group_id: GroupId, live_topic_id: GroupId
) -> None:
    assert live_settings.access_token is not None  # the fixture skips otherwise
    # The conftest scrubbed GROUPME_* from os.environ; hand the subprocess the
    # token explicitly, and run it from an empty directory so no .env is read.
    env = {**os.environ, "GROUPME_ACCESS_TOKEN": live_settings.access_token.get_secret_value()}
    transport = StdioTransport(
        command="uv",
        args=["run", "--project", str(_PROJECT_ROOT), "groupme-mcp-server"],
        env=env,
        cwd=str(tmp_path),
        keep_alive=False,
    )
    topic = {"kind": "topic", "parent_group_id": live_group_id, "topic_id": live_topic_id}
    async with Client(transport) as client:
        context = await client.call_tool(
            "get_conversation_context", {"group_id": live_group_id, "recent_message_count": 1}
        )
        assert context.structured_content is not None
        assert context.structured_content["group_id"] == live_group_id
        assert live_topic_id in [t["topic_id"] for t in context.structured_content["topics"]]

        page = await client.call_tool("read_messages", {"conversation": topic, "limit": 1})
        assert page.structured_content is not None
        assert "messages" in page.structured_content

        found = await client.call_tool(
            "search_messages", {"conversation": topic, "query": "smoke-", "limit": 1}
        )
        assert found.structured_content is not None
        assert found.structured_content["messages_scanned"] >= 1
