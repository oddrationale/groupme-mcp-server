"""The FastMCP server instance.

Prefect Horizon is configured to load this module and use the ``mcp`` object as
its entrypoint (``src/groupme_mcp_server/server.py:mcp``).
"""

from __future__ import annotations

from fastmcp import FastMCP

from groupme_mcp_server.observability import configure_observability
from groupme_mcp_server.settings import get_settings
from groupme_mcp_server.tools import register_all

configure_observability(get_settings())

INSTRUCTIONS = """\
Tools for reading, searching, and writing GroupMe conversations (GroupMe API
v3) as the authenticated user; requests need a GROUPME_ACCESS_TOKEN in the
server's environment. Start with list_conversations: it lists groups and
direct-message chats along with the group_id / other_user_id values every
other tool takes. From there, read history with read_messages (oldest first,
next_before_id cursor), orient yourself in one group with
get_conversation_context, find specific messages with search_messages, and
catch up on a busy group with get_highlights; write with send_message (each
call posts a new message) and react_to_message (like/unlike, ids from
read_messages' detailed format). Read tools accept response_format="concise"
(default, human-readable) or "detailed" (full ids and metadata).
"""

mcp: FastMCP = FastMCP(name="groupme-mcp-server", instructions=INSTRUCTIONS)

register_all(mcp)
