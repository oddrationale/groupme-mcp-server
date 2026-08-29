from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from groupme_mcp_server.models import Message
from groupme_mcp_server.search import (
    SearchFilters,
    SearchScan,
    apply_search_page,
    message_matches,
    next_page_size,
    scan_complete,
    validate_search_filters,
)

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)


def make_message(
    message_id: str = "m1",
    *,
    text: str | None = "hello world",
    sender_name: str = "Ada Lovelace",
    seconds_ago: int = 60,
) -> Message:
    return Message(
        id=message_id,
        conversation_id="42",
        sender_id="22",
        sender_name=sender_name,
        text=text,
        created_at=NOW - timedelta(seconds=seconds_ago),
        favorited_by_count=0,
        attachments=(),
    )


def filters(query: str = "hello", sender_name: str | None = None) -> SearchFilters:
    return SearchFilters(query=query, sender_name=sender_name)


# --- validate_search_filters ------------------------------------------------


@pytest.mark.parametrize(("query", "sender"), [("", None), ("   ", None), ("", "  ")])
def test_filterless_search_is_rejected(query: str, sender: str | None) -> None:
    with pytest.raises(ValueError, match="query is empty"):
        validate_search_filters(filters(query, sender))


@pytest.mark.parametrize(("query", "sender"), [("pizza", None), ("", "Ada"), ("pizza", "Ada")])
def test_any_real_filter_is_accepted(query: str, sender: str | None) -> None:
    validate_search_filters(filters(query, sender))


# --- message_matches --------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("hello", True),
        ("HELLO", True),  # case-insensitive
        ("  hello  ", True),  # filter whitespace ignored
        ("lo wo", True),  # substring across words
        ("goodbye", False),
        ("", True),  # empty query matches any text
    ],
)
def test_query_matches_text_case_insensitively(query: str, expected: bool) -> None:  # noqa: FBT001
    assert message_matches(make_message(), filters(query)) is expected


def test_textless_message_only_matches_an_empty_query() -> None:
    message = make_message(text=None)
    assert message_matches(message, filters("hello")) is False
    assert message_matches(message, filters("", "Ada")) is True


@pytest.mark.parametrize(
    ("sender", "expected"),
    [
        (None, True),
        ("", True),  # blank sender filter is no filter
        ("ada", True),
        ("LOVELACE", True),
        (" Ada ", True),
        ("Grace", False),
    ],
)
def test_sender_filter_matches_names_case_insensitively(
    sender: str | None,
    expected: bool,  # noqa: FBT001
) -> None:
    assert message_matches(make_message(), filters("hello", sender)) is expected


def test_both_filters_must_match() -> None:
    assert message_matches(make_message(), filters("hello", "Grace")) is False
    assert message_matches(make_message(text="bye"), filters("hello", "Ada")) is False
    assert message_matches(make_message(), filters("hello", "Ada")) is True


# --- apply_search_page ------------------------------------------------------


def test_apply_page_accumulates_matches_and_counts() -> None:
    page = [make_message("m3", text="pizza night"), make_message("m2", text="nope")]
    scan = apply_search_page(
        SearchScan(),
        page,
        requested=2,
        filters=filters("pizza"),
        limit=10,
        max_messages_scanned=500,
    )
    assert [str(m.id) for m in scan.matches] == ["m3"]
    assert scan.scanned == 2
    assert scan.oldest_reached is False  # full page: more history may exist
    assert scan.last_scanned_id == "m2"


def test_apply_page_stops_at_the_limit_without_advancing_the_cursor() -> None:
    # The 3rd message hits the limit; the 4th and 5th are never examined, so
    # the resume cursor stays on the final match and no match is discarded.
    page = [
        make_message("m5", text="pizza"),
        make_message("m4", text="nope"),
        make_message("m3", text="pizza"),
        make_message("m2", text="pizza"),
        make_message("m1", text="pizza"),
    ]
    scan = apply_search_page(
        SearchScan(),
        page,
        requested=5,
        filters=filters("pizza"),
        limit=2,
        max_messages_scanned=500,
    )
    assert [str(m.id) for m in scan.matches] == ["m5", "m3"]
    assert scan.scanned == 3  # only what was actually examined
    assert scan.last_scanned_id == "m3"
    assert scan.oldest_reached is False  # the page's tail was never examined


def test_apply_page_never_examines_past_the_scan_cap() -> None:
    # An unsizable (direct-message) page bigger than the remaining budget:
    # examination stops at the cap, honestly.
    page = [make_message(f"m{i}", text="pizza") for i in range(20, 0, -1)]
    scan = apply_search_page(
        SearchScan(scanned=498),
        page,
        requested=None,
        filters=filters("pizza"),
        limit=100,
        max_messages_scanned=500,
    )
    assert scan.scanned == 500
    assert len(scan.matches) == 2
    assert scan.last_scanned_id == "m19"
    assert scan.oldest_reached is False


def test_apply_short_page_means_oldest_reached_when_size_was_requested() -> None:
    page = [make_message("m1")]
    scan = apply_search_page(
        SearchScan(),
        page,
        requested=50,
        filters=filters("hello"),
        limit=10,
        max_messages_scanned=500,
    )
    assert scan.oldest_reached is True


def test_apply_short_page_is_inconclusive_without_a_requested_size() -> None:
    # Direct-message pages have no requested size; only an empty page ends them.
    page = [make_message("m1")]
    scan = apply_search_page(
        SearchScan(),
        page,
        requested=None,
        filters=filters("hello"),
        limit=10,
        max_messages_scanned=500,
    )
    assert scan.oldest_reached is False


def test_apply_partially_examined_short_page_is_not_oldest_reached() -> None:
    # The page is shorter than requested, but the limit stopped examination
    # before its end - the scan cannot claim it reached the oldest message.
    page = [make_message("m2", text="pizza"), make_message("m1", text="pizza")]
    scan = apply_search_page(
        SearchScan(),
        page,
        requested=50,
        filters=filters("pizza"),
        limit=1,
        max_messages_scanned=500,
    )
    assert scan.oldest_reached is False
    assert scan.last_scanned_id == "m2"


def test_apply_empty_page_always_means_oldest_reached() -> None:
    previous = SearchScan(matches=(), scanned=7, oldest_reached=False, last_scanned_id="m7")
    scan = apply_search_page(
        previous, [], requested=None, filters=filters("x"), limit=5, max_messages_scanned=500
    )
    assert scan.oldest_reached is True
    assert scan.scanned == 7
    assert scan.last_scanned_id == "m7"  # cursor survives an empty page


# --- scan_complete / next_page_size ----------------------------------------


def test_scan_stops_for_each_reason_and_otherwise_continues() -> None:
    match = make_message()
    fresh = SearchScan()
    assert scan_complete(fresh, limit=1, max_messages_scanned=100) is False
    enough = SearchScan(matches=(match,), scanned=10)
    assert scan_complete(enough, limit=1, max_messages_scanned=100) is True
    capped = SearchScan(scanned=100)
    assert scan_complete(capped, limit=1, max_messages_scanned=100) is True
    exhausted = SearchScan(oldest_reached=True)
    assert scan_complete(exhausted, limit=1, max_messages_scanned=100) is True


def test_next_page_size_never_exceeds_cap_or_page_limit() -> None:
    assert next_page_size(SearchScan(), max_messages_scanned=500, page_limit=100) == 100
    assert next_page_size(SearchScan(scanned=450), max_messages_scanned=500, page_limit=100) == 50
    assert next_page_size(SearchScan(scanned=499), max_messages_scanned=500, page_limit=100) == 1
