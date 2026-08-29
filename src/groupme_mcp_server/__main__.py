"""Console-script entrypoint for running the server locally over stdio."""

from __future__ import annotations

from groupme_mcp_server.observability import configure_observability
from groupme_mcp_server.server import mcp
from groupme_mcp_server.settings import get_settings


def main() -> None:
    """Run the GroupMe MCP server over stdio."""
    configure_observability(get_settings())
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
