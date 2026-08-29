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


def envelope(data: object) -> dict[str, Any]:
    return {"response": data, "meta": {"code": 200}}


def raw_liked_message(
    message_id: str,
    *,
    likes: int,
    name: str = "Ada",
    sender_id: str = "22",
    text: str | None = "hello",
    created_at: int | None = None,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "sender_id": sender_id,
        "name": name,
        "text": text,
        "created_at": created_at if created_at is not None else NOW - 600,
        "group_id": "42",
        "favorited_by": [str(i) for i in range(likes)],
        "attachments": [],
    }


def leaderboard_handler(
    messages: list[dict[str, Any]],
) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v3/groups/42/likes"
        return httpx2.Response(200, json=envelope({"messages": messages}))

    return handler


async def test_highlights_rank_messages_and_summarize_members(
    groupme_transport: TransportInstaller,
) -> None:
    # Deliberately unsorted: the leaderboard's claimed order is not trusted.
    messages = [
        raw_liked_message("m1", likes=2, name="Ada", sender_id="1"),
        raw_liked_message("m2", likes=9, name="Grace", sender_id="2"),
        raw_liked_message("m3", likes=5, name="Ada", sender_id="1"),
    ]
    requests = groupme_transport(leaderboard_handler(messages))
    async with Client(mcp) as client:
        result = await client.call_tool("get_highlights", {"group_id": "42"})
    assert dict(requests[0].url.params) == {"period": "week"}  # the default
    highlights = result.structured_content
    assert highlights is not None
    assert highlights["group_id"] == "42"
    assert highlights["period"] == "week"
    assert [m["message_id"] for m in highlights["top_messages"]] == ["m2", "m3", "m1"]
    top = highlights["top_messages"][0]
    assert top["sender_name"] == "Grace"
    assert top["likes"] == 9
    assert top["preview"] == "hello"
    assert top.get("sender_id") is None  # concise
    assert [
        (m["name"], m["messages_in_top"], m["likes_received"]) for m in highlights["top_members"]
    ] == [
        ("Grace", 1, 9),
        ("Ada", 2, 7),
    ]
    assert highlights.get("note") is None


async def test_highlights_forward_the_period_and_detailed_format(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(
        leaderboard_handler([raw_liked_message("m1", likes=3, created_at=NOW - 120)])
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_highlights", {"group_id": "42", "period": "day", "response_format": "detailed"}
        )
    assert dict(requests[0].url.params) == {"period": "day"}
    highlights = result.structured_content
    assert highlights is not None
    assert highlights["period"] == "day"
    assert highlights["top_messages"][0]["sender_id"] == "22"
    assert highlights["top_messages"][0]["created_at"] is not None
    assert highlights["top_members"][0]["user_id"] == "22"


async def test_highlights_empty_period_is_a_note_not_an_error(
    groupme_transport: TransportInstaller,
) -> None:
    groupme_transport(leaderboard_handler([]))
    async with Client(mcp) as client:
        result = await client.call_tool("get_highlights", {"group_id": "42", "period": "month"})
    highlights = result.structured_content
    assert highlights is not None
    assert highlights["top_messages"] == []
    assert highlights["top_members"] == []
    assert "nothing to highlight" in highlights["note"]


async def test_highlights_404_warns_the_endpoint_may_be_retired(
    groupme_transport: TransportInstaller,
) -> None:
    groupme_transport(lambda _: httpx2.Response(404, json={"meta": {"code": 404}}))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="retired") as excinfo:
            await client.call_tool("get_highlights", {"group_id": "42"})
    assert "A valid call looks like" in str(excinfo.value)


async def test_highlights_reject_an_unknown_period(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(leaderboard_handler([]))
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("get_highlights", {"group_id": "42", "period": "year"})
    assert requests == []
