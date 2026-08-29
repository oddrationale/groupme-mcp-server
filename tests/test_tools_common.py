from __future__ import annotations

import pytest
from fastmcp.exceptions import ToolError

from groupme_mcp_server.client import GroupMeClient
from groupme_mcp_server.errors import (
    GroupMeApiError,
    GroupMeAuthError,
    GroupMeNotFoundError,
    GroupMeRateLimitError,
)
from groupme_mcp_server.tools import common

EXAMPLE = "some_tool(arg=1)"


def test_auth_errors_keep_their_guidance() -> None:
    message = common.tool_error_message(GroupMeAuthError("set GROUPME_ACCESS_TOKEN"), EXAMPLE)
    assert "set GROUPME_ACCESS_TOKEN" in message
    assert message.endswith(f"A valid call looks like: {EXAMPLE}")


def test_rate_limit_errors_advise_waiting() -> None:
    message = common.tool_error_message(GroupMeRateLimitError("limited", status=420), EXAMPLE)
    assert "rate limiting" in message
    assert "retry" in message


def test_not_found_errors_point_at_list_conversations() -> None:
    message = common.tool_error_message(GroupMeNotFoundError("missing", status=404), EXAMPLE)
    assert "list_conversations" in message


def test_other_errors_pass_their_text_through() -> None:
    message = common.tool_error_message(GroupMeApiError("HTTP 500: kaboom", status=500), EXAMPLE)
    assert "HTTP 500: kaboom" in message
    assert EXAMPLE in message


async def test_tool_client_wraps_api_errors() -> None:
    msg = "kaboom"
    with pytest.raises(ToolError, match="kaboom"):
        async with common.tool_client(EXAMPLE):
            raise GroupMeApiError(msg)


async def test_tool_client_wraps_value_errors() -> None:
    msg = "bad cursor combo"
    with pytest.raises(ToolError, match="Invalid arguments"):
        async with common.tool_client(EXAMPLE):
            raise ValueError(msg)


async def test_create_client_builds_from_process_settings() -> None:
    client = common.create_client()
    try:
        assert isinstance(client, GroupMeClient)
    finally:
        await client.aclose()
