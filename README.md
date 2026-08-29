# groupme-mcp-server

[![CI](https://github.com/oddrationale/groupme-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/oddrationale/groupme-mcp-server/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/oddrationale/groupme-mcp-server/branch/main/graph/badge.svg)](https://codecov.io/gh/oddrationale/groupme-mcp-server)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/oddrationale/groupme-mcp-server/badge)](https://scorecard.dev/viewer/?uri=github.com/oddrationale/groupme-mcp-server)
[![PyPI](https://img.shields.io/pypi/v/groupme-mcp-server.svg)](https://pypi.org/project/groupme-mcp-server/)
[![Python](https://img.shields.io/pypi/pyversions/groupme-mcp-server.svg)](https://pypi.org/project/groupme-mcp-server/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

An [MCP](https://modelcontextprotocol.io) server that exposes the
[GroupMe API v3](https://dev.groupme.com/docs/v3) to MCP clients, built with
[FastMCP](https://gofastmcp.com) and hosted on
[Prefect Horizon](https://gofastmcp.com/deployment/prefect-horizon).

> **Status: early.** Read, search/highlights, and the core write tools
> (sending messages, likes) are implemented; image upload is not yet.

## Tools

The read tools accept `response_format`: `"concise"` (default, human-readable)
or `"detailed"` (full ids and metadata).

| Tool                       | What it does                                                                 |
| -------------------------- | ---------------------------------------------------------------------------- |
| `list_conversations`       | Merge groups and DMs into one recency-sorted list with last-message previews. |
| `read_messages`            | Read one group or DM conversation, oldest first, with a `next_before_id` cursor. |
| `get_conversation_context` | One group's metadata, member list, and recent messages in a single call.      |
| `search_messages`          | Search a conversation's history client-side (GroupMe has no search API), with honest scan accounting. |
| `get_highlights`           | A group's top-liked messages for a day/week/month plus a member summary.      |
| `send_message`             | Post to a group or DM, optionally as a reply or with a GroupMe-hosted image.  |
| `react_to_message`         | Like or unlike one message (ids from `read_messages` detailed format).        |

## Quick start

```bash
uvx groupme-mcp-server
```

Or point an MCP client at it directly:

```json
{
  "mcpServers": {
    "groupme": {
      "command": "uvx",
      "args": ["groupme-mcp-server"],
      "env": {
        "GROUPME_ACCESS_TOKEN": "your-token-from-dev.groupme.com",
        "GROUPME_LOG_LEVEL": "INFO"
      }
    }
  }
}
```

## Configuration

All settings are read from the environment with the `GROUPME_` prefix, or
from a local `.env` file. See [`.env.example`](.env.example).

| Variable                    | Default                       | Description                                                        |
| --------------------------- | ----------------------------- | ------------------------------------------------------------------ |
| `GROUPME_ACCESS_TOKEN`      | *(unset)*                     | GroupMe API token from <https://dev.groupme.com>. Optional at startup; required when a tool calls the API. |
| `GROUPME_LOG_LEVEL`         | `INFO`                        | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`.                |
| `GROUPME_API_BASE_URL`      | `https://api.groupme.com/v3`  | GroupMe REST API base URL (override mainly for testing).           |
| `GROUPME_IMAGE_API_BASE_URL`| `https://image.groupme.com`   | GroupMe image-upload service base URL.                             |

Tracing is opt-in: spans are exported only when `OTEL_EXPORTER_OTLP_ENDPOINT`
is set (and `OTEL_SDK_DISABLED` is not truthy).

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13+.

```bash
git clone https://github.com/oddrationale/groupme-mcp-server.git
cd groupme-mcp-server
uv sync --all-groups
uv run lefthook install     # install the git hooks
```

Common tasks:

| Command                          | What it does                                        |
| -------------------------------- | --------------------------------------------------- |
| `uv run ruff format .`           | Format.                                             |
| `uv run ruff check --fix .`      | Lint and autofix.                                   |
| `uv run ty check`                | Type check.                                         |
| `uv run pytest`                  | Run tests. **Fails below 100% coverage.**           |
| `uv run pytest --no-cov -k name` | Run a subset without the coverage gate.             |
| `uv run fastmcp inspect src/groupme_mcp_server/server.py:mcp` | See what Horizon sees. |

Coverage is enforced at **100%** (branch coverage included). If a line is
genuinely untestable, exclude it deliberately with `# pragma: no cover` and say
why in the PR — do not lower the threshold.

## Deployment

The server is live at **https://groupme.fastmcp.app/mcp**, deployed on
[Prefect Horizon](https://horizon.prefect.io), which builds directly from this
repository via its GitHub App.

- **Entrypoint:** `src/groupme_mcp_server/server.py:mcp`
- **Dependencies:** installed with `uv sync --frozen --no-dev`, so `uv.lock`
  must be committed and current or the build fails
- **Environment variables:** registered in the Horizon UI
- **Auth:** Horizon's built-in OAuth — clients must present a bearer token

The `production` target tracks `main` and deploys only after CI passes; every
pull request gets its own preview deployment. There is no deploy step in GitHub
Actions — CI gates quality and security, Horizon does the shipping.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Security issues go through
[private vulnerability reporting](https://github.com/oddrationale/groupme-mcp-server/security/advisories/new),
not public issues — see [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) © Dariel Dato-on
