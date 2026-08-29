from __future__ import annotations

import groupme_mcp_server


def test_version_is_exported() -> None:
    assert groupme_mcp_server.__version__
    assert isinstance(groupme_mcp_server.__version__, str)


def test_public_api() -> None:
    assert set(groupme_mcp_server.__all__) == {
        "GroupMeApiError",
        "GroupMeAuthError",
        "GroupMeClient",
        "GroupMeNotFoundError",
        "GroupMeRateLimitError",
        "Settings",
        "__version__",
        "get_settings",
        "mcp",
    }
    for name in groupme_mcp_server.__all__:
        assert hasattr(groupme_mcp_server, name)
