from __future__ import annotations

import pytest
from pydantic import ValidationError

from groupme_mcp_server.settings import Settings, get_settings


def test_defaults(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # ty: ignore[invalid-argument-type]
    assert Settings().log_level == "INFO"


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUPME_MCP_LOG_LEVEL", "DEBUG")
    assert Settings().log_level == "DEBUG"


def test_rejects_unknown_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUPME_MCP_LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        Settings()


def test_is_frozen() -> None:
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
