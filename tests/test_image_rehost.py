"""send_message re-hosts images from any URL on GroupMe's image service."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pydantic import SecretStr

from groupme_mcp_server.client import GroupMeClient
from groupme_mcp_server.errors import GroupMeApiError, GroupMeAuthError
from groupme_mcp_server.media import ImageFetcher
from groupme_mcp_server.models import groupme_image_attachment, is_groupme_image_url
from groupme_mcp_server.server import mcp
from groupme_mcp_server.settings import Settings
from groupme_mcp_server.tools import common

if TYPE_CHECKING:
    from collections.abc import Callable

    Handler = Callable[[httpx2.Request], httpx2.Response]
    TransportInstaller = Callable[..., list[httpx2.Request]]

NOW = int(time.time())
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
REMOTE_URL = "https://img.example/cat.png"
HOSTED_URL = "https://i.groupme.com/1x1.png.e5733df14d2a4dd1b2955f4058a1b06c"
GROUP_TARGET = {"kind": "group", "group_id": "42"}
TOPIC_TARGET = {"kind": "topic", "parent_group_id": "42", "topic_id": "43"}


def upload_response(url: object = HOSTED_URL, status: int = 200) -> httpx2.Response:
    return httpx2.Response(status, json={"payload": {"url": url, "picture_url": url}})


def created(request: httpx2.Request, group_id: str) -> httpx2.Response:
    sent = json.loads(request.content)["message"]
    message = {
        "id": "m100",
        "group_id": group_id,
        "sender_id": "me",
        "name": "Me",
        "text": sent["text"],
        "created_at": NOW,
        "favorited_by": [],
        "attachments": sent["attachments"],
    }
    return httpx2.Response(201, json={"response": {"message": message}, "meta": {"code": 201}})


@pytest.fixture
def remote_images(monkeypatch: pytest.MonkeyPatch) -> list[httpx2.Request]:
    """Serve REMOTE_URL from a fake public host; record every download request."""
    downloads: list[httpx2.Request] = []

    async def resolve(host: str, port: int) -> list[str]:
        assert (host, port) == ("img.example", 443)
        return ["93.184.216.34"]

    def handler(request: httpx2.Request) -> httpx2.Response:
        downloads.append(request)
        return httpx2.Response(200, content=PNG, headers={"Content-Type": "image/png"})

    monkeypatch.setattr(
        common,
        "create_image_fetcher",
        lambda: ImageFetcher(transport=httpx2.MockTransport(handler), resolver=resolve),
    )
    return downloads


def groupme_handler(*, upload: httpx2.Response | None = None, topic_ok: bool = True) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "image.groupme.com":
            assert request.method == "POST"
            assert request.url.path == "/pictures"
            assert request.headers["Content-Type"] == "image/png"
            assert request.headers["X-Access-Token"] == "test-token"
            assert request.content == PNG
            return upload if upload is not None else upload_response()
        if request.url.path == "/v3/groups/42/subgroups/43":
            if not topic_ok:
                return httpx2.Response(404, json={"response": None, "meta": {"code": 404}})
            topic = {"id": 43, "parent_id": 42, "topic": "T", "type": "private"}
            return httpx2.Response(200, json={"response": topic, "meta": {"code": 200}})
        assert request.method == "POST"
        group_id = request.url.path.split("/")[3]
        return created(request, group_id)

    return handler


# --- client.upload_image -----------------------------------------------------------------------


def client_for(handler: Handler) -> GroupMeClient:
    return GroupMeClient(
        Settings(access_token=SecretStr("tok"), image_api_base_url="https://image.test/"),
        transport=httpx2.MockTransport(handler),
        max_retries=1,
        sleep=lambda _: _noop(),
    )


async def _noop() -> None:
    return None


async def test_upload_image_posts_bytes_and_returns_the_hosted_url() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return upload_response()

    async with client_for(handler) as client:
        assert await client.upload_image(PNG, "image/png") == HOSTED_URL
    (request,) = seen
    assert str(request.url) == "https://image.test/pictures"
    assert request.headers["X-Access-Token"] == "tok"
    assert request.content == PNG


async def test_upload_image_retries_rate_limits() -> None:
    responses = [httpx2.Response(429), upload_response()]
    async with client_for(lambda _: responses.pop(0)) as client:
        assert await client.upload_image(PNG, "image/png") == HOSTED_URL
    assert responses == []


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx2.Response(401), GroupMeAuthError),
        (httpx2.Response(500, json={"meta": {"errors": ["boom"]}}), GroupMeApiError),
        (httpx2.Response(200, content=b"not json"), GroupMeApiError),
        (httpx2.Response(200, json={"payload": {}}), GroupMeApiError),
        (httpx2.Response(200, json=[1]), GroupMeApiError),
        (upload_response("https://evil.example/x.png"), GroupMeApiError),
        (upload_response(123), GroupMeApiError),
    ],
)
async def test_upload_image_failures(response: httpx2.Response, error: type[Exception]) -> None:
    async with client_for(lambda _: response) as client:
        with pytest.raises(error):
            await client.upload_image(PNG, "image/png")


@pytest.mark.parametrize(
    ("url", "hosted"),
    [
        ("https://i.groupme.com/a.png", True),
        ("https://image.groupme.com/a.png", True),
        (" https://i.groupme.com/a.png ", True),
        ("http://i.groupme.com/a.png", False),
        ("https://i.groupme.com.evil.example/a.png", False),
        ("https://example.com/a.png", False),
    ],
)
def test_is_groupme_image_url(url: str, *, hosted: bool) -> None:
    assert is_groupme_image_url(url) is hosted


# --- send_message ------------------------------------------------------------------------------


async def test_send_rehosts_a_remote_image(
    groupme_transport: TransportInstaller, remote_images: list[httpx2.Request]
) -> None:
    requests = groupme_transport(groupme_handler())
    async with Client(mcp) as client:
        result = await client.call_tool(
            "send_message", {"conversation": GROUP_TARGET, "text": "look", "image_url": REMOTE_URL}
        )
    assert len(remote_images) == 1
    assert "X-Access-Token" not in remote_images[0].headers
    assert [(r.url.host, r.url.path) for r in requests] == [
        ("image.groupme.com", "/pictures"),
        ("api.groupme.test", "/v3/groups/42/messages"),
    ]
    assert json.loads(requests[1].content)["message"]["attachments"] == [
        {"type": "image", "url": HOSTED_URL}
    ]
    assert result.structured_content is not None
    assert result.structured_content["attachments"] == [HOSTED_URL]


async def test_image_only_message_to_a_topic_checks_the_topic_before_downloading(
    groupme_transport: TransportInstaller, remote_images: list[httpx2.Request]
) -> None:
    requests = groupme_transport(groupme_handler())
    async with Client(mcp) as client:
        await client.call_tool(
            "send_message",
            {
                "conversation": TOPIC_TARGET,
                "text": "",
                "image_url": REMOTE_URL,
                "reply_to_message_id": "m1",
            },
        )
    assert [r.url.path for r in requests] == [
        "/v3/groups/42/subgroups/43",
        "/pictures",
        "/v3/groups/43/messages",
    ]
    assert json.loads(requests[2].content)["message"]["attachments"] == [
        {"type": "reply", "reply_id": "m1", "base_reply_id": "m1"},
        {"type": "image", "url": HOSTED_URL},
    ]
    assert len(remote_images) == 1


async def test_groupme_hosted_images_are_never_downloaded(
    groupme_transport: TransportInstaller, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_fetcher() -> ImageFetcher:
        pytest.fail("a GroupMe-hosted image must be attached as-is")

    monkeypatch.setattr(common, "create_image_fetcher", no_fetcher)
    requests = groupme_transport(groupme_handler())
    async with Client(mcp) as client:
        await client.call_tool(
            "send_message", {"conversation": GROUP_TARGET, "text": "x", "image_url": HOSTED_URL}
        )
    assert [r.url.path for r in requests] == ["/v3/groups/42/messages"]


@pytest.mark.parametrize(
    ("args", "handler_kwargs"),
    [
        ({"conversation": GROUP_TARGET, "text": "x" * 1001}, {}),  # invalid text
        ({"conversation": TOPIC_TARGET, "text": "hi"}, {"topic_ok": False}),  # unknown topic
    ],
)
async def test_nothing_is_downloaded_when_the_send_would_fail_anyway(
    groupme_transport: TransportInstaller,
    remote_images: list[httpx2.Request],
    args: dict[str, Any],
    handler_kwargs: dict[str, Any],
) -> None:
    groupme_transport(groupme_handler(**handler_kwargs))
    async with Client(mcp) as client:
        with pytest.raises(ToolError):
            await client.call_tool("send_message", {**args, "image_url": REMOTE_URL})
    assert remote_images == []


async def test_a_private_image_url_is_refused_and_nothing_is_sent(
    groupme_transport: TransportInstaller,
) -> None:
    requests = groupme_transport(groupme_handler())
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="not a public internet address") as error:
            await client.call_tool(
                "send_message",
                {"conversation": GROUP_TARGET, "text": "x", "image_url": "http://127.0.0.1/a.png"},
            )
    assert "Invalid arguments" in str(error.value)
    assert requests == []


async def test_a_failed_upload_sends_no_message(
    groupme_transport: TransportInstaller, remote_images: list[httpx2.Request]
) -> None:
    requests = groupme_transport(groupme_handler(upload=httpx2.Response(500)))
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="HTTP 500"):
            await client.call_tool(
                "send_message", {"conversation": GROUP_TARGET, "text": "x", "image_url": REMOTE_URL}
            )
    assert [r.url.path for r in requests] == ["/pictures"]
    assert len(remote_images) == 1


def test_create_image_fetcher_builds_a_fresh_fetcher() -> None:
    assert isinstance(common.create_image_fetcher(), ImageFetcher)


def test_attachments_refuse_urls_that_were_not_rehosted() -> None:
    with pytest.raises(ValueError, match="GroupMe image-service URL"):
        groupme_image_attachment("https://example.com/a.png")
    assert groupme_image_attachment(f" {HOSTED_URL} ") == {"type": "image", "url": HOSTED_URL}
