"""The hardened image downloader behind send_message's image re-hosting."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx2
import pytest

from groupme_mcp_server import media
from groupme_mcp_server.media import (
    ImageFetcher,
    ImageFetchError,
    image_content_type,
    is_public_address,
    parse_image_url,
    resolve_host,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from groupme_mcp_server.media import Resolver

    Handler = Callable[[httpx2.Request], httpx2.Response]

PUBLIC_IP = "93.184.216.34"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def resolver(table: dict[str, list[str]]) -> Resolver:
    async def resolve(host: str, port: int) -> list[str]:
        assert port in (80, 443, 8443)
        if host[0].isdigit():  # IP literals resolve to themselves, as with getaddrinfo
            return [host]
        if host not in table:
            msg = "no such host"
            raise OSError(msg)
        return table[host]

    return resolve


def image_response(
    body: bytes = PNG, content_type: str = "image/png", **headers: str
) -> httpx2.Response:
    return httpx2.Response(200, content=body, headers={"Content-Type": content_type, **headers})


def fetcher(
    handler: Handler,
    table: dict[str, list[str]] | None = None,
    *,
    max_bytes: int = media.MAX_IMAGE_BYTES,
    max_redirects: int = media.MAX_REDIRECTS,
) -> ImageFetcher:
    return ImageFetcher(
        transport=httpx2.MockTransport(handler),
        resolver=resolver(table if table is not None else {"img.example": [PUBLIC_IP]}),
        max_bytes=max_bytes,
        max_redirects=max_redirects,
    )


# --- pure checks ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["https://img.example/cat.png", "http://img.example:8080/a.jpg?x=1", " https://a.b/c "]
)
def test_parse_image_url_accepts_http_urls(url: str) -> None:
    assert parse_image_url(url).hostname


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("ftp://img.example/a.png", "http or https"),
        ("file:///etc/passwd", "http or https"),
        ("https:///no-host.png", "http or https"),
        ("not a url", "http or https"),
        ("https://user:pw@img.example/a.png", "credentials"),
        ("https://user@img.example/a.png", "credentials"),
        ("https://img.example:99999/a.png", "invalid port"),
    ],
)
def test_parse_image_url_rejects(url: str, message: str) -> None:
    with pytest.raises(ImageFetchError, match=message):
        parse_image_url(url)


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("93.184.216.34", True),
        ("2606:4700::1111", True),
        ("127.0.0.1", False),
        ("10.1.2.3", False),
        ("172.16.0.1", False),
        ("192.168.1.1", False),
        ("169.254.169.254", False),  # cloud metadata
        ("100.64.0.1", False),  # carrier-grade NAT
        ("0.0.0.0", False),  # noqa: S104 - an address under test, not a bind
        ("224.0.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fd00::1", False),
        ("::ffff:127.0.0.1", False),  # IPv4-mapped loopback
        ("::ffff:93.184.216.34", True),
        ("ff02::1", False),
    ],
)
def test_is_public_address(address: str, *, public: bool) -> None:
    assert is_public_address(address) is public


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("image/png", "image/png"),
        ("IMAGE/JPEG; charset=binary", "image/jpeg"),
        ("image/webp", "image/webp"),
    ],
)
def test_image_content_type_accepts_raster_images(header: str, expected: str) -> None:
    assert image_content_type(header) == expected


@pytest.mark.parametrize(
    "header", [None, "", "text/html", "application/octet-stream", "image/svg+xml"]
)
def test_image_content_type_rejects_everything_else(header: str | None) -> None:
    with pytest.raises(ImageFetchError, match="did not return an image"):
        image_content_type(header)


async def test_resolve_host_uses_the_system_resolver() -> None:
    assert "127.0.0.1" in await resolve_host("localhost", 80)


# --- downloads --------------------------------------------------------------------------------


async def test_fetch_connects_to_the_checked_address_with_the_real_hostname() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return image_response()

    async with fetcher(handler) as images:
        image = await images.fetch("https://img.example/cat.png?size=2")
    assert image.data == PNG
    assert image.content_type == "image/png"
    (request,) = seen
    assert request.url.host == PUBLIC_IP
    assert request.url.path == "/cat.png"
    assert request.url.params["size"] == "2"
    assert request.headers["Host"] == "img.example"
    assert request.headers["Accept"] == "image/*"
    assert "X-Access-Token" not in request.headers
    assert request.extensions["sni_hostname"] == "img.example"


async def test_plain_http_and_ipv6_addresses() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return image_response()

    async with fetcher(handler, {"img.example": ["2606:4700::1111"]}) as images:
        await images.fetch("http://img.example:8443/a.png")
    (request,) = seen
    assert request.url.host == "2606:4700::1111"
    assert request.url.port == 8443
    assert request.headers["Host"] == "img.example:8443"
    assert "sni_hostname" not in request.extensions


async def test_redirects_are_followed_and_rechecked() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.headers["Host"] == "img.example":
            return httpx2.Response(302, headers={"Location": "https://cdn.example/final.png"})
        assert request.url.host == "93.184.216.35"
        return image_response()

    table = {"img.example": [PUBLIC_IP], "cdn.example": ["93.184.216.35"]}
    async with fetcher(handler, table) as images:
        image = await images.fetch("https://img.example/start")
    assert image.data == PNG


async def test_relative_redirects_resolve_against_the_current_url() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/start":
            return httpx2.Response(301, headers={"Location": "/images/final.png"})
        assert request.url.path == "/images/final.png"
        return image_response()

    async with fetcher(handler) as images:
        assert (await images.fetch("https://img.example/start")).data == PNG


async def test_a_redirect_into_a_private_network_is_refused() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(307, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    async with fetcher(handler) as images:
        with pytest.raises(ImageFetchError, match="not a public internet address"):
            await images.fetch("https://img.example/start")
    assert len(requests) == 1


async def test_too_many_redirects() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(302, headers={"Location": f"{request.url.path}x"})

    async with fetcher(handler, max_redirects=2) as images:
        with pytest.raises(ImageFetchError, match="more than 2 times"):
            await images.fetch("https://img.example/a")


async def test_redirect_without_a_location() -> None:
    async with fetcher(lambda _: httpx2.Response(302)) as images:
        with pytest.raises(ImageFetchError, match="without a target"):
            await images.fetch("https://img.example/a")


@pytest.mark.parametrize(
    ("table", "message"),
    [
        ({}, "could not be resolved"),
        ({"img.example": []}, "not a public internet address"),
        ({"img.example": [PUBLIC_IP, "10.0.0.5"]}, "not a public internet address"),
    ],
)
async def test_hosts_must_resolve_only_to_public_addresses(
    table: dict[str, list[str]], message: str
) -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return image_response()

    async with fetcher(handler, table) as images:
        with pytest.raises(ImageFetchError, match=message):
            await images.fetch("https://img.example/a.png")
    assert requests == []


async def test_error_statuses_are_reported() -> None:
    async with fetcher(lambda _: httpx2.Response(404)) as images:
        with pytest.raises(ImageFetchError, match="HTTP 404"):
            await images.fetch("https://img.example/a.png")


async def test_non_images_are_refused() -> None:
    async with fetcher(lambda _: image_response(b"<html>", "text/html")) as images:
        with pytest.raises(ImageFetchError, match="text/html"):
            await images.fetch("https://img.example/a.png")


async def test_declared_size_over_the_limit_is_refused() -> None:
    async with fetcher(
        lambda _: image_response(PNG, **{"Content-Length": "999"}), max_bytes=100
    ) as images:
        with pytest.raises(ImageFetchError, match="larger than"):
            await images.fetch("https://img.example/a.png")


async def test_streamed_size_over_the_limit_is_refused() -> None:
    async def body() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"x" * 50

    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=body(), headers={"Content-Type": "image/png"})

    async with fetcher(handler, max_bytes=120) as images:
        with pytest.raises(ImageFetchError, match="larger than"):
            await images.fetch("https://img.example/a.png")


async def test_empty_bodies_are_refused() -> None:
    async with fetcher(lambda _: image_response(b"")) as images:
        with pytest.raises(ImageFetchError, match="empty body"):
            await images.fetch("https://img.example/a.png")


async def test_slow_downloads_time_out() -> None:
    async def slow(_: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(1)
        return image_response()  # pragma: no cover - the timeout fires first

    images = ImageFetcher(
        transport=httpx2.MockTransport(slow),
        resolver=resolver({"img.example": [PUBLIC_IP]}),
        timeout=0.05,
    )
    async with images:
        with pytest.raises(ImageFetchError, match=r"longer than 0\.05 seconds"):
            await images.fetch("https://img.example/a.png")


async def test_transport_errors_do_not_leak_details() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        msg = "secret internal detail"
        raise httpx2.ConnectError(msg, request=request)

    async with fetcher(handler) as images:
        with pytest.raises(ImageFetchError) as error:
            await images.fetch("https://img.example/a.png")
    assert "ConnectError" in str(error.value)
    assert "secret internal detail" not in str(error.value)


async def test_default_fetcher_refuses_localhost_without_connecting() -> None:
    async with ImageFetcher() as images:
        with pytest.raises(ImageFetchError, match="not a public internet address"):
            await images.fetch("http://localhost:1/a.png")


def test_limits_are_the_documented_defaults() -> None:
    assert media.MAX_IMAGE_BYTES == 15 * 1024 * 1024
    assert media.DOWNLOAD_TIMEOUT_SECONDS == 10.0
    assert media.MAX_REDIRECTS == 3
