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


async def test_client_can_connect_in_memory() -> None:
    """The server is loadable and exposes exactly the read toolset."""
    async with Client(mcp) as client:
        tools = await client.list_tools()
        assert sorted(t.name for t in tools) == [
            "get_conversation_context",
            "list_conversations",
            "read_messages",
        ]
        assert await client.list_resources() == []
        assert await client.list_prompts() == []


async def test_all_tools_are_annotated_read_only() -> None:
    async with Client(mcp) as client:
        for tool in await client.list_tools():
            assert tool.annotations is not None, tool.name
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False
            assert tool.annotations.idempotentHint is True
            assert tool.annotations.openWorldHint is True
            assert tool.description is not None
            assert "Use this" in tool.description


def test_instructions_orient_an_agent() -> None:
    assert mcp.instructions is not None
    for name in ("list_conversations", "read_messages", "get_conversation_context"):
        assert name in mcp.instructions


def test_main_runs_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[()]] = []
    monkeypatch.setattr(mcp, "run", lambda: calls.append(()))
    main()
    assert len(calls) == 1


def test_import_configured_observability() -> None:
    """Importing server.py must have run the observability setup."""
    assert observability._configured is True
