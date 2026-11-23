"""Live write smoke tests: send, reply, and read back through the MCP tools.

These tests WRITE to GroupMe, so they only ever touch the dedicated test
group and topic owned by the token's account (``GROUPME_LIVE_GROUP_ID`` /
``GROUPME_LIVE_TOPIC_ID`` in ``.env``, see ``conftest.py``). They skip when
either id or the access token is missing; they never pick a group on their
own. Each run posts two short tagged messages to the test topic and nothing
to the test group's main chat.

They drive the real MCP protocol through FastMCP's in-memory client, so the
tool layer (validation, routing, rendering) is exercised end to end.

Run with: ``uv run pytest -m integration --no-cov``
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from groupme_mcp_server.client import GroupMeClient
from groupme_mcp_server.server import mcp
from groupme_mcp_server.tools import common

if TYPE_CHECKING:
    from groupme_mcp_server.models import GroupId
    from groupme_mcp_server.settings import Settings

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _tools_use_the_live_api(monkeypatch: pytest.MonkeyPatch, live_settings: Settings) -> None:
    monkeypatch.setattr(common, "create_client", lambda: GroupMeClient(live_settings))


@pytest.fixture
def topic_ref(live_group_id: GroupId, live_topic_id: GroupId) -> dict[str, str]:
    return {"kind": "topic", "parent_group_id": live_group_id, "topic_id": live_topic_id}


async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args)
    assert result.structured_content is not None
    return result.structured_content


async def newest_main_chat_id(group_id: GroupId) -> str | None:
    page = await call(
        "read_messages", {"conversation": {"kind": "group", "group_id": group_id}, "limit": 1}
    )
    return page["messages"][0]["id"] if page["messages"] else None


async def test_test_group_context_lists_the_test_topic(
    live_group_id: GroupId, live_topic_id: GroupId
) -> None:
    # get_conversation_context, not list_conversations: the inbox listing would
    # also fetch the topics of every other group on the account.
    context = await call("get_conversation_context", {"group_id": live_group_id})
    assert context["topic"] is None
    assert live_topic_id in [topic["topic_id"] for topic in context["topics"]]


async def test_send_reply_and_read_back_in_the_topic(
    live_group_id: GroupId, live_topic_id: GroupId, topic_ref: dict[str, str]
) -> None:
    tag = f"smoke-{uuid.uuid4().hex[:10]}"
    main_chat_before = await newest_main_chat_id(live_group_id)

    sent = await call("send_message", {"conversation": topic_ref, "text": f"{tag} topic send"})
    assert sent["kind"] == "topic"
    assert sent["topic_id"] == sent["conversation_id"] == live_topic_id
    assert sent["parent_group_id"] == live_group_id

    reply = await call(
        "send_message",
        {
            "conversation": topic_ref,
            "text": f"{tag} topic reply",
            "reply_to_message_id": sent["message_id"],
        },
    )
    assert reply["attachments"] == [f"reply to message {sent['message_id']}"]

    page = await call(
        "read_messages", {"conversation": topic_ref, "limit": 5, "response_format": "detailed"}
    )
    assert [m["text"] for m in page["messages"][-2:]] == [f"{tag} topic send", f"{tag} topic reply"]
    assert all(m["conversation_id"] == live_topic_id for m in page["messages"])

    found = await call("search_messages", {"conversation": topic_ref, "query": tag})
    assert found["count"] == 2

    # Topic messages never land in, or touch, the parent's main chat.
    assert await newest_main_chat_id(live_group_id) == main_chat_before

    context = await call(
        "get_conversation_context",
        {"group_id": live_group_id, "topic_id": live_topic_id, "recent_message_count": 2},
    )
    assert context["topic"]["topic_id"] == live_topic_id
    assert context["recent_messages"][-1]["text"] == f"{tag} topic reply"


async def test_send_to_a_mismatched_parent_posts_nothing(
    live_topic_id: GroupId, topic_ref: dict[str, str]
) -> None:
    tag = f"smoke-{uuid.uuid4().hex[:10]}"
    # "1" is not a group the account belongs to, so the topic cannot be under it.
    wrong = {"kind": "topic", "parent_group_id": "1", "topic_id": live_topic_id}
    with pytest.raises(ToolError, match="could not find that topic"):
        await call("send_message", {"conversation": wrong, "text": f"{tag} must not send"})
    found = await call("search_messages", {"conversation": topic_ref, "query": tag})
    assert found["count"] == 0


async def test_topic_highlights_respond(live_group_id: GroupId, live_topic_id: GroupId) -> None:
    highlights = await call(
        "get_highlights", {"group_id": live_group_id, "topic_id": live_topic_id, "period": "month"}
    )
    assert highlights["topic_id"] == live_topic_id
    assert highlights["group_id"] == live_group_id
