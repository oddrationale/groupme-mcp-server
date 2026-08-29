"""Domain models and pure parsing for the GroupMe API v3.

This is the functional core: frozen pydantic models plus pure functions that
normalize raw GroupMe JSON into them. Nothing here performs IO.

GroupMe API facts encoded here:

- Every response body is ``{"response": ..., "meta": {"code": int, "errors": [str]}}``.
- Message GET endpoints return HTTP 304 with an empty body when there are no
  (new) messages; [`unwrap_envelope`][groupme_mcp_server.models.unwrap_envelope]
  returns ``None`` for that case.
- Message pagination uses ``before_id`` / ``since_id`` / ``after_id`` with a
  ``limit`` of at most [`MAX_MESSAGE_LIMIT`][groupme_mcp_server.models.MAX_MESSAGE_LIMIT].
- Rate limiting surfaces as HTTP 420 (sometimes 429).
"""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any, Literal, NewType
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from groupme_mcp_server.errors import GroupMeApiError, error_for_status

GroupId = NewType("GroupId", str)
"""Identifier of a GroupMe group."""

UserId = NewType("UserId", str)
"""Identifier of a GroupMe user."""

MessageId = NewType("MessageId", str)
"""Identifier of a GroupMe message."""

BotId = NewType("BotId", str)
"""Identifier of a GroupMe bot."""

ConversationId = NewType("ConversationId", str)
"""Identifier accepted by the like endpoints: a group id, or the composite
direct-chat id (e.g. ``"123+456"``)."""

HTTP_NOT_MODIFIED = 304
"""Status GroupMe uses to signal an empty message page."""

MAX_MESSAGE_LIMIT = 100
"""Largest ``limit`` the message endpoints accept."""

RATE_LIMIT_STATUSES = frozenset({420, 429})
"""Statuses GroupMe uses to signal rate limiting."""

MAX_MESSAGE_TEXT_LENGTH = 1000
"""Longest message text GroupMe accepts."""

GROUPME_IMAGE_HOSTS = frozenset({"i.groupme.com", "image.groupme.com"})
"""Hosts of GroupMe's image service — the only image URLs a message can attach."""

LeaderboardPeriod = Literal["day", "week", "month"]
"""Time window accepted by the likes leaderboard endpoint."""


class GroupMeModel(BaseModel):
    """Base class for all GroupMe domain models: frozen, extras ignored."""

    model_config = ConfigDict(frozen=True)


class ImageAttachment(GroupMeModel):
    """An image hosted on GroupMe's image service."""

    type: Literal["image"] = "image"
    url: str


class LocationAttachment(GroupMeModel):
    """A shared geographic location."""

    type: Literal["location"] = "location"
    name: str
    lat: str
    lng: str


class MentionsAttachment(GroupMeModel):
    """User mentions within the message text."""

    type: Literal["mentions"] = "mentions"
    user_ids: tuple[UserId, ...]
    loci: tuple[tuple[int, int], ...] = ()


class ReplyAttachment(GroupMeModel):
    """A reply reference to an earlier message."""

    type: Literal["reply"] = "reply"
    reply_id: MessageId
    base_reply_id: MessageId


class UnknownAttachment(GroupMeModel):
    """Catch-all for attachment types this server does not model.

    GroupMe adds attachment types over time; unknown ones are preserved
    verbatim instead of failing the whole message.
    """

    type: str
    data: dict[str, Any]


Attachment = (
    ImageAttachment | LocationAttachment | MentionsAttachment | ReplyAttachment | UnknownAttachment
)
"""Any message attachment; ``UnknownAttachment`` absorbs unrecognized types."""


class Me(GroupMeModel):
    """The authenticated GroupMe user's profile."""

    id: UserId
    name: str
    image_url: str | None = None


class Message(GroupMeModel):
    """A normalized GroupMe message (group or direct)."""

    id: MessageId
    conversation_id: ConversationId | None = None
    sender_id: UserId
    sender_name: str
    text: str | None
    created_at: datetime
    favorited_by_count: int
    attachments: tuple[Attachment, ...] = ()


class Member(GroupMeModel):
    """One membership entry of a GroupMe group."""

    user_id: UserId
    nickname: str
    roles: tuple[str, ...] = ()


class MessagePreview(GroupMeModel):
    """Lightweight preview of a conversation's most recent message."""

    sender_name: str | None = None
    text: str | None = None
    created_at: datetime | None = None


class Group(GroupMeModel):
    """A GroupMe group conversation."""

    kind: Literal["group"] = "group"
    id: GroupId
    name: str
    description: str | None = None
    image_url: str | None = None
    member_count: int | None = None
    members: tuple[Member, ...] | None = None
    creator_user_id: UserId | None = None
    share_url: str | None = None
    updated_at: datetime | None = None
    preview: MessagePreview | None = None


class DirectChat(GroupMeModel):
    """A one-on-one GroupMe conversation."""

    kind: Literal["direct"] = "direct"
    conversation_id: ConversationId | None = None
    other_user_id: UserId
    other_user_name: str
    updated_at: datetime | None = None
    preview: MessagePreview | None = None


Conversation = Annotated[Group | DirectChat, Field(discriminator="kind")]
"""Either kind of conversation, discriminated by the ``kind`` tag."""


class GroupRef(GroupMeModel):
    """Reference to a group conversation, for tool inputs.

    Extra fields are forbidden so a call mixing group and direct-chat ids
    fails validation instead of silently dropping one of them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["group"] = "group"
    group_id: GroupId = Field(description="The group's id, e.g. from list_conversations.")


class DirectRef(GroupMeModel):
    """Reference to a direct-message conversation, for tool inputs.

    Extra fields are forbidden so a call mixing group and direct-chat ids
    fails validation instead of silently dropping one of them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["direct"] = "direct"
    other_user_id: UserId = Field(
        description="The other participant's user id, e.g. from list_conversations."
    )


ConversationRef = Annotated[GroupRef | DirectRef, Field(discriminator="kind")]
"""A conversation reference: exactly one of a group id or a DM partner id."""

_KNOWN_ATTACHMENTS: dict[str, type[Attachment]] = {
    "image": ImageAttachment,
    "location": LocationAttachment,
    "mentions": MentionsAttachment,
    "reply": ReplyAttachment,
}


def parse_attachment(raw: dict[str, Any]) -> Attachment:
    """Parse one raw attachment dict, tolerating unknown types.

    Args:
        raw: The attachment object as returned by GroupMe.

    Returns:
        The typed attachment, or an ``UnknownAttachment`` preserving the raw
        payload when the ``type`` is not recognized.
    """
    model = _KNOWN_ATTACHMENTS.get(str(raw.get("type")))
    if model is None:
        return UnknownAttachment(type=str(raw.get("type")), data=dict(raw))
    return model.model_validate(raw)


def parse_message(raw: dict[str, Any]) -> Message:
    """Normalize a raw GroupMe message dict into a ``Message``.

    Args:
        raw: The message object as returned by GroupMe.

    Returns:
        The normalized message, with ``created_at`` as an aware UTC datetime
        and ``favorited_by_count`` derived from the ``favorited_by`` list.
    """
    conversation = raw.get("conversation_id", raw.get("group_id"))
    return Message(
        id=MessageId(str(raw["id"])),
        conversation_id=ConversationId(str(conversation)) if conversation is not None else None,
        sender_id=UserId(str(raw.get("sender_id", raw.get("user_id", "")))),
        sender_name=str(raw.get("name", "")),
        text=raw.get("text"),
        created_at=datetime.fromtimestamp(int(raw["created_at"]), tz=UTC),
        favorited_by_count=len(raw.get("favorited_by", ())),
        attachments=tuple(parse_attachment(a) for a in raw.get("attachments", ())),
    )


def parse_me(raw: dict[str, Any]) -> Me:
    """Normalize the raw ``users/me`` payload into a ``Me``.

    Args:
        raw: The user object as returned by GroupMe.

    Returns:
        The normalized profile.
    """
    return Me(
        id=UserId(str(raw["id"])),
        name=str(raw["name"]),
        image_url=raw.get("image_url"),
    )


def ensure_single_cursor(*cursors: object) -> None:
    """Reject mutually conflicting pagination cursors.

    GroupMe's message endpoints treat ``before_id`` / ``since_id`` /
    ``after_id`` as alternative cursor modes with different ordering
    semantics, so at most one may be supplied per request.

    Args:
        *cursors: The cursor values as given by the caller (``None`` when
            not supplied).

    Raises:
        ValueError: If more than one cursor is not ``None``.
    """
    if sum(cursor is not None for cursor in cursors) > 1:
        msg = "provide at most one pagination cursor (before_id / since_id / after_id)"
        raise ValueError(msg)


def optional_epoch(value: object) -> datetime | None:
    """Convert an optional Unix timestamp to an aware UTC datetime.

    Args:
        value: A candidate timestamp from a raw payload.

    Returns:
        The datetime for an int or float value, otherwise ``None``
        (booleans, strings, and missing values all normalize to ``None``).
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value, tz=UTC)


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None


def _preview_from(sender_name: object, text: object, created_at: object) -> MessagePreview | None:
    preview = MessagePreview(
        sender_name=_optional_str(sender_name),
        text=_optional_str(text),
        created_at=optional_epoch(created_at),
    )
    return None if preview == MessagePreview() else preview


def _group_preview(raw: dict[str, Any]) -> MessagePreview | None:
    messages = raw.get("messages")
    if not isinstance(messages, dict):
        return None
    preview = messages.get("preview")
    if not isinstance(preview, dict):
        preview = {}
    return _preview_from(
        preview.get("nickname"),
        preview.get("text"),
        messages.get("last_message_created_at"),
    )


def _parse_member(raw: dict[str, Any]) -> Member:
    roles = raw.get("roles")
    return Member(
        user_id=UserId(str(raw["user_id"])),
        nickname=str(raw.get("nickname", "")),
        roles=tuple(str(role) for role in roles) if isinstance(roles, list) else (),
    )


def parse_group(raw: dict[str, Any]) -> Group:
    """Normalize a raw GroupMe group dict into a ``Group``.

    Args:
        raw: The group object as returned by GroupMe.

    Returns:
        The normalized group. ``members`` and ``member_count`` are ``None``
        when memberships were omitted from the listing.
    """
    raw_members = raw.get("members")
    members = (
        tuple(_parse_member(member) for member in raw_members)
        if isinstance(raw_members, list)
        else None
    )
    creator = raw.get("creator_user_id")
    return Group(
        id=GroupId(str(raw["id"])),
        name=str(raw["name"]),
        description=raw.get("description"),
        image_url=raw.get("image_url"),
        member_count=len(members) if members is not None else None,
        members=members,
        creator_user_id=UserId(str(creator)) if creator is not None else None,
        share_url=raw.get("share_url"),
        updated_at=optional_epoch(raw.get("updated_at")),
        preview=_group_preview(raw),
    )


def parse_direct_chat(raw: dict[str, Any]) -> DirectChat:
    """Normalize a raw GroupMe chat dict into a ``DirectChat``.

    Args:
        raw: The chat object as returned by GroupMe's ``/chats`` listing.

    Returns:
        The normalized direct chat. ``conversation_id`` (the id the like
        endpoints need) is recovered from the chat's last message when
        present.
    """
    other = raw["other_user"]
    last_message = raw.get("last_message")
    preview = (
        _preview_from(
            last_message.get("name"),
            last_message.get("text"),
            last_message.get("created_at"),
        )
        if isinstance(last_message, dict)
        else None
    )
    return DirectChat(
        conversation_id=_chat_conversation_id(raw),
        other_user_id=UserId(str(other["id"])),
        other_user_name=str(other["name"]),
        updated_at=optional_epoch(raw.get("updated_at")),
        preview=preview,
    )


def _chat_conversation_id(raw: dict[str, Any]) -> ConversationId | None:
    last_message = raw.get("last_message")
    if not isinstance(last_message, dict):
        return None
    value = last_message.get("conversation_id")
    return ConversationId(str(value)) if value is not None else None


def meta_errors(payload: object) -> tuple[str, ...]:
    """Extract ``meta.errors`` strings from a response payload, tolerantly.

    Args:
        payload: The decoded JSON body, of any shape (or ``None``).

    Returns:
        The error strings, or an empty tuple when the shape does not match.
    """
    if not isinstance(payload, dict):
        return ()
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return ()
    errors = meta.get("errors")
    if not isinstance(errors, list):
        return ()
    return tuple(str(e) for e in errors)


def unwrap_envelope(status_code: int, payload: object) -> object | None:
    """Unwrap GroupMe's ``{"response": ..., "meta": ...}`` envelope.

    Pure: raises the typed error matching a failure status instead of
    performing any recovery.

    Args:
        status_code: HTTP status of the response.
        payload: The decoded JSON body, or ``None`` when the body was empty.

    Returns:
        The ``response`` value for a success, or ``None`` for HTTP 304
        (GroupMe's "no messages" signal).

    Raises:
        GroupMeApiError: For failure statuses (or subclasses: 401 auth,
            404 not found, 420/429 rate limit), and for a success body that
            is not an envelope.
    """
    if status_code == HTTP_NOT_MODIFIED:
        return None
    if status_code >= HTTPStatus.BAD_REQUEST:
        raise error_for_status(status_code, meta_errors(payload))
    if not isinstance(payload, dict):
        msg = f"GroupMe returned a malformed response body (HTTP {status_code})"
        raise GroupMeApiError(msg, status=status_code)
    return payload.get("response")


def validate_outgoing_text(text: str, *, has_attachments: bool) -> None:
    """Validate message text before it is sent to GroupMe.

    Pure precondition check run *before* any HTTP request so the caller gets
    an actionable error instead of an opaque API failure. The error text
    reports lengths and never echoes the message content.

    Args:
        text: The message text the caller wants to send.
        has_attachments: Whether the outgoing message carries attachments
            (an attachment-only message may have empty text).

    Raises:
        ValueError: If the text exceeds
            [`MAX_MESSAGE_TEXT_LENGTH`][groupme_mcp_server.models.MAX_MESSAGE_TEXT_LENGTH],
            or is empty/whitespace with no attachments to carry the message.
    """
    if len(text) > MAX_MESSAGE_TEXT_LENGTH:
        msg = (
            f"text is {len(text)} characters but GroupMe allows at most "
            f"{MAX_MESSAGE_TEXT_LENGTH}; shorten it or split it into several messages"
        )
        raise ValueError(msg)
    if not text.strip() and not has_attachments:
        msg = "text is empty; provide message text (or attach an image_url)"
        raise ValueError(msg)


def groupme_image_attachment(image_url: str) -> dict[str, Any]:
    """Build an image attachment from a GroupMe image-service URL.

    Args:
        image_url: An ``https`` URL on one of
            [`GROUPME_IMAGE_HOSTS`][groupme_mcp_server.models.GROUPME_IMAGE_HOSTS].

    Returns:
        The raw attachment object GroupMe expects.

    Raises:
        ValueError: If the URL is not a GroupMe image-service URL.
            Downloading and re-uploading arbitrary images is not supported
            yet (future work: the image-upload service).
    """
    parsed = urlsplit(image_url)
    if parsed.scheme != "https" or parsed.hostname not in GROUPME_IMAGE_HOSTS:
        hosts = " or ".join(f"https://{host}/..." for host in sorted(GROUPME_IMAGE_HOSTS))
        msg = (
            f"image_url must be a GroupMe image-service URL ({hosts}); "
            "other image URLs are not supported yet, so upload the image to "
            "GroupMe's image service first and pass the resulting URL"
        )
        raise ValueError(msg)
    return {"type": "image", "url": image_url}


def build_outgoing_attachments(
    reply_to_message_id: MessageId | None, image_url: str | None
) -> list[dict[str, Any]]:
    """Compose the raw attachment list for an outgoing message.

    Args:
        reply_to_message_id: Message being replied to, if any; becomes a
            ``reply`` attachment.
        image_url: GroupMe image-service URL to attach, if any.

    Returns:
        The raw attachment objects, in reply-then-image order.

    Raises:
        ValueError: If ``image_url`` is not a GroupMe image-service URL.
    """
    attachments: list[dict[str, Any]] = []
    if reply_to_message_id is not None:
        attachments.append(
            {
                "type": "reply",
                "reply_id": str(reply_to_message_id),
                "base_reply_id": str(reply_to_message_id),
            }
        )
    if image_url is not None:
        attachments.append(groupme_image_attachment(image_url))
    return attachments


def backoff_delay(attempt: int, *, jitter: float, base: float = 0.5, cap: float = 8.0) -> float:
    """Compute a bounded exponential-backoff delay with jitter.

    Args:
        attempt: Zero-based retry attempt number.
        jitter: Random factor in ``[0.0, 1.0]`` supplied by the caller so the
            function stays pure.
        base: Delay in seconds for attempt 0 before jitter.
        cap: Upper bound on the un-jittered delay in seconds.

    Returns:
        A delay in seconds in ``(0, cap]``: the capped exponential term scaled
        by ``0.5 + jitter / 2``.

    Raises:
        ValueError: If ``jitter`` is outside ``[0.0, 1.0]``.
    """
    if not 0.0 <= jitter <= 1.0:
        msg = f"jitter must be within [0.0, 1.0], got {jitter}"
        raise ValueError(msg)
    return min(cap, base * 2**attempt) * (0.5 + jitter / 2)
