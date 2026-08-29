from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from groupme_mcp_server.models import (
    DirectChat,
    Group,
    ImageAttachment,
    LocationAttachment,
    Member,
    MentionsAttachment,
    Message,
    MessagePreview,
    ReplyAttachment,
    UnknownAttachment,
)
from groupme_mcp_server.rendering import (
    EMPTY_HIGHLIGHTS_NOTE,
    EMPTY_MESSAGES_NOTE,
    TOP_MEMBERS_LIMIT,
    TOP_MESSAGES_LIMIT,
    build_group_context,
    build_group_highlights,
    build_message_page,
    build_search_page,
    conversation_summary,
    describe_attachment,
    last_activity,
    member_highlights,
    member_view,
    merge_conversations,
    message_view,
    preview_text,
    rank_by_likes,
    relative_age,
    search_note,
    select_newest,
    sort_conversations,
)
from groupme_mcp_server.search import SearchScan

NOW = datetime(2026, 8, 29, 12, 0, 0, tzinfo=UTC)


def at(seconds_ago: int) -> datetime:
    return NOW - timedelta(seconds=seconds_ago)


def make_message(
    message_id: str = "m1",
    *,
    created_at: datetime | None = None,
    conversation_id: str | None = "42",
    text: str | None = "hello",
) -> Message:
    return Message(
        id=message_id,
        conversation_id=conversation_id,
        sender_id="22",
        sender_name="Ada",
        text=text,
        created_at=created_at if created_at is not None else at(90),
        favorited_by_count=2,
        attachments=(),
    )


def make_group(
    group_id: str = "42",
    *,
    updated_at: datetime | None = None,
    preview: MessagePreview | None = None,
    **kwargs: object,
) -> Group:
    return Group.model_validate(
        {"id": group_id, "name": f"Group {group_id}", "updated_at": updated_at, "preview": preview}
        | kwargs
    )


def make_chat(
    user_id: str = "7",
    *,
    updated_at: datetime | None = None,
    preview: MessagePreview | None = None,
    conversation_id: str | None = None,
) -> DirectChat:
    return DirectChat(
        conversation_id=conversation_id,
        other_user_id=user_id,
        other_user_name=f"User {user_id}",
        updated_at=updated_at,
        preview=preview,
    )


# ---------------------------------------------------------------------------
# relative_age / preview_text / describe_attachment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds_ago", "expected"),
    [
        (0, "just now"),
        (59, "just now"),
        (-30, "just now"),  # clock skew reads as "just now"
        (60, "1m ago"),
        (59 * 60, "59m ago"),
        (60 * 60, "1h ago"),
        (23 * 3600, "23h ago"),
        (24 * 3600, "1d ago"),
        (6 * 86400, "6d ago"),
    ],
)
def test_relative_age_buckets(seconds_ago: int, expected: str) -> None:
    assert relative_age(at(seconds_ago), NOW) == expected


def test_relative_age_falls_back_to_date_after_a_week() -> None:
    moment = at(8 * 86400)
    assert relative_age(moment, NOW) == moment.date().isoformat()


@pytest.mark.parametrize("text", [None, "", "   \n\t "])
def test_preview_text_empty_input(text: str | None) -> None:
    assert preview_text(text) == "(no text)"


def test_preview_text_flattens_whitespace() -> None:
    assert preview_text("a\nb\t c") == "a b c"


def test_preview_text_keeps_short_text_intact() -> None:
    assert preview_text("x" * 80) == "x" * 80


def test_preview_text_truncates_with_ellipsis() -> None:
    result = preview_text("x" * 81)
    assert result == "x" * 79 + "…"
    assert len(result) == 80


@pytest.mark.parametrize(
    ("attachment", "expected"),
    [
        (ImageAttachment(url="https://i.groupme.com/x.jpg"), "https://i.groupme.com/x.jpg"),
        (LocationAttachment(name="HQ", lat="40.7", lng="-74.0"), "location: HQ"),
        (MentionsAttachment(user_ids=("1", "2")), "mentions 2 user(s)"),
        (ReplyAttachment(reply_id="9", base_reply_id="9"), "reply to message 9"),
        (UnknownAttachment(type="poll", data={"poll_id": "1"}), "[poll attachment]"),
    ],
)
def test_describe_attachment(attachment: object, expected: str) -> None:
    assert describe_attachment(attachment) == expected  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------
# last_activity / sort_conversations
# ---------------------------------------------------------------------------


def test_last_activity_prefers_the_last_message_timestamp() -> None:
    # updated_at is newer (a metadata change); the message time must win so
    # ordering agrees with the displayed preview.
    conv = make_group(updated_at=at(10), preview=MessagePreview(created_at=at(999)))
    assert last_activity(conv) == at(999)


def test_last_activity_falls_back_to_updated_at() -> None:
    assert last_activity(make_chat(updated_at=at(20))) == at(20)
    assert last_activity(make_group(updated_at=at(30), preview=MessagePreview(text="hi"))) == at(30)


def test_last_activity_none_when_unknown() -> None:
    assert last_activity(make_group()) is None
    assert last_activity(make_chat(preview=MessagePreview(text="hi"))) is None


def test_merge_conversations_sorts_and_truncates() -> None:
    groups = [make_group("a", updated_at=at(300)), make_group("b", updated_at=at(100))]
    chats = [make_chat("7", updated_at=at(200))]
    merged = merge_conversations(groups, chats, limit=2)
    assert merged == [groups[1], chats[0]]


def test_select_newest_truncates_and_passes_short_lists_through() -> None:
    messages = [make_message("m3"), make_message("m2"), make_message("m1")]
    assert list(select_newest(messages, 2)) == messages[:2]
    assert list(select_newest(messages, 10)) == messages
    assert list(select_newest([], 5)) == []


def test_sort_conversations_most_recent_first_with_unknown_last() -> None:
    old_group = make_group("old", updated_at=at(3600))
    fresh_chat = make_chat("7", updated_at=at(60))
    unknown = make_group("unknown")
    preview_only = make_chat("8", preview=MessagePreview(created_at=at(600)))
    ordered = sort_conversations([unknown, old_group, preview_only, fresh_chat])
    assert ordered == [fresh_chat, preview_only, old_group, unknown]


def test_sort_orders_by_message_time_when_updated_at_disagrees() -> None:
    # The group's metadata was touched recently, but its last message is
    # older than the chat's - the chat must sort first.
    stale_group = make_group(
        "g", updated_at=at(10), preview=MessagePreview(text="old", created_at=at(600))
    )
    active_chat = make_chat("7", preview=MessagePreview(text="new", created_at=at(300)))
    assert sort_conversations([stale_group, active_chat]) == [active_chat, stale_group]


# ---------------------------------------------------------------------------
# conversation_summary
# ---------------------------------------------------------------------------


def test_group_summary_concise() -> None:
    group = make_group(
        "42",
        updated_at=at(120),
        preview=MessagePreview(sender_name="Ada", text="hello there", created_at=at(120)),
        member_count=3,
        description="secret",
        share_url="https://groupme.com/join_group/42/SHARE",
        creator_user_id="9",
        image_url="https://i.groupme.com/g.jpg",
    )
    summary = conversation_summary(group, NOW, detailed=False)
    assert summary.kind == "group"
    assert summary.name == "Group 42"
    assert summary.group_id == "42"  # chaining id stays in concise mode
    assert summary.member_count == 3
    assert summary.last_message == "Ada: hello there"
    assert summary.last_active == "2m ago"
    assert summary.description is None
    assert summary.share_url is None
    assert summary.creator_user_id is None
    assert summary.image_url is None
    assert summary.updated_at is None


def test_group_summary_detailed() -> None:
    group = make_group(
        "42",
        updated_at=at(120),
        description="We read",
        share_url="https://groupme.com/join_group/42/SHARE",
        creator_user_id="9",
        image_url="https://i.groupme.com/g.jpg",
    )
    summary = conversation_summary(group, NOW, detailed=True)
    assert summary.description == "We read"
    assert summary.share_url == "https://groupme.com/join_group/42/SHARE"
    assert summary.creator_user_id == "9"
    assert summary.image_url == "https://i.groupme.com/g.jpg"
    assert summary.updated_at == at(120).isoformat()


def test_group_summary_detailed_without_creator() -> None:
    summary = conversation_summary(make_group("42"), NOW, detailed=True)
    assert summary.creator_user_id is None
    assert summary.updated_at is None
    assert summary.last_active is None
    assert summary.last_message is None


def test_direct_summary_concise() -> None:
    chat = make_chat(
        "7",
        updated_at=at(30),
        preview=MessagePreview(text="yo", created_at=at(30)),
        conversation_id="1+7",
    )
    summary = conversation_summary(chat, NOW, detailed=False)
    assert summary.kind == "direct"
    assert summary.name == "User 7"
    assert summary.other_user_id == "7"
    assert summary.group_id is None
    assert summary.member_count is None
    assert summary.last_message == "yo"  # no sender prefix when unknown
    assert summary.last_active == "just now"
    assert summary.conversation_id is None


def test_direct_summary_detailed() -> None:
    chat = make_chat("7", updated_at=at(30), conversation_id="1+7")
    summary = conversation_summary(chat, NOW, detailed=True)
    assert summary.conversation_id == "1+7"
    assert summary.updated_at == at(30).isoformat()
    assert conversation_summary(make_chat("8"), NOW, detailed=True).conversation_id is None


# ---------------------------------------------------------------------------
# message_view / build_message_page
# ---------------------------------------------------------------------------


def test_message_view_concise() -> None:
    message = Message(
        id="m1",
        conversation_id="42",
        sender_id="22",
        sender_name="Ada",
        text="hello",
        created_at=at(3600),
        favorited_by_count=2,
        attachments=(ImageAttachment(url="https://i.groupme.com/x.jpg"),),
    )
    view = message_view(message, NOW, detailed=False)
    assert view.id == "m1"
    assert view.sender_name == "Ada"
    assert view.text == "hello"
    assert view.sent == "1h ago"
    assert view.likes == 2
    assert view.attachments == ("https://i.groupme.com/x.jpg",)
    assert view.sender_id is None
    assert view.conversation_id is None
    assert view.created_at is None


def test_message_view_detailed() -> None:
    view = message_view(make_message(created_at=at(3600)), NOW, detailed=True)
    assert view.sender_id == "22"
    assert view.conversation_id == "42"
    assert view.created_at == at(3600).isoformat()


def test_message_view_detailed_without_conversation_id() -> None:
    view = message_view(make_message(conversation_id=None), NOW, detailed=True)
    assert view.conversation_id is None


def test_build_message_page_reverses_to_oldest_first() -> None:
    newest = make_message("m3", created_at=at(10))
    middle = make_message("m2", created_at=at(20))
    oldest = make_message("m1", created_at=at(30))
    page = build_message_page([newest, middle, oldest], NOW, detailed=False)
    assert [m.id for m in page.messages] == ["m1", "m2", "m3"]
    assert page.next_before_id == "m1"
    assert page.note is None


def test_build_message_page_empty_gets_a_note() -> None:
    page = build_message_page([], NOW, detailed=False)
    assert page.messages == ()
    assert page.next_before_id is None
    assert page.note == EMPTY_MESSAGES_NOTE


# ---------------------------------------------------------------------------
# member_view / build_group_context
# ---------------------------------------------------------------------------


def test_member_view_formats() -> None:
    member = Member(user_id="1", nickname="Ada", roles=("admin",))
    concise = member_view(member, detailed=False)
    assert concise.nickname == "Ada"
    assert concise.roles == ("admin",)
    assert concise.user_id is None
    assert member_view(member, detailed=True).user_id == "1"


def test_build_group_context_concise() -> None:
    group = make_group(
        "42",
        updated_at=at(60),
        member_count=1,
        members=(Member(user_id="1", nickname="Ada", roles=("owner",)),),
        description="We read",
        share_url="https://groupme.com/join_group/42/SHARE",
        creator_user_id="9",
        image_url="https://i.groupme.com/g.jpg",
    )
    context = build_group_context(group, [make_message("m1")], NOW, detailed=False)
    assert context.group_id == "42"
    assert context.name == "Group 42"
    assert context.description == "We read"  # description helps orientation even when concise
    assert context.member_count == 1
    assert context.last_active == "1m ago"
    assert context.members == (
        member_view(Member(user_id="1", nickname="Ada", roles=("owner",)), detailed=False),
    )
    assert [m.id for m in context.recent_messages] == ["m1"]
    assert context.message_note is None
    assert context.share_url is None
    assert context.creator_user_id is None
    assert context.image_url is None
    assert context.updated_at is None


def test_build_group_context_detailed() -> None:
    group = make_group(
        "42",
        updated_at=at(60),
        creator_user_id="9",
        share_url="https://groupme.com/join_group/42/SHARE",
        image_url="https://i.groupme.com/g.jpg",
    )
    context = build_group_context(group, [], NOW, detailed=True)
    assert context.share_url == "https://groupme.com/join_group/42/SHARE"
    assert context.creator_user_id == "9"
    assert context.image_url == "https://i.groupme.com/g.jpg"
    assert context.updated_at == at(60).isoformat()
    assert context.members == ()  # members omitted upstream -> empty, not an error
    assert context.recent_messages == ()
    assert context.message_note == EMPTY_MESSAGES_NOTE


def test_build_group_context_detailed_without_creator() -> None:
    context = build_group_context(make_group("42"), [], NOW, detailed=True)
    assert context.creator_user_id is None
    assert context.last_active is None


# --- search rendering -------------------------------------------------------


def make_liked_message(
    message_id: str,
    *,
    sender_id: str = "22",
    sender_name: str = "Ada",
    likes: int = 0,
    text: str | None = "hello",
    created_at: datetime | None = None,
) -> Message:
    return Message(
        id=message_id,
        conversation_id="42",
        sender_id=sender_id,
        sender_name=sender_name,
        text=text,
        created_at=created_at if created_at is not None else at(60),
        favorited_by_count=likes,
        attachments=(),
    )


def test_search_note_reports_a_hit_cap_never_silently() -> None:
    scan = SearchScan(matches=(make_message("m1"),), scanned=500, last_scanned_id="m9")
    note = search_note(scan, limit=5)
    assert note is not None
    assert "500" in note
    assert "NOT searched" in note
    assert "max_messages_scanned" in note


def test_search_note_flags_an_early_stop_with_history_left() -> None:
    scan = SearchScan(matches=(make_message("m1"), make_message("m2")), scanned=40)
    note = search_note(scan, limit=2)
    assert note is not None
    assert "more matches may exist" in note


def test_search_note_explains_a_fully_scanned_no_match_search() -> None:
    scan = SearchScan(matches=(), scanned=12, oldest_reached=True)
    note = search_note(scan, limit=5)
    assert note is not None
    assert "entire history" in note


def test_search_note_is_none_when_the_result_needs_no_caveat() -> None:
    partial = SearchScan(matches=(make_message("m1"),), scanned=12, oldest_reached=True)
    assert search_note(partial, limit=5) is None
    complete = SearchScan(
        matches=(make_message("m1"), make_message("m2")), scanned=12, oldest_reached=True
    )
    assert search_note(complete, limit=2) is None


def test_build_search_page_reports_scan_accounting() -> None:
    scan = SearchScan(
        matches=(make_message("m3"), make_message("m1")),
        scanned=7,
        oldest_reached=True,
        last_scanned_id="m1",
    )
    page = build_search_page(scan, 2, NOW, detailed=False)
    assert [m.id for m in page.matches] == ["m3", "m1"]
    assert page.count == 2
    assert page.messages_scanned == 7
    assert page.oldest_message_reached is True
    assert page.next_before_id == "m1"
    assert page.note is None
    assert page.matches[0].sender_id is None  # concise


def test_build_search_page_empty_scan_has_no_cursor() -> None:
    page = build_search_page(SearchScan(oldest_reached=True), 5, NOW, detailed=True)
    assert page.matches == ()
    assert page.next_before_id is None
    assert page.note is not None


def test_build_search_page_detailed_includes_ids() -> None:
    scan = SearchScan(matches=(make_message("m1"),), scanned=1, oldest_reached=True)
    page = build_search_page(scan, 1, NOW, detailed=True)
    assert page.matches[0].sender_id == "22"
    assert page.matches[0].created_at is not None


# --- highlights rendering ---------------------------------------------------


def test_rank_by_likes_orders_by_likes_then_recency() -> None:
    older_tie = make_liked_message("m1", likes=3, created_at=at(300))
    newer_tie = make_liked_message("m2", likes=3, created_at=at(100))
    top = make_liked_message("m3", likes=9, created_at=at(500))
    ranked = rank_by_likes([older_tie, top, newer_tie])
    assert [str(m.id) for m in ranked] == ["m3", "m2", "m1"]


def test_member_highlights_aggregates_counts_and_likes() -> None:
    ranked = rank_by_likes(
        [
            make_liked_message("m1", sender_id="1", sender_name="Ada", likes=5),
            make_liked_message("m2", sender_id="2", sender_name="Grace", likes=4),
            make_liked_message("m3", sender_id="1", sender_name="Ada", likes=2),
        ]
    )
    members = member_highlights(ranked, detailed=False)
    assert [(m.name, m.messages_in_top, m.likes_received) for m in members] == [
        ("Ada", 2, 7),
        ("Grace", 1, 4),
    ]
    assert members[0].user_id is None  # concise


def test_member_highlights_breaks_ties_by_count_then_name() -> None:
    ranked = [
        make_liked_message("m1", sender_id="1", sender_name="Zed", likes=4),
        make_liked_message("m2", sender_id="2", sender_name="Ada", likes=2),
        make_liked_message("m3", sender_id="2", sender_name="Ada", likes=2),
        make_liked_message("m4", sender_id="3", sender_name="Bob", likes=4),
    ]
    members = member_highlights(ranked, detailed=True)
    # Ada ties Zed and Bob on likes (4) but has more messages; Bob beats Zed by name.
    assert [m.name for m in members] == ["Ada", "Bob", "Zed"]
    assert members[0].user_id == "2"


def test_member_highlights_names_come_from_the_most_liked_message() -> None:
    ranked = rank_by_likes(
        [
            make_liked_message("m1", sender_id="1", sender_name="Old Nick", likes=1),
            make_liked_message("m2", sender_id="1", sender_name="New Nick", likes=8),
        ]
    )
    members = member_highlights(ranked, detailed=False)
    assert members[0].name == "New Nick"


def test_member_highlights_respects_its_limit() -> None:
    ranked = [
        make_liked_message(f"m{i}", sender_id=str(i), sender_name=f"U{i}", likes=10 - i)
        for i in range(TOP_MEMBERS_LIMIT + 3)
    ]
    assert len(member_highlights(ranked, detailed=False)) == TOP_MEMBERS_LIMIT


def test_build_group_highlights_takes_the_top_messages_only() -> None:
    messages = [
        make_liked_message(f"m{i}", likes=i, text=f"msg {i}") for i in range(TOP_MESSAGES_LIMIT + 5)
    ]
    highlights = build_group_highlights(messages, "42", "week", NOW, detailed=False)
    assert len(highlights.top_messages) == TOP_MESSAGES_LIMIT
    assert highlights.top_messages[0].likes == TOP_MESSAGES_LIMIT + 4
    assert highlights.group_id == "42"
    assert highlights.period == "week"
    assert highlights.note is None
    assert highlights.top_messages[0].sender_id is None  # concise


def test_build_group_highlights_previews_and_details() -> None:
    long_text = "word " * 50
    highlights = build_group_highlights(
        [make_liked_message("m1", likes=2, text=long_text)], "42", "day", NOW, detailed=True
    )
    top = highlights.top_messages[0]
    assert top.preview.endswith("…")
    assert top.sent == "1m ago"
    assert top.sender_id == "22"
    assert top.created_at == at(60).isoformat()
    assert highlights.top_members[0].user_id == "22"


def test_build_group_highlights_empty_period_gets_a_note() -> None:
    highlights = build_group_highlights([], "42", "month", NOW, detailed=False)
    assert highlights.top_messages == ()
    assert highlights.top_members == ()
    assert highlights.note == EMPTY_HIGHLIGHTS_NOTE
