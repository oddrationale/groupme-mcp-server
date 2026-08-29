"""Integration checks against the deployed Horizon server.

The deployment at https://groupme.fastmcp.app/mcp sits behind Horizon Auth.
The unauthenticated tests exist to catch a fail-open regression: an MCP
request without credentials must be rejected outright, never answered.

Run with: ``uv run pytest -m integration --no-cov``
"""

from __future__ import annotations

import os
from typing import Any

import httpx2
import pytest
from fastmcp import Client

pytestmark = pytest.mark.integration

DEPLOYED_URL = "https://groupme.fastmcp.app/mcp"

_INITIALIZE_PARAMS: dict[str, Any] = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "groupme-mcp-integration-test", "version": "0"},
}


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("initialize", _INITIALIZE_PARAMS),
        ("tools/list", {}),
    ],
)
async def test_unauthenticated_requests_are_rejected(method: str, params: dict[str, Any]) -> None:
    """Horizon Auth must reject anonymous MCP traffic with 401/403, not serve it."""
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": "2025-06-18",
    }
    async with httpx2.AsyncClient(timeout=30.0) as http:
        response = await http.post(DEPLOYED_URL, json=body, headers=headers)
    # The deployed revision may lag this branch, so nothing is asserted about
    # the response content — only the auth rejection itself.
    assert response.status_code in {401, 403}, (
        f"expected 401/403 for unauthenticated {method}, got HTTP {response.status_code} "
        "- the deployed server may be failing open"
    )


async def test_authenticated_tool_listing_via_oauth() -> None:
    """List tools through Horizon's OAuth flow.

    fastmcp's ``auth="oauth"`` opens a real browser for the Horizon login,
    which cannot run unattended (CI, agents, plain terminal runs) — so this
    check is opt-in via ``HORIZON_OAUTH_INTERACTIVE=1``.
    """
    if os.environ.get("HORIZON_OAUTH_INTERACTIVE") != "1":
        pytest.skip("set HORIZON_OAUTH_INTERACTIVE=1 to run the interactive OAuth check")
    async with Client(DEPLOYED_URL, auth="oauth") as client:
        tools = await client.list_tools()
    # The deployed revision may predate this branch; assert only that an
    # authenticated session works and serves a non-empty tool list.
    assert tools
