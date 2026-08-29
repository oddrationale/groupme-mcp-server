"""Pure scan logic for the ``search_messages`` tool.

GroupMe has no server-side search API, so searching means paging backwards
through message history client-side. This module is part of the functional
core: the tool's imperative loop fetches pages and folds them into a
[`SearchScan`][groupme_mcp_server.search.SearchScan] with
[`apply_search_page`][groupme_mcp_server.search.apply_search_page], asking
[`scan_complete`][groupme_mcp_server.search.scan_complete] when to stop.
Nothing here performs IO.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from groupme_mcp_server.models import GroupMeModel, Message, MessageId

if TYPE_CHECKING:
    from collections.abc import Sequence


class SearchFilters(GroupMeModel):
    """What a search is looking for.

    Both filters are case-insensitive substring matches with surrounding
    whitespace ignored. An empty ``query`` matches any text (a sender-only
    search); a ``sender_name`` of ``None`` applies no sender filter.
    """

    query: str
    sender_name: str | None = None


class SearchScan(GroupMeModel):
    """Accumulated state of one backwards scan through message history.

    ``matches`` holds the matching messages found so far in scan order
    (newest first, since the scan walks backwards from the present).
    ``last_scanned_id`` is the id of the last message actually examined —
    the ``before_id`` cursor for continuing the scan; messages after it were
    never looked at, even if they arrived on an already-fetched page.
    """

    matches: tuple[Message, ...] = ()
    scanned: int = 0
    oldest_reached: bool = False
    last_scanned_id: MessageId | None = None


def validate_search_filters(filters: SearchFilters) -> None:
    """Reject a search that filters on nothing.

    Args:
        filters: The requested filters.

    Raises:
        ValueError: If both the query and the sender filter are empty, which
            would match every message.
    """
    if not filters.query.strip() and (
        filters.sender_name is None or not filters.sender_name.strip()
    ):
        msg = (
            "query is empty; provide text to search for "
            "(or a sender_name to filter by sender alone)"
        )
        raise ValueError(msg)


def message_matches(message: Message, filters: SearchFilters) -> bool:
    """Decide whether one message satisfies the search filters.

    Args:
        message: The message to test.
        filters: The search filters. A message with no text can only match
            an empty query.

    Returns:
        True when the message passes every supplied filter.
    """
    needle = filters.query.strip().lower()
    if needle and needle not in (message.text or "").lower():
        return False
    sender = filters.sender_name.strip().lower() if filters.sender_name is not None else ""
    return not sender or sender in message.sender_name.lower()


def apply_search_page(
    scan: SearchScan,
    page: Sequence[Message],
    *,
    requested: int | None,
    filters: SearchFilters,
    limit: int,
    max_messages_scanned: int,
) -> SearchScan:
    """Fold one fetched history page (newest first) into the scan state.

    Messages are examined one at a time, stopping the moment ``limit``
    matches exist or ``max_messages_scanned`` messages have been examined in
    total — so ``scanned`` never overshoots the cap and ``last_scanned_id``
    never advances past an unexamined (or discarded) message, which keeps
    the resume cursor exact.

    Args:
        scan: The state accumulated so far.
        page: The messages fetched, newest first; empty when GroupMe
            answered HTTP 304 (no messages beyond the cursor).
        requested: The page size asked of GroupMe, or ``None`` when the
            endpoint takes no size parameter (direct messages). A fully
            examined page shorter than ``requested`` means the oldest
            message was reached; an empty page always does.
        filters: The search filters.
        limit: Maximum matches to collect.
        max_messages_scanned: Total examination cap across the whole scan.

    Returns:
        The new scan state.
    """
    matches = list(scan.matches)
    examined = 0
    last_scanned_id = scan.last_scanned_id
    for message in page[: max_messages_scanned - scan.scanned]:
        examined += 1
        last_scanned_id = message.id
        if message_matches(message, filters):
            matches.append(message)
            if len(matches) >= limit:
                break
    whole_page_examined = examined == len(page)
    return SearchScan(
        matches=tuple(matches),
        scanned=scan.scanned + examined,
        oldest_reached=(
            scan.oldest_reached
            or not page
            or (whole_page_examined and requested is not None and len(page) < requested)
        ),
        last_scanned_id=last_scanned_id,
    )


def scan_complete(scan: SearchScan, *, limit: int, max_messages_scanned: int) -> bool:
    """Decide whether the scan loop should stop.

    Args:
        scan: The state accumulated so far.
        limit: Matches requested by the caller.
        max_messages_scanned: The caller's scan cap.

    Returns:
        True once enough matches are found, the cap is hit, or the oldest
        message has been reached.
    """
    return scan.oldest_reached or len(scan.matches) >= limit or scan.scanned >= max_messages_scanned


def next_page_size(scan: SearchScan, *, max_messages_scanned: int, page_limit: int) -> int:
    """Size the next group-message page so the scan never exceeds its cap.

    Args:
        scan: The state accumulated so far (must not be complete).
        max_messages_scanned: The caller's scan cap.
        page_limit: The largest page the endpoint accepts.

    Returns:
        The ``limit`` to request for the next page, at least 1.
    """
    return min(page_limit, max_messages_scanned - scan.scanned)
