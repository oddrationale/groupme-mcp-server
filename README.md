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
> (sending messages with images from any URL, likes) are implemented.

## Tools

| Tool                       | What it does                                                                 |
| -------------------------- | ---------------------------------------------------------------------------- |
| `list_conversations`       | Merge groups and DMs into one recency-sorted list with last-message previews; groups list their topics. |
| `read_messages`            | Read one group, topic, or DM, oldest first, with a `next_before_id` cursor. |
| `get_conversation_context` | One group's (or topic's) metadata, member list, and recent messages in a single call. |
| `search_messages`          | Search a conversation's history client-side (GroupMe has no search API), with honest scan accounting. |
| `get_highlights`           | A group's or topic's top-liked messages for a day/week/month plus a member summary. |
| `send_message`             | Post to a group, topic, or DM, optionally as a reply or with an image from any public URL. |
| `react_to_message`         | Like or unlike one message (ids from `read_messages` detailed format).        |

The read tools accept `response_format`: `"concise"` (default, human-readable)
or `"detailed"` (full ids and metadata).

### Group topics

A GroupMe group can contain **topics**: separate chats such as
"Announcements" that sit inside the group. A group's main chat does not
contain its topics' messages, so the tools address topics explicitly.

`list_conversations` lists each group's topics under the group, most recently
active first, with their own last-message previews:

```json
{
  "kind": "group",
  "name": "Trip Planning",
  "group_id": "12345678",
  "topics": [
    {
      "topic_id": "23456789",
      "parent_group_id": "12345678",
      "name": "Announcements",
      "type": "announcement",
      "last_message": "Ada: Trip details are ready.",
      "last_active": "2h ago"
    }
  ]
}
```

Pass a topic to `read_messages`, `search_messages`, or `send_message` as a
`conversation`:

```json
{"kind": "topic", "parent_group_id": "12345678", "topic_id": "23456789"}
```

`get_conversation_context` and `get_highlights` take an optional `topic_id`
next to the parent's `group_id`. Before sending to a topic, `send_message`
checks that the topic belongs to the stated parent; a failed send is never
redirected to the parent's main chat. GroupMe enforces who may post, for
example in announcement topics.

Topics come from GroupMe's **undocumented** `GET /groups/{id}/subgroups`
route, which the GroupMe web client uses. If it fails, `list_conversations`
still lists the group (without its topics) and says so in its `note`.
Creating, editing, and deleting topics is not supported.

### Images

`send_message` accepts an `image_url`. GroupMe can only attach images hosted
on its own image service, so a `https://i.groupme.com/...` URL is attached
as-is and any other URL is downloaded and re-hosted there first. Because the
server fetches a URL the caller chose, the download is locked down:

- only `http`/`https` URLs without embedded credentials;
- the host must resolve only to public addresses (no loopback, private,
  link-local, or cloud-metadata ranges), and the connection goes to the
  address that was checked, so DNS cannot redirect it internally;
- at most 3 redirects, each checked again;
- at most 15 MB, within 10 seconds, of a raster `image/*` type (no SVG);
- the GroupMe token is never sent to the image's host.

The download happens only after the message text (and, for a topic, the
topic) has been validated, so a send that would fail fetches nothing.

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
| `GROUPME_IMAGE_API_BASE_URL`   | `https://image.groupme.com`   | GroupMe image service base URL, where `send_message` re-hosts images. |
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
- **Outbound image downloads are restricted.** Re-hosting an `image_url` is
  the only time the server fetches a caller-chosen URL; it refuses private and
  internal addresses, caps size and time, and never forwards the GroupMe
  token (see [Images](#images)).
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
