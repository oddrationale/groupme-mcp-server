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


def raw_group(group_id: str, updated_at: int | None = None, **extra: object) -> dict[str, Any]:
    raw: dict[str, Any] = {"id": group_id, "name": f"Group {group_id}", "members": []}
    if updated_at is not None:
        raw["updated_at"] = updated_at
    raw.update(extra)
    return raw


def raw_chat(user_id: str, updated_at: int | None = None) -> dict[str, Any]:
    raw: dict[str, Any] = {"other_user": {"id": user_id, "name": f"User {user_id}"}}
    if updated_at is not None:
        raw["updated_at"] = updated_at
        raw["last_message"] = {
            "name": f"User {user_id}",
            "text": "latest words",
            "created_at": updated_at,
            "conversation_id": f"1+{user_id}",
        }
    return raw


def paged_handler(
    groups: dict[int, list[dict[str, Any]]],
    chats: dict[int, list[dict[str, Any]]],
) -> Callable[[httpx2.Request], httpx2.Response]:
    def handler(request: httpx2.Request) -> httpx2.Response:
        page = int(request.url.params.get("page", "1"))
        if request.url.path == "/v3/groups":
            return httpx2.Response(200, json=envelope(groups.get(page, [])))
        assert request.url.path == "/v3/chats"
        return httpx2.Response(200, json=envelope(chats.get(page, [])))

    return handler


async def test_merges_and_sorts_by_recency(groupme_transport: TransportInstaller) -> None:
    groupme_transport(
        paged_handler(
            {1: [raw_group("g-old", NOW - 3600), raw_group("g-new", NOW - 30)]},
            {1: [raw_chat("7", NOW - 300)]},
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {})
    page = result.structured_content
    assert page is not None
    names = [c["name"] for c in page["conversations"]]
    assert names == ["Group g-new", "User 7", "Group g-old"]
    assert page["count"] == 3
    assert page.get("note") is None
    chat = page["conversations"][1]
    assert chat["kind"] == "direct"
    assert chat["other_user_id"] == "7"
    assert chat["last_message"] == "User 7: latest words"
    assert chat["last_active"] == "5m ago"


async def test_kind_groups_skips_the_chats_endpoint(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(paged_handler({1: [raw_group("g1", NOW)]}, {}))
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {"kind": "groups"})
    assert [r.url.path for r in requests] == ["/v3/groups"]
    assert result.structured_content is not None
    assert result.structured_content["count"] == 1


async def test_kind_dms_skips_the_groups_endpoint(groupme_transport: TransportInstaller) -> None:
    requests = groupme_transport(paged_handler({}, {1: [raw_chat("7", NOW)]}))
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {"kind": "dms"})
    assert [r.url.path for r in requests] == ["/v3/chats"]
    assert result.structured_content is not None
    assert result.structured_content["conversations"][0]["kind"] == "direct"


@pytest.mark.parametrize(
    ("kind", "path"),
    [("groups", "/v3/groups"), ("dms", "/v3/chats")],
)
async def test_paginates_upstream_until_a_short_page(
    groupme_transport: TransportInstaller, kind: str, path: str
) -> None:
    def item(i: int) -> dict[str, Any]:
        return raw_group(f"g{i}", NOW - i) if kind == "groups" else raw_chat(str(i), NOW - i)

    pages = {1: [item(i) for i in range(50)], 2: [item(i) for i in range(50, 60)]}
    groups = pages if kind == "groups" else {}
    chats = pages if kind == "dms" else {}
    requests = groupme_transport(paged_handler(groups, chats))
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {"kind": kind, "limit": 60})
    assert [(r.url.path, r.url.params["page"]) for r in requests] == [(path, "1"), (path, "2")]
    assert result.structured_content is not None
    assert result.structured_content["count"] == 60


async def test_chats_stop_paginating_once_limit_is_reached(
    groupme_transport: TransportInstaller,
) -> None:
    pages = {
        1: [raw_chat(str(i), NOW - i) for i in range(50)],
        2: [raw_chat(str(i), NOW - i) for i in range(50, 100)],
    }
    requests = groupme_transport(paged_handler({}, pages))
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {"kind": "dms", "limit": 50})
    assert [r.url.params["page"] for r in requests] == ["1"]
    assert result.structured_content is not None
    assert result.structured_content["count"] == 50


async def test_groups_are_fetched_through_the_terminal_page_regardless_of_limit(
    groupme_transport: TransportInstaller,
) -> None:
    # /groups pages carry no documented ordering, so a small limit must not
    # stop the fetch early: the most recent group sits on page 2 here.
    pages = {
        1: [raw_group(f"g{i}", NOW - 3600 - i) for i in range(50)],
        2: [raw_group("fresh", NOW - 5)],
    }
    requests = groupme_transport(paged_handler(pages, {}))
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {"kind": "groups", "limit": 1})
    assert [r.url.params["page"] for r in requests] == ["1", "2"]
    assert result.structured_content is not None
    assert [c["name"] for c in result.structured_content["conversations"]] == ["Group fresh"]


async def test_group_fetch_is_capped_against_a_pathological_upstream(
    groupme_transport: TransportInstaller,
) -> None:
    full_page = [raw_group(f"g{i}", NOW - i) for i in range(50)]

    def endless(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v3/groups"
        return httpx2.Response(200, json=envelope(full_page))

    requests = groupme_transport(endless)
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {"kind": "groups", "limit": 5})
    assert len(requests) == 100  # the safety cap, not an infinite loop
    assert result.structured_content is not None
    assert result.structured_content["count"] == 5


async def test_ordering_follows_message_time_not_updated_at(
    groupme_transport: TransportInstaller,
) -> None:
    # The group's updated_at was bumped by a metadata change, but its last
    # message is older than the chat's - the chat must list first, and the
    # group's last_active must agree with its preview.
    group = raw_group(
        "g1",
        NOW - 10,
        messages={
            "last_message_created_at": NOW - 600,
            "preview": {"nickname": "Ada", "text": "old news"},
        },
    )
    groupme_transport(paged_handler({1: [group]}, {1: [raw_chat("7", NOW - 300)]}))
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {})
    assert result.structured_content is not None
    conversations = result.structured_content["conversations"]
    assert [c["name"] for c in conversations] == ["User 7", "Group g1"]
    assert conversations[1]["last_active"] == "10m ago"
    assert conversations[1]["last_message"] == "Ada: old news"


async def test_concise_omits_detailed_fields(groupme_transport: TransportInstaller) -> None:
    group = raw_group(
        "g1",
        NOW,
        description="We read",
        share_url="https://groupme.com/join_group/g1/SHARE",
        creator_user_id="9",
    )
    groupme_transport(paged_handler({1: [group]}, {}))
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {"kind": "groups"})
    assert result.structured_content is not None
    summary = result.structured_content["conversations"][0]
    assert summary["group_id"] == "g1"  # chaining id survives concise mode
    assert summary.get("description") is None
    assert summary.get("share_url") is None
    assert summary.get("creator_user_id") is None
    assert summary.get("updated_at") is None


async def test_detailed_includes_metadata(groupme_transport: TransportInstaller) -> None:
    group = raw_group(
        "g1",
        NOW,
        description="We read",
        share_url="https://groupme.com/join_group/g1/SHARE",
        creator_user_id="9",
    )
    groupme_transport(paged_handler({1: [group]}, {1: [raw_chat("7", NOW - 10)]}))
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {"response_format": "detailed"})
    assert result.structured_content is not None
    by_kind = {c["kind"]: c for c in result.structured_content["conversations"]}
    assert by_kind["group"]["description"] == "We read"
    assert by_kind["group"]["share_url"] == "https://groupme.com/join_group/g1/SHARE"
    assert by_kind["group"]["creator_user_id"] == "9"
    assert by_kind["group"]["updated_at"] is not None
    assert by_kind["direct"]["conversation_id"] == "1+7"


async def test_empty_account_gets_a_note(groupme_transport: TransportInstaller) -> None:
    groupme_transport(paged_handler({}, {}))
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {})
    assert result.structured_content is not None
    assert result.structured_content["count"] == 0
    assert "No conversations" in result.structured_content["note"]


async def test_auth_error_is_actionable(groupme_transport: TransportInstaller) -> None:
    groupme_transport(lambda _: httpx2.Response(401, json={"meta": {"code": 401, "errors": []}}))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="GROUPME_ACCESS_TOKEN") as excinfo:
            await client.call_tool("list_conversations", {})
    assert "A valid call looks like" in str(excinfo.value)


async def test_out_of_range_limit_is_rejected(groupme_transport: TransportInstaller) -> None:
    requests = groupme_transport(paged_handler({}, {}))
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("list_conversations", {"limit": 0})
    assert requests == []
