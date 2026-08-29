"""Thin async HTTP client for the GroupMe API v3 (imperative shell).

All parsing and error mapping lives in the functional core
([`groupme_mcp_server.models`][] and [`groupme_mcp_server.errors`][]); this
module only performs IO. The access token is read from settings at request
time via ``SecretStr.get_secret_value()`` and is never stored on the client.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import TYPE_CHECKING, Any, Literal

import httpx2

from groupme_mcp_server.errors import GroupMeApiError
from groupme_mcp_server.models import (
    MAX_MESSAGE_LIMIT,
    RATE_LIMIT_STATUSES,
    backoff_delay,
    ensure_single_cursor,
    parse_direct_chat,
    parse_group,
    parse_me,
    parse_message,
    unwrap_envelope,
)
from groupme_mcp_server.observability import instrumented_async_transport

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from types import TracebackType
    from typing import Self

    from groupme_mcp_server.models import (
        ConversationId,
        DirectChat,
        Group,
        GroupId,
        Me,
        Message,
        MessageId,
        UserId,
    )
    from groupme_mcp_server.settings import Settings

LeaderboardPeriod = Literal["day", "week", "month"]
"""Time window accepted by the likes leaderboard endpoint."""


def _parse[T](parser: Callable[[dict[str, Any]], T], raw: dict[str, Any], context: str) -> T:
    """Run a functional-core parser, mapping failures to the typed hierarchy."""
    try:
        return parser(raw)
    except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError) as exc:
        msg = f"GroupMe returned a malformed {context} object"
        raise GroupMeApiError(msg) from exc


def _parse_list[T](
    parser: Callable[[dict[str, Any]], T], items: list[dict[str, Any]], context: str
) -> list[T]:
    return [_parse(parser, item, context) for item in items]


def _expect_dict(payload: object, context: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        msg = f"GroupMe returned an unexpected payload shape for {context}"
        raise GroupMeApiError(msg)
    return payload


def _expect_list(payload: object, context: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        msg = f"GroupMe returned an unexpected payload shape for {context}"
        raise GroupMeApiError(msg)
    return [_expect_dict(item, context) for item in payload]


class GroupMeClient:
    """Async client for the raw GroupMe API v3 endpoints.

    Handles the response envelope, HTTP 304 empty-message pages, and
    rate-limit retries (HTTP 420/429) with bounded exponential backoff.
    Use as an async context manager to close the connection pool::

        async with GroupMeClient(settings) as client:
            me = await client.get_me()
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        rng: Callable[[], float] | None = None,
        max_retries: int = 3,
    ) -> None:
        """Initialize the client.

        Args:
            settings: Runtime settings supplying the base URL and token.
            transport: Injectable transport for tests; when ``None`` the
                default OTel-instrumented transport is used.
            sleep: Injectable async sleep for tests; defaults to
                ``asyncio.sleep``.
            rng: Injectable jitter source returning floats in ``[0, 1)``;
                defaults to ``random.random``.
            max_retries: Retries attempted after a rate-limited request.
        """
        self._settings = settings
        self._sleep = sleep if sleep is not None else asyncio.sleep
        # Jitter only spreads retries out; it is not security-sensitive.
        self._rng = rng if rng is not None else random.random
        self._max_retries = max_retries
        self._client = httpx2.AsyncClient(
            base_url=settings.api_base_url,
            transport=transport if transport is not None else instrumented_async_transport(),
        )

    async def __aenter__(self) -> Self:
        """Enter the async context.

        Returns:
            This client.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the connection pool on context exit.

        Args:
            exc_type: Exception type, if the block raised.
            exc: Exception instance, if the block raised.
            tb: Traceback, if the block raised.
        """
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying connection pool."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> object | None:
        token = self._settings.require_access_token().get_secret_value()
        attempt = 0
        while True:
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=json_body,
                    headers={"X-Access-Token": token},
                )
            except httpx2.HTTPError as exc:
                # Only the exception type, never its text: transport errors
                # can echo request internals (headers included) back.
                msg = (
                    f"GroupMe request failed before a response was received ({type(exc).__name__})"
                )
                raise GroupMeApiError(msg) from exc
            if response.status_code not in RATE_LIMIT_STATUSES or attempt >= self._max_retries:
                break
            await self._sleep(backoff_delay(attempt, jitter=self._rng()))
            attempt += 1
        try:
            payload: object = response.json()
        except ValueError:
            payload = None
        return unwrap_envelope(response.status_code, payload)

    async def get_me(self) -> Me:
        """Fetch the authenticated user's profile.

        Returns:
            The normalized profile.
        """
        raw = _expect_dict(await self._request("GET", "/users/me"), "users/me")
        return _parse(parse_me, raw, "users/me")

    async def list_groups(
        self,
        page: int = 1,
        per_page: int = 10,
        *,
        omit_memberships: bool = False,
    ) -> list[Group]:
        """List the authenticated user's groups.

        Args:
            page: 1-based page number.
            per_page: Groups per page.
            omit_memberships: Skip the (large) member lists in the response.

        Returns:
            The normalized groups.
        """
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if omit_memberships:
            params["omit"] = "memberships"
        raw = _expect_list(await self._request("GET", "/groups", params=params), "groups")
        return _parse_list(parse_group, raw, "group")

    async def get_group(self, group_id: GroupId) -> Group:
        """Fetch one group by id.

        Args:
            group_id: The group to fetch.

        Returns:
            The normalized group.
        """
        raw = _expect_dict(await self._request("GET", f"/groups/{group_id}"), "group")
        return _parse(parse_group, raw, "group")

    async def list_chats(self, page: int = 1, per_page: int = 10) -> list[DirectChat]:
        """List the authenticated user's direct-message chats.

        Args:
            page: 1-based page number.
            per_page: Chats per page.

        Returns:
            The normalized direct chats.
        """
        params = {"page": page, "per_page": per_page}
        raw = _expect_list(await self._request("GET", "/chats", params=params), "chats")
        return _parse_list(parse_direct_chat, raw, "chat")

    async def list_group_messages(
        self,
        group_id: GroupId,
        *,
        before_id: MessageId | None = None,
        since_id: MessageId | None = None,
        after_id: MessageId | None = None,
        limit: int = 20,
    ) -> list[Message]:
        """List messages in a group, newest first.

        Args:
            group_id: The group to read.
            before_id: Return messages created before this message id.
            since_id: Return the *most recent* messages since this id.
            after_id: Return messages created immediately after this id.
            limit: Messages per page, at most 100.

        Returns:
            The normalized messages; empty when GroupMe answers HTTP 304.

        Raises:
            ValueError: If ``limit`` is out of range or more than one
                pagination cursor is supplied.
        """
        _check_limit(limit)
        ensure_single_cursor(before_id, since_id, after_id)
        params: dict[str, Any] = {"limit": limit}
        if before_id is not None:
            params["before_id"] = before_id
        if since_id is not None:
            params["since_id"] = since_id
        if after_id is not None:
            params["after_id"] = after_id
        payload = await self._request("GET", f"/groups/{group_id}/messages", params=params)
        if payload is None:
            return []
        raw = _expect_list(_expect_dict(payload, "messages").get("messages"), "messages")
        return _parse_list(parse_message, raw, "message")

    async def list_direct_messages(
        self,
        other_user_id: UserId,
        *,
        before_id: MessageId | None = None,
        since_id: MessageId | None = None,
    ) -> list[Message]:
        """List direct messages with another user, newest first.

        Args:
            other_user_id: The other participant.
            before_id: Return messages created before this message id.
            since_id: Return the most recent messages since this id.

        Returns:
            The normalized messages; empty when GroupMe answers HTTP 304.

        Raises:
            ValueError: If both pagination cursors are supplied.
        """
        ensure_single_cursor(before_id, since_id)
        params: dict[str, Any] = {"other_user_id": other_user_id}
        if before_id is not None:
            params["before_id"] = before_id
        if since_id is not None:
            params["since_id"] = since_id
        payload = await self._request("GET", "/direct_messages", params=params)
        if payload is None:
            return []
        raw = _expect_list(
            _expect_dict(payload, "direct_messages").get("direct_messages"), "direct_messages"
        )
        return _parse_list(parse_message, raw, "message")

    async def create_group_message(
        self,
        group_id: GroupId,
        text: str,
        *,
        source_guid: str | None = None,
        attachments: Sequence[dict[str, Any]] | None = None,
    ) -> Message:
        """Send a message to a group.

        Args:
            group_id: The destination group.
            text: The message text.
            source_guid: Client-side dedupe id; generated when omitted.
            attachments: Raw attachment objects to include.

        Returns:
            The created message, normalized.
        """
        body = {"message": _message_body(text, source_guid, attachments)}
        payload = await self._request("POST", f"/groups/{group_id}/messages", json_body=body)
        raw = _expect_dict(_expect_dict(payload, "message").get("message"), "message")
        return _parse(parse_message, raw, "message")

    async def create_direct_message(
        self,
        recipient_id: UserId,
        text: str,
        *,
        source_guid: str | None = None,
        attachments: Sequence[dict[str, Any]] | None = None,
    ) -> Message:
        """Send a direct message to another user.

        Args:
            recipient_id: The recipient.
            text: The message text.
            source_guid: Client-side dedupe id; generated when omitted.
            attachments: Raw attachment objects to include.

        Returns:
            The created message, normalized.
        """
        message = _message_body(text, source_guid, attachments)
        message["recipient_id"] = recipient_id
        payload = await self._request(
            "POST", "/direct_messages", json_body={"direct_message": message}
        )
        raw = _expect_dict(
            _expect_dict(payload, "direct_message").get("direct_message"), "direct_message"
        )
        return _parse(parse_message, raw, "message")

    async def like(self, conversation_id: ConversationId, message_id: MessageId) -> None:
        """Like a message.

        Args:
            conversation_id: The group id or direct-chat conversation id
                (available on ``Message.conversation_id`` and
                ``DirectChat.conversation_id``).
            message_id: The message to like.
        """
        await self._request("POST", f"/messages/{conversation_id}/{message_id}/like")

    async def unlike(self, conversation_id: ConversationId, message_id: MessageId) -> None:
        """Remove a like from a message.

        Args:
            conversation_id: The group id or direct-chat conversation id
                (available on ``Message.conversation_id`` and
                ``DirectChat.conversation_id``).
            message_id: The message to unlike.
        """
        await self._request("POST", f"/messages/{conversation_id}/{message_id}/unlike")

    async def leaderboard(
        self, group_id: GroupId, period: LeaderboardPeriod = "day"
    ) -> list[Message]:
        """Fetch a group's most-liked messages for a period.

        Args:
            group_id: The group to inspect.
            period: The time window.

        Returns:
            The normalized messages, most liked first.
        """
        payload = await self._request("GET", f"/groups/{group_id}/likes", params={"period": period})
        raw = _expect_list(_expect_dict(payload, "likes").get("messages"), "likes")
        return _parse_list(parse_message, raw, "message")


def _check_limit(limit: int) -> None:
    if not 1 <= limit <= MAX_MESSAGE_LIMIT:
        msg = f"limit must be between 1 and {MAX_MESSAGE_LIMIT}, got {limit}"
        raise ValueError(msg)


def _message_body(
    text: str,
    source_guid: str | None,
    attachments: Sequence[dict[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "source_guid": source_guid if source_guid is not None else str(uuid.uuid4()),
        "text": text,
        "attachments": list(attachments) if attachments is not None else [],
    }
