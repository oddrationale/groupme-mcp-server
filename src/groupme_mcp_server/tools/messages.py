"""The ``read_messages`` tool: unified group and direct-message reading."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import Field

from groupme_mcp_server.models import (
    MAX_MESSAGE_LIMIT,
    ConversationRef,
    GroupId,
    GroupRef,
    MessageId,
    UserId,
)
from groupme_mcp_server.rendering import (
    MessagePage,
    ResponseFormat,
    build_message_page,
    select_newest,
)
from groupme_mcp_server.tools import common

_EXAMPLE = (
    'read_messages(conversation={"kind": "group", "group_id": "12345678"}, limit=20) or '
    'read_messages(conversation={"kind": "direct", "other_user_id": "87654321"})'
)


async def read_messages(
    conversation: ConversationRef,
    before_id: str | None = None,
    since_id: str | None = None,
    limit: Annotated[int, Field(ge=1, le=MAX_MESSAGE_LIMIT)] = 20,
    response_format: ResponseFormat = "concise",
) -> MessagePage:
    """Read messages from one GroupMe conversation, oldest first.

    Use this to read or page through the history of a specific group or
    direct-message chat once you know its id (from ``list_conversations``).
    Sender names are resolved and attachments are normalized (image URLs,
    reply/mention summaries). An empty page is a normal answer, not an
    error: it means the conversation has no messages in the requested range.

    Args:
        conversation: Which conversation to read: ``{"kind": "group",
            "group_id": ...}`` or ``{"kind": "direct", "other_user_id":
            ...}``.
        before_id: Read messages older than this message id (use the
            previous page's ``next_before_id``). At most one of ``before_id``
            and ``since_id`` may be given.
        since_id: Read the most recent messages newer than this message id.
        limit: Maximum messages to return (1-100). Direct chats may return
            fewer per page regardless of ``limit``.
        response_format: ``"concise"`` (default) for sender names, text,
            relative ages, and like counts; ``"detailed"`` adds sender ids,
            conversation ids, and ISO timestamps.

    Returns:
        The messages oldest first, with ``next_before_id`` for paging
        further back and a ``note`` when the page is empty.
    """
    now = datetime.now(tz=UTC)
    detailed = response_format == "detailed"
    before = MessageId(before_id) if before_id is not None else None
    since = MessageId(since_id) if since_id is not None else None
    async with common.tool_client(_EXAMPLE) as client:
        if isinstance(conversation, GroupRef):
            newest_first = await client.list_group_messages(
                GroupId(conversation.group_id),
                before_id=before,
                since_id=since,
                limit=limit,
            )
        else:
            newest_first = await client.list_direct_messages(
                UserId(conversation.other_user_id),
                before_id=before,
                since_id=since,
            )
    return build_message_page(select_newest(newest_first, limit), now, detailed=detailed)
