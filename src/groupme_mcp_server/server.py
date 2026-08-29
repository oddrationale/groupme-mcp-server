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
Tools for reading and writing GroupMe conversations via the GroupMe API v3.

Start with list_conversations to discover the user's groups and
direct-message chats along with the group_id / other_user_id values the
other tools take. Use read_messages to page through one conversation's
history (oldest first, with a next_before_id cursor), and
get_conversation_context to orient yourself in a single group - metadata,
member list, and recent messages in one call. The read tools accept
response_format="concise" (default, human-readable) or "detailed" (full
ids and metadata).

send_message posts as the authenticated user to a group or direct chat
(each call sends a new message), and react_to_message likes or unlikes one
message using the conversation_id and message id from read_messages'
detailed format. Requests need a GROUPME_ACCESS_TOKEN in the server's
environment.
"""

mcp: FastMCP = FastMCP(name="groupme-mcp-server", instructions=INSTRUCTIONS)

register_all(mcp)
