"""Shared plumbing for the MCP tool layer (imperative shell).

Provides the client factory the tools use (tests replace it to inject a mock
transport) and the mapping from the typed error hierarchy to actionable
``ToolError`` text.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastmcp.exceptions import ToolError

from groupme_mcp_server.client import GroupMeClient
from groupme_mcp_server.errors import (
    GroupMeApiError,
    GroupMeAuthError,
    GroupMeNotFoundError,
    GroupMeRateLimitError,
)
from groupme_mcp_server.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

READ_ONLY_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
"""Honest annotations shared by every read-only tool: they never write, and
they talk to the external GroupMe service."""

SEND_MESSAGE_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
"""Honest annotations for ``send_message``: it writes (but destroys nothing),
repeating it sends duplicate messages, and it talks to GroupMe."""

REACTION_ANNOTATIONS: dict[str, bool] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
"""Honest annotations for ``react_to_message``: liking or unliking twice lands
in the same state, and an unlike only removes this user's own like."""


def create_client() -> GroupMeClient:
    """Build a GroupMe client from the process-wide settings.

    Tool modules call this through the module (``common.create_client``) so
    tests can replace it with a factory that injects a mock transport.

    Returns:
        A fresh client; close it with ``async with`` or ``aclose``.
    """
    return GroupMeClient(get_settings())


def tool_error_message(
    exc: GroupMeApiError, example: str, *, not_found_detail: str | None = None
) -> str:
    """Translate a client error into actionable tool-facing text.

    Pure mapping: what went wrong, what to do about it, and what a valid
    call looks like. Never includes credentials.

    Args:
        exc: The typed error raised by the client layer.
        example: A valid example call for the failing tool.
        not_found_detail: Tool-specific guidance for a 404; the default
            points at ``list_conversations`` for conversation ids.

    Returns:
        The message to raise as a ``ToolError``.
    """
    if isinstance(exc, GroupMeAuthError):
        detail = str(exc)
    elif isinstance(exc, GroupMeRateLimitError):
        detail = "GroupMe is rate limiting this client; wait about 30 seconds and retry."
    elif isinstance(exc, GroupMeNotFoundError):
        detail = (
            not_found_detail
            if not_found_detail is not None
            else (
                "GroupMe could not find that conversation. Check the id - "
                "call list_conversations to discover valid group_id / other_user_id values."
            )
        )
    else:
        detail = str(exc)
    return f"{detail} A valid call looks like: {example}"


@asynccontextmanager
async def tool_client(
    example: str, *, not_found_detail: str | None = None
) -> AsyncIterator[GroupMeClient]:
    """Yield an open client, mapping failures to actionable ``ToolError`` text.

    Args:
        example: A valid example call for the tool, included in every error.
        not_found_detail: Tool-specific guidance for a 404, forwarded to
            [`tool_error_message`][groupme_mcp_server.tools.common.tool_error_message].

    Yields:
        An open GroupMe client; its connection pool closes on exit.

    Raises:
        ToolError: For any ``GroupMeApiError`` from the client, or a
            ``ValueError`` from argument validation (e.g. conflicting
            pagination cursors).
    """
    try:
        async with create_client() as client:
            yield client
    except GroupMeApiError as exc:
        raise ToolError(
            tool_error_message(exc, example, not_found_detail=not_found_detail)
        ) from exc
    except ValueError as exc:
        msg = f"Invalid arguments: {exc}. A valid call looks like: {example}"
        raise ToolError(msg) from exc
