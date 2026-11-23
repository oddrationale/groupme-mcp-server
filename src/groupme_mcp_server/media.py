"""Download an image from an arbitrary URL, safely, for re-hosting on GroupMe.

GroupMe messages can only attach images hosted on GroupMe's own image service,
so ``send_message`` re-hosts any other image URL: it downloads the image here
and uploads it with ``GroupMeClient.upload_image``.

Because the URL comes from the caller and the server may run hosted, the
download is hardened against server-side request forgery and abuse:

- Only ``http`` and ``https`` URLs without embedded credentials are accepted.
- The host must resolve exclusively to public addresses; loopback, private,
  link-local, multicast, reserved, and cloud-metadata ranges are refused.
- The connection goes to the exact address that was checked (TLS is still
  verified against the real hostname), so DNS cannot swap in an internal
  address between the check and the request.
- Redirects are followed manually, at most
  [`MAX_REDIRECTS`][groupme_mcp_server.media.MAX_REDIRECTS], and every hop is
  checked again.
- The whole download must finish within
  [`DOWNLOAD_TIMEOUT_SECONDS`][groupme_mcp_server.media.DOWNLOAD_TIMEOUT_SECONDS]
  and may not exceed [`MAX_IMAGE_BYTES`][groupme_mcp_server.media.MAX_IMAGE_BYTES],
  checked against the declared length and the bytes actually received.
- The response must declare a raster ``image/*`` content type (SVG is refused).
- The GroupMe access token is never sent to the remote host, and the
  environment's proxy settings are ignored.

Failures raise [`ImageFetchError`][groupme_mcp_server.media.ImageFetchError], a
``ValueError``: the tool layer reports them as invalid arguments.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

import httpx2

from groupme_mcp_server.observability import instrumented_async_transport

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType
    from typing import Self
    from urllib.parse import SplitResult

    Resolver = Callable[[str, int], Awaitable[list[str]]]

MAX_IMAGE_BYTES = 15 * 1024 * 1024
"""Largest image ``send_message`` will download and re-host."""

DOWNLOAD_TIMEOUT_SECONDS = 10.0
"""Total time allowed for one image download, redirects included."""

MAX_REDIRECTS = 3
"""Most redirects followed for one image download."""

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_DEFAULT_PORTS = {"http": 80, "https": 443}


class ImageFetchError(ValueError):
    """An image URL could not, or must not, be downloaded."""


@dataclass(frozen=True)
class DownloadedImage:
    """A downloaded image, ready to upload to GroupMe's image service."""

    data: bytes
    content_type: str


def parse_image_url(url: str) -> SplitResult:
    """Validate the shape of an image URL before any network access.

    Args:
        url: The caller-supplied image URL.

    Returns:
        The parsed URL.

    Raises:
        ImageFetchError: If the URL is not an ``http``/``https`` URL with a
            host, carries credentials, or has an invalid port.
    """
    parts = urlsplit(url.strip())
    if parts.scheme not in _DEFAULT_PORTS or not parts.hostname:
        msg = "image_url must be an http or https URL"
        raise ImageFetchError(msg)
    if parts.username is not None or parts.password is not None:
        msg = "image_url must not contain credentials"
        raise ImageFetchError(msg)
    try:
        _ = parts.port
    except ValueError as exc:
        msg = "image_url has an invalid port"
        raise ImageFetchError(msg) from exc
    return parts


def is_public_address(address: str) -> bool:
    """Whether an IP address is safe for the server to connect to.

    Args:
        address: An IPv4 or IPv6 address literal.

    Returns:
        ``True`` only for globally routable unicast addresses. IPv4-mapped
        IPv6 addresses are judged by their IPv4 address.
    """
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def image_content_type(header: str | None) -> str:
    """Normalize and check a response's ``Content-Type`` for an image.

    Args:
        header: The ``Content-Type`` header value, if any.

    Returns:
        The bare media type, lowercased (parameters dropped).

    Raises:
        ImageFetchError: Unless it is a raster ``image/*`` type (SVG, which
            can carry script, is refused).
    """
    media_type = (header or "").split(";", 1)[0].strip().lower()
    if not media_type.startswith("image/") or media_type.startswith("image/svg"):
        shown = media_type or "none"
        msg = f"image_url did not return an image (content type: {shown})"
        raise ImageFetchError(msg)
    return media_type


async def resolve_host(host: str, port: int) -> list[str]:
    """Resolve a hostname to its IP addresses.

    Args:
        host: Hostname or IP literal.
        port: Port to resolve for.

    Returns:
        The distinct addresses, sorted.
    """
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({str(info[4][0]) for info in infos})


class ImageFetcher:
    """Downloads caller-supplied image URLs under the module's safety rules.

    Use as an async context manager to close the connection pool.
    """

    def __init__(
        self,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        resolver: Resolver | None = None,
        max_bytes: int = MAX_IMAGE_BYTES,
        timeout: float = DOWNLOAD_TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        """Initialize the fetcher.

        Args:
            transport: Injectable transport for tests; when ``None`` the
                default OTel-instrumented transport is used.
            resolver: Injectable DNS resolver for tests; defaults to
                [`resolve_host`][groupme_mcp_server.media.resolve_host].
            max_bytes: Largest image accepted.
            timeout: Total seconds allowed per download.
            max_redirects: Most redirects followed per download.
        """
        self._resolve = resolver if resolver is not None else resolve_host
        self._max_bytes = max_bytes
        self._timeout = timeout
        self._max_redirects = max_redirects
        self._client = httpx2.AsyncClient(
            transport=transport if transport is not None else instrumented_async_transport(),
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        )

    async def __aenter__(self) -> Self:
        """Enter the async context.

        Returns:
            This fetcher.
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
        await self._client.aclose()

    async def fetch(self, url: str) -> DownloadedImage:
        """Download one image.

        Args:
            url: The caller-supplied image URL.

        Returns:
            The image bytes and their content type.

        Raises:
            ImageFetchError: If the URL is refused, the download fails or is
                too slow, the response is not an image, or it is too large.
        """
        try:
            async with asyncio.timeout(self._timeout):
                return await self._follow(url)
        except TimeoutError as exc:
            msg = f"image download took longer than {self._timeout:g} seconds"
            raise ImageFetchError(msg) from exc
        except httpx2.HTTPError as exc:
            # Only the exception type: transport errors can echo request details.
            msg = f"image download failed before a response was received ({type(exc).__name__})"
            raise ImageFetchError(msg) from exc

    async def _follow(self, url: str) -> DownloadedImage:
        current = url
        for _ in range(self._max_redirects + 1):
            request = await self._pinned_request(parse_image_url(current))
            response = await self._client.send(request, stream=True)
            try:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        msg = f"image_url redirected (HTTP {response.status_code}) without a target"
                        raise ImageFetchError(msg)
                    current = urljoin(current, location)
                    continue
                return await self._read_image(response)
            finally:
                await response.aclose()
        msg = f"image_url redirected more than {self._max_redirects} times"
        raise ImageFetchError(msg)

    async def _pinned_request(self, parts: SplitResult) -> httpx2.Request:
        host = str(parts.hostname)  # parse_image_url guarantees a hostname
        port = parts.port or _DEFAULT_PORTS[parts.scheme]
        try:
            addresses = await self._resolve(host, port)
        except OSError as exc:
            msg = f"image_url host {host!r} could not be resolved"
            raise ImageFetchError(msg) from exc
        if not addresses or not all(is_public_address(address) for address in addresses):
            msg = f"image_url host {host!r} is not a public internet address"
            raise ImageFetchError(msg)
        url = httpx2.URL(parts.geturl()).copy_with(host=addresses[0])
        headers = {"Host": parts.netloc.rsplit("@", 1)[-1], "Accept": "image/*"}
        extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
        return self._client.build_request("GET", url, headers=headers, extensions=extensions)

    async def _read_image(self, response: httpx2.Response) -> DownloadedImage:
        if not response.is_success:
            msg = f"image download failed: HTTP {response.status_code}"
            raise ImageFetchError(msg)
        content_type = image_content_type(response.headers.get("content-type"))
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > self._max_bytes:
            raise ImageFetchError(self._too_large())
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > self._max_bytes:
                raise ImageFetchError(self._too_large())
        if not data:
            msg = "image_url returned an empty body"
            raise ImageFetchError(msg)
        return DownloadedImage(data=bytes(data), content_type=content_type)

    def _too_large(self) -> str:
        return f"image is larger than the {self._max_bytes // (1024 * 1024)} MB limit"
