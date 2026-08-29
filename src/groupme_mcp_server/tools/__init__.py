"""MCP tools for the GroupMe server.

Tool modules define plain async functions; nothing registers at import time.
``register_all`` attaches every tool to a given ``FastMCP`` instance, which
keeps registration working no matter how ``server.py`` is loaded (installed
package, ``fastmcp inspect`` on the file path, or Horizon's build).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from groupme_mcp_server.tools import common
from groupme_mcp_server.tools.conversations import list_conversations
from groupme_mcp_server.tools.group_context import get_conversation_context
from groupme_mcp_server.tools.messages import read_messages

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = ["get_conversation_context", "list_conversations", "read_messages", "register_all"]


def register_all(mcp: FastMCP) -> None:
    """Register every GroupMe tool on ``mcp``.

    All three tools are read-only, idempotent, and talk to the external
    GroupMe API, so they share the same honest annotations.

    Args:
        mcp: The server instance to register the tools on.
    """
    for fn in (list_conversations, read_messages, get_conversation_context):
        mcp.tool(fn, annotations=common.READ_ONLY_ANNOTATIONS)
