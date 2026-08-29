"""Console-script entrypoint for running the server locally over stdio."""

from __future__ import annotations

from groupme_mcp_server.server import mcp


def main() -> None:
    """Run the GroupMe MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":  # pragma: no cover
    main()
