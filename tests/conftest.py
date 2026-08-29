from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


def _import_package_without_local_env() -> None:
    """Import the package before the repo's real ``.env`` can leak in.

    Importing ``groupme_mcp_server`` runs ``server.py``, which calls
    ``configure_observability(get_settings())`` at module scope — and
    ``Settings`` reads ``.env`` from the current directory. Scrubbing the
    environment and importing from an empty temp directory guarantees the
    repository's real secrets never enter the test process, even at
    collection time.
    """
    # Case-insensitive: pydantic-settings matches env vars case-insensitively
    # by default, so a lowercase groupme_access_token would otherwise slip by.
    for key in [k for k in os.environ if k.upper().startswith(("GROUPME_", "OTEL_"))]:
        del os.environ[key]
    original = Path.cwd()
    os.chdir(tempfile.mkdtemp(prefix="groupme-mcp-tests-"))
    try:
        import groupme_mcp_server  # noqa: F401, PLC0415 - must run after the scrub above
    finally:
        os.chdir(original)


_import_package_without_local_env()

import httpx2  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from groupme_mcp_server.client import GroupMeClient  # noqa: E402
from groupme_mcp_server.settings import Settings, get_settings  # noqa: E402
from groupme_mcp_server.tools import common  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable

    Handler = Callable[[httpx2.Request], httpx2.Response]
    TransportInstaller = Callable[..., list[httpx2.Request]]


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Keep the ``get_settings`` LRU cache from leaking between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def groupme_transport(monkeypatch: pytest.MonkeyPatch) -> TransportInstaller:
    """Route tool HTTP calls through a mock transport.

    Returns an installer: call it with a ``httpx2`` handler (and optionally a
    ``token`` / ``max_retries`` override) and it replaces the tool layer's
    client factory, returning the list that records every outgoing request.
    """

    def install(
        handler: Handler,
        *,
        token: str | None = "test-token",  # noqa: S107 - a made-up test value
        max_retries: int = 0,
    ) -> list[httpx2.Request]:
        requests: list[httpx2.Request] = []

        def recording(request: httpx2.Request) -> httpx2.Response:
            requests.append(request)
            return handler(request)

        def factory() -> GroupMeClient:
            return GroupMeClient(
                Settings(
                    access_token=SecretStr(token) if token is not None else None,
                    api_base_url="https://api.groupme.test/v3",
                ),
                transport=httpx2.MockTransport(recording),
                max_retries=max_retries,
            )

        monkeypatch.setattr(common, "create_client", factory)
        return requests

    return install


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep host ``GROUPME_*`` / ``OTEL_*`` variables and ``.env`` out of tests.

    Deletes every matching environment variable and moves the working
    directory to an empty temp dir so the repository's real ``.env`` (which
    holds live secrets) is never loaded.
    """
    for key in list(os.environ):
        if key.upper().startswith(("GROUPME_", "OTEL_")):
            monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)
