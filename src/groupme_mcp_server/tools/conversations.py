"""The ``list_conversations`` tool: a unified, recency-sorted inbox view."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from groupme_mcp_server.rendering import (
    ConversationPage,
    ResponseFormat,
    conversation_summary,
    merge_conversations,
)
from groupme_mcp_server.tools import common

if TYPE_CHECKING:
    from groupme_mcp_server.client import GroupMeClient
    from groupme_mcp_server.models import DirectChat, Group

_PAGE_SIZE = 50
_MAX_GROUP_PAGES = 100  # safety valve against an upstream that never sends a short page
_EXAMPLE = 'list_conversations(kind="all", limit=10)'
_EMPTY_NOTE = "No conversations found for this account and kind filter."


async def _all_groups(client: GroupMeClient) -> list[Group]:
    """Fetch every group page (up to a safety cap).

    GroupMe documents ``/groups`` as paginated but does not promise any
    ordering, so all pages must be seen before sorting locally - stopping
    early could drop a recently active group that sits on a later page.
    """
    groups: list[Group] = []
    for page in range(1, _MAX_GROUP_PAGES + 1):
        batch = await client.list_groups(page=page, per_page=_PAGE_SIZE)
        groups.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
    return groups


async def _all_chats(client: GroupMeClient, limit: int) -> list[DirectChat]:
    """Fetch up to ``limit`` direct chats, stitching upstream pages as needed.

    GroupMe documents ``/chats`` as sorted by most recent activity, so it is
    safe to stop once ``limit`` chats are in hand.
    """
    chats: list[DirectChat] = []
    page = 1
    while len(chats) < limit:
        batch = await client.list_chats(page=page, per_page=_PAGE_SIZE)
        chats.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        page += 1
    return chats


async def list_conversations(
    kind: Literal["groups", "dms", "all"] = "all",
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    response_format: ResponseFormat = "concise",
) -> ConversationPage:
    """List the user's GroupMe conversations, most recently active first.

    Use this first, whenever you need to find a conversation or the ids the
    other tools take: it merges groups and direct-message chats into one
    recency-sorted list with last-message previews and member counts. Every
    entry carries the ``group_id`` or ``other_user_id`` that ``read_messages``
    and ``get_conversation_context`` need.

    Args:
        kind: Which conversations to include - ``"groups"``, ``"dms"``, or
            ``"all"`` (default).
        limit: Maximum conversations to return (1-100).
        response_format: ``"concise"`` (default) for names, previews, and
            relative ages; ``"detailed"`` adds descriptions, share URLs,
            creator ids, and ISO timestamps.

    Returns:
        The merged conversation listing, most recently active first.
    """
    now = datetime.now(tz=UTC)
    detailed = response_format == "detailed"
    async with common.tool_client(_EXAMPLE) as client:
        groups = await _all_groups(client) if kind != "dms" else []
        chats = await _all_chats(client, limit) if kind != "groups" else []
    merged = merge_conversations(groups, chats, limit)
    summaries = tuple(conversation_summary(c, now, detailed=detailed) for c in merged)
    return ConversationPage(
        conversations=summaries,
        count=len(summaries),
        note=_EMPTY_NOTE if not summaries else None,
    )
