"""Live smoke tests of every MCP tool against the real GroupMe API.

The goal is release confidence: each tool is driven through the real MCP
protocol (FastMCP's in-memory client, so validation, routing, and rendering
all run) against the live API, in both kinds of group conversation, the test
group's main chat and its test topic.

These tests WRITE to GroupMe, so they only ever touch the dedicated test
group and topic owned by the token's account (``GROUPME_LIVE_GROUP_ID`` /
``GROUPME_LIVE_TOPIC_ID`` in ``.env``, see ``conftest.py``). They skip when
either id or the access token is missing and never pick a group on their own.
A run posts about a dozen short tagged messages split between the main chat
and the topic. Tags carry the GitHub Actions run id when there is one
(``smoke-gh<run id>-...``), else ``local``, so every message traces back to
the run that posted it.

GroupMe may take a moment to show a new message or like, so read-backs retry
until ``READ_BACK_TIMEOUT`` seconds have passed instead of reading once.

Not covered here: direct messages, which need a second account to talk to.

Run with: ``uv run pytest -m integration --no-cov``
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from groupme_mcp_server.client import GroupMeClient
from groupme_mcp_server.server import mcp
from groupme_mcp_server.tools import common

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from groupme_mcp_server.models import GroupId
    from groupme_mcp_server.settings import Settings

pytestmark = pytest.mark.integration

READ_BACK_TIMEOUT = 30.0
"""Seconds a read-back keeps retrying before the test fails."""

TEST_IMAGE_URL = "https://avatars.githubusercontent.com/u/80038?v=4"
"""A stable public image (the repository owner's avatar) to re-host."""

NOT_AN_IMAGE_URL = (
    "https://raw.githubusercontent.com/oddrationale/groupme-mcp-server/main/README.md"
)
"""A stable public URL that serves text, not an image (this repository's README)."""


def new_tag() -> str:
    """A unique message tag that names the run that posted it."""
    run = os.environ.get("GITHUB_RUN_ID", "").strip()
    return f"smoke-{f'gh{run}' if run else 'local'}-{uuid.uuid4().hex[:8]}"


async def eventually[T](check: Callable[[], Awaitable[T | None]], what: str) -> T:
    """Retry ``check`` every 2 seconds until it returns a value, or fail."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + READ_BACK_TIMEOUT
    while True:
        result = await check()
        if result is not None:
            return result
        if loop.time() >= deadline:
            pytest.fail(f"{what} not seen within {READ_BACK_TIMEOUT:g} seconds")
        await asyncio.sleep(2)


async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    async with Client(mcp) as client:
        result = await client.call_tool(tool, args)
    assert result.structured_content is not None
    return result.structured_content


@dataclass(frozen=True)
class Destination:
    """One of the two conversations under test, in the shapes the tools take."""

    name: str
    ref: dict[str, str]
    conversation_id: str
    context_args: dict[str, str]
    other: dict[str, str]  # the sibling conversation, which must stay untouched


@pytest.fixture(autouse=True)
def _tools_use_the_live_api(monkeypatch: pytest.MonkeyPatch, live_settings: Settings) -> None:
    monkeypatch.setattr(common, "create_client", lambda: GroupMeClient(live_settings))


@pytest.fixture
def main_chat_ref(live_group_id: GroupId) -> dict[str, str]:
    return {"kind": "group", "group_id": live_group_id}


@pytest.fixture
def topic_ref(live_group_id: GroupId, live_topic_id: GroupId) -> dict[str, str]:
    return {"kind": "topic", "parent_group_id": live_group_id, "topic_id": live_topic_id}


@pytest.fixture(params=["main_chat", "topic"])
def destination(
    request: pytest.FixtureRequest,
    live_group_id: GroupId,
    live_topic_id: GroupId,
    main_chat_ref: dict[str, str],
    topic_ref: dict[str, str],
) -> Destination:
    if request.param == "main_chat":
        return Destination(
            "main_chat", main_chat_ref, live_group_id, {"group_id": live_group_id}, topic_ref
        )
    return Destination(
        "topic",
        topic_ref,
        live_topic_id,
        {"group_id": live_group_id, "topic_id": live_topic_id},
        main_chat_ref,
    )


async def read(ref: dict[str, str], **args: object) -> list[dict[str, Any]]:
    page = await call(
        "read_messages", {"conversation": ref, "limit": 20, "response_format": "detailed", **args}
    )
    return page["messages"]


async def tagged(ref: dict[str, str], tag: str) -> list[dict[str, Any]]:
    return [m for m in await read(ref) if tag in (m["text"] or "")]


async def wait_for_tagged(ref: dict[str, str], tag: str, count: int) -> list[dict[str, Any]]:
    async def check() -> list[dict[str, Any]] | None:
        found = await tagged(ref, tag)
        return found if len(found) == count else None

    return await eventually(check, f"{count} message(s) tagged {tag}")


async def newest_id(ref: dict[str, str]) -> str | None:
    messages = await read(ref, limit=1)
    return messages[-1]["id"] if messages else None


# --- send, reply, read, page, search, context ------------------------------------------------


async def test_send_reply_read_page_search_and_context(destination: Destination) -> None:
    tag = new_tag()
    other_before = await newest_id(destination.other)

    sent = await call("send_message", {"conversation": destination.ref, "text": f"{tag} send"})
    assert sent["kind"] == destination.ref["kind"]
    assert sent["conversation_id"] == destination.conversation_id
    reply = await call(
        "send_message",
        {
            "conversation": destination.ref,
            "text": f"{tag} reply",
            "reply_to_message_id": sent["message_id"],
        },
    )
    assert reply["attachments"] == [f"reply to message {sent['message_id']}"]

    # read_messages, detailed: both messages, oldest first, with ids and senders.
    first, second = await wait_for_tagged(destination.ref, tag, 2)
    assert (first["text"], second["text"]) == (f"{tag} send", f"{tag} reply")
    assert (first["id"], second["id"]) == (sent["message_id"], reply["message_id"])
    assert second["attachments"] == [f"reply to message {sent['message_id']}"]
    assert first["conversation_id"] == destination.conversation_id
    assert first["sender_id"]
    assert first["created_at"]
    me = first["sender_name"]

    # read_messages, concise: same messages, no detailed-only fields.
    concise = await call("read_messages", {"conversation": destination.ref, "limit": 20})
    mine = [m for m in concise["messages"] if tag in (m["text"] or "")]
    assert [m["text"] for m in mine] == [f"{tag} send", f"{tag} reply"]
    assert all(m["sender_id"] is None and m["conversation_id"] is None for m in mine)

    # Paging: since_id returns what came after; before_id what came before.
    after = await read(destination.ref, since_id=sent["message_id"])
    assert reply["message_id"] in [m["id"] for m in after]
    assert sent["message_id"] not in [m["id"] for m in after]
    before = await read(destination.ref, before_id=reply["message_id"], limit=5)
    assert sent["message_id"] in [m["id"] for m in before]
    assert reply["message_id"] not in [m["id"] for m in before]

    # search_messages: by text, by text and sender, and a sender that matches nobody.
    found = await call("search_messages", {"conversation": destination.ref, "query": tag})
    assert [m["id"] for m in found["matches"]] == [reply["message_id"], sent["message_id"]]
    by_me = await call(
        "search_messages", {"conversation": destination.ref, "query": tag, "sender_name": me}
    )
    assert by_me["count"] == 2
    nobody = await call(
        "search_messages",
        {"conversation": destination.ref, "query": tag, "sender_name": f"nobody-{tag}"},
    )
    assert nobody["count"] == 0

    # get_conversation_context: the new messages, plus the right topic framing.
    context = await call(
        "get_conversation_context", {**destination.context_args, "recent_message_count": 20}
    )
    assert f"{tag} reply" in [m["text"] for m in context["recent_messages"]]
    assert me in [member["nickname"] for member in context["members"]]
    if destination.name == "topic":
        assert context["topic"]["topic_id"] == destination.conversation_id
    else:
        assert context["topic"] is None
        assert destination.other["topic_id"] in [t["topic_id"] for t in context["topics"]]

    # The sibling conversation never sees these messages.
    assert await newest_id(destination.other) == other_before


# --- react_to_message and get_highlights ------------------------------------------------------


async def likes_of(destination: Destination, message_id: str) -> int:
    messages = await read(destination.ref)
    return next(m["likes"] for m in messages if m["id"] == message_id)


async def test_like_unlike_and_highlights(destination: Destination) -> None:
    tag = new_tag()
    sent = await call("send_message", {"conversation": destination.ref, "text": f"{tag} like me"})
    message_id = sent["message_id"]
    await wait_for_tagged(destination.ref, tag, 1)
    reaction = {"conversation_id": sent["conversation_id"], "message_id": message_id}

    liked = await call("react_to_message", {**reaction, "action": "like"})
    assert (liked["action"], liked["message_id"]) == ("like", message_id)
    # Liking again is idempotent.
    await call("react_to_message", {**reaction, "action": "like"})

    async def liked_once() -> int | None:
        return 1 if await likes_of(destination, message_id) == 1 else None

    await eventually(liked_once, "the like")

    # GroupMe's likes leaderboard is a cached snapshot refreshed on its own
    # schedule per period, so a fresh like can take a while to appear (and an
    # unlike to disappear). Check that real leaderboard data comes back,
    # parsed and attributed to this conversation, not when the cache refreshes.
    async def highlights() -> dict[str, Any] | None:
        result = await call(
            "get_highlights",
            {**destination.context_args, "period": "month", "response_format": "detailed"},
        )
        return result if result["top_messages"] else None

    month = await eventually(highlights, "liked messages in this month's highlights")
    assert month["group_id"] == destination.context_args["group_id"]
    assert month["topic_id"] == destination.context_args.get("topic_id")
    assert all(m["message_id"] and m["likes"] >= 1 for m in month["top_messages"])
    assert month["top_members"]

    await call("react_to_message", {**reaction, "action": "unlike"})
    # Unliking again is idempotent too.
    await call("react_to_message", {**reaction, "action": "unlike"})

    async def unliked() -> int | None:
        return 0 if await likes_of(destination, message_id) == 0 else None

    await eventually(unliked, "the unlike")


# --- images -------------------------------------------------------------------------------------


async def test_images_from_any_url_and_from_groupme(destination: Destination) -> None:
    tag = new_tag()
    rehosted = await call(
        "send_message",
        {"conversation": destination.ref, "text": f"{tag} image", "image_url": TEST_IMAGE_URL},
    )
    (hosted,) = rehosted["attachments"]
    assert hosted.startswith("https://i.groupme.com/")

    # A GroupMe-hosted URL is attached as-is, and an image needs no text.
    again = await call(
        "send_message", {"conversation": destination.ref, "text": "", "image_url": hosted}
    )
    assert again["attachments"] == [hosted]

    (message,) = await wait_for_tagged(destination.ref, tag, 1)
    assert message["attachments"] == [hosted]
    newest = await read(destination.ref, limit=1)
    assert newest[-1]["id"] == again["message_id"]
    assert newest[-1]["attachments"] == [hosted]


# --- list_conversations ------------------------------------------------------------------------


async def test_list_conversations_shows_the_test_group_and_topic(
    live_group_id: GroupId, live_topic_id: GroupId, main_chat_ref: dict[str, str]
) -> None:
    # Posting to the main chat makes the test group the most recently active
    # group, so limit=1 lists only it: the inbox never fans out to the topics
    # of any other group on the account.
    tag = new_tag()
    await call("send_message", {"conversation": main_chat_ref, "text": f"{tag} inbox"})

    async def first_group() -> dict[str, Any] | None:
        inbox = await call(
            "list_conversations", {"kind": "groups", "limit": 1, "response_format": "detailed"}
        )
        (entry,) = inbox["conversations"]
        return entry if tag in (entry["last_message"] or "") else None

    entry = await eventually(first_group, "the test group at the top of the inbox")
    assert entry["kind"] == "group"
    assert entry["group_id"] == live_group_id
    assert entry["updated_at"]
    topic = next(t for t in entry["topics"] if t["topic_id"] == live_topic_id)
    assert topic["parent_group_id"] == live_group_id
    assert topic["last_active"]


# --- refusals ---------------------------------------------------------------------------------


async def test_bad_requests_are_refused_and_post_nothing(
    live_group_id: GroupId,
    live_topic_id: GroupId,
    main_chat_ref: dict[str, str],
    topic_ref: dict[str, str],
) -> None:
    tag = new_tag()
    refusals: list[tuple[dict[str, Any], str]] = [
        # The topic is not under group "1" (or anywhere the account can see).
        (
            {
                "conversation": {
                    "kind": "topic",
                    "parent_group_id": "1",
                    "topic_id": live_topic_id,
                },
                "text": f"{tag} wrong parent",
            },
            "could not find that topic",
        ),
        # Topic "1" is not a topic of the test group.
        (
            {
                "conversation": {
                    "kind": "topic",
                    "parent_group_id": live_group_id,
                    "topic_id": "1",
                },
                "text": f"{tag} wrong topic",
            },
            "could not find that topic",
        ),
        (
            {
                "conversation": topic_ref,
                "text": f"{tag} private",
                "image_url": "http://127.0.0.1/a.png",
            },
            "not a public internet address",
        ),
        (
            {"conversation": topic_ref, "text": f"{tag} html", "image_url": NOT_AN_IMAGE_URL},
            "did not return an image",
        ),
        ({"conversation": main_chat_ref, "text": "   "}, "empty"),
        ({"conversation": main_chat_ref, "text": f"{tag} " + "x" * 1000}, "1000"),
    ]
    for args, message in refusals:
        with pytest.raises(ToolError, match=message):
            await call("send_message", args)

    with pytest.raises(ToolError, match="could not find"):
        await call(
            "react_to_message",
            {"conversation_id": live_topic_id, "message_id": "1", "action": "like"},
        )

    for ref in (main_chat_ref, topic_ref):
        found = await call("search_messages", {"conversation": ref, "query": tag})
        assert found["count"] == 0, f"a refused send posted to {ref}"
