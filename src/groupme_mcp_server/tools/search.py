"""The ``search_messages`` tool: client-side search over message history.

GroupMe has no search API, so this tool pages backwards through a
conversation's history and filters client-side. The imperative shell here
only fetches pages and reports progress; every matching, stopping, and
accounting decision lives in the pure [`groupme_mcp_server.search`][] module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from groupme_mcp_server.models import (
    MAX_MESSAGE_LIMIT,
    ConversationRef,
    GroupId,
    GroupRef,
    UserId,
)
from groupme_mcp_server.rendering import MessageSearchPage, ResponseFormat, build_search_page
from groupme_mcp_server.search import (
    SearchFilters,
    SearchScan,
    apply_search_page,
    next_page_size,
    scan_complete,
    validate_search_filters,
)
from groupme_mcp_server.tools import common

MAX_SCAN_CAP = 5000
"""Largest slice of history one search call may scan."""

_EXAMPLE = (
    'search_messages(conversation={"kind": "group", "group_id": "12345678"}, '
    'query="pizza", limit=5)'
)


async def search_messages(  # noqa: PLR0913, PLR0917 - the search contract needs each knob; ctx is injected
    conversation: ConversationRef,
    query: str,
    sender_name: str | None = None,
    limit: Annotated[int, Field(ge=1, le=MAX_MESSAGE_LIMIT)] = 20,
    max_messages_scanned: Annotated[int, Field(ge=1, le=MAX_SCAN_CAP)] = 500,
    response_format: ResponseFormat = "concise",
    ctx: Context | None = None,
) -> MessageSearchPage:
    """Search one conversation's message history for matching messages.

    Use this to find specific messages ("who mentioned pizza?", "what did
    Ada say last week?") instead of paging manually with ``read_messages``.
    GroupMe has no search API, so this scans backwards from the newest
    message, matching ``query`` against message text and ``sender_name``
    against sender names (both case-insensitive substrings), until ``limit``
    matches are found, the oldest message is reached, or
    ``max_messages_scanned`` messages have been examined. The result reports
    exactly how far the scan got — check ``oldest_message_reached`` and
    ``note`` before concluding something was never said.

    Args:
        conversation: Which conversation to search: ``{"kind": "group",
            "group_id": ...}`` or ``{"kind": "direct", "other_user_id":
            ...}`` (ids from ``list_conversations``).
        query: Text to look for in message text. May be empty only when
            ``sender_name`` is given (a sender-only search).
        sender_name: Only match messages whose sender's display name
            contains this.
        limit: Stop after this many matches (1-100).
        max_messages_scanned: Stop after examining this many messages
            (1-5000); a hit cap is reported in ``note``, never silent.
        response_format: ``"concise"`` (default) for sender names, text,
            relative ages, and like counts; ``"detailed"`` adds sender ids,
            conversation ids, and ISO timestamps.
        ctx: The request context, injected by FastMCP, used for progress
            reporting during long scans.

    Returns:
        Matching messages newest first, with honest scan accounting:
        ``messages_scanned``, ``oldest_message_reached``, a ``note`` for any
        truncation, and ``next_before_id`` for continuing into unscanned
        history via ``read_messages``.
    """
    now = datetime.now(tz=UTC)
    detailed = response_format == "detailed"
    filters = SearchFilters(query=query, sender_name=sender_name)
    scan = SearchScan()
    async with common.tool_client(_EXAMPLE) as client:
        validate_search_filters(filters)
        if ctx is not None:
            await ctx.info(
                f"Searching backwards through up to {max_messages_scanned} message(s) "
                f"for up to {limit} match(es)."
            )
        while not scan_complete(scan, limit=limit, max_messages_scanned=max_messages_scanned):
            if isinstance(conversation, GroupRef):
                requested = next_page_size(
                    scan, max_messages_scanned=max_messages_scanned, page_limit=MAX_MESSAGE_LIMIT
                )
                page = await client.list_group_messages(
                    GroupId(conversation.group_id),
                    before_id=scan.last_scanned_id,
                    limit=requested,
                )
            else:
                requested = None
                page = await client.list_direct_messages(
                    UserId(conversation.other_user_id), before_id=scan.last_scanned_id
                )
            scan = apply_search_page(
                scan,
                page,
                requested=requested,
                filters=filters,
                limit=limit,
                max_messages_scanned=max_messages_scanned,
            )
            if ctx is not None:
                # apply_search_page never examines past the cap, so scanned
                # is always a valid progress value.
                await ctx.report_progress(scan.scanned, max_messages_scanned)
        if ctx is not None:
            await ctx.info(
                f"Search finished: {len(scan.matches)} match(es) in "
                f"{scan.scanned} scanned message(s)."
            )
    return build_search_page(scan, limit, now, detailed=detailed)
