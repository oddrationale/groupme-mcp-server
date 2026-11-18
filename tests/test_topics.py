from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import TypeAdapter, ValidationError

from groupme_mcp_server.models import ConversationRef, parse_group, parse_topic
from groupme_mcp_server.rendering import build_group_context, conversation_summary
from groupme_mcp_server.server import mcp

if TYPE_CHECKING:
    from collections.abc import Callable

    TransportInstaller = Callable[..., list[httpx2.Request]]

TOPIC_REF = {"kind": "topic", "parent_group_id": "42", "topic_id": "43"}
TOPIC = {"id": 43, "parent_id": 42, "topic": "Announcements", "type": "announcement"}


def response(data: object, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json={"response": data, "meta": {"code": status}})


def message() -> dict[str, Any]:
    return {
        "id": "m1",
        "group_id": "43",
        "sender_id": "7",
        "name": "Tester",
        "created_at": 1700000000,
        "text": "topic test",
        "attachments": [],
        "favorited_by": [],
    }


@pytest.mark.parametrize("ids", [(43, 42), ("43", "42")])
def test_topic_parser(ids: tuple[int | str, int | str]) -> None:
    topic = parse_topic({**TOPIC, "id": ids[0], "parent_id": ids[1], "extra": True})
    assert topic.id == "43"
    assert topic.parent_id == "42"
    assert topic.name == "Announcements"
    assert topic.description is None
    assert parse_topic({**TOPIC, "type": "future"}).type == "future"


def test_children_count() -> None:
    assert parse_group({"id": "42", "name": "Test", "children_count": 2}).children_count == 2
    assert parse_group({"id": "42", "name": "Test"}).children_count is None


@pytest.mark.parametrize("detailed", [False, True])
def test_children_count_visible_in_context_and_inbox(*, detailed: bool) -> None:
    group = parse_group({"id": "42", "name": "Test", "children_count": 2})
    now = datetime.now(UTC)
    assert conversation_summary(group, now, detailed=detailed).children_count == 2
    assert build_group_context(group, [], now, detailed=detailed).children_count == 2


@pytest.mark.parametrize(
    "change",
    [{"topic_id": "../42"}, {"parent_group_id": ""}, {"group_id": "42"}, {"other_user_id": "7"}],
)
def test_topic_reference_rejects_ambiguous_ids(change: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ConversationRef).validate_python({**TOPIC_REF, **change})


@pytest.mark.parametrize("detailed", [False, True])
async def test_discovery(groupme_transport: TransportInstaller, *, detailed: bool) -> None:
    requests = groupme_transport(lambda _: response([{**TOPIC, "description": "Trip details"}]))
    async with Client(mcp) as client:
        result = await client.call_tool(
            "list_topics",
            {
                "group_id": "42",
                "response_format": "detailed" if detailed else "concise",
            },
        )
    assert result.structured_content == {
        "group_id": "42",
        "count": 1,
        "topics": [
            {
                "topic_id": "43",
                "parent_group_id": "42",
                "name": "Announcements",
                "type": "announcement",
                "description": "Trip details" if detailed else None,
            }
        ],
    }
    assert requests[0].url.path == "/v3/groups/42/subgroups"
    assert requests[0].headers["X-Access-Token"] == "test-token"
    assert "token" not in str(requests[0].url)


async def test_empty_discovery(groupme_transport: TransportInstaller) -> None:
    groupme_transport(lambda _: response([]))
    async with Client(mcp) as client:
        result = await client.call_tool("list_topics", {"group_id": "42"})
    assert result.structured_content == {"group_id": "42", "topics": [], "count": 0}


@pytest.mark.parametrize(
    "payload", [{}, [None], [{}], [{**TOPIC, "id": None}], [{**TOPIC, "parent_id": 99}]]
)
async def test_malformed_discovery(groupme_transport: TransportInstaller, payload: object) -> None:
    groupme_transport(lambda _: response(payload))
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("list_topics", {"group_id": "42"})


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read_messages", {"before_id": "older", "limit": 5, "response_format": "detailed"}),
        ("search_messages", {"query": "topic", "limit": 1}),
        (
            "send_message",
            {
                "text": "topic test",
                "reply_to_message_id": "m0",
                "image_url": "https://i.groupme.com/test.png",
            },
        ),
    ],
)
async def test_topic_routing(
    groupme_transport: TransportInstaller, tool: str, args: dict[str, Any]
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.url.path.endswith("/subgroups"):
            return response([TOPIC])
        assert req.url.path == "/v3/groups/43/messages"
        if req.method == "POST":
            body = json.loads(req.content)["message"]
            assert body["text"] == "topic test"
            assert body["attachments"] == [
                {"type": "reply", "reply_id": "m0", "base_reply_id": "m0"},
                {"type": "image", "url": "https://i.groupme.com/test.png"},
            ]
            return response({"message": {**message(), "attachments": body["attachments"]}}, 201)
        if tool == "read_messages":
            assert req.url.params["before_id"] == "older"
        return response({"messages": [message()]})

    requests = groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool(tool, {"conversation": TOPIC_REF, **args})
    assert len(requests) == 2
    data = result.structured_content
    assert data is not None
    if tool == "send_message":
        assert data["kind"] == "topic"
        assert data["parent_group_id"] == "42"
        assert data["topic_id"] == data["conversation_id"] == "43"
        assert data["group_id"] is None
        assert data["other_user_id"] is None
        assert data["attachments"] == [
            "reply to message m0",
            "https://i.groupme.com/test.png",
        ]
    else:
        assert data["messages" if tool == "read_messages" else "matches"][0]["id"] == "m1"


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read_messages", {}),
        ("search_messages", {"query": "x"}),
        ("send_message", {"text": "must not send"}),
    ],
)
async def test_missing_topic_never_falls_back(
    groupme_transport: TransportInstaller, tool: str, args: dict[str, Any]
) -> None:
    requests = groupme_transport(lambda _: response([{**TOPIC, "id": 44}]))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="not available under this parent"):
            await client.call_tool(tool, {"conversation": TOPIC_REF, **args})
    assert len(requests) == 1


@pytest.mark.parametrize("status", [401, 403, 404, 420, 429, 500])
async def test_discovery_errors(groupme_transport: TransportInstaller, status: int) -> None:
    groupme_transport(lambda _: response(None, status))
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("list_topics", {"group_id": "42"})


@pytest.mark.parametrize("missing", ["parent", "topic"])
@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("read_messages", {}),
        ("search_messages", {"query": "trip"}),
        ("send_message", {"text": "trip details"}),
    ],
)
async def test_topic_not_found_guidance_preserves_destination(
    groupme_transport: TransportInstaller, missing: str, tool: str, args: dict[str, str]
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if missing == "topic" and req.url.path.endswith("subgroups"):
            return response([TOPIC])
        return response(None, 404)

    requests = groupme_transport(handler)
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as error:
            await client.call_tool(tool, {"conversation": TOPIC_REF, **args})
    guidance = str(error.value)
    assert "list_topics with parent_group_id to verify topic_id" in guidance
    assert "keep the topic destination" in guidance
    assert f'{tool}(conversation={{"kind": "topic"' in guidance
    assert [req.url.path for req in requests] == (
        ["/v3/groups/42/subgroups"]
        if missing == "parent"
        else ["/v3/groups/42/subgroups", "/v3/groups/43/messages"]
    )


@pytest.mark.parametrize("failure", ["timeout", "forbidden", "server", "malformed", "deleted"])
async def test_send_failure_not_retried_or_rerouted(
    groupme_transport: TransportInstaller, failure: str
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.method == "GET":
            return response([TOPIC])
        assert req.url.path == "/v3/groups/43/messages"
        if failure == "timeout":
            msg = "sensitive token must not appear"
            raise httpx2.ReadTimeout(msg, request=req)
        if failure == "server":
            return response(None, 500)
        if failure == "malformed":
            return response({"message": {}}, 201)
        if failure == "deleted":
            return response(None, 404)
        return response(None, 403)

    requests = groupme_transport(handler, max_retries=3)
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as error:
            await client.call_tool("send_message", {"conversation": TOPIC_REF, "text": "test"})
    assert "sensitive token" not in str(error.value)
    assert len(requests) == 2


async def test_empty_topic_history(groupme_transport: TransportInstaller) -> None:
    groupme_transport(
        lambda req: (
            response([TOPIC]) if req.url.path.endswith("subgroups") else httpx2.Response(304)
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool("read_messages", {"conversation": TOPIC_REF})
    assert result.structured_content is not None
    assert result.structured_content["messages"] == []
    assert result.structured_content["next_before_id"] is None


async def test_topic_search_pages_without_revalidating_parent(
    groupme_transport: TransportInstaller,
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.url.path.endswith("subgroups"):
            return response([TOPIC, {**TOPIC, "id": 44}])
        assert req.url.path == "/v3/groups/43/messages"
        if "before_id" not in req.url.params:
            assert req.url.params["limit"] == "100"
            return response({"messages": [{**message(), "id": str(i)} for i in range(100, 0, -1)]})
        assert req.url.params["before_id"] == "1"
        return response({"messages": [{**message(), "id": "0", "text": "needle"}]})

    requests = groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_messages", {"conversation": TOPIC_REF, "query": "needle"}
        )
    assert len(requests) == 3
    assert result.structured_content is not None
    assert result.structured_content["messages_scanned"] == 101
    assert result.structured_content["matches"][0]["id"] == "0"


async def test_topic_since_cursor(groupme_transport: TransportInstaller) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.url.path.endswith("subgroups"):
            return response([TOPIC])
        assert req.url.path == "/v3/groups/43/messages"
        assert req.url.params["since_id"] == "previous"
        return response({"messages": [message()]})

    groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "read_messages", {"conversation": TOPIC_REF, "since_id": "previous"}
        )
    assert result.structured_content is not None
    assert result.structured_content["next_before_id"] == "m1"


@pytest.mark.parametrize("text", ["", " ", "x" * 1001])
async def test_invalid_topic_text_never_sends(
    groupme_transport: TransportInstaller, text: str
) -> None:
    requests = groupme_transport(lambda _: response([TOPIC]))
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("send_message", {"conversation": TOPIC_REF, "text": text})
    assert requests == []


@pytest.mark.parametrize("status", [420, 429])
async def test_topic_rate_limit_retry_keeps_destination_and_dedupe_id(
    groupme_transport: TransportInstaller, status: int
) -> None:
    bodies: list[bytes] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.url.path.endswith("subgroups"):
            return response([TOPIC])
        assert req.method == "POST"
        assert req.url.path == "/v3/groups/43/messages"
        bodies.append(req.content)
        if len(bodies) == 1:
            return response(None, status)
        return response({"message": message()}, 201)

    groupme_transport(handler, max_retries=1)
    async with Client(mcp) as client:
        await client.call_tool("send_message", {"conversation": TOPIC_REF, "text": "topic test"})
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    assert json.loads(bodies[0])["message"]["source_guid"]
