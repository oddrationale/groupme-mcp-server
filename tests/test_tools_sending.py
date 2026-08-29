from __future__ import annotations

import json
import logging
import time
import uuid
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

GROUP_TARGET = {"kind": "group", "group_id": "42"}
DIRECT_TARGET = {"kind": "direct", "other_user_id": "7"}

GROUPME_IMAGE_URL = "https://i.groupme.com/640x480.jpeg.abc123"


def envelope(data: object) -> dict[str, Any]:
    return {"response": data, "meta": {"code": 201}}


def created_message(sent: dict[str, Any], **extra: str) -> dict[str, Any]:
    """Echo the sent body back the way GroupMe echoes a created message."""
    return {
        "id": "m100",
        "sender_id": "me1",
        "name": "Me",
        "text": sent["text"],
        "created_at": NOW,
        "favorited_by": [],
        "attachments": sent["attachments"],
        **extra,
    }


def group_send_handler(request: httpx2.Request) -> httpx2.Response:
    assert request.method == "POST"
    assert request.url.path == "/v3/groups/42/messages"
    sent = json.loads(request.content)["message"]
    return httpx2.Response(201, json=envelope({"message": created_message(sent, group_id="42")}))


def direct_send_handler(request: httpx2.Request) -> httpx2.Response:
    assert request.method == "POST"
    assert request.url.path == "/v3/direct_messages"
    sent = json.loads(request.content)["direct_message"]
    # A brand-new chat: GroupMe's echo carries no conversation_id yet.
    return httpx2.Response(201, json=envelope({"direct_message": created_message(sent)}))


async def test_group_send_posts_to_the_group_endpoint(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(group_send_handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "send_message", {"conversation": GROUP_TARGET, "text": "Hello everyone!"}
        )
    body = json.loads(requests[0].content)
    assert body == {
        "message": {
            "source_guid": body["message"]["source_guid"],
            "text": "Hello everyone!",
            "attachments": [],
        }
    }
    sent = result.structured_content
    assert sent is not None
    assert sent["message_id"] == "m100"
    assert sent["kind"] == "group"
    assert sent["group_id"] == "42"
    assert sent.get("other_user_id") is None
    assert sent["conversation_id"] == "42"
    assert sent["text"] == "Hello everyone!"
    assert sent["sent_at"] is not None


async def test_direct_send_routes_the_recipient(groupme_transport: TransportInstaller) -> None:
    requests = groupme_transport(direct_send_handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "send_message", {"conversation": DIRECT_TARGET, "text": "Hi!"}
        )
    body = json.loads(requests[0].content)
    assert body == {
        "direct_message": {
            "source_guid": body["direct_message"]["source_guid"],
            "text": "Hi!",
            "attachments": [],
            "recipient_id": "7",
        }
    }
    assert uuid.UUID(body["direct_message"]["source_guid"]).version == 4
    sent = result.structured_content
    assert sent is not None
    assert sent["kind"] == "direct"
    assert sent["other_user_id"] == "7"
    assert sent.get("group_id") is None
    assert sent.get("conversation_id") is None  # new chat: GroupMe sent no id yet


async def test_source_guid_is_a_fresh_uuid4_per_call(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(group_send_handler)
    async with Client(mcp) as client:
        await client.call_tool("send_message", {"conversation": GROUP_TARGET, "text": "one"})
        await client.call_tool("send_message", {"conversation": GROUP_TARGET, "text": "two"})
    guids = [json.loads(request.content)["message"]["source_guid"] for request in requests]
    assert len(set(guids)) == 2  # GroupMe silently dedups reused guids
    for guid in guids:
        assert uuid.UUID(guid).version == 4


async def test_reply_becomes_a_reply_attachment(groupme_transport: TransportInstaller) -> None:
    requests = groupme_transport(group_send_handler)
    async with Client(mcp) as client:
        await client.call_tool(
            "send_message",
            {"conversation": GROUP_TARGET, "text": "replying", "reply_to_message_id": "m0"},
        )
    body = json.loads(requests[0].content)
    assert body["message"]["attachments"] == [
        {"type": "reply", "reply_id": "m0", "base_reply_id": "m0"}
    ]


async def test_groupme_image_url_is_attached_directly(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(group_send_handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "send_message",
            {"conversation": GROUP_TARGET, "text": "look", "image_url": GROUPME_IMAGE_URL},
        )
    body = json.loads(requests[0].content)
    assert body["message"]["attachments"] == [{"type": "image", "url": GROUPME_IMAGE_URL}]
    sent = result.structured_content
    assert sent is not None
    assert sent["attachments"] == [GROUPME_IMAGE_URL]


@pytest.mark.parametrize(
    "image_url",
    [
        "https://example.com/cat.jpg",  # wrong host
        "http://i.groupme.com/640x480.jpeg.abc123",  # wrong scheme
    ],
)
async def test_non_groupme_image_url_is_rejected_before_the_api(
    groupme_transport: TransportInstaller, image_url: str
) -> None:
    requests = groupme_transport(group_send_handler)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="image-service") as excinfo:
            await client.call_tool(
                "send_message",
                {"conversation": GROUP_TARGET, "text": "look", "image_url": image_url},
            )
    assert "not supported yet" in str(excinfo.value)
    assert "A valid call looks like" in str(excinfo.value)
    assert requests == []


async def test_overlong_text_is_rejected_before_the_api(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(group_send_handler)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="at most 1000") as excinfo:
            await client.call_tool(
                "send_message", {"conversation": GROUP_TARGET, "text": "x" * 1001}
            )
    assert "1001 characters" in str(excinfo.value)
    assert requests == []


async def test_empty_text_without_attachments_is_rejected(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(group_send_handler)
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="text is empty"):
            await client.call_tool("send_message", {"conversation": GROUP_TARGET, "text": "   "})
    assert requests == []


async def test_attachment_only_message_may_have_empty_text(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(group_send_handler)
    async with Client(mcp) as client:
        result = await client.call_tool(
            "send_message",
            {"conversation": GROUP_TARGET, "text": "", "image_url": GROUPME_IMAGE_URL},
        )
    assert json.loads(requests[0].content)["message"]["text"] == ""
    assert result.structured_content is not None


@pytest.mark.parametrize(
    "conversation",
    [
        {"group_id": "42", "other_user_id": "7"},  # no discriminator
        {"kind": "group", "group_id": "42", "other_user_id": "7"},  # extras forbidden
        {"kind": "direct", "other_user_id": "7", "group_id": "42"},  # extras forbidden
    ],
)
async def test_conversation_mixing_ids_is_unrepresentable(
    groupme_transport: TransportInstaller, conversation: dict[str, str]
) -> None:
    requests = groupme_transport(group_send_handler)
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("send_message", {"conversation": conversation, "text": "hello"})
    assert requests == []


async def test_message_text_is_never_logged_at_info_or_above(
    groupme_transport: TransportInstaller,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Production keeps the package logger off the root handler; re-enable
    # propagation so caplog observes exactly what would be logged.
    monkeypatch.setattr(logging.getLogger("groupme_mcp_server"), "propagate", True)
    groupme_transport(group_send_handler)
    user_text = "the launch code is hunter2"
    with caplog.at_level(logging.INFO):
        async with Client(mcp) as client:
            await client.call_tool(
                "send_message", {"conversation": GROUP_TARGET, "text": user_text}
            )
    messages = [record.getMessage() for record in caplog.records]
    assert any("m100" in message for message in messages)  # ids are logged...
    for message in messages:  # ...message content never is
        assert user_text not in message
        assert "hunter2" not in message
