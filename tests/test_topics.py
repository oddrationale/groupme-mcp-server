"""Group topics (GroupMe "subgroups") across the models, client, and tools."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import SecretStr, TypeAdapter, ValidationError

from groupme_mcp_server.client import GroupMeClient
from groupme_mcp_server.errors import GroupMeApiError, GroupMeNotFoundError
from groupme_mcp_server.models import (
    ConversationRef,
    GroupId,
    GroupRef,
    TopicRef,
    parse_group,
    parse_topic,
)
from groupme_mcp_server.rendering import sort_topics, topic_summary
from groupme_mcp_server.server import mcp
from groupme_mcp_server.settings import Settings
from groupme_mcp_server.tools import common

if TYPE_CHECKING:
    from collections.abc import Callable

    Handler = Callable[[httpx2.Request], httpx2.Response]
    TransportInstaller = Callable[..., list[httpx2.Request]]

NOW = int(time.time())
TOPIC_REF = {"kind": "topic", "parent_group_id": "42", "topic_id": "43"}


def response(data: object, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json={"response": data, "meta": {"code": status}})


def raw_topic(topic_id: int = 43, parent_id: int = 42, **extra: object) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": topic_id,
        "parent_id": parent_id,
        "topic": f"Topic {topic_id}",
        "type": "private",
        "description": "",
    }
    raw.update(extra)
    return raw


def with_preview(raw: dict[str, Any], at: int, text: str = "hi") -> dict[str, Any]:
    return {
        **raw,
        "updated_at": at,
        "messages": {
            "count": 3,
            "last_message_created_at": at,
            "preview": {"nickname": "Ada", "text": text},
        },
    }


def raw_group(group_id: str = "42", children: object = 2, **extra: object) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": group_id,
        "name": f"Group {group_id}",
        "children_count": children,
        "updated_at": NOW,
        "members": [{"user_id": "7", "nickname": "Ada", "roles": ["admin"]}],
    }
    raw.update(extra)
    return raw


def raw_message(message_id: str = "m1", conversation: str = "43") -> dict[str, Any]:
    return {
        "id": message_id,
        "group_id": conversation,
        "sender_id": "7",
        "name": "Ada",
        "created_at": NOW,
        "text": "topic words",
        "attachments": [],
        "favorited_by": ["8"],
    }


def client_for(handler: Handler) -> GroupMeClient:
    return GroupMeClient(
        Settings(access_token=SecretStr("t"), api_base_url="https://api.groupme.test/v3"),
        transport=httpx2.MockTransport(handler),
        max_retries=0,
    )


# --- models -------------------------------------------------------------------------------


@pytest.mark.parametrize("ids", [(43, 42), ("43", "42")])
def test_parse_topic_normalizes_ids_and_preview(ids: tuple[object, object]) -> None:
    raw = with_preview(raw_topic(), NOW, text="56741")
    topic = parse_topic({**raw, "id": ids[0], "parent_id": ids[1], "extra": True})
    assert (topic.id, topic.parent_id, topic.name, topic.type) == (
        "43",
        "42",
        "Topic 43",
        "private",
    )
    assert topic.description is None  # GroupMe sends "" for no description
    assert topic.preview is not None
    assert topic.preview.sender_name == "Ada"
    assert topic.preview.text == "56741"
    assert topic.updated_at == datetime.fromtimestamp(NOW, tz=UTC)


def test_parse_topic_keeps_unknown_types_and_descriptions() -> None:
    topic = parse_topic(raw_topic(type="future-kind", description="About this topic"))
    assert topic.type == "future-kind"
    assert topic.description == "About this topic"
    assert topic.preview is None
    assert topic.updated_at is None


@pytest.mark.parametrize(
    ("children", "expected"), [(6, 6), (0, 0), (None, 0), ("6", 0), (True, 0), (-1, 0)]
)
def test_group_topic_count(children: object, expected: int) -> None:
    assert parse_group(raw_group(children=children)).topic_count == expected


def test_group_topic_count_defaults_to_zero_when_absent() -> None:
    assert parse_group({"id": "1", "name": "No topics"}).topic_count == 0


@pytest.mark.parametrize(
    "change",
    [
        {"topic_id": "../42"},
        {"parent_group_id": ""},
        {"group_id": "42"},
        {"other_user_id": "7"},
    ],
)
def test_topic_reference_rejects_malformed_or_mixed_ids(change: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(ConversationRef).validate_python({**TOPIC_REF, **change})


def test_topic_reference_parses() -> None:
    ref = TypeAdapter(ConversationRef).validate_python(TOPIC_REF)
    assert ref == TopicRef(parent_group_id=GroupId("42"), topic_id=GroupId("43"))


# --- rendering ------------------------------------------------------------------------------


def test_sort_topics_puts_recent_first_and_unknown_last() -> None:
    old = parse_topic(with_preview(raw_topic(1), NOW - 3600))
    new = parse_topic(with_preview(raw_topic(2), NOW))
    unknown = parse_topic(raw_topic(3))
    assert [t.id for t in sort_topics([unknown, old, new])] == ["2", "1", "3"]


@pytest.mark.parametrize("detailed", [False, True])
def test_topic_summary(*, detailed: bool) -> None:
    now = datetime.fromtimestamp(NOW, tz=UTC)
    raw = with_preview(raw_topic(description="Pics"), NOW - 120, text="look")
    view = topic_summary(parse_topic(raw), now, detailed=detailed)
    assert view.topic_id == "43"
    assert view.parent_group_id == "42"
    assert view.last_message == "Ada: look"
    assert view.last_active == "2m ago"
    assert view.description == ("Pics" if detailed else None)
    assert (view.updated_at is not None) is detailed


def test_topic_summary_without_activity() -> None:
    view = topic_summary(parse_topic(raw_topic()), datetime.now(UTC), detailed=False)
    assert view.last_message is None
    assert view.last_active is None


# --- client ---------------------------------------------------------------------------------


async def test_client_lists_topics() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        assert req.url.path == "/v3/groups/42/subgroups"
        return response([raw_topic(43), raw_topic(44)])

    async with client_for(handler) as client:
        topics = await client.list_topics(GroupId("42"))
    assert [t.id for t in topics] == ["43", "44"]


@pytest.mark.parametrize("payload", [{}, [None], [{}], [raw_topic(parent_id=99)]])
async def test_client_rejects_malformed_topic_listings(payload: object) -> None:
    async with client_for(lambda _: response(payload)) as client:
        with pytest.raises(GroupMeApiError):
            await client.list_topics(GroupId("42"))


async def test_client_gets_one_topic() -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        assert req.url.path == "/v3/groups/42/subgroups/43"
        return response(raw_topic())

    async with client_for(handler) as client:
        topic = await client.get_topic(GroupId("42"), GroupId("43"))
    assert topic.name == "Topic 43"


@pytest.mark.parametrize("payload", [raw_topic(parent_id=99), raw_topic(topic_id=44), [], {}])
async def test_client_rejects_a_mismatched_topic(payload: object) -> None:
    async with client_for(lambda _: response(payload)) as client:
        with pytest.raises(GroupMeApiError):
            await client.get_topic(GroupId("42"), GroupId("43"))


async def test_client_topic_lookup_404() -> None:
    async with client_for(lambda _: response(None, 404)) as client:
        with pytest.raises(GroupMeNotFoundError):
            await client.get_topic(GroupId("42"), GroupId("43"))


# --- shared tool helpers ----------------------------------------------------------------------


def test_message_group_id_uses_the_topic_never_the_parent() -> None:
    topic = TopicRef(parent_group_id=GroupId("42"), topic_id=GroupId("43"))
    assert common.message_group_id(topic) == "43"
    assert common.message_group_id(GroupRef(group_id=GroupId("42"))) == "42"


def test_not_found_detail_for() -> None:
    topic = TopicRef(parent_group_id=GroupId("42"), topic_id=GroupId("43"))
    assert common.not_found_detail_for(topic) == common.TOPIC_NOT_FOUND_DETAIL
    assert common.not_found_detail_for(GroupRef(group_id=GroupId("42"))) is None


# --- list_conversations -----------------------------------------------------------------------


def inbox_handler(
    groups: list[dict[str, Any]],
    topics: dict[str, httpx2.Response],
) -> Handler:
    def handler(req: httpx2.Request) -> httpx2.Response:
        path = req.url.path
        if path == "/v3/groups":
            return response(groups if req.url.params["page"] == "1" else [])
        if path == "/v3/chats":
            return response([])
        assert path.endswith("/subgroups"), path
        return topics[path.split("/")[3]]

    return handler


@pytest.mark.parametrize("detailed", [False, True])
async def test_list_conversations_nests_topics_under_their_group(
    groupme_transport: TransportInstaller, *, detailed: bool
) -> None:
    topics = [
        with_preview(raw_topic(43, description="Old one"), NOW - 7200, text="older"),
        with_preview(raw_topic(44, type="announcement"), NOW - 60, text="newer"),
    ]
    requests = groupme_transport(
        inbox_handler(
            [raw_group("42"), raw_group("50", children=0)],
            {"42": response(topics)},
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool(
            "list_conversations",
            {"response_format": "detailed" if detailed else "concise"},
        )
    data = result.structured_content
    assert data is not None
    assert data["note"] is None
    by_id = {c["group_id"]: c for c in data["conversations"]}
    assert by_id["50"]["topics"] is None
    listed = by_id["42"]["topics"]
    assert [t["topic_id"] for t in listed] == ["44", "43"]  # most recently active first
    assert listed[0] == {
        "topic_id": "44",
        "parent_group_id": "42",
        "name": "Topic 44",
        "type": "announcement",
        "last_message": "Ada: newer",
        "last_active": "1m ago",
        "description": None,
        "updated_at": datetime.fromtimestamp(NOW - 60, tz=UTC).isoformat() if detailed else None,
    }
    assert listed[1]["description"] == ("Old one" if detailed else None)
    assert "children_count" not in by_id["42"]
    # Only the group that has topics is asked for them.
    assert [r.url.path for r in requests if r.url.path.endswith("subgroups")] == [
        "/v3/groups/42/subgroups"
    ]


async def test_list_conversations_survives_a_topic_lookup_failure(
    groupme_transport: TransportInstaller,
) -> None:
    groupme_transport(
        inbox_handler(
            [raw_group("42", name="Leaders"), raw_group("60", updated_at=NOW - 10)],
            {"42": response(None, 500), "60": response([raw_topic(61, parent_id=60)])},
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {"kind": "groups"})
    data = result.structured_content
    assert data is not None
    by_id = {c["group_id"]: c for c in data["conversations"]}
    assert by_id["42"]["topics"] is None
    assert [t["topic_id"] for t in by_id["60"]["topics"]] == ["61"]
    assert "Topics could not be loaded for: Leaders" in data["note"]


async def test_list_conversations_only_fetches_topics_of_listed_groups(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(
        inbox_handler(
            [raw_group("42"), raw_group("70", updated_at=NOW - 999)],
            {"42": response([])},
        )
    )
    async with Client(mcp) as client:
        result = await client.call_tool("list_conversations", {"kind": "groups", "limit": 1})
    data = result.structured_content
    assert data is not None
    assert [c["group_id"] for c in data["conversations"]] == ["42"]
    assert data["conversations"][0]["topics"] is None  # no topics came back
    assert "/v3/groups/70/subgroups" not in [r.url.path for r in requests]


async def test_list_conversations_dms_never_fetch_topics(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(lambda _: response([]))
    async with Client(mcp) as client:
        await client.call_tool("list_conversations", {"kind": "dms"})
    assert [r.url.path for r in requests] == ["/v3/chats"]


# --- read_messages / search_messages ----------------------------------------------------------


async def test_read_messages_reads_the_topic_not_the_parent(
    groupme_transport: TransportInstaller,
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        assert req.url.path == "/v3/groups/43/messages"
        assert req.url.params["before_id"] == "older"
        return response({"messages": [raw_message()]})

    requests = groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "read_messages",
            {"conversation": TOPIC_REF, "before_id": "older", "response_format": "detailed"},
        )
    assert len(requests) == 1  # reads need no extra validation round trip
    data = result.structured_content
    assert data is not None
    assert data["messages"][0]["conversation_id"] == "43"


async def test_empty_topic_history(groupme_transport: TransportInstaller) -> None:
    groupme_transport(lambda _: httpx2.Response(304))
    async with Client(mcp) as client:
        result = await client.call_tool("read_messages", {"conversation": TOPIC_REF})
    data = result.structured_content
    assert data is not None
    assert data["messages"] == []
    assert data["next_before_id"] is None


async def test_search_messages_pages_through_the_topic(
    groupme_transport: TransportInstaller,
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        assert req.url.path == "/v3/groups/43/messages"
        if "before_id" not in req.url.params:
            return response({"messages": [raw_message(str(i)) for i in range(100, 0, -1)]})
        assert req.url.params["before_id"] == "1"
        return response({"messages": [{**raw_message("0"), "text": "needle"}]})

    requests = groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_messages", {"conversation": TOPIC_REF, "query": "needle"}
        )
    assert len(requests) == 2  # both pages from the topic; no validation round trip
    data = result.structured_content
    assert data is not None
    assert data["messages_scanned"] == 101
    assert data["matches"][0]["id"] == "0"


@pytest.mark.parametrize(
    ("tool", "args"),
    [("read_messages", {}), ("search_messages", {"query": "x"})],
)
async def test_topic_404_guidance_keeps_the_topic_destination(
    groupme_transport: TransportInstaller, tool: str, args: dict[str, str]
) -> None:
    groupme_transport(lambda _: response(None, 404))
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as error:
            await client.call_tool(tool, {"conversation": TOPIC_REF, **args})
    message = str(error.value)
    assert "could not find that topic" in message
    assert "main chat" in message


# --- send_message -----------------------------------------------------------------------------


async def test_send_to_topic_checks_the_parent_then_posts_to_the_topic(
    groupme_transport: TransportInstaller,
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.method == "GET":
            assert req.url.path == "/v3/groups/42/subgroups/43"
            return response(raw_topic())
        assert req.url.path == "/v3/groups/43/messages"
        body = json.loads(req.content)["message"]
        assert body["text"] == "topic words"
        assert body["attachments"] == [
            {"type": "reply", "reply_id": "m0", "base_reply_id": "m0"},
            {"type": "image", "url": "https://i.groupme.com/test.png"},
        ]
        return response({"message": {**raw_message(), "attachments": body["attachments"]}}, 201)

    requests = groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "send_message",
            {
                "conversation": TOPIC_REF,
                "text": "topic words",
                "reply_to_message_id": "m0",
                "image_url": "https://i.groupme.com/test.png",
            },
        )
    assert [r.method for r in requests] == ["GET", "POST"]
    data = result.structured_content
    assert data is not None
    assert data["kind"] == "topic"
    assert data["parent_group_id"] == "42"
    assert data["topic_id"] == data["conversation_id"] == "43"
    assert data["group_id"] is None
    assert data["other_user_id"] is None
    assert data["attachments"] == ["reply to message m0", "https://i.groupme.com/test.png"]


@pytest.mark.parametrize("lookup", [response(None, 404), response(raw_topic(parent_id=99))])
async def test_send_never_posts_when_the_topic_is_not_under_the_parent(
    groupme_transport: TransportInstaller, lookup: httpx2.Response
) -> None:
    requests = groupme_transport(lambda _: lookup)
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as error:
            await client.call_tool(
                "send_message", {"conversation": TOPIC_REF, "text": "must not send"}
            )
    assert [r.method for r in requests] == ["GET"]
    assert "topic" in str(error.value)


@pytest.mark.parametrize("failure", ["timeout", "forbidden", "server", "malformed", "deleted"])
async def test_send_failure_is_not_retried_or_rerouted(
    groupme_transport: TransportInstaller, failure: str
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.method == "GET":
            return response(raw_topic())
        assert req.url.path == "/v3/groups/43/messages"
        if failure == "timeout":
            msg = "sensitive token must not appear"
            raise httpx2.ReadTimeout(msg, request=req)
        statuses = {"forbidden": 403, "server": 500, "deleted": 404}
        if failure in statuses:
            return response(None, statuses[failure])
        return response({"message": {}}, 201)

    requests = groupme_transport(handler, max_retries=3)
    async with Client(mcp) as client:
        with pytest.raises(ToolError) as error:
            await client.call_tool("send_message", {"conversation": TOPIC_REF, "text": "hi"})
    assert "sensitive token" not in str(error.value)
    assert [r.url.path for r in requests] == [
        "/v3/groups/42/subgroups/43",
        "/v3/groups/43/messages",
    ]


@pytest.mark.parametrize("text", ["", " ", "x" * 1001])
async def test_invalid_topic_text_never_reaches_groupme(
    groupme_transport: TransportInstaller, text: str
) -> None:
    requests = groupme_transport(lambda _: response(raw_topic()))
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("send_message", {"conversation": TOPIC_REF, "text": text})
    assert requests == []


@pytest.mark.parametrize("status", [420, 429])
async def test_rate_limited_topic_send_retries_with_the_same_destination_and_guid(
    groupme_transport: TransportInstaller, status: int
) -> None:
    bodies: list[bytes] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.method == "GET":
            return response(raw_topic())
        assert req.url.path == "/v3/groups/43/messages"
        bodies.append(req.content)
        if len(bodies) == 1:
            return response(None, status)
        return response({"message": raw_message()}, 201)

    groupme_transport(handler, max_retries=1)
    async with Client(mcp) as client:
        await client.call_tool("send_message", {"conversation": TOPIC_REF, "text": "hi"})
    assert len(bodies) == 2
    assert bodies[0] == bodies[1]
    assert json.loads(bodies[0])["message"]["source_guid"]


# --- get_conversation_context -----------------------------------------------------------------


async def test_group_context_lists_the_groups_topics(groupme_transport: TransportInstaller) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.url.path == "/v3/groups/42":
            return response(raw_group())
        if req.url.path == "/v3/groups/42/subgroups":
            return response([with_preview(raw_topic(), NOW)])
        assert req.url.path == "/v3/groups/42/messages"
        return response({"messages": [raw_message(conversation="42")]})

    groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool("get_conversation_context", {"group_id": "42"})
    data = result.structured_content
    assert data is not None
    assert data["topic"] is None
    assert [t["topic_id"] for t in data["topics"]] == ["43"]
    assert "children_count" not in data


async def test_group_context_skips_topic_lookup_for_groups_without_topics(
    groupme_transport: TransportInstaller,
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.url.path == "/v3/groups/42":
            return response(raw_group(children=0))
        assert req.url.path == "/v3/groups/42/messages"
        return response({"messages": []})

    requests = groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool("get_conversation_context", {"group_id": "42"})
    assert result.structured_content is not None
    assert result.structured_content["topics"] == []
    assert len(requests) == 2


async def test_topic_context_uses_parent_members_and_topic_messages(
    groupme_transport: TransportInstaller,
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if req.url.path == "/v3/groups/42":
            return response(raw_group())
        if req.url.path == "/v3/groups/42/subgroups/43":
            return response(with_preview(raw_topic(type="announcement"), NOW - 300))
        assert req.url.path == "/v3/groups/43/messages"
        assert req.url.params["limit"] == "5"
        return response({"messages": [raw_message()]})

    groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_conversation_context",
            {"group_id": "42", "topic_id": "43", "recent_message_count": 5},
        )
    data = result.structured_content
    assert data is not None
    assert data["group_id"] == "42"
    assert data["topic"]["topic_id"] == "43"
    assert data["topic"]["type"] == "announcement"
    assert data["topics"] == []
    assert data["last_active"] == "5m ago"  # the topic's activity, not the group's
    assert [m["nickname"] for m in data["members"]] == ["Ada"]
    assert [m["text"] for m in data["recent_messages"]] == ["topic words"]


async def test_topic_context_404_names_the_topic(groupme_transport: TransportInstaller) -> None:
    groupme_transport(
        lambda req: (
            response(raw_group()) if req.url.path == "/v3/groups/42" else response(None, 404)
        )
    )
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="could not find that topic"):
            await client.call_tool("get_conversation_context", {"group_id": "42", "topic_id": "9"})


# --- get_highlights ---------------------------------------------------------------------------


async def test_highlights_for_a_topic_use_the_topic_leaderboard(
    groupme_transport: TransportInstaller,
) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        assert req.url.path == "/v3/groups/43/likes"
        assert req.url.params["period"] == "month"
        return response({"messages": [raw_message()]})

    groupme_transport(handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "get_highlights", {"group_id": "42", "topic_id": "43", "period": "month"}
        )
    data = result.structured_content
    assert data is not None
    assert data["group_id"] == "42"
    assert data["topic_id"] == "43"
    assert data["top_messages"][0]["likes"] == 1


async def test_group_highlights_have_no_topic_id(groupme_transport: TransportInstaller) -> None:
    groupme_transport(lambda _: response({"messages": []}))
    async with Client(mcp) as client:
        result = await client.call_tool("get_highlights", {"group_id": "42"})
    assert result.structured_content is not None
    assert result.structured_content["topic_id"] is None
