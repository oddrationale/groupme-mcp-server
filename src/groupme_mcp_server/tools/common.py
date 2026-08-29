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


def create_client() -> GroupMeClient:
    """Build a GroupMe client from the process-wide settings.

    Tool modules call this through the module (``common.create_client``) so
    tests can replace it with a factory that injects a mock transport.

    Returns:
        A fresh client; close it with ``async with`` or ``aclose``.
    """
    return GroupMeClient(get_settings())


def tool_error_message(exc: GroupMeApiError, example: str) -> str:
    """Translate a client error into actionable tool-facing text.

    Pure mapping: what went wrong, what to do about it, and what a valid
    call looks like. Never includes credentials.

    Args:
        exc: The typed error raised by the client layer.
        example: A valid example call for the failing tool.

    Returns:
        The message to raise as a ``ToolError``.
    """
    if isinstance(exc, GroupMeAuthError):
        detail = str(exc)
    elif isinstance(exc, GroupMeRateLimitError):
        detail = "GroupMe is rate limiting this client; wait about 30 seconds and retry."
    elif isinstance(exc, GroupMeNotFoundError):
        detail = (
            "GroupMe could not find that conversation. Check the id - "
            "call list_conversations to discover valid group_id / other_user_id values."
        )
    else:
        detail = str(exc)
    return f"{detail} A valid call looks like: {example}"


@asynccontextmanager
async def tool_client(example: str) -> AsyncIterator[GroupMeClient]:
    """Yield an open client, mapping failures to actionable ``ToolError`` text.

    Args:
        example: A valid example call for the tool, included in every error.

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
        raise ToolError(tool_error_message(exc, example)) from exc
    except ValueError as exc:
        msg = f"Invalid arguments: {exc}. A valid call looks like: {example}"
        raise ToolError(msg) from exc
