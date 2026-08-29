from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from groupme_mcp_server.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    """Keep the ``get_settings`` LRU cache from leaking between tests."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure host ``GROUPME_MCP_*`` variables never influence a test."""
    monkeypatch.delenv("GROUPME_MCP_LOG_LEVEL", raising=False)
