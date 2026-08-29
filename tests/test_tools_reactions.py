from __future__ import annotations

from typing import TYPE_CHECKING

import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from groupme_mcp_server.server import mcp

if TYPE_CHECKING:
    from collections.abc import Callable

    TransportInstaller = Callable[..., list[httpx2.Request]]


def ok_handler(_: httpx2.Request) -> httpx2.Response:
    return httpx2.Response(200, json={"response": None, "meta": {"code": 200}})


@pytest.mark.parametrize("action", ["like", "unlike"])
async def test_reactions_route_to_the_matching_endpoint(
    groupme_transport: TransportInstaller, action: str
) -> None:
    requests = groupme_transport(ok_handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "react_to_message",
            {"conversation_id": "42", "message_id": "m1", "action": action},
        )
    assert requests[0].method == "POST"
    assert requests[0].url.path == f"/v3/messages/42/m1/{action}"
    data = result.structured_content
    assert data is not None
    assert data["action"] == action
    assert data["conversation_id"] == "42"
    assert data["message_id"] == "m1"
    expected_verb = "Liked" if action == "like" else "Removed the like from"
    assert data["confirmation"] == f"{expected_verb} message m1."


async def test_composite_direct_chat_conversation_id_is_forwarded(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(ok_handler)
    async with Client(mcp) as client:
        await client.call_tool(
            "react_to_message",
            {"conversation_id": "42+7", "message_id": "m1", "action": "like"},
        )
    assert requests[0].url.path == "/v3/messages/42+7/m1/like"


async def test_404_points_at_read_messages_detailed_format(
    groupme_transport: TransportInstaller,
) -> None:
    groupme_transport(lambda _: httpx2.Response(404, json={"meta": {"code": 404}}))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="message or conversation") as excinfo:
            await client.call_tool(
                "react_to_message",
                {"conversation_id": "42", "message_id": "nope", "action": "like"},
            )
    assert "read_messages" in str(excinfo.value)
    assert 'response_format="detailed"' in str(excinfo.value)


async def test_420_maps_to_rate_limit_guidance(groupme_transport: TransportInstaller) -> None:
    groupme_transport(
        lambda _: httpx2.Response(420, json={"meta": {"code": 420, "errors": ["slow down"]}})
    )
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="rate limiting"):
            await client.call_tool(
                "react_to_message",
                {"conversation_id": "42", "message_id": "m1", "action": "unlike"},
            )
