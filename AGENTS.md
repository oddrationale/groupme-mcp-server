# AGENTS.md

Instructions for AI coding agents (Claude Code, Copilot, Cursor, …) working in
this repository. Humans: see [CONTRIBUTING.md](CONTRIBUTING.md).

## What this is

`groupme-mcp-server` is a [FastMCP](https://gofastmcp.com) server exposing the
[GroupMe API v3](https://dev.groupme.com/docs/v3) over MCP. It is deployed to
[Prefect Horizon](https://gofastmcp.com/deployment/prefect-horizon).

**Current state: read, agentic, and write tools implemented**
(`list_conversations`, `read_messages`, `get_conversation_context`,
`search_messages`, `get_highlights`, `send_message`, `react_to_message`);
image upload (arbitrary-URL images) is not yet.

## Toolchain — use these, not the alternatives

| Concern      | Tool                    | Do not use               |
| ------------ | ----------------------- | ------------------------ |
| Packaging    | `uv` (src layout)       | pip, poetry, pdm, hatch  |
| Lint + format| `ruff`                  | black, flake8, isort, pylint |
| Type check   | `ty`                    | mypy, pyright            |
| Tests        | `pytest` + `pytest-cov` | unittest, nose           |
| Git hooks    | `lefthook`              | pre-commit               |

Every command runs through uv: `uv run <cmd>`, `uv add <pkg>`,
`uv add --dev <pkg>`. Never call `pip` or edit `uv.lock` by hand.

Exception: on Windows ARM64 the `lefthook` PyPI wrapper cannot find its binary
(upstream arch-detection bug), so lefthook is installed natively via scoop and
invoked as bare `lefthook`, not `uv run lefthook`.

## Before you claim you are done

```bash
uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest
```

All four must pass. There is no partial credit.

## Integration tests

`tests/integration/` holds tests marked `integration` that talk to real
services: read-only smoke tests against `api.groupme.com`, auth checks
against the deployed server at `https://groupme.fastmcp.app/mcp`, and a
stdio end-to-end test that spawns `uv run groupme-mcp-server` as a
subprocess. They are **deselected by default** (`-m "not integration"` in
`addopts`), so the ordinary gate, CI, and the 100% coverage requirement
never touch them. Run them with:

```bash
uv run pytest -m integration --no-cov
```

- The live GroupMe tests need `GROUPME_ACCESS_TOKEN` (in `.env`); they skip
  themselves when it is absent, and they are strictly read-only — never add
  a test that sends, likes, or otherwise mutates the account.
- The stdio and deployed-server tests need no credentials, only network
  access; the authenticated deployed-server check additionally requires an
  interactive browser OAuth login and only runs with
  `HORIZON_OAUTH_INTERACTIVE=1`.
- `--no-cov` matters: integration runs exercise a subset of the code and
  would otherwise fail the 100% coverage gate.

## Non-negotiables

1. **100% coverage, branch coverage included.** New code needs new tests. Do not
   lower `fail_under` in `pyproject.toml`. If something is genuinely
   untestable, use `# pragma: no cover` on that specific line with a comment
   explaining why.
2. **`from __future__ import annotations` at the top of every module.** Ruff
   enforces it.
3. **No relative imports.** Always `from groupme_mcp_server.foo import bar`.
4. **Google-style docstrings** on public modules, classes, and functions.
5. **Full type annotations**, including `-> None` on procedures.
6. **Never print.** Use logging (`T20` is enabled).
7. **Conventional Commits.** The `commit-msg` hook rejects anything else.
8. **Never commit secrets.** GroupMe tokens come from the environment via
   `Settings`. `.env` is git-ignored; `.env.example` documents the variables.

## Layout

```
src/groupme_mcp_server/
  __init__.py      # public API re-exports + __version__
  __main__.py      # console-script entrypoint (stdio)
  server.py        # the FastMCP instance -- Horizon's entrypoint
  settings.py      # pydantic-settings configuration
  client.py        # imperative shell: async GroupMe HTTP client (httpx2)
  errors.py        # typed GroupMe exception hierarchy
  models.py        # frozen pydantic models of GroupMe payloads
  rendering.py     # functional core: view models + concise/detailed rendering
  search.py        # functional core: client-side search scan logic
  observability.py # stderr logging + opt-in OTel tracing
  tools/           # one module per MCP tool + register_all()
tests/             # top-level, mirrors src/ module names
tests/integration/ # opt-in live/e2e suites (see "Integration tests" above)
scripts/           # standalone git-hook helper scripts
```

## How the Horizon build actually works

Verified against the build log of the live deployment, not assumed. Horizon
builds this repository with:

```
RUN cd . && UV_PROJECT_ENVIRONMENT=/usr/local uv sync --frozen --no-dev --inexact
RUN fastmcp inspect -f fastmcp -o /tmp/server-info.json /app/src/groupme_mcp_server/server.py:mcp
```

What follows from that:

- **The package is installed**, not merely copied
  (`+ groupme-mcp-server==0.1.0 (from file:///app)`), so `server.py` may freely
  import `groupme_mcp_server.settings` and any other first-party module.
- **`uv.lock` is the source of truth** and `--frozen` means a lockfile that does
  not match `pyproject.toml` **fails the build**. Always commit the refreshed
  lockfile after `uv add`. The `uv-lock-is-current` pre-commit hook and
  `UV_FROZEN=1` in CI exist to catch this before Horizon does.
- **`--no-dev` excludes the `dev` group.** Anything the server needs at runtime
  belongs in `[project.dependencies]`, never in the dev group.
- **Horizon runs `fastmcp inspect` itself**, so a server object that fails to
  load fails the build. The `entrypoint` CI job runs the same command, which is
  how you find out on the pull request instead of after merge.
- Keep the module-level `mcp = FastMCP(...)` assignment in `server.py`; the
  entrypoint is `src/groupme_mcp_server/server.py:mcp`.
- Horizon ignores any `if __name__ == "__main__"` block.

Deployment settings live in Horizon, not in this repository: the `production`
target tracks `main` with `deployOnSuccess`, so a build only ships after CI
passes, and every pull request gets its own preview target automatically.
Runtime environment variables are registered in the Horizon UI. `Settings`
reads `GROUPME_`-prefixed variables (see `.env.example`); every field has a
default, but `GROUPME_ACCESS_TOKEN` must be registered in the Horizon UI or
every tool call on the deployed server fails with an auth error.

## Adding a GroupMe tool

Tool modules in `src/groupme_mcp_server/tools/` define **plain async
functions** — do not decorate them with `@mcp.tool` at import time.
(`fastmcp inspect server.py:mcp` loads `server.py` as a standalone module, so
import-time registration against `groupme_mcp_server.server.mcp` would attach
tools to a *different* instance than the one being inspected.) Instead,
`tools/__init__.py` exposes `register_all(mcp)`, which `server.py` calls right
after creating the instance — add new tools to the tuple in `register_all`.

```python
from __future__ import annotations


async def list_groups(limit: int = 10) -> list[dict[str, str]]:
    """List the authenticated user's GroupMe groups.

    Args:
        limit: Maximum number of groups to return.

    Returns:
        A list of group summaries.
    """
```

Test it through the in-memory client, which exercises the real MCP protocol:

```python
from fastmcp import Client

from groupme_mcp_server.server import mcp


async def test_list_groups() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("list_groups", {"limit": 1})
        assert result.data == []
```

Mock GroupMe HTTP calls — never hit the real API in tests. Warnings are errors
(`filterwarnings = ["error"]`).

## Dependencies

- Runtime deps go in `[project.dependencies]` via `uv add`. Keep this list
  small; it is what Horizon installs at build time.
- Dev deps go in the `dev` dependency group via `uv add --dev`.
- Adding a dependency changes `uv.lock`; commit it. CI runs with `UV_FROZEN=1`
  and will fail on a stale lockfile.

## CI/CD

- `ci.yml` — lint, ty, test matrix (3.13/3.14 × Linux/macOS/Windows), Horizon
  entrypoint check, build.
- `codeql.yml`, `scorecard.yml`, `zizmor.yml`, `dependency-review.yml` — security.
- `autofix.yml` — autofix.ci pushes formatting fixes to PRs.
- `release.yml` — tag `v*` publishes to PyPI via Trusted Publishing.
- **Deployment is not in Actions.** Horizon's GitHub App builds on push to
  `main`. Do not add a deploy job.

If you edit a workflow: all `uses:` are pinned to full commit SHAs with a
trailing `# vX` comment. Keep it that way — Dependabot bumps them. Run
`uvx zizmor --persona=pedantic .` after editing.

## Versioning is automated — do not touch it

[Release Please](https://github.com/googleapis/release-please) owns the version
number and the changelog. **Never** hand-edit any of these:

- `CHANGELOG.md`
- `version` in `pyproject.toml`
- the `groupme-mcp-server` entry in `uv.lock`
- `.release-please-manifest.json`

They are regenerated from Conventional Commit subjects on every push to `main`,
and hand edits are silently overwritten by the next release pull request. If
asked to "bump the version", the answer is that merging the open release pull
request does it.

The changelog shows `feat`, `fix`, `perf`, `docs`, `deps`, and `revert`; it
hides `chore`, `ci`, `build`, `refactor`, `test`, and `style`. Choose the commit
type with that in mind — a user-visible change should not be a `chore`.

## `main` is protected

Pull requests are required and CI must be green. Work on a branch; never push
directly to `main`.
