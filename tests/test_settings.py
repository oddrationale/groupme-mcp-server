from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from groupme_mcp_server.errors import GroupMeAuthError
from groupme_mcp_server.settings import Settings, get_settings


def test_defaults() -> None:
    settings = Settings()
    assert settings.access_token is None
    assert settings.log_level == "INFO"
    assert settings.api_base_url == "https://api.groupme.com/v3"
    assert settings.image_api_base_url == "https://image.groupme.com"


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUPME_ACCESS_TOKEN", "sekrit-token")
    monkeypatch.setenv("GROUPME_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("GROUPME_API_BASE_URL", "https://api.example.test/v3")
    monkeypatch.setenv("GROUPME_IMAGE_API_BASE_URL", "https://image.example.test")
    settings = Settings()
    assert settings.access_token is not None
    assert settings.access_token.get_secret_value() == "sekrit-token"
    assert settings.log_level == "DEBUG"
    assert settings.api_base_url == "https://api.example.test/v3"
    assert settings.image_api_base_url == "https://image.example.test"


def test_reads_lowercase_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # pydantic-settings matches env vars case-insensitively by default; the
    # conftest scrub must therefore be case-insensitive too.
    monkeypatch.setenv("groupme_log_level", "DEBUG")
    assert Settings().log_level == "DEBUG"


def test_reads_dotenv_file() -> None:
    # _isolate_env already chdir'd into an empty tmp dir; drop a .env there.
    Path(".env").write_text("GROUPME_LOG_LEVEL=WARNING\n", encoding="utf-8")
    assert Settings().log_level == "WARNING"


def test_token_is_masked_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUPME_ACCESS_TOKEN", "sekrit-token")
    settings = Settings()
    assert "sekrit-token" not in repr(settings)
    assert "sekrit-token" not in str(settings)


def test_require_access_token_returns_token() -> None:
    settings = Settings(access_token=SecretStr("sekrit-token"))
    assert settings.require_access_token().get_secret_value() == "sekrit-token"


def test_require_access_token_raises_actionable_error() -> None:
    with pytest.raises(GroupMeAuthError, match="Set GROUPME_ACCESS_TOKEN") as excinfo:
        Settings().require_access_token()
    assert "https://dev.groupme.com" in str(excinfo.value)


def test_require_access_token_rejects_control_characters() -> None:
    settings = Settings(access_token=SecretStr("bad\r\ntoken"))
    with pytest.raises(GroupMeAuthError, match="control characters") as excinfo:
        settings.require_access_token()
    assert "bad" not in str(excinfo.value)  # the token value itself never leaks


@pytest.mark.parametrize(
    "token",
    [
        " padded-token",  # leading whitespace: httpx2's header error echoes it
        "padded-token ",  # trailing whitespace
        "\ttabbed-token",
        "inner space",
        "delete\x7fchar",
        "töken",  # non-ASCII
        "token\U0001f512",
    ],
)
def test_require_access_token_rejects_non_visible_ascii(token: str) -> None:
    settings = Settings(access_token=SecretStr(token))
    with pytest.raises(GroupMeAuthError, match="cannot be sent") as excinfo:
        settings.require_access_token()
    assert token.strip() not in str(excinfo.value)  # the token value itself never leaks


def test_rejects_unknown_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROUPME_LOG_LEVEL", "LOUD")
    with pytest.raises(ValidationError):
        Settings()


def test_is_frozen() -> None:
    settings = Settings()
    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
