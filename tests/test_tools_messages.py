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

GROUP_REF = {"kind": "group", "group_id": "42"}
DIRECT_REF = {"kind": "direct", "other_user_id": "7"}


def envelope(data: object) -> dict[str, Any]:
    return {"response": data, "meta": {"code": 200}}


def raw_message(
    message_id: str,
    created_at: int,
    *,
    text: str | None = "hello",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "sender_id": "22",
        "name": "Ada",
        "text": text,
        "created_at": created_at,
        "group_id": "42",
        "favorited_by": ["1"],
        "attachments": attachments if attachments is not None else [],
    }


def group_messages_handler(
    messages: list[dict[str, Any]],
) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v3/groups/42/messages"
        return httpx2.Response(200, json=envelope({"count": len(messages), "messages": messages}))

    return handler


def direct_messages_handler(
    messages: list[dict[str, Any]],
) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v3/direct_messages"
        return httpx2.Response(
            200, json=envelope({"count": len(messages), "direct_messages": messages})
        )

    return handler


async def test_group_messages_come_back_oldest_first_with_cursor(
    groupme_transport: TransportInstaller,
) -> None:
    newest_first = [
        raw_message("m3", NOW - 10),
        raw_message("m2", NOW - 20),
        raw_message("m1", NOW - 30),
    ]
    requests = groupme_transport(group_messages_handler(newest_first))
    async with Client(mcp) as client:
        result = await client.call_tool("read_messages", {"conversation": GROUP_REF, "limit": 3})
    page = result.structured_content
    assert page is not None
    assert [m["id"] for m in page["messages"]] == ["m1", "m2", "m3"]
    assert page["next_before_id"] == "m1"
    assert page.get("note") is None
    assert page["messages"][0]["sender_name"] == "Ada"
    assert page["messages"][0]["likes"] == 1
    assert dict(requests[0].url.params) == {"limit": "3"}


@pytest.mark.parametrize("cursor", ["before_id", "since_id"])
async def test_cursors_are_forwarded_upstream(
    groupme_transport: TransportInstaller, cursor: str
) -> None:
    requests = groupme_transport(group_messages_handler([raw_message("m1", NOW - 5)]))
    async with Client(mcp) as client:
        await client.call_tool(
            "read_messages", {"conversation": GROUP_REF, cursor: "m9", "limit": 10}
        )
    assert dict(requests[0].url.params) == {"limit": "10", cursor: "m9"}


async def test_304_maps_to_empty_page_with_note_not_an_error(
    groupme_transport: TransportInstaller,
) -> None:
    groupme_transport(lambda _: httpx2.Response(304))
    async with Client(mcp) as client:
        result = await client.call_tool("read_messages", {"conversation": GROUP_REF})
    page = result.structured_content
    assert page is not None
    assert page["messages"] == []
    assert page.get("next_before_id") is None
    assert "No messages" in page["note"]


async def test_attachments_are_normalized(groupme_transport: TransportInstaller) -> None:
    attachments = [
        {"type": "image", "url": "https://i.groupme.com/x.jpg"},
        {"type": "location", "name": "HQ", "lat": "1", "lng": "2"},
        {"type": "mentions", "user_ids": ["1", "2"]},
        {"type": "reply", "reply_id": "m0", "base_reply_id": "m0"},
        {"type": "poll", "poll_id": "p1"},
    ]
    groupme_transport(
        group_messages_handler([raw_message("m1", NOW - 5, text=None, attachments=attachments)])
    )
    async with Client(mcp) as client:
        result = await client.call_tool("read_messages", {"conversation": GROUP_REF})
    assert result.structured_content is not None
    message = result.structured_content["messages"][0]
    assert message["attachments"] == [
        "https://i.groupme.com/x.jpg",
        "location: HQ",
        "mentions 2 user(s)",
        "reply to message m0",
        "[poll attachment]",
    ]


async def test_concise_omits_ids_detailed_includes_them(
    groupme_transport: TransportInstaller,
) -> None:
    groupme_transport(group_messages_handler([raw_message("m1", NOW - 5)]))
    async with Client(mcp) as client:
        concise = await client.call_tool("read_messages", {"conversation": GROUP_REF})
        detailed = await client.call_tool(
            "read_messages", {"conversation": GROUP_REF, "response_format": "detailed"}
        )
    assert concise.structured_content is not None
    assert detailed.structured_content is not None
    concise_message = concise.structured_content["messages"][0]
    detailed_message = detailed.structured_content["messages"][0]
    assert concise_message.get("sender_id") is None
    assert concise_message.get("created_at") is None
    assert detailed_message["sender_id"] == "22"
    assert detailed_message["conversation_id"] == "42"
    assert detailed_message["created_at"] is not None


async def test_direct_messages_read_and_truncate(groupme_transport: TransportInstaller) -> None:
    newest_first = [
        raw_message("m3", NOW - 10),
        raw_message("m2", NOW - 20),
        raw_message("m1", NOW - 30),
    ]
    requests = groupme_transport(direct_messages_handler(newest_first))
    async with Client(mcp) as client:
        result = await client.call_tool("read_messages", {"conversation": DIRECT_REF, "limit": 2})
    page = result.structured_content
    assert page is not None
    assert [m["id"] for m in page["messages"]] == ["m2", "m3"]  # two newest, oldest first
    assert page["next_before_id"] == "m2"
    assert dict(requests[0].url.params) == {"other_user_id": "7"}


async def test_conflicting_cursors_become_an_actionable_tool_error(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(group_messages_handler([]))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Invalid arguments") as excinfo:
            await client.call_tool(
                "read_messages",
                {"conversation": GROUP_REF, "before_id": "a", "since_id": "b"},
            )
    assert "A valid call looks like" in str(excinfo.value)
    assert requests == []


@pytest.mark.parametrize(
    "conversation",
    [
        {"group_id": "42", "other_user_id": "7"},  # no discriminator
        {"kind": "group", "group_id": "42", "other_user_id": "7"},  # extras forbidden
        {"kind": "direct", "other_user_id": "7", "group_id": "42"},  # extras forbidden
    ],
)
async def test_conversation_ref_with_both_ids_is_unrepresentable(
    groupme_transport: TransportInstaller, conversation: dict[str, str]
) -> None:
    requests = groupme_transport(group_messages_handler([]))
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("read_messages", {"conversation": conversation})
    assert requests == []


async def test_404_maps_to_actionable_not_found(groupme_transport: TransportInstaller) -> None:
    groupme_transport(lambda _: httpx2.Response(404, json={"meta": {"code": 404}}))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="list_conversations"):
            await client.call_tool("read_messages", {"conversation": GROUP_REF})


async def test_420_maps_to_rate_limit_guidance(groupme_transport: TransportInstaller) -> None:
    groupme_transport(
        lambda _: httpx2.Response(420, json={"meta": {"code": 420, "errors": ["slow down"]}})
    )
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="rate limiting"):
            await client.call_tool("read_messages", {"conversation": GROUP_REF})


async def test_missing_token_is_actionable(groupme_transport: TransportInstaller) -> None:
    requests = groupme_transport(group_messages_handler([]), token=None)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="GROUPME_ACCESS_TOKEN"):
            await client.call_tool("read_messages", {"conversation": GROUP_REF})
    assert requests == []
