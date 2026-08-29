"""The ``react_to_message`` tool: like or unlike one message."""

from __future__ import annotations

import logging
from typing import Literal

from groupme_mcp_server.models import ConversationId, MessageId
from groupme_mcp_server.rendering import ReactionResult, reaction_result
from groupme_mcp_server.tools import common

logger = logging.getLogger(__name__)

_EXAMPLE = 'react_to_message(conversation_id="12345678", message_id="1234567890", action="like")'

_NOT_FOUND_DETAIL = (
    "GroupMe could not find that message or conversation. Get valid ids from "
    'read_messages with response_format="detailed": use its conversation_id '
    "and message id fields."
)


async def react_to_message(
    conversation_id: str,
    message_id: str,
    action: Literal["like", "unlike"],
) -> ReactionResult:
    """Like a GroupMe message, or remove your like from one.

    Both actions are idempotent: liking an already-liked message (or
    unliking one you never liked) leaves it in the requested state. Ids come
    from ``read_messages`` with ``response_format="detailed"``: use its
    ``conversation_id`` (a group id, or a composite direct-chat id like
    ``"123+456"``) and message ``id``.

    Args:
        conversation_id: The conversation holding the message.
        message_id: The message to react to.
        action: ``"like"`` to like it, ``"unlike"`` to remove your like.

    Returns:
        A confirmation echoing the action and ids.
    """
    async with common.tool_client(_EXAMPLE, not_found_detail=_NOT_FOUND_DETAIL) as client:
        if action == "like":
            await client.like(ConversationId(conversation_id), MessageId(message_id))
        else:
            await client.unlike(ConversationId(conversation_id), MessageId(message_id))
    logger.info(
        "react_to_message: %s message %s in conversation %s", action, message_id, conversation_id
    )
    return reaction_result(action, conversation_id, message_id)
