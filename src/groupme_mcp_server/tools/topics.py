"""Discover the separately addressed topics inside a GroupMe group."""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from groupme_mcp_server.models import GroupId
from groupme_mcp_server.rendering import ResponseFormat, TopicPage, TopicSummary
from groupme_mcp_server.tools import common


async def list_topics(
    group_id: Annotated[str, Field(pattern=r"^[0-9]+$")],
    response_format: ResponseFormat = "concise",
) -> TopicPage:
    """List topics within one group, including announcement topics.

    Use this after list_conversations or get_conversation_context reports
    children_count, or whenever you need a group's topic destinations.
    Pass a returned parent_group_id and topic_id with kind="topic" to
    read_messages, search_messages, or send_message. Announcement posting
    permissions remain enforced by GroupMe. This endpoint is undocumented;
    failures are reported rather than treated as an empty topic list.

    Args:
        group_id: Parent group id from list_conversations.
        response_format: Concise destinations or detailed descriptions too.

    Returns:
        Topics and their parent ids; an empty list means no topics were returned.
    """
    async with common.tool_client(
        'list_topics(group_id="12345678")',
        not_found_detail=(
            "Group or topic endpoint unavailable; verify the parent with list_conversations."
        ),
    ) as client:
        topics = await client.list_topics(GroupId(group_id))
    return TopicPage(
        group_id=group_id,
        topics=tuple(
            TopicSummary(
                topic_id=str(topic.id),
                parent_group_id=str(topic.parent_id),
                name=topic.name,
                type=topic.type,
                description=topic.description if response_format == "detailed" else None,
            )
            for topic in topics
        ),
        count=len(topics),
    )
