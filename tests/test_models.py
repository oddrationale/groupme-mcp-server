from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from groupme_mcp_server.errors import (
    GroupMeApiError,
    GroupMeAuthError,
    GroupMeNotFoundError,
    GroupMeRateLimitError,
)
from groupme_mcp_server.models import (
    Conversation,
    DirectChat,
    Group,
    ImageAttachment,
    LocationAttachment,
    Me,
    MentionsAttachment,
    ReplyAttachment,
    UnknownAttachment,
    backoff_delay,
    ensure_single_cursor,
    meta_errors,
    parse_attachment,
    parse_direct_chat,
    parse_group,
    parse_me,
    parse_message,
    unwrap_envelope,
)

# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


def test_parse_image_attachment() -> None:
    parsed = parse_attachment({"type": "image", "url": "https://i.groupme.com/x.jpg"})
    assert parsed == ImageAttachment(url="https://i.groupme.com/x.jpg")


def test_parse_location_attachment() -> None:
    parsed = parse_attachment({"type": "location", "name": "HQ", "lat": "40.7", "lng": "-74.0"})
    assert parsed == LocationAttachment(name="HQ", lat="40.7", lng="-74.0")


def test_parse_mentions_attachment() -> None:
    parsed = parse_attachment(
        {"type": "mentions", "user_ids": ["1", "2"], "loci": [[0, 5], [6, 3]]}
    )
    assert isinstance(parsed, MentionsAttachment)
    assert parsed.user_ids == ("1", "2")
    assert parsed.loci == ((0, 5), (6, 3))


def test_parse_mentions_attachment_without_loci() -> None:
    parsed = parse_attachment({"type": "mentions", "user_ids": ["1"]})
    assert isinstance(parsed, MentionsAttachment)
    assert parsed.loci == ()


def test_parse_reply_attachment() -> None:
    parsed = parse_attachment({"type": "reply", "reply_id": "9", "base_reply_id": "9"})
    assert parsed == ReplyAttachment(reply_id="9", base_reply_id="9")


def test_parse_unknown_attachment_is_tolerated() -> None:
    raw = {"type": "poll", "poll_id": "123"}
    parsed = parse_attachment(raw)
    assert parsed == UnknownAttachment(type="poll", data=raw)


def test_parse_attachment_without_type_is_unknown() -> None:
    parsed = parse_attachment({"url": "https://example.test"})
    assert isinstance(parsed, UnknownAttachment)
    assert parsed.type == "None"


def test_attachments_are_frozen() -> None:
    attachment = ImageAttachment(url="https://i.groupme.com/x.jpg")
    with pytest.raises(ValidationError):
        attachment.url = "https://evil.test"  # ty: ignore[invalid-assignment]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


def test_parse_message_normalizes_fields() -> None:
    message = parse_message(
        {
            "id": "111",
            "sender_id": "22",
            "name": "Ada",
            "text": "hello",
            "created_at": 1700000000,
            "favorited_by": ["1", "2", "3"],
            "attachments": [{"type": "image", "url": "https://i.groupme.com/x.jpg"}],
        }
    )
    assert message.id == "111"
    assert message.sender_id == "22"
    assert message.sender_name == "Ada"
    assert message.text == "hello"
    assert message.created_at == datetime.fromtimestamp(1700000000, tz=UTC)
    assert message.created_at.tzinfo is not None
    assert message.favorited_by_count == 3
    assert message.attachments == (ImageAttachment(url="https://i.groupme.com/x.jpg"),)


def test_parse_message_conversation_id_from_group_id() -> None:
    message = parse_message({"id": "1", "group_id": "42", "created_at": 0, "text": None})
    assert message.conversation_id == "42"


def test_parse_message_prefers_conversation_id() -> None:
    message = parse_message(
        {"id": "1", "conversation_id": "12+34", "group_id": "42", "created_at": 0, "text": None}
    )
    assert message.conversation_id == "12+34"


def test_parse_message_falls_back_to_user_id() -> None:
    message = parse_message({"id": "1", "user_id": "99", "created_at": 0, "text": None})
    assert message.sender_id == "99"


def test_parse_message_prefers_sender_id_over_user_id() -> None:
    message = parse_message(
        {"id": "1", "sender_id": "22", "user_id": "99", "created_at": 0, "text": None}
    )
    assert message.sender_id == "22"


def test_parse_message_minimal() -> None:
    message = parse_message({"id": "1", "created_at": 0, "text": None})
    assert message.sender_id == ""
    assert message.sender_name == ""
    assert message.text is None
    assert message.favorited_by_count == 0
    assert message.attachments == ()


def test_message_is_frozen() -> None:
    message = parse_message({"id": "1", "created_at": 0, "text": None})
    with pytest.raises(ValidationError):
        message.text = "edited"  # ty: ignore[invalid-assignment]


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------


def test_parse_group_with_members() -> None:
    group = parse_group(
        {
            "id": "42",
            "name": "Book club",
            "description": "We read",
            "image_url": "https://i.groupme.com/g.jpg",
            "members": [{"user_id": "1"}, {"user_id": "2"}],
        }
    )
    assert group == Group(
        id="42",
        name="Book club",
        description="We read",
        image_url="https://i.groupme.com/g.jpg",
        member_count=2,
    )


def test_parse_group_without_members() -> None:
    group = parse_group({"id": "42", "name": "Book club"})
    assert group.member_count is None
    assert group.description is None
    assert group.image_url is None


def test_parse_me() -> None:
    me = parse_me({"id": "5", "name": "Ada", "image_url": "https://i.groupme.com/a.jpg"})
    assert me == Me(id="5", name="Ada", image_url="https://i.groupme.com/a.jpg")
    assert parse_me({"id": "5", "name": "Ada"}).image_url is None


def test_ensure_single_cursor_accepts_zero_or_one() -> None:
    ensure_single_cursor(None, None, None)
    ensure_single_cursor("b", None, None)
    ensure_single_cursor(None, "s", None)


def test_ensure_single_cursor_rejects_conflicts() -> None:
    with pytest.raises(ValueError, match="at most one pagination cursor"):
        ensure_single_cursor("b", "s", None)


def test_parse_direct_chat() -> None:
    chat = parse_direct_chat({"other_user": {"id": "7", "name": "Grace"}})
    assert chat == DirectChat(other_user_id="7", other_user_name="Grace")
    assert chat.conversation_id is None


def test_parse_direct_chat_recovers_conversation_id() -> None:
    chat = parse_direct_chat(
        {
            "other_user": {"id": "7", "name": "Grace"},
            "last_message": {"conversation_id": "12+34"},
        }
    )
    assert chat.conversation_id == "12+34"


def test_parse_direct_chat_last_message_without_conversation_id() -> None:
    chat = parse_direct_chat(
        {"other_user": {"id": "7", "name": "Grace"}, "last_message": {"id": "m1"}}
    )
    assert chat.conversation_id is None


def test_conversation_discriminates_on_kind() -> None:
    adapter: TypeAdapter[Group | DirectChat] = TypeAdapter(Conversation)
    group = adapter.validate_python({"kind": "group", "id": "1", "name": "g"})
    assert isinstance(group, Group)
    chat = adapter.validate_python({"kind": "direct", "other_user_id": "2", "other_user_name": "n"})
    assert isinstance(chat, DirectChat)


# ---------------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------------


def test_meta_errors_happy_path() -> None:
    payload = {"meta": {"code": 400, "errors": ["bad", 5]}}
    assert meta_errors(payload) == ("bad", "5")


@pytest.mark.parametrize(
    "payload",
    [None, "nope", {"no_meta": 1}, {"meta": "nope"}, {"meta": {"errors": "nope"}}],
)
def test_meta_errors_tolerates_odd_shapes(payload: object) -> None:
    assert meta_errors(payload) == ()


def test_unwrap_envelope_success() -> None:
    payload = {"response": {"id": "1"}, "meta": {"code": 200}}
    assert unwrap_envelope(200, payload) == {"id": "1"}


def test_unwrap_envelope_304_means_no_messages() -> None:
    assert unwrap_envelope(304, None) is None


def test_unwrap_envelope_401() -> None:
    with pytest.raises(GroupMeAuthError):
        unwrap_envelope(401, {"meta": {"code": 401, "errors": ["unauthorized"]}})


def test_unwrap_envelope_404() -> None:
    with pytest.raises(GroupMeNotFoundError):
        unwrap_envelope(404, None)


@pytest.mark.parametrize("status", [420, 429])
def test_unwrap_envelope_rate_limit(status: int) -> None:
    with pytest.raises(GroupMeRateLimitError):
        unwrap_envelope(status, None)


def test_unwrap_envelope_other_error_carries_meta_errors() -> None:
    with pytest.raises(GroupMeApiError) as excinfo:
        unwrap_envelope(500, {"meta": {"code": 500, "errors": ["kaboom"]}})
    assert excinfo.value.messages == ("kaboom",)


def test_unwrap_envelope_malformed_success_body() -> None:
    with pytest.raises(GroupMeApiError, match="malformed"):
        unwrap_envelope(200, ["not", "an", "envelope"])


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def test_backoff_delay_grows_exponentially() -> None:
    assert backoff_delay(0, jitter=1.0) == 0.5
    assert backoff_delay(1, jitter=1.0) == 1.0
    assert backoff_delay(2, jitter=1.0) == 2.0


def test_backoff_delay_is_capped() -> None:
    assert backoff_delay(10, jitter=1.0) == 8.0


def test_backoff_delay_jitter_scales_down() -> None:
    assert backoff_delay(0, jitter=0.0) == 0.25
    assert backoff_delay(0, jitter=0.5) == pytest.approx(0.375)


@pytest.mark.parametrize("jitter", [-0.1, 1.1])
def test_backoff_delay_rejects_bad_jitter(jitter: float) -> None:
    with pytest.raises(ValueError, match="jitter"):
        backoff_delay(0, jitter=jitter)
