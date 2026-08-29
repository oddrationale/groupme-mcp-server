# groupme-mcp-server

[![CI](https://github.com/oddrationale/groupme-mcp-server/actions/workflows/ci.yml/badge.svg)](https://github.com/oddrationale/groupme-mcp-server/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/oddrationale/groupme-mcp-server/branch/main/graph/badge.svg)](https://codecov.io/gh/oddrationale/groupme-mcp-server)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/oddrationale/groupme-mcp-server/badge)](https://scorecard.dev/viewer/?uri=github.com/oddrationale/groupme-mcp-server)
[![PyPI](https://img.shields.io/pypi/v/groupme-mcp-server.svg)](https://pypi.org/project/groupme-mcp-server/)
[![Python](https://img.shields.io/pypi/pyversions/groupme-mcp-server.svg)](https://pypi.org/project/groupme-mcp-server/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

An [MCP](https://modelcontextprotocol.io) server for
[GroupMe](https://groupme.com), built with [FastMCP](https://gofastmcp.com).

It is a set of **agentic tools, not an endpoint wrapper**: instead of mirroring
the [GroupMe API v3](https://dev.groupme.com/docs/v3) route-for-route, each
tool does one job an assistant actually needs — merging groups and DMs into a
single inbox, paginating history with cursors, searching client-side because
GroupMe has no search endpoint, and reporting honestly when a result is
truncated.

> **Status: early.** Read, search/highlights, and the core write tools
> (sending messages, likes) are implemented; image upload is not yet.

## Tools

| Tool                       | What it does                                                                 |
| -------------------------- | ---------------------------------------------------------------------------- |
| `list_conversations`       | Merge groups and DMs into one recency-sorted list with last-message previews. |
| `read_messages`            | Read one group or DM conversation, oldest first, with a `next_before_id` cursor. |
| `get_conversation_context` | One group's metadata, member list, and recent messages in a single call.      |
| `search_messages`          | Search a conversation's history client-side (GroupMe has no search API), with honest scan accounting. |
| `get_highlights`           | A group's top-liked messages for a day/week/month plus a member summary.      |
| `send_message`             | Post to a group or DM, optionally as a reply or with a GroupMe-hosted image.  |
| `react_to_message`         | Like or unlike one message (ids from `read_messages` detailed format).        |

The read tools accept `response_format`: `"concise"` (default, human-readable)
or `"detailed"` (full ids and metadata).

## Connecting to the hosted server

The server is deployed on
[Prefect Horizon](https://gofastmcp.com/deployment/prefect-horizon) at:

```
https://groupme.fastmcp.app/mcp
```

The deployment is protected by Horizon's built-in auth: clients sign in via
OAuth, and only users the deployment owner has authorized can connect —
unauthenticated requests are rejected. Note that this is a single-tenant
deployment (see [Security](#security)): every authorized client acts as the
one GroupMe account whose token is configured on the server.

## Running locally

The package runs as a stdio MCP server. Get a GroupMe access token from
<https://dev.groupme.com> (sign in and copy your access token), then:

```bash
GROUPME_ACCESS_TOKEN=... uvx groupme-mcp-server
```

Or configure an MCP client to launch it:

```json
{
  "mcpServers": {
    "groupme": {
      "command": "uvx",
      "args": ["groupme-mcp-server"],
      "env": {
        "GROUPME_ACCESS_TOKEN": "your-token-from-dev.groupme.com"
      }
    }
  }
}
```

## Configuration

Everything is configured through environment variables (`GROUPME_*` may also
come from a local `.env` file — see [`.env.example`](.env.example)).

| Variable                       | Default                       | Description                                                        |
| ------------------------------ | ----------------------------- | ------------------------------------------------------------------ |
| `GROUPME_ACCESS_TOKEN`         | *(unset)*                     | GroupMe API token from <https://dev.groupme.com>. Optional at startup; required when a tool calls the API. |
| `GROUPME_LOG_LEVEL`            | `INFO`                        | Verbosity of the server's own loggers: `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `GROUPME_API_BASE_URL`         | `https://api.groupme.com/v3`  | GroupMe REST API base URL (override mainly for testing).           |
| `GROUPME_IMAGE_API_BASE_URL`   | `https://image.groupme.com`   | GroupMe image-upload service base URL. Reserved: unused until image upload is implemented. |
| `OTEL_EXPORTER_OTLP_ENDPOINT`  | *(unset)*                     | OTLP/HTTP collector endpoint. Setting it turns tracing on.         |
| `OTEL_EXPORTER_OTLP_HEADERS`   | *(unset)*                     | Extra headers for the OTLP exporter (e.g. `authorization=Bearer%20...`). |
| `OTEL_SERVICE_NAME`            | `groupme-mcp-server`          | The `service.name` resource attribute on exported spans.           |
| `OTEL_SDK_DISABLED`            | *(unset)*                     | Set to `true`/`1` to keep tracing off even when an endpoint is set. |
| `FASTMCP_LOG_LEVEL`            | `INFO`                        | Verbosity of FastMCP's own `fastmcp.*` loggers.                    |

## Security

- **Single-tenant by design.** The server holds exactly one GroupMe token and
  every tool acts as that token's owner — reading their conversations, posting
  as them, liking as them. Anyone allowed to connect (locally, or through
  Horizon's auth on the hosted deployment) gets that full identity; there is
  no per-client GroupMe account mapping.
- **The token never crosses the MCP boundary.** Clients never send or receive
  it: the token lives server-side, goes to GroupMe only as the
  `X-Access-Token` request header (never in URLs), is excluded from tool
  output and error messages, and is registered for redaction if OTel header
  capture is enabled.
- **Hosted-deployment caveat.** On Horizon, tool requests and responses pass
  through Prefect's infrastructure and may appear in its request/payload
  logs. Message content read or written through the hosted server is visible
  to whoever operates the deployment; run the server locally if that is not
  acceptable.

Report vulnerabilities through
[private vulnerability reporting](https://github.com/oddrationale/groupme-mcp-server/security/advisories/new),
not public issues — see [SECURITY.md](SECURITY.md).

## Observability

- **Logs** are structured single lines on **stderr** (stdout would corrupt the
  stdio transport), each carrying the current OTel `trace_id`/`span_id` when
  a span is active. `GROUPME_LOG_LEVEL` controls the server's own loggers.
- **Traces** are opt-in: when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (and
  `OTEL_SDK_DISABLED` is not truthy), the server installs an OTLP/HTTP span
  exporter. FastMCP emits a span for every `tools/call`, and outbound GroupMe
  HTTP requests get client spans via instrumented `httpx2` transports —
  without an endpoint everything no-ops.

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
| `uv run pytest -m integration --no-cov` | Opt-in live/e2e suites (see `tests/integration/`). |
| `uv run fastmcp inspect src/groupme_mcp_server/server.py:mcp` | See what Horizon sees. |

Coverage is enforced at **100%** (branch coverage included). If a line is
genuinely untestable, exclude it deliberately with `# pragma: no cover` and say
why in the PR — do not lower the threshold.

## Deployment

The hosted server is deployed on [Prefect Horizon](https://horizon.prefect.io),
which builds directly from this repository via its GitHub App.

- **Entrypoint:** `src/groupme_mcp_server/server.py:mcp`
- **Dependencies:** installed with `uv sync --frozen --no-dev`, so `uv.lock`
  must be committed and current or the build fails
- **Environment variables:** registered in the Horizon UI
  (`GROUPME_ACCESS_TOKEN` at minimum)
- **Auth:** Horizon's built-in OAuth — clients must present a bearer token

The `production` target tracks `main` and deploys only after CI passes; every
pull request gets its own preview deployment. There is no deploy step in GitHub
Actions — CI gates quality and security, Horizon does the shipping.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © Dariel Dato-on
