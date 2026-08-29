"""Read-only live smoke tests against the real GroupMe API.

These tests require ``GROUPME_ACCESS_TOKEN`` (loaded from the repository
``.env`` file, never printed) and are skipped when it is absent. Every test
is STRICTLY read-only: nothing is sent, liked, joined, or mutated.

Run with: ``uv run pytest -m integration --no-cov``
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from groupme_mcp_server.client import GroupMeClient
from groupme_mcp_server.errors import GroupMeNotFoundError
from groupme_mcp_server.models import DirectChat, Group, Me, Message
from groupme_mcp_server.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _live_settings() -> Settings:
    """Load settings from the repository ``.env`` by absolute path.

    The unit-test conftest scrubs ``GROUPME_*`` from the environment and
    moves the working directory away from the repository, so the dotenv
    file must be addressed explicitly.
    """
    return Settings(_env_file=_ENV_FILE)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _live_settings().access_token is None,
        reason="GROUPME_ACCESS_TOKEN is not configured; set it in .env to run live tests",
    ),
]


@pytest.fixture
async def client() -> AsyncIterator[GroupMeClient]:
    async with GroupMeClient(_live_settings()) as live_client:
        yield live_client


@pytest.fixture
async def first_group(client: GroupMeClient) -> Group:
    groups = await client.list_groups(per_page=10, omit_memberships=True)
    if not groups:
        pytest.skip("this GroupMe account belongs to no groups")
    return groups[0]


async def test_get_me_returns_the_callers_profile(client: GroupMeClient) -> None:
    me = await client.get_me()
    assert isinstance(me, Me)
    assert me.id
    assert me.name


async def test_list_groups_parses_into_models(client: GroupMeClient) -> None:
    groups = await client.list_groups(per_page=25, omit_memberships=True)
    assert all(isinstance(group, Group) for group in groups)
    assert all(group.id for group in groups)


async def test_list_groups_with_memberships_parses(client: GroupMeClient) -> None:
    groups = await client.list_groups(per_page=5)
    for group in groups:
        assert isinstance(group, Group)
        for member in group.members or ():
            assert member.user_id


async def test_group_messages_page_and_not_modified_path(
    client: GroupMeClient, first_group: Group
) -> None:
    messages = await client.list_group_messages(first_group.id, limit=5)
    assert len(messages) <= 5
    assert all(isinstance(message, Message) for message in messages)
    if not messages:
        pytest.skip("first group has no messages to exercise the 304 path against")
    newest = messages[0]
    assert newest.created_at.tzinfo is not None
    # Nothing exists after the newest message, so GroupMe answers HTTP 304
    # (or an empty page); either way the client returns an empty list.
    unchanged = await client.list_group_messages(first_group.id, after_id=newest.id, limit=5)
    assert unchanged == []


async def test_direct_chats_parse_into_models(client: GroupMeClient) -> None:
    chats = await client.list_chats(per_page=25)
    for chat in chats:
        assert isinstance(chat, DirectChat)
        assert chat.other_user_id


async def test_leaderboard_endpoint_responds(client: GroupMeClient, first_group: Group) -> None:
    try:
        top = await client.leaderboard(first_group.id, period="week")
    except GroupMeNotFoundError:
        pytest.xfail("GroupMe appears to have retired the undocumented likes leaderboard endpoint")
    assert all(isinstance(message, Message) for message in top)
