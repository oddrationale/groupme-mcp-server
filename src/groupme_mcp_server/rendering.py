"""Pure presentation logic for the MCP tool layer.

This module is part of the functional core: it turns domain models from
[`groupme_mcp_server.models`][] into the frozen view models the tools return,
with no IO anywhere. Everything here is deterministic — functions that need
"now" take it as an argument.

View-model convention: fields documented as *detailed-only* are ``None`` when
a tool is called with ``response_format="concise"``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

# LeaderboardPeriod is a pydantic field annotation below, so it must exist at
# runtime even though it is "only" a type.
from groupme_mcp_server.models import (
    Group,
    GroupMeModel,
    GroupRef,
    ImageAttachment,
    LeaderboardPeriod,
    LocationAttachment,
    MentionsAttachment,
    ReplyAttachment,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import datetime

    from groupme_mcp_server.models import Attachment, DirectChat, DirectRef, Member, Message
    from groupme_mcp_server.search import SearchScan

ResponseFormat = Literal["concise", "detailed"]
"""How much detail a tool should include in its result."""

PREVIEW_MAX_CHARS = 80
"""Longest last-message preview shown in conversation listings."""

_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_HOURS_PER_DAY = 24
_DAYS_PER_WEEK = 7

EMPTY_MESSAGES_NOTE = (
    "No messages here: the conversation is empty, or there is nothing beyond the given cursor."
)
"""Note attached to an empty message page (GroupMe signals this as HTTP 304)."""


class ConversationSummary(GroupMeModel):
    """One conversation in a ``list_conversations`` result.

    ``group_id`` (groups) and ``other_user_id`` (direct chats) are always
    present so results can be chained into ``read_messages``. The
    ``description``, ``share_url``, ``creator_user_id``, ``conversation_id``,
    ``image_url``, and ``updated_at`` fields are detailed-only.
    """

    kind: Literal["group", "direct"]
    name: str
    group_id: str | None = None
    other_user_id: str | None = None
    member_count: int | None = None
    last_message: str | None = None
    last_active: str | None = None
    description: str | None = None
    share_url: str | None = None
    creator_user_id: str | None = None
    conversation_id: str | None = None
    image_url: str | None = None
    updated_at: str | None = None


class ConversationPage(GroupMeModel):
    """The result of ``list_conversations``: most recently active first."""

    conversations: tuple[ConversationSummary, ...]
    count: int
    note: str | None = None


class MessageView(GroupMeModel):
    """One message in a tool result.

    ``id`` is always present so pagination and future tools can chain from
    it. ``sender_id``, ``conversation_id``, and ``created_at`` are
    detailed-only; ``sent`` is a human-readable relative age.
    """

    id: str
    sender_name: str
    text: str | None
    sent: str
    likes: int
    attachments: tuple[str, ...] = ()
    sender_id: str | None = None
    conversation_id: str | None = None
    created_at: str | None = None


class MessagePage(GroupMeModel):
    """The result of ``read_messages``: oldest first.

    ``next_before_id`` is the oldest message id on this page; pass it as
    ``before_id`` to read further back. It is ``None`` only when the page is
    empty, in which case ``note`` explains why.
    """

    messages: tuple[MessageView, ...]
    next_before_id: str | None = None
    note: str | None = None


class MemberView(GroupMeModel):
    """One group member in a ``get_conversation_context`` result.

    ``user_id`` is detailed-only.
    """

    nickname: str
    roles: tuple[str, ...] = ()
    user_id: str | None = None


class GroupContext(GroupMeModel):
    """The result of ``get_conversation_context``.

    ``recent_messages`` is oldest first; ``message_note`` is set when the
    group has no (recent) messages. ``share_url``, ``creator_user_id``,
    ``image_url``, and ``updated_at`` are detailed-only.
    """

    group_id: str
    name: str
    description: str | None = None
    member_count: int | None = None
    last_active: str | None = None
    members: tuple[MemberView, ...] = ()
    recent_messages: tuple[MessageView, ...] = ()
    message_note: str | None = None
    share_url: str | None = None
    creator_user_id: str | None = None
    image_url: str | None = None
    updated_at: str | None = None


class SentMessage(GroupMeModel):
    """Confirmation returned by ``send_message``.

    ``conversation_id`` is the id the react/like endpoints take; for a brand
    new direct chat GroupMe may omit it, in which case it is ``None``.
    """

    message_id: str
    kind: Literal["group", "direct"]
    group_id: str | None = None
    other_user_id: str | None = None
    conversation_id: str | None = None
    text: str | None
    attachments: tuple[str, ...] = ()
    sent_at: str


class ReactionResult(GroupMeModel):
    """Confirmation returned by ``react_to_message``."""

    action: Literal["like", "unlike"]
    conversation_id: str
    message_id: str
    confirmation: str


class MessageSearchPage(GroupMeModel):
    """The result of ``search_messages``: matches in scan order (newest first).

    The scan is honest about its own limits: ``messages_scanned`` counts every
    message examined, ``oldest_message_reached`` says whether the scan hit the
    beginning of the conversation's history, and ``note`` calls out any
    truncation — a scan stopped by its cap is never silent. ``next_before_id``
    is the oldest message id scanned; pass it to ``read_messages`` as
    ``before_id`` to keep working backwards through unscanned history.
    """

    matches: tuple[MessageView, ...]
    count: int
    messages_scanned: int
    oldest_message_reached: bool
    next_before_id: str | None = None
    note: str | None = None


class HighlightedMessage(GroupMeModel):
    """One top-liked message in a ``get_highlights`` result.

    ``preview`` is a one-line text preview; ``sender_id`` and ``created_at``
    are detailed-only.
    """

    message_id: str
    sender_name: str
    likes: int
    preview: str
    sent: str
    sender_id: str | None = None
    created_at: str | None = None


class MemberHighlight(GroupMeModel):
    """One member's aggregate over a group's most-liked messages.

    ``messages_in_top`` and ``likes_received`` count only the leaderboard's
    top-liked messages for the period, not the group's full history.
    ``user_id`` is detailed-only.
    """

    name: str
    messages_in_top: int
    likes_received: int
    user_id: str | None = None


class GroupHighlights(GroupMeModel):
    """The result of ``get_highlights``: what mattered in one group.

    Built from GroupMe's likes leaderboard for the period: the top-liked
    messages plus per-member aggregates over those messages. ``note`` is set
    when the period has no liked messages.
    """

    group_id: str
    period: LeaderboardPeriod
    top_messages: tuple[HighlightedMessage, ...]
    top_members: tuple[MemberHighlight, ...]
    note: str | None = None


def relative_age(moment: datetime, now: datetime) -> str:
    """Format how long ago ``moment`` was, for compact human display.

    Args:
        moment: The (aware) time being described.
        now: The current (aware) time.

    Returns:
        ``"just now"``, ``"5m ago"``, ``"3h ago"``, ``"2d ago"``, or an ISO
        date once it is a week or more in the past. Future moments (clock
        skew) read as ``"just now"``.
    """
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < _SECONDS_PER_MINUTE:
        return "just now"
    minutes = seconds // _SECONDS_PER_MINUTE
    if minutes < _MINUTES_PER_HOUR:
        return f"{minutes}m ago"
    hours = minutes // _MINUTES_PER_HOUR
    if hours < _HOURS_PER_DAY:
        return f"{hours}h ago"
    days = hours // _HOURS_PER_DAY
    if days < _DAYS_PER_WEEK:
        return f"{days}d ago"
    return moment.date().isoformat()


def preview_text(text: str | None, max_chars: int = PREVIEW_MAX_CHARS) -> str:
    """Flatten and truncate message text for a one-line preview.

    Args:
        text: The raw message text, possibly ``None`` or whitespace-only.
        max_chars: Longest preview to produce.

    Returns:
        A single-line preview, ellipsized when truncated, or ``"(no text)"``
        for empty input (e.g. an attachment-only message).
    """
    if text is None or not text.strip():
        return "(no text)"
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    return flat[: max_chars - 1] + "…"


def describe_attachment(attachment: Attachment) -> str:
    """Summarize one attachment as a short human-readable string.

    Args:
        attachment: Any parsed attachment.

    Returns:
        The image URL for images, and a bracketed or prefixed summary for
        locations, mentions, replies, and unknown attachment types.
    """
    if isinstance(attachment, ImageAttachment):
        return attachment.url
    if isinstance(attachment, LocationAttachment):
        return f"location: {attachment.name}"
    if isinstance(attachment, MentionsAttachment):
        return f"mentions {len(attachment.user_ids)} user(s)"
    if isinstance(attachment, ReplyAttachment):
        return f"reply to message {attachment.reply_id}"
    return f"[{attachment.type} attachment]"


def last_activity(conversation: Group | DirectChat) -> datetime | None:
    """Determine when a conversation last saw message activity.

    The last message's timestamp is preferred over ``updated_at`` because
    GroupMe bumps ``updated_at`` for metadata changes too; using the message
    time keeps ordering and ``last_active`` consistent with the displayed
    last-message preview.

    Args:
        conversation: Either kind of conversation.

    Returns:
        The preview message's timestamp, falling back to ``updated_at``, or
        ``None`` when neither is known.
    """
    if conversation.preview is not None and conversation.preview.created_at is not None:
        return conversation.preview.created_at
    return conversation.updated_at


def sort_conversations(conversations: Iterable[Group | DirectChat]) -> list[Group | DirectChat]:
    """Order conversations by recency, most recently active first.

    Args:
        conversations: Groups and direct chats, in any order.

    Returns:
        A new list sorted by [`last_activity`][groupme_mcp_server.rendering.last_activity]
        descending; conversations with no known activity sort last.
    """

    def key(conversation: Group | DirectChat) -> tuple[bool, float]:
        activity = last_activity(conversation)
        return (activity is None, -activity.timestamp() if activity is not None else 0.0)

    return sorted(conversations, key=key)


def merge_conversations(
    groups: Iterable[Group], chats: Iterable[DirectChat], limit: int
) -> list[Group | DirectChat]:
    """Merge groups and direct chats into one bounded recency-sorted list.

    Args:
        groups: The group conversations.
        chats: The direct-chat conversations.
        limit: Maximum conversations to keep after sorting.

    Returns:
        The most recently active ``limit`` conversations, most recent first.
    """
    return sort_conversations([*groups, *chats])[:limit]


def select_newest(newest_first: Sequence[Message], limit: int) -> Sequence[Message]:
    """Keep the ``limit`` newest messages of a newest-first listing.

    Args:
        newest_first: Messages as GroupMe returns them (newest first).
        limit: Maximum messages to keep.

    Returns:
        The first ``limit`` entries, still newest first.
    """
    return newest_first[:limit]


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _last_message_line(conversation: Group | DirectChat) -> str | None:
    preview = conversation.preview
    if preview is None:
        return None
    body = preview_text(preview.text)
    return f"{preview.sender_name}: {body}" if preview.sender_name else body


def conversation_summary(
    conversation: Group | DirectChat, now: datetime, *, detailed: bool
) -> ConversationSummary:
    """Render one conversation for a listing.

    Args:
        conversation: Either kind of conversation.
        now: The current (aware) time, for relative ages.
        detailed: Include the detailed-only fields.

    Returns:
        The summary view; detailed-only fields are ``None`` when
        ``detailed`` is false.
    """
    activity = last_activity(conversation)
    last_message = _last_message_line(conversation)
    last_active = relative_age(activity, now) if activity is not None else None
    updated_at = _iso(conversation.updated_at) if detailed else None
    if isinstance(conversation, Group):
        return ConversationSummary(
            kind="group",
            name=conversation.name,
            group_id=str(conversation.id),
            member_count=conversation.member_count,
            last_message=last_message,
            last_active=last_active,
            description=conversation.description if detailed else None,
            share_url=conversation.share_url if detailed else None,
            creator_user_id=(
                str(conversation.creator_user_id)
                if detailed and conversation.creator_user_id is not None
                else None
            ),
            image_url=conversation.image_url if detailed else None,
            updated_at=updated_at,
        )
    return ConversationSummary(
        kind="direct",
        name=conversation.other_user_name,
        other_user_id=str(conversation.other_user_id),
        last_message=last_message,
        last_active=last_active,
        conversation_id=(
            str(conversation.conversation_id)
            if detailed and conversation.conversation_id is not None
            else None
        ),
        updated_at=updated_at,
    )


def message_view(message: Message, now: datetime, *, detailed: bool) -> MessageView:
    """Render one message for a tool result.

    Args:
        message: The normalized message.
        now: The current (aware) time, for the relative age.
        detailed: Include the detailed-only fields.

    Returns:
        The message view; detailed-only fields are ``None`` when
        ``detailed`` is false.
    """
    return MessageView(
        id=str(message.id),
        sender_name=message.sender_name,
        text=message.text,
        sent=relative_age(message.created_at, now),
        likes=message.favorited_by_count,
        attachments=tuple(describe_attachment(a) for a in message.attachments),
        sender_id=str(message.sender_id) if detailed else None,
        conversation_id=(
            str(message.conversation_id)
            if detailed and message.conversation_id is not None
            else None
        ),
        created_at=message.created_at.isoformat() if detailed else None,
    )


def build_message_page(
    newest_first: Sequence[Message], now: datetime, *, detailed: bool
) -> MessagePage:
    """Assemble a message page from a newest-first API listing.

    Args:
        newest_first: Messages as GroupMe returns them (newest first);
            empty when GroupMe answered HTTP 304.
        now: The current (aware) time, for relative ages.
        detailed: Include the detailed-only fields.

    Returns:
        The page, oldest first, with ``next_before_id`` set to the oldest
        message id — or an empty page with an explanatory ``note``.
    """
    if not newest_first:
        return MessagePage(messages=(), next_before_id=None, note=EMPTY_MESSAGES_NOTE)
    return MessagePage(
        messages=tuple(message_view(m, now, detailed=detailed) for m in reversed(newest_first)),
        next_before_id=str(newest_first[-1].id),
        note=None,
    )


def sent_message_view(message: Message, conversation: GroupRef | DirectRef) -> SentMessage:
    """Render the confirmation for a just-sent message.

    Args:
        message: The created message as GroupMe echoed it back.
        conversation: The conversation reference the caller sent to.

    Returns:
        The confirmation view: ids for chaining (into ``react_to_message``
        or ``read_messages``) plus the echoed text and attachments.
    """
    return SentMessage(
        message_id=str(message.id),
        kind=conversation.kind,
        group_id=str(conversation.group_id) if isinstance(conversation, GroupRef) else None,
        other_user_id=(
            None if isinstance(conversation, GroupRef) else str(conversation.other_user_id)
        ),
        conversation_id=(
            str(message.conversation_id) if message.conversation_id is not None else None
        ),
        text=message.text,
        attachments=tuple(describe_attachment(a) for a in message.attachments),
        sent_at=message.created_at.isoformat(),
    )


def reaction_result(
    action: Literal["like", "unlike"], conversation_id: str, message_id: str
) -> ReactionResult:
    """Render the confirmation for a like or unlike.

    Args:
        action: Which reaction was performed.
        conversation_id: The conversation the message lives in.
        message_id: The message that was reacted to.

    Returns:
        The confirmation view, echoing the ids back.
    """
    verb = "Liked" if action == "like" else "Removed the like from"
    return ReactionResult(
        action=action,
        conversation_id=conversation_id,
        message_id=message_id,
        confirmation=f"{verb} message {message_id}.",
    )


def member_view(member: Member, *, detailed: bool) -> MemberView:
    """Render one group member.

    Args:
        member: The membership entry.
        detailed: Include the detailed-only ``user_id``.

    Returns:
        The member view.
    """
    return MemberView(
        nickname=member.nickname,
        roles=member.roles,
        user_id=str(member.user_id) if detailed else None,
    )


def build_group_context(
    group: Group, newest_first: Sequence[Message], now: datetime, *, detailed: bool
) -> GroupContext:
    """Assemble the orientation bundle for one group.

    Args:
        group: The group's metadata (with members, when GroupMe sent them).
        newest_first: Recent messages as GroupMe returns them (newest first).
        now: The current (aware) time, for relative ages.
        detailed: Include the detailed-only fields.

    Returns:
        Group metadata, member list, and recent messages (oldest first) in
        one view.
    """
    page = build_message_page(newest_first, now, detailed=detailed)
    activity = last_activity(group)
    return GroupContext(
        group_id=str(group.id),
        name=group.name,
        description=group.description,
        member_count=group.member_count,
        last_active=relative_age(activity, now) if activity is not None else None,
        members=tuple(member_view(m, detailed=detailed) for m in group.members or ()),
        recent_messages=page.messages,
        message_note=page.note,
        share_url=group.share_url if detailed else None,
        creator_user_id=(
            str(group.creator_user_id) if detailed and group.creator_user_id is not None else None
        ),
        image_url=group.image_url if detailed else None,
        updated_at=_iso(group.updated_at) if detailed else None,
    )


def search_note(scan: SearchScan, limit: int) -> str | None:
    """Explain how a finished search scan ended, when it needs explaining.

    Pure honesty policy for ``search_messages``: any scan that stopped short
    of the full history gets a note saying what was and was not searched.

    Args:
        scan: The completed scan state.
        limit: The number of matches the caller asked for.

    Returns:
        A note for a capped scan (found fewer than ``limit`` without
        reaching the oldest message), an early stop (found ``limit`` with
        history left), or a fully scanned no-match search; ``None`` when the
        result needs no caveat.
    """
    found = len(scan.matches)
    if found < limit and not scan.oldest_reached:
        return (
            f"Scan cap reached: examined {scan.scanned} message(s) and found {found} of the "
            f"requested {limit} match(es); older history was NOT searched. Raise "
            "max_messages_scanned, or continue manually with read_messages using "
            "next_before_id as before_id."
        )
    if found >= limit and not scan.oldest_reached:
        return (
            f"Stopped after finding the requested {limit} match(es); older messages were not "
            "searched, so more matches may exist further back. Continue with a higher limit, "
            "or with read_messages using next_before_id as before_id."
        )
    if found == 0:
        return "No messages matched; the conversation's entire history was scanned."
    return None


def build_search_page(
    scan: SearchScan, limit: int, now: datetime, *, detailed: bool
) -> MessageSearchPage:
    """Assemble the ``search_messages`` result from a completed scan.

    Args:
        scan: The completed scan state.
        limit: The number of matches the caller asked for.
        now: The current (aware) time, for relative ages.
        detailed: Include the detailed-only fields.

    Returns:
        The search page: matches newest first, honest scan accounting, and a
        [`search_note`][groupme_mcp_server.rendering.search_note] when the
        scan stopped short of the full history.
    """
    return MessageSearchPage(
        matches=tuple(message_view(m, now, detailed=detailed) for m in scan.matches),
        count=len(scan.matches),
        messages_scanned=scan.scanned,
        oldest_message_reached=scan.oldest_reached,
        next_before_id=(str(scan.last_scanned_id) if scan.last_scanned_id is not None else None),
        note=search_note(scan, limit),
    )


TOP_MESSAGES_LIMIT = 10
"""Most-liked messages shown by ``get_highlights``."""

TOP_MEMBERS_LIMIT = 5
"""Members shown in the ``get_highlights`` member summary."""

EMPTY_HIGHLIGHTS_NOTE = (
    "No liked messages in this group for the requested period, so there is nothing to highlight."
)
"""Note attached to a ``get_highlights`` result when the leaderboard is empty."""


def rank_by_likes(messages: Iterable[Message]) -> list[Message]:
    """Order messages by like count, most liked first; ties break newest first.

    Args:
        messages: Messages in any order.

    Returns:
        A new list, most liked first. The leaderboard endpoint already claims
        this order, but it is undocumented, so it is not trusted.
    """
    return sorted(messages, key=lambda m: (-m.favorited_by_count, -m.created_at.timestamp()))


def member_highlights(
    ranked: Sequence[Message], *, detailed: bool, limit: int = TOP_MEMBERS_LIMIT
) -> tuple[MemberHighlight, ...]:
    """Aggregate per-member activity over the leaderboard's messages.

    Args:
        ranked: The leaderboard messages, most liked first (each member's
            display name is taken from their most-liked message).
        detailed: Include the detailed-only ``user_id``.
        limit: Maximum members to return.

    Returns:
        Members ordered by likes received, then messages in the top, then
        name.
    """
    counts: dict[str, int] = {}
    likes: dict[str, int] = {}
    names: dict[str, str] = {}
    for message in ranked:
        sender = str(message.sender_id)
        counts[sender] = counts.get(sender, 0) + 1
        likes[sender] = likes.get(sender, 0) + message.favorited_by_count
        names.setdefault(sender, message.sender_name)
    order = sorted(counts, key=lambda sender: (-likes[sender], -counts[sender], names[sender]))
    return tuple(
        MemberHighlight(
            name=names[sender],
            messages_in_top=counts[sender],
            likes_received=likes[sender],
            user_id=sender if detailed else None,
        )
        for sender in order[:limit]
    )


def _highlighted_message(message: Message, now: datetime, *, detailed: bool) -> HighlightedMessage:
    return HighlightedMessage(
        message_id=str(message.id),
        sender_name=message.sender_name,
        likes=message.favorited_by_count,
        preview=preview_text(message.text),
        sent=relative_age(message.created_at, now),
        sender_id=str(message.sender_id) if detailed else None,
        created_at=message.created_at.isoformat() if detailed else None,
    )


def build_group_highlights(
    messages: Iterable[Message],
    group_id: str,
    period: LeaderboardPeriod,
    now: datetime,
    *,
    detailed: bool,
) -> GroupHighlights:
    """Assemble the ``get_highlights`` result from leaderboard messages.

    Args:
        messages: The group's most-liked messages for the period, in any
            order.
        group_id: The group they belong to.
        period: The leaderboard window that produced them.
        now: The current (aware) time, for relative ages.
        detailed: Include the detailed-only fields.

    Returns:
        The top [`TOP_MESSAGES_LIMIT`][groupme_mcp_server.rendering.TOP_MESSAGES_LIMIT]
        messages plus the member summary, or an empty result with an
        explanatory ``note``.
    """
    ranked = rank_by_likes(messages)
    return GroupHighlights(
        group_id=group_id,
        period=period,
        top_messages=tuple(
            _highlighted_message(m, now, detailed=detailed) for m in ranked[:TOP_MESSAGES_LIMIT]
        ),
        top_members=member_highlights(ranked, detailed=detailed),
        note=EMPTY_HIGHLIGHTS_NOTE if not ranked else None,
    )
