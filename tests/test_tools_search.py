from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from groupme_mcp_server.models import GroupId, GroupRef
from groupme_mcp_server.server import mcp
from groupme_mcp_server.tools.search import search_messages

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
    name: str = "Ada",
) -> dict[str, Any]:
    return {
        "id": message_id,
        "sender_id": "22",
        "name": name,
        "text": text,
        "created_at": created_at,
        "group_id": "42",
        "favorited_by": [],
        "attachments": [],
    }


def group_history_handler(
    pages: dict[str | None, list[dict[str, Any]] | int],
) -> Callable[[httpx2.Request], httpx2.Response]:
    """Serve group-message pages keyed by the ``before_id`` param (None = first)."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v3/groups/42/messages"
        before = dict(request.url.params).get("before_id")
        page = pages[before]
        if isinstance(page, int):
            return httpx2.Response(page)
        return httpx2.Response(200, json=envelope({"count": len(page), "messages": page}))

    return handler


async def test_found_early_stop_with_history_left(groupme_transport: TransportInstaller) -> None:
    # A full page (len == requested) whose matches satisfy the limit: the scan
    # must stop without claiming it reached the oldest message.
    page = [
        raw_message("m5", NOW - 10, text="pizza friday"),
        raw_message("m4", NOW - 20, text="nope"),
        raw_message("m3", NOW - 30, text="PIZZA again"),
        raw_message("m2", NOW - 40, text="unrelated"),
        raw_message("m1", NOW - 50, text="pizza pizza"),
    ]
    requests = groupme_transport(group_history_handler({None: page}))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_messages",
            {
                "conversation": GROUP_REF,
                "query": "pizza",
                "limit": 2,
                "max_messages_scanned": 5,
            },
        )
    assert len(requests) == 1
    assert dict(requests[0].url.params) == {"limit": "5"}
    page_out = result.structured_content
    assert page_out is not None
    assert [m["id"] for m in page_out["matches"]] == ["m5", "m3"]  # newest first, capped at limit
    assert page_out["count"] == 2
    # Examination stopped at the limit-hitting message: m2/m1 were never
    # scanned, and the resume cursor cannot skip them.
    assert page_out["messages_scanned"] == 3
    assert page_out["oldest_message_reached"] is False
    assert page_out["next_before_id"] == "m3"
    assert "more matches may exist" in page_out["note"]


async def test_scan_cap_truncation_is_reported_not_silent(
    groupme_transport: TransportInstaller,
) -> None:
    page = [
        raw_message("m4", NOW - 10, text="pizza"),
        raw_message("m3", NOW - 20),
        raw_message("m2", NOW - 30),
        raw_message("m1", NOW - 40),
    ]
    requests = groupme_transport(group_history_handler({None: page}))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_messages",
            {
                "conversation": GROUP_REF,
                "query": "pizza",
                "limit": 5,
                "max_messages_scanned": 4,
            },
        )
    # The page size is capped to exactly the scan budget.
    assert [dict(r.url.params) for r in requests] == [{"limit": "4"}]
    page_out = result.structured_content
    assert page_out is not None
    assert page_out["count"] == 1
    assert page_out["messages_scanned"] == 4
    assert page_out["oldest_message_reached"] is False
    assert "NOT searched" in page_out["note"]
    assert page_out["next_before_id"] == "m1"


async def test_history_exhausted_by_short_page(groupme_transport: TransportInstaller) -> None:
    page = [raw_message("m2", NOW - 10, text="pizza"), raw_message("m1", NOW - 20)]
    groupme_transport(group_history_handler({None: page}))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_messages", {"conversation": GROUP_REF, "query": "pizza", "limit": 5}
        )
    page_out = result.structured_content
    assert page_out is not None
    assert page_out["count"] == 1
    assert page_out["messages_scanned"] == 2
    assert page_out["oldest_message_reached"] is True
    assert page_out.get("note") is None  # complete scan needs no caveat


async def test_empty_conversation_scans_nothing_and_says_so(
    groupme_transport: TransportInstaller,
) -> None:
    groupme_transport(lambda _: httpx2.Response(304))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_messages", {"conversation": GROUP_REF, "query": "pizza"}
        )
    page_out = result.structured_content
    assert page_out is not None
    assert page_out["matches"] == []
    assert page_out["messages_scanned"] == 0
    assert page_out["oldest_message_reached"] is True
    assert page_out.get("next_before_id") is None
    assert "entire history" in page_out["note"]


async def test_direct_chat_search_ends_on_a_304_mid_scan(
    groupme_transport: TransportInstaller,
) -> None:
    first_page = [raw_message("m2", NOW - 10, text="lunch pizza"), raw_message("m1", NOW - 20)]

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v3/direct_messages"
        params = dict(request.url.params)
        assert params["other_user_id"] == "7"
        if "before_id" not in params:
            return httpx2.Response(200, json=envelope({"count": 2, "direct_messages": first_page}))
        assert params["before_id"] == "m1"
        return httpx2.Response(304)

    requests = groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_messages", {"conversation": DIRECT_REF, "query": "pizza", "limit": 5}
        )
    # A short DM page is inconclusive (no requested size), so a second fetch
    # was needed to prove the history was exhausted.
    assert len(requests) == 2
    page_out = result.structured_content
    assert page_out is not None
    assert [m["id"] for m in page_out["matches"]] == ["m2"]
    assert page_out["messages_scanned"] == 2
    assert page_out["oldest_message_reached"] is True


async def test_direct_chat_page_never_blows_past_the_scan_cap(
    groupme_transport: TransportInstaller,
) -> None:
    # /direct_messages takes no page-size parameter, so GroupMe may return
    # more messages than the remaining scan budget; the extras must not be
    # examined or counted.
    page = [raw_message(f"m{i}", NOW - 10 * i, text="pizza") for i in range(1, 21)]

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/v3/direct_messages"
        return httpx2.Response(200, json=envelope({"count": len(page), "direct_messages": page}))

    requests = groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_messages",
            {
                "conversation": DIRECT_REF,
                "query": "pizza",
                "limit": 50,
                "max_messages_scanned": 3,
            },
        )
    assert len(requests) == 1
    page_out = result.structured_content
    assert page_out is not None
    assert page_out["messages_scanned"] == 3
    assert [m["id"] for m in page_out["matches"]] == ["m1", "m2", "m3"]
    assert page_out["next_before_id"] == "m3"
    assert page_out["oldest_message_reached"] is False
    assert "NOT searched" in page_out["note"]


async def test_sender_filter_and_detailed_format(groupme_transport: TransportInstaller) -> None:
    page = [
        raw_message("m3", NOW - 10, text="pizza", name="Grace"),
        raw_message("m2", NOW - 20, text="pizza", name="Ada"),
        raw_message("m1", NOW - 30, text="salad", name="Ada"),
    ]
    groupme_transport(group_history_handler({None: page}))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_messages",
            {
                "conversation": GROUP_REF,
                "query": "pizza",
                "sender_name": "ada",
                "response_format": "detailed",
            },
        )
    page_out = result.structured_content
    assert page_out is not None
    assert [m["id"] for m in page_out["matches"]] == ["m2"]
    assert page_out["matches"][0]["sender_id"] == "22"
    assert page_out["matches"][0]["created_at"] is not None


async def test_sender_only_search_is_allowed(groupme_transport: TransportInstaller) -> None:
    page = [
        raw_message("m2", NOW - 10, name="Grace"),
        raw_message("m1", NOW - 20, name="Ada", text=None),  # textless still matches
    ]
    groupme_transport(group_history_handler({None: page}))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_messages", {"conversation": GROUP_REF, "query": "", "sender_name": "Ada"}
        )
    page_out = result.structured_content
    assert page_out is not None
    assert [m["id"] for m in page_out["matches"]] == ["m1"]


async def test_filterless_search_is_an_actionable_error_before_any_request(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(group_history_handler({None: []}))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="Invalid arguments") as excinfo:
            await client.call_tool("search_messages", {"conversation": GROUP_REF, "query": " "})
    assert "A valid call looks like" in str(excinfo.value)
    assert requests == []


async def test_progress_and_info_are_reported_during_the_scan(
    groupme_transport: TransportInstaller,
) -> None:
    # Page sizes track the cap: a full 100-message page, then the 50 remaining.
    first_page = [raw_message(f"a{i}", NOW - 10 * i) for i in range(1, 101)]
    second_page = [raw_message(f"b{i}", NOW - 2000 - 10 * i) for i in range(1, 51)]
    pages: dict[str | None, list[dict[str, Any]] | int] = {
        None: first_page,
        "a100": second_page,
    }
    requests = groupme_transport(group_history_handler(pages))
    progress: list[tuple[float, float | None]] = []
    logs: list[str] = []

    async def on_progress(value: float, total: float | None, message: str | None) -> None:  # noqa: ARG001
        progress.append((value, total))

    async def on_log(message: Any) -> None:  # noqa: ANN401 - fastmcp's LogMessage type
        logs.append(str(message.data))

    async with Client(mcp, log_handler=on_log) as client:
        await client.call_tool(
            "search_messages",
            {
                "conversation": GROUP_REF,
                "query": "pizza",
                "limit": 5,
                "max_messages_scanned": 150,
            },
            progress_handler=on_progress,
        )
    assert [dict(r.url.params) for r in requests] == [
        {"limit": "100"},
        {"limit": "50", "before_id": "a100"},
    ]
    assert progress == [(100.0, 150.0), (150.0, 150.0)]
    assert any("Searching backwards" in entry for entry in logs)
    assert any("Search finished" in entry for entry in logs)


async def test_direct_function_call_without_ctx_still_searches(
    groupme_transport: TransportInstaller,
) -> None:
    """The plain function works outside FastMCP (ctx is None: no progress)."""
    page = [raw_message("m1", NOW - 10, text="pizza")]
    groupme_transport(group_history_handler({None: page}))
    result = await search_messages(GroupRef(group_id=GroupId("42")), query="pizza")
    assert [m.id for m in result.matches] == ["m1"]
    assert result.oldest_message_reached is True


async def test_upstream_404_maps_to_actionable_not_found(
    groupme_transport: TransportInstaller,
) -> None:
    groupme_transport(lambda _: httpx2.Response(404, json={"meta": {"code": 404}}))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="list_conversations"):
            await client.call_tool("search_messages", {"conversation": GROUP_REF, "query": "pizza"})
