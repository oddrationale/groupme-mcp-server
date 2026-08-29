from __future__ import annotations

import json
import uuid
from typing import Any

import httpx2
import pytest
from opentelemetry.instrumentation.httpx import AsyncOpenTelemetryTransportHttpx2
from pydantic import SecretStr

from groupme_mcp_server.client import GroupMeClient
from groupme_mcp_server.errors import (
    GroupMeApiError,
    GroupMeAuthError,
    GroupMeNotFoundError,
    GroupMeRateLimitError,
)
from groupme_mcp_server.models import (
    ConversationId,
    DirectChat,
    Group,
    GroupId,
    Me,
    MessageId,
    UserId,
)
from groupme_mcp_server.settings import Settings

TOKEN = "test-token-abc123"  # noqa: S105 - a made-up test value, not a real credential

RAW_MESSAGE = {
    "id": "m1",
    "sender_id": "22",
    "name": "Ada",
    "text": "hello",
    "created_at": 1700000000,
    "favorited_by": [],
    "attachments": [],
}

RAW_GROUP = {"id": "42", "name": "Book club", "members": []}


class SleepRecorder:
    """Fake asyncio.sleep that records requested delays."""

    def __init__(self) -> None:
        """Start with no recorded delays."""
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        """Record ``delay`` instead of sleeping."""
        self.delays.append(delay)


def envelope(data: object, code: int = 200) -> dict[str, Any]:
    return {"response": data, "meta": {"code": code}}


def make_settings(token: str | None = TOKEN) -> Settings:
    return Settings(
        access_token=SecretStr(token) if token is not None else None,
        api_base_url="https://api.groupme.test/v3",
    )


def make_client(
    transport: httpx2.MockTransport,
    *,
    token: str | None = TOKEN,
    max_retries: int = 3,
) -> tuple[GroupMeClient, SleepRecorder]:
    sleep = SleepRecorder()
    client = GroupMeClient(
        make_settings(token),
        transport=transport,
        sleep=sleep,
        rng=lambda: 1.0,
        max_retries=max_retries,
    )
    return client, sleep


def recording_transport(
    responses: list[httpx2.Response],
    requests: list[httpx2.Request],
) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return responses[min(len(requests), len(responses)) - 1]

    return httpx2.MockTransport(handler)


async def test_sends_token_header_and_unwraps_envelope() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport(
        [httpx2.Response(200, json=envelope({"id": "1", "name": "Ada"}))], requests
    )
    client, _ = make_client(transport)
    assert await client.get_me() == Me(id=UserId("1"), name="Ada")
    assert requests[0].headers["X-Access-Token"] == TOKEN
    assert requests[0].url.path == "/v3/users/me"


async def test_missing_token_fails_before_any_request() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport([httpx2.Response(200, json=envelope({}))], requests)
    client, _ = make_client(transport, token=None)
    with pytest.raises(GroupMeAuthError, match="GROUPME_ACCESS_TOKEN"):
        await client.get_me()
    assert requests == []


async def test_list_groups_params_and_models() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport([httpx2.Response(200, json=envelope([RAW_GROUP]))], requests)
    client, _ = make_client(transport)
    groups = await client.list_groups(page=2, per_page=5, omit_memberships=True)
    assert groups == [Group(id=GroupId("42"), name="Book club", member_count=0)]
    params = dict(requests[0].url.params)
    assert params == {"page": "2", "per_page": "5", "omit": "memberships"}


async def test_list_groups_defaults_do_not_send_omit() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport([httpx2.Response(200, json=envelope([]))], requests)
    client, _ = make_client(transport)
    assert await client.list_groups() == []
    assert "omit" not in dict(requests[0].url.params)


async def test_get_group() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport([httpx2.Response(200, json=envelope(RAW_GROUP))], requests)
    client, _ = make_client(transport)
    group = await client.get_group(GroupId("42"))
    assert group.id == GroupId("42")
    assert requests[0].url.path == "/v3/groups/42"


async def test_list_chats() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport(
        [httpx2.Response(200, json=envelope([{"other_user": {"id": "7", "name": "Grace"}}]))],
        requests,
    )
    client, _ = make_client(transport)
    chats = await client.list_chats(page=3, per_page=7)
    assert chats == [DirectChat(other_user_id=UserId("7"), other_user_name="Grace")]
    assert dict(requests[0].url.params) == {"page": "3", "per_page": "7"}


@pytest.mark.parametrize("cursor", ["before_id", "since_id", "after_id"])
async def test_list_group_messages_single_cursor(cursor: str) -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport(
        [httpx2.Response(200, json=envelope({"count": 1, "messages": [RAW_MESSAGE]}))],
        requests,
    )
    client, _ = make_client(transport)
    kwargs: dict[str, Any] = {cursor: MessageId("x")}
    messages = await client.list_group_messages(GroupId("42"), limit=50, **kwargs)
    assert [m.id for m in messages] == [MessageId("m1")]
    assert messages[0].sender_name == "Ada"
    assert dict(requests[0].url.params) == {"limit": "50", cursor: "x"}


async def test_list_group_messages_rejects_conflicting_cursors() -> None:
    client, _ = make_client(httpx2.MockTransport(lambda _: httpx2.Response(200)))
    with pytest.raises(ValueError, match="cursor"):
        await client.list_group_messages(
            GroupId("42"), before_id=MessageId("b"), since_id=MessageId("s")
        )


async def test_list_group_messages_defaults_and_304() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport([httpx2.Response(304)], requests)
    client, _ = make_client(transport)
    assert await client.list_group_messages(GroupId("42")) == []
    assert dict(requests[0].url.params) == {"limit": "20"}


@pytest.mark.parametrize("limit", [0, 101])
async def test_list_group_messages_rejects_bad_limit(limit: int) -> None:
    client, _ = make_client(httpx2.MockTransport(lambda _: httpx2.Response(200)))
    with pytest.raises(ValueError, match="limit"):
        await client.list_group_messages(GroupId("42"), limit=limit)


@pytest.mark.parametrize("cursor", ["before_id", "since_id"])
async def test_list_direct_messages_single_cursor(cursor: str) -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport(
        [httpx2.Response(200, json=envelope({"count": 1, "direct_messages": [RAW_MESSAGE]}))],
        requests,
    )
    client, _ = make_client(transport)
    kwargs: dict[str, Any] = {cursor: MessageId("x")}
    messages = await client.list_direct_messages(UserId("7"), **kwargs)
    assert [m.id for m in messages] == [MessageId("m1")]
    assert dict(requests[0].url.params) == {"other_user_id": "7", cursor: "x"}


async def test_list_direct_messages_rejects_conflicting_cursors() -> None:
    client, _ = make_client(httpx2.MockTransport(lambda _: httpx2.Response(200)))
    with pytest.raises(ValueError, match="cursor"):
        await client.list_direct_messages(
            UserId("7"), before_id=MessageId("b"), since_id=MessageId("s")
        )


async def test_list_direct_messages_304() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport([httpx2.Response(304)], requests)
    client, _ = make_client(transport)
    assert await client.list_direct_messages(UserId("7")) == []
    assert dict(requests[0].url.params) == {"other_user_id": "7"}


async def test_create_group_message_generates_source_guid() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport(
        [httpx2.Response(201, json=envelope({"message": RAW_MESSAGE}))], requests
    )
    client, _ = make_client(transport)
    created = await client.create_group_message(GroupId("42"), "hi")
    assert created.id == MessageId("m1")
    assert requests[0].url.path == "/v3/groups/42/messages"
    body = json.loads(requests[0].content)
    assert body["message"]["text"] == "hi"
    assert body["message"]["attachments"] == []
    uuid.UUID(body["message"]["source_guid"])  # generated and well-formed


async def test_create_group_message_passes_through_guid_and_attachments() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport(
        [httpx2.Response(201, json=envelope({"message": RAW_MESSAGE}))], requests
    )
    client, _ = make_client(transport)
    attachment = {"type": "image", "url": "https://i.groupme.com/x.jpg"}
    await client.create_group_message(
        GroupId("42"), "hi", source_guid="guid-1", attachments=[attachment]
    )
    body = json.loads(requests[0].content)
    assert body["message"]["source_guid"] == "guid-1"
    assert body["message"]["attachments"] == [attachment]


async def test_create_direct_message() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport(
        [httpx2.Response(201, json=envelope({"direct_message": RAW_MESSAGE}))], requests
    )
    client, _ = make_client(transport)
    created = await client.create_direct_message(UserId("7"), "yo", source_guid="guid-2")
    assert created.id == MessageId("m1")
    body = json.loads(requests[0].content)
    assert body["direct_message"]["recipient_id"] == "7"
    assert body["direct_message"]["source_guid"] == "guid-2"


async def test_like_and_unlike() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport([httpx2.Response(200, json={"meta": {"code": 200}})], requests)
    client, _ = make_client(transport)
    assert await client.like(ConversationId("42"), MessageId("m1")) is None
    assert await client.unlike(ConversationId("42"), MessageId("m1")) is None
    assert requests[0].url.path == "/v3/messages/42/m1/like"
    assert requests[1].url.path == "/v3/messages/42/m1/unlike"


async def test_leaderboard() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport(
        [httpx2.Response(200, json=envelope({"messages": [RAW_MESSAGE]}))], requests
    )
    client, _ = make_client(transport)
    messages = await client.leaderboard(GroupId("42"), period="week")
    assert [m.id for m in messages] == [MessageId("m1")]
    assert requests[0].url.path == "/v3/groups/42/likes"
    assert dict(requests[0].url.params) == {"period": "week"}


async def test_rate_limit_retries_then_succeeds() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport(
        [
            httpx2.Response(420, json={"meta": {"code": 420, "errors": ["slow down"]}}),
            httpx2.Response(200, json=envelope({"id": "1", "name": "Ada"})),
        ],
        requests,
    )
    client, sleep = make_client(transport)
    assert (await client.get_me()).id == UserId("1")
    assert len(requests) == 2
    assert sleep.delays == [0.5]  # attempt 0, jitter pinned to 1.0


async def test_rate_limit_gives_up_after_retries() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport(
        [httpx2.Response(429, json={"meta": {"code": 429, "errors": []}})], requests
    )
    client, sleep = make_client(transport, max_retries=2)
    with pytest.raises(GroupMeRateLimitError):
        await client.get_me()
    assert len(requests) == 3  # initial try + 2 retries
    assert sleep.delays == [0.5, 1.0]


async def test_401_maps_to_auth_error() -> None:
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(401, json={"meta": {"code": 401, "errors": []}})
    )
    client, _ = make_client(transport)
    with pytest.raises(GroupMeAuthError, match="GROUPME_ACCESS_TOKEN"):
        await client.get_me()


async def test_404_maps_to_not_found() -> None:
    transport = httpx2.MockTransport(lambda _: httpx2.Response(404))
    client, _ = make_client(transport)
    with pytest.raises(GroupMeNotFoundError):
        await client.get_group(GroupId("nope"))


async def test_other_error_carries_meta_errors() -> None:
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(500, json={"meta": {"code": 500, "errors": ["kaboom"]}})
    )
    client, _ = make_client(transport)
    with pytest.raises(GroupMeApiError) as excinfo:
        await client.get_me()
    assert excinfo.value.status == 500
    assert excinfo.value.messages == ("kaboom",)


async def test_non_json_error_body_is_tolerated() -> None:
    transport = httpx2.MockTransport(lambda _: httpx2.Response(502, text="Bad Gateway"))
    client, _ = make_client(transport)
    with pytest.raises(GroupMeApiError) as excinfo:
        await client.get_me()
    assert excinfo.value.messages == ()


async def test_malformed_success_payload_shape() -> None:
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=envelope("not-a-dict")))
    client, _ = make_client(transport)
    with pytest.raises(GroupMeApiError, match="unexpected payload shape"):
        await client.get_me()


async def test_list_payload_with_non_dict_items_is_rejected() -> None:
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=envelope([1, 2])))
    client, _ = make_client(transport)
    with pytest.raises(GroupMeApiError, match="unexpected payload shape"):
        await client.list_groups()


async def test_non_list_where_list_expected_is_rejected() -> None:
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json=envelope({"count": 0})))
    client, _ = make_client(transport)
    with pytest.raises(GroupMeApiError, match="unexpected payload shape"):
        await client.list_group_messages(GroupId("42"))


async def test_transport_failures_map_to_api_error() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        msg = "connection refused"
        raise httpx2.ConnectError(msg, request=request)

    client, _ = make_client(httpx2.MockTransport(handler))
    with pytest.raises(GroupMeApiError, match="before a response was received"):
        await client.get_me()


async def test_control_character_token_fails_before_any_request() -> None:
    requests: list[httpx2.Request] = []
    transport = recording_transport([httpx2.Response(200, json=envelope({}))], requests)
    client, _ = make_client(transport, token="bad\r\ntoken")  # noqa: S106 - made-up value
    with pytest.raises(GroupMeAuthError, match="control characters"):
        await client.get_me()
    assert requests == []


@pytest.mark.parametrize(
    "raw_message",
    [
        {"created_at": 0},  # no id -> KeyError
        {"id": "m1", "created_at": 0, "attachments": [None]},  # AttributeError
        {"id": "m1", "created_at": 10**20},  # timestamp overflow -> OverflowError/OSError
    ],
)
async def test_malformed_message_variants_map_to_api_error(raw_message: dict[str, Any]) -> None:
    transport = httpx2.MockTransport(
        lambda _: httpx2.Response(200, json=envelope({"count": 1, "messages": [raw_message]}))
    )
    client, _ = make_client(transport)
    with pytest.raises(GroupMeApiError, match="malformed message object"):
        await client.list_group_messages(GroupId("42"))


async def test_token_never_appears_in_repr() -> None:
    client, _ = make_client(httpx2.MockTransport(lambda _: httpx2.Response(200)))
    assert TOKEN not in repr(client)
    assert TOKEN not in repr(vars(client))


async def test_context_manager_closes_pool() -> None:
    async with GroupMeClient(
        make_settings(), transport=httpx2.MockTransport(lambda _: httpx2.Response(200))
    ) as client:
        assert isinstance(client, GroupMeClient)
    assert client._client.is_closed


async def test_default_construction_uses_instrumented_transport() -> None:
    client = GroupMeClient(make_settings())
    try:
        transport = client._client._transport
        assert isinstance(transport, AsyncOpenTelemetryTransportHttpx2)
    finally:
        await client.aclose()
