"""Read-only live smoke tests against the real GroupMe API.

These tests require ``GROUPME_ACCESS_TOKEN`` (loaded from the repository
``.env`` file, never printed) and are skipped when it is absent. Every test
here is STRICTLY read-only: nothing is sent, liked, joined, or mutated.
Account-wide listings read whatever the account has; anything specific to
one group or topic reads only the dedicated test group and topic
(``GROUPME_LIVE_GROUP_ID`` / ``GROUPME_LIVE_TOPIC_ID``, see ``conftest.py``)
and skips when they are not configured.

Run with: ``uv run pytest -m integration --no-cov``
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from groupme_mcp_server.client import GroupMeClient
from groupme_mcp_server.errors import GroupMeNotFoundError
from groupme_mcp_server.models import DirectChat, Group, GroupId, Me, Message, Topic
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
async def live_group(client: GroupMeClient, live_group_id: GroupId) -> Group:
    return await client.get_group(live_group_id)


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
    client: GroupMeClient, live_group: Group
) -> None:
    messages = await client.list_group_messages(live_group.id, limit=5)
    assert len(messages) <= 5
    assert all(isinstance(message, Message) for message in messages)
    if not messages:
        pytest.skip("the test group has no messages to exercise the 304 path against")
    newest = messages[0]
    assert newest.created_at.tzinfo is not None
    # Nothing exists after the newest message, so GroupMe answers HTTP 304
    # (or an empty page); either way the client returns an empty list.
    unchanged = await client.list_group_messages(live_group.id, after_id=newest.id, limit=5)
    assert unchanged == []


async def test_direct_chats_parse_into_models(client: GroupMeClient) -> None:
    chats = await client.list_chats(per_page=25)
    for chat in chats:
        assert isinstance(chat, DirectChat)
        assert chat.other_user_id


async def test_test_topic_and_its_messages_are_readable(
    client: GroupMeClient, live_group_id: GroupId, live_topic_id: GroupId
) -> None:
    parent = await client.get_group(live_group_id)
    assert parent.topic_count > 0, "GroupMe no longer reports topics on the test group"
    topics = await client.list_topics(live_group_id)
    assert all(isinstance(topic, Topic) and topic.parent_id == live_group_id for topic in topics)
    assert live_topic_id in [topic.id for topic in topics]
    topic = await client.get_topic(live_group_id, live_topic_id)
    assert topic.id == live_topic_id
    messages = await client.list_group_messages(topic.id, limit=5)
    assert all(message.conversation_id == topic.id for message in messages)
    # The likes leaderboard accepts a topic id too (an empty period is fine).
    await client.leaderboard(topic.id, period="month")


async def test_leaderboard_endpoint_responds(client: GroupMeClient, live_group: Group) -> None:
    try:
        top = await client.leaderboard(live_group.id, period="week")
    except GroupMeNotFoundError:
        pytest.xfail("GroupMe appears to have retired the undocumented likes leaderboard endpoint")
    else:
        assert all(isinstance(message, Message) for message in top)
