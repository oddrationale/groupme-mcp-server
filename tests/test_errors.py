from __future__ import annotations

import pytest

from groupme_mcp_server.errors import (
    MISSING_TOKEN_MESSAGE,
    GroupMeApiError,
    GroupMeAuthError,
    GroupMeNotFoundError,
    GroupMeRateLimitError,
    error_for_status,
)


def test_api_error_attributes() -> None:
    error = GroupMeApiError("boom", status=500, messages=["a", "b"])
    assert str(error) == "boom"
    assert error.status == 500
    assert error.messages == ("a", "b")


def test_api_error_defaults() -> None:
    error = GroupMeApiError("boom")
    assert error.status is None
    assert error.messages == ()


def test_subclasses_are_api_errors() -> None:
    for cls in (GroupMeAuthError, GroupMeRateLimitError, GroupMeNotFoundError):
        assert issubclass(cls, GroupMeApiError)


def test_error_for_status_401() -> None:
    error = error_for_status(401, ["unauthorized"])
    assert isinstance(error, GroupMeAuthError)
    assert str(error) == MISSING_TOKEN_MESSAGE
    assert "GROUPME_ACCESS_TOKEN" in str(error)
    assert error.status == 401
    assert error.messages == ("unauthorized",)


def test_error_for_status_404() -> None:
    error = error_for_status(404, [])
    assert isinstance(error, GroupMeNotFoundError)
    assert error.status == 404


@pytest.mark.parametrize("status", [420, 429])
def test_error_for_status_rate_limit(status: int) -> None:
    error = error_for_status(status, [])
    assert isinstance(error, GroupMeRateLimitError)
    assert error.status == status


def test_error_for_status_other_with_messages() -> None:
    error = error_for_status(500, ["kaboom", "oops"])
    assert type(error) is GroupMeApiError
    assert "HTTP 500" in str(error)
    assert "kaboom; oops" in str(error)
    assert error.messages == ("kaboom", "oops")


def test_error_for_status_other_without_messages() -> None:
    error = error_for_status(502, [])
    assert type(error) is GroupMeApiError
    assert "no error details provided" in str(error)
