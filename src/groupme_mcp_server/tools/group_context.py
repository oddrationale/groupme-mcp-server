"""The ``get_conversation_context`` tool: orient an agent in one group or topic."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import Field

from groupme_mcp_server.models import MAX_MESSAGE_LIMIT, GroupId
from groupme_mcp_server.rendering import GroupContext, ResponseFormat, build_group_context
from groupme_mcp_server.tools import common

_EXAMPLE = (
    'get_conversation_context(group_id="12345678", recent_message_count=20) or '
    'get_conversation_context(group_id="12345678", topic_id="23456789")'
)


async def get_conversation_context(
    group_id: str,
    topic_id: Annotated[str | None, Field(pattern=r"^[0-9]+$")] = None,
    recent_message_count: Annotated[int, Field(ge=1, le=MAX_MESSAGE_LIMIT)] = 20,
    response_format: ResponseFormat = "concise",
) -> GroupContext:
    """Get one group's metadata, member list, and recent messages in one call.

    Use this to orient yourself in a group before reading further or
    replying: it bundles what would otherwise take several calls. For a
    group's main chat it also lists the group's topics; pass ``topic_id`` to
    get a topic's details and recent messages instead (members are then the
    parent group's). For direct-message chats or for paging deeper into
    history, use ``read_messages`` instead.

    Args:
        group_id: The group's id (from ``list_conversations``); for a topic,
            its parent group's id.
        topic_id: A topic of that group (from the group's ``topics`` in
            ``list_conversations``), or omit for the group's main chat.
        recent_message_count: How many recent messages to include (1-100).
        response_format: ``"concise"`` (default) for names, nicknames,
            roles, and relative ages; ``"detailed"`` adds user ids, the
            share URL, descriptions, and ISO timestamps.

    Returns:
        The group's metadata, members (nicknames and roles), topics or the
        selected topic, and recent messages oldest first.
    """
    now = datetime.now(tz=UTC)
    detailed = response_format == "detailed"
    parent = GroupId(group_id)
    not_found = common.TOPIC_NOT_FOUND_DETAIL if topic_id is not None else None
    async with common.tool_client(_EXAMPLE, not_found_detail=not_found) as client:
        group = await client.get_group(parent)
        if topic_id is not None:
            topic = await client.get_topic(parent, GroupId(topic_id))
            newest_first = await client.list_group_messages(topic.id, limit=recent_message_count)
            return build_group_context(group, newest_first, now, detailed=detailed, topic=topic)
        topics = await client.list_topics(parent) if group.topic_count > 0 else []
        newest_first = await client.list_group_messages(parent, limit=recent_message_count)
    return build_group_context(group, newest_first, now, detailed=detailed, topics=topics)
