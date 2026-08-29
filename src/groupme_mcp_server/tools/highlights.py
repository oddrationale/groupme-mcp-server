"""The ``get_highlights`` tool: what mattered recently in one group."""

from __future__ import annotations

from datetime import UTC, datetime

from groupme_mcp_server.models import GroupId, LeaderboardPeriod
from groupme_mcp_server.rendering import GroupHighlights, ResponseFormat, build_group_highlights
from groupme_mcp_server.tools import common

_EXAMPLE = 'get_highlights(group_id="12345678", period="week")'

_NOT_FOUND_DETAIL = (
    "GroupMe answered 404 for the likes leaderboard. Either the group_id is wrong "
    "(get valid ids from list_conversations), or GroupMe has retired the leaderboard "
    "endpoint - it is undocumented and may disappear. If the group_id works with "
    "get_conversation_context, fall back to read_messages and aggregate like counts "
    "yourself."
)


async def get_highlights(
    group_id: str,
    period: LeaderboardPeriod = "week",
    response_format: ResponseFormat = "concise",
) -> GroupHighlights:
    """Summarize what mattered in a group: its most-liked recent messages.

    Use this to catch up on a busy group without reading everything: it
    wraps GroupMe's likes leaderboard into the period's top-liked messages
    (sender, like count, text preview) plus a per-member summary of who was
    most liked and most represented among them. The member summary covers
    only the leaderboard's messages, not the group's full history. The
    leaderboard endpoint is undocumented; if GroupMe has retired it, this
    tool fails with guidance rather than guessing.

    Args:
        group_id: The group's id (from ``list_conversations``).
        period: The leaderboard window: ``"day"``, ``"week"`` (default), or
            ``"month"``.
        response_format: ``"concise"`` (default) for names, previews, and
            relative ages; ``"detailed"`` adds user ids and ISO timestamps.

    Returns:
        The top-liked messages (most liked first) and member summary, or an
        empty result with a ``note`` when nothing was liked in the period.
    """
    now = datetime.now(tz=UTC)
    detailed = response_format == "detailed"
    async with common.tool_client(_EXAMPLE, not_found_detail=_NOT_FOUND_DETAIL) as client:
        messages = await client.leaderboard(GroupId(group_id), period)
    return build_group_highlights(messages, group_id, period, now, detailed=detailed)
