"""Typed exceptions for GroupMe API failures.

Pure exception types raised by the HTTP client. The tool layer maps them to
actionable ``fastmcp.exceptions.ToolError`` text; nothing here performs IO.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Guidance text about a token, not a credential itself.
MISSING_TOKEN_MESSAGE = (
    "GroupMe access token is missing or invalid. "  # noqa: S105
    "Set GROUPME_ACCESS_TOKEN — get a token at https://dev.groupme.com"
)
"""Actionable guidance shown whenever authentication with GroupMe fails."""


class GroupMeApiError(Exception):
    """A GroupMe API call failed.

    Attributes:
        status: HTTP status code of the failed call, or ``None`` when the
            failure happened before a response was received.
        messages: Error strings from the response envelope's ``meta.errors``.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        messages: Sequence[str] = (),
    ) -> None:
        """Initialize the error.

        Args:
            message: Human-readable description of the failure.
            status: HTTP status code, if a response was received.
            messages: Error strings from the envelope's ``meta.errors``.
        """
        super().__init__(message)
        self.status = status
        self.messages = tuple(messages)


class GroupMeAuthError(GroupMeApiError):
    """Authentication with GroupMe failed (missing or rejected token)."""


class GroupMeRateLimitError(GroupMeApiError):
    """GroupMe rate-limited the request and retries were exhausted."""


class GroupMeNotFoundError(GroupMeApiError):
    """The requested GroupMe resource does not exist or is inaccessible."""


def error_for_status(status: int, messages: Sequence[str]) -> GroupMeApiError:
    """Build the typed error matching an HTTP failure status.

    Pure mapping used by the client after envelope parsing.

    Args:
        status: HTTP status code of the failed response.
        messages: Error strings extracted from the envelope's ``meta.errors``.

    Returns:
        The most specific error type for ``status``.
    """
    if status == HTTPStatus.UNAUTHORIZED:
        return GroupMeAuthError(MISSING_TOKEN_MESSAGE, status=status, messages=messages)
    if status == HTTPStatus.NOT_FOUND:
        return GroupMeNotFoundError("GroupMe resource not found", status=status, messages=messages)
    if status in (420, 429):
        return GroupMeRateLimitError(
            "GroupMe rate limit exceeded", status=status, messages=messages
        )
    detail = "; ".join(messages) or "no error details provided"
    return GroupMeApiError(
        f"GroupMe API error (HTTP {status}): {detail}", status=status, messages=messages
    )
