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
    """Scaffolding smoke test: the server is loadable but exposes nothing yet."""
    async with Client(mcp) as client:
        assert await client.list_tools() == []
        assert await client.list_resources() == []
        assert await client.list_prompts() == []


def test_main_runs_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[()]] = []
    monkeypatch.setattr(mcp, "run", lambda: calls.append(()))
    main()
    assert len(calls) == 1


def test_import_configured_observability() -> None:
    """Importing server.py must have run the observability setup."""
    assert observability._configured is True
