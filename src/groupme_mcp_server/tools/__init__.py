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
from groupme_mcp_server.tools.reactions import react_to_message
from groupme_mcp_server.tools.sending import send_message

if TYPE_CHECKING:
    from fastmcp import FastMCP

__all__ = [
    "get_conversation_context",
    "list_conversations",
    "react_to_message",
    "read_messages",
    "register_all",
    "send_message",
]


def register_all(mcp: FastMCP) -> None:
    """Register every GroupMe tool on ``mcp``.

    Each tool carries honest annotations: the read tools are read-only and
    idempotent, ``send_message`` is a non-idempotent write, and
    ``react_to_message`` is an idempotent write. All of them talk to the
    external GroupMe API.

    Args:
        mcp: The server instance to register the tools on.
    """
    for fn in (list_conversations, read_messages, get_conversation_context):
        mcp.tool(fn, annotations=common.READ_ONLY_ANNOTATIONS)
    mcp.tool(send_message, annotations=common.SEND_MESSAGE_ANNOTATIONS)
    mcp.tool(react_to_message, annotations=common.REACTION_ANNOTATIONS)
