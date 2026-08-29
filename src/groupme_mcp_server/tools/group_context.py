"""The ``get_conversation_context`` tool: orient an agent in one group."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import Field

from groupme_mcp_server.models import MAX_MESSAGE_LIMIT, GroupId
from groupme_mcp_server.rendering import GroupContext, ResponseFormat, build_group_context
from groupme_mcp_server.tools import common

_EXAMPLE = 'get_conversation_context(group_id="12345678", recent_message_count=20)'


async def get_conversation_context(
    group_id: str,
    recent_message_count: Annotated[int, Field(ge=1, le=MAX_MESSAGE_LIMIT)] = 20,
    response_format: ResponseFormat = "concise",
) -> GroupContext:
    """Get one group's metadata, member list, and recent messages in one call.

    Use this to orient yourself in a group before reading further or
    replying: it bundles what would otherwise take several calls. For
    direct-message chats or for paging deeper into history, use
    ``read_messages`` instead.

    Args:
        group_id: The group's id (from ``list_conversations``).
        recent_message_count: How many recent messages to include (1-100).
        response_format: ``"concise"`` (default) for names, nicknames,
            roles, and relative ages; ``"detailed"`` adds user ids, the
            share URL, and ISO timestamps.

    Returns:
        The group's metadata, members (nicknames and roles), and recent
        messages oldest first.
    """
    now = datetime.now(tz=UTC)
    detailed = response_format == "detailed"
    async with common.tool_client(_EXAMPLE) as client:
        group = await client.get_group(GroupId(group_id))
        newest_first = await client.list_group_messages(
            GroupId(group_id), limit=recent_message_count
        )
    return build_group_context(group, newest_first, now, detailed=detailed)
