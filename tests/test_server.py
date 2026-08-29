from __future__ import annotations

import pytest
from fastmcp import Client, FastMCP

from groupme_mcp_server import observability
from groupme_mcp_server.__main__ import main
from groupme_mcp_server.server import mcp


def test_server_instance() -> None:
    assert isinstance(mcp, FastMCP)
    assert mcp.name == "groupme-mcp-server"
    assert mcp.instructions is not None
    assert "GroupMe" in mcp.instructions


READ_TOOLS = ("get_conversation_context", "list_conversations", "read_messages")
WRITE_TOOLS = ("react_to_message", "send_message")


async def test_client_can_connect_in_memory() -> None:
    """The server is loadable and exposes exactly the expected toolset."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert sorted(t.name for t in tools) == sorted(READ_TOOLS + WRITE_TOOLS)
        assert await client.list_resources() == []
        assert await client.list_prompts() == []


async def test_all_tools_carry_honest_annotations() -> None:
    async with Client(mcp) as client:
        for tool in await client.list_tools():
            annotations = tool.annotations
            assert annotations is not None, tool.name
            # Every tool talks to the external GroupMe API; none destroys data.
            assert annotations.openWorldHint is True
            assert annotations.destructiveHint is False
            assert annotations.readOnlyHint is (tool.name in READ_TOOLS)
            # Sending is the only non-idempotent tool: repeats post duplicates.
            assert annotations.idempotentHint is (tool.name != "send_message")
            assert tool.description is not None
            assert "Use this" in tool.description or "idempotent" in tool.description


def test_instructions_orient_an_agent() -> None:
    assert mcp.instructions is not None
    for name in READ_TOOLS + WRITE_TOOLS:
        assert name in mcp.instructions


def test_main_runs_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[()]] = []
    monkeypatch.setattr(mcp, "run", lambda: calls.append(()))
    main()
    assert len(calls) == 1


def test_import_configured_observability() -> None:
    """Importing server.py must have run the observability setup."""
    assert observability._configured is True
