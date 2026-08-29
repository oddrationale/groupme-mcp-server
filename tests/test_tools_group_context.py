from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from groupme_mcp_server.server import mcp

if TYPE_CHECKING:
    from collections.abc import Callable

    TransportInstaller = Callable[..., list[httpx2.Request]]

NOW = int(time.time())

RAW_GROUP = {
    "id": "42",
    "name": "Book club",
    "description": "We read",
    "share_url": "https://groupme.com/join_group/42/SHARE",
    "creator_user_id": "1",
    "updated_at": NOW - 120,
    "members": [
        {"user_id": "1", "nickname": "Ada", "roles": ["owner", "admin"]},
        {"user_id": "2", "nickname": "Grace", "roles": ["user"]},
    ],
}


def envelope(data: object) -> dict[str, Any]:
    return {"response": data, "meta": {"code": 200}}


def raw_message(message_id: str, created_at: int) -> dict[str, Any]:
    return {
        "id": message_id,
        "sender_id": "2",
        "name": "Grace",
        "text": f"words {message_id}",
        "created_at": created_at,
        "group_id": "42",
        "favorited_by": [],
        "attachments": [],
    }


def context_handler(
    group: dict[str, Any], messages: list[dict[str, Any]] | None
) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/v3/groups/42":
            return httpx2.Response(200, json=envelope(group))
        assert request.url.path == "/v3/groups/42/messages"
        if messages is None:
            return httpx2.Response(304)
        return httpx2.Response(200, json=envelope({"count": len(messages), "messages": messages}))

    return handler


async def test_bundles_metadata_members_and_messages(
    groupme_transport: TransportInstaller,
) -> None:
    newest_first = [raw_message("m2", NOW - 10), raw_message("m1", NOW - 20)]
    requests = groupme_transport(context_handler(RAW_GROUP, newest_first))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_conversation_context", {"group_id": "42", "recent_message_count": 2}
        )
    context = result.structured_content
    assert context is not None
    assert context["group_id"] == "42"
    assert context["name"] == "Book club"
    assert context["description"] == "We read"
    assert context["member_count"] == 2
    assert context["last_active"] == "2m ago"
    assert [m["nickname"] for m in context["members"]] == ["Ada", "Grace"]
    assert context["members"][0]["roles"] == ["owner", "admin"]
    assert [m["id"] for m in context["recent_messages"]] == ["m1", "m2"]  # oldest first
    assert context.get("message_note") is None
    assert [r.url.path for r in requests] == ["/v3/groups/42", "/v3/groups/42/messages"]
    assert dict(requests[1].url.params) == {"limit": "2"}


async def test_concise_hides_ids_and_urls(groupme_transport: TransportInstaller) -> None:
    groupme_transport(context_handler(RAW_GROUP, []))
    async with Client(mcp) as client:
        result = await client.call_tool("get_conversation_context", {"group_id": "42"})
    context = result.structured_content
    assert context is not None
    assert context.get("share_url") is None
    assert context.get("creator_user_id") is None
    assert context.get("updated_at") is None
    assert context["members"][0].get("user_id") is None


async def test_detailed_includes_ids_and_urls(groupme_transport: TransportInstaller) -> None:
    groupme_transport(context_handler(RAW_GROUP, []))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_conversation_context", {"group_id": "42", "response_format": "detailed"}
        )
    context = result.structured_content
    assert context is not None
    assert context["share_url"] == "https://groupme.com/join_group/42/SHARE"
    assert context["creator_user_id"] == "1"
    assert context["updated_at"] is not None
    assert context["members"][0]["user_id"] == "1"


async def test_group_with_no_messages_gets_a_note(
    groupme_transport: TransportInstaller,
) -> None:
    groupme_transport(context_handler(RAW_GROUP, None))  # 304 from the messages endpoint
    async with Client(mcp) as client:
        result = await client.call_tool("get_conversation_context", {"group_id": "42"})
    context = result.structured_content
    assert context is not None
    assert context["recent_messages"] == []
    assert "No messages" in context["message_note"]


async def test_unknown_group_is_an_actionable_error(
    groupme_transport: TransportInstaller,
) -> None:
    groupme_transport(lambda _: httpx2.Response(404, json={"meta": {"code": 404}}))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="list_conversations") as excinfo:
            await client.call_tool("get_conversation_context", {"group_id": "nope"})
    assert "A valid call looks like" in str(excinfo.value)
