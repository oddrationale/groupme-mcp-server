"""Shared fixtures for the live GroupMe suites.

Live tests that touch a specific group or topic only ever use the account
owner's dedicated test group and topic, named in the repository ``.env``:

- ``GROUPME_LIVE_GROUP_ID``: a group the token's owner controls, used for
  every group-specific read and for the write smoke tests.
- ``GROUPME_LIVE_TOPIC_ID``: a topic inside that group.

Tests that need them skip when they are unset, so a live run never falls
back to reading from, or writing to, some other group.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

from groupme_mcp_server.models import GroupId
from groupme_mcp_server.settings import Settings

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
"""The repository ``.env``, addressed absolutely: the unit-test conftest moves
the working directory and scrubs ``GROUPME_*`` from the environment."""


class LiveTarget(BaseSettings):
    """The dedicated test group and topic for live runs, from ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="GROUPME_LIVE_", env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    group_id: str | None = None
    topic_id: str | None = None


@pytest.fixture
def live_settings() -> Settings:
    """Server settings loaded from ``.env``; skips without an access token."""
    settings = Settings(_env_file=ENV_FILE)
    if settings.access_token is None:
        pytest.skip("GROUPME_ACCESS_TOKEN is not configured; set it in .env to run live tests")
    return settings


@pytest.fixture
def live_group_id() -> GroupId:
    """The dedicated test group; skips when ``GROUPME_LIVE_GROUP_ID`` is unset."""
    group_id = LiveTarget().group_id
    if not group_id:
        pytest.skip("GROUPME_LIVE_GROUP_ID is not configured; set it in .env to your test group")
    return GroupId(group_id)


@pytest.fixture
def live_topic_id() -> GroupId:
    """A topic of the test group; skips when ``GROUPME_LIVE_TOPIC_ID`` is unset."""
    topic_id = LiveTarget().topic_id
    if not topic_id:
        pytest.skip("GROUPME_LIVE_TOPIC_ID is not configured; set it in .env to a test topic")
    return GroupId(topic_id)
