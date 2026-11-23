"""The ``send_message`` tool: post to a group, topic, or direct-message chat."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from groupme_mcp_server.media import parse_image_url
from groupme_mcp_server.models import (
    ConversationRef,
    DirectRef,
    MessageId,
    TopicRef,
    UserId,
    build_outgoing_attachments,
    is_groupme_image_url,
    validate_outgoing_text,
)
from groupme_mcp_server.rendering import SentMessage, sent_message_view
from groupme_mcp_server.tools import common

if TYPE_CHECKING:
    from groupme_mcp_server.client import GroupMeClient

logger = logging.getLogger(__name__)

_EXAMPLE = (
    'send_message(conversation={"kind": "group", "group_id": "12345678"}, '
    'text="Hello everyone!") or '
    'send_message(conversation={"kind": "direct", "other_user_id": "87654321"}, text="Hi!") or '
    'send_message(conversation={"kind": "topic", "parent_group_id": "12345678", '
    '"topic_id": "23456789"}, text="Trip details are ready.")'
)


async def _rehost_image(client: GroupMeClient, image_url: str) -> str:
    """Download an image from any URL and re-host it on GroupMe's image service.

    Args:
        client: Open GroupMe client, used for the upload.
        image_url: The caller's image URL (never sent the GroupMe token).

    Returns:
        The GroupMe-hosted URL to attach.
    """
    async with common.create_image_fetcher() as fetcher:
        image = await fetcher.fetch(image_url)
    hosted = await client.upload_image(image.data, image.content_type)
    # The source URL is user data: log only the size and type.
    logger.info(
        "send_message: re-hosted a %d-byte %s image on GroupMe", len(image.data), image.content_type
    )
    return hosted


async def send_message(
    conversation: ConversationRef,
    text: str,
    reply_to_message_id: str | None = None,
    image_url: str | None = None,
) -> SentMessage:
    """Send a message to a GroupMe group, topic, or direct-message chat.

    Use this to post as the authenticated user once you know where to send
    (ids come from ``list_conversations``). Each call sends a new message —
    calling twice posts twice. A topic is checked against its parent group
    before posting, and a failed send is never redirected to the parent's
    main chat. Announcement topics may only accept posts from some members;
    GroupMe enforces that. To reply to a specific message, pass its id
    (from ``read_messages``) as ``reply_to_message_id``.

    Args:
        conversation: Where to send: ``{"kind": "group", "group_id": ...}``,
            ``{"kind": "direct", "other_user_id": ...}``, or
            ``{"kind": "topic", "parent_group_id": ..., "topic_id": ...}``.
        text: The message text, at most 1000 characters. May be empty only
            when ``image_url`` is given.
        reply_to_message_id: Id of the message being replied to, attached as
            a GroupMe reply so clients render it threaded.
        image_url: Image to attach, from any public ``http``/``https``
            URL. GroupMe image-service URLs (``https://i.groupme.com/...``)
            are attached as-is; anything else is downloaded (raster images
            up to 15 MB, 10 seconds) and re-hosted on GroupMe first. URLs on
            private or internal networks are refused.

    Returns:
        The sent message's id, conversation ids, timestamp, and echoed
        content, for chaining into ``react_to_message`` or ``read_messages``.
    """
    async with common.tool_client(
        _EXAMPLE, not_found_detail=common.not_found_detail_for(conversation)
    ) as client:
        reply_to = MessageId(reply_to_message_id) if reply_to_message_id is not None else None
        needs_rehost = image_url is not None and not is_groupme_image_url(image_url)
        if needs_rehost:
            parse_image_url(str(image_url))  # cheap shape check before any network access
        validate_outgoing_text(text, has_attachments=reply_to is not None or image_url is not None)
        if isinstance(conversation, TopicRef):
            # Writes are the costly place to be wrong: confirm the topic belongs
            # to the stated parent (GroupMe answers 404 otherwise) before posting.
            await client.get_topic(conversation.parent_group_id, conversation.topic_id)
        if needs_rehost:
            image_url = await _rehost_image(client, str(image_url))
        attachments = build_outgoing_attachments(reply_to, image_url)
        if not isinstance(conversation, DirectRef):
            message = await client.create_group_message(
                common.message_group_id(conversation), text, attachments=attachments
            )
        else:
            message = await client.create_direct_message(
                UserId(conversation.other_user_id), text, attachments=attachments
            )
    # Message text is user data: log ids and counts only, never the content.
    logger.info(
        "send_message: message %s sent to %s conversation (%d attachment(s))",
        message.id,
        conversation.kind,
        len(attachments),
    )
    return sent_message_view(message, conversation)
