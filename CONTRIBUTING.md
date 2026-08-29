# Contributing

Thanks for your interest. This is a small, solo-maintained project; issues and
pull requests are welcome.

## Before you start

For anything larger than a bug fix, please open an issue first so we can agree
on the approach before you spend time on it.

**Do not report security vulnerabilities as issues or pull requests.** See
[SECURITY.md](SECURITY.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.13+.

```bash
git clone https://github.com/oddrationale/groupme-mcp-server.git
cd groupme-mcp-server
uv sync --all-groups
uv run lefthook install
```

`lefthook install` wires up the git hooks:

| Hook         | What runs                                                     |
| ------------ | ------------------------------------------------------------- |
| `pre-commit` | `ruff format`, `ruff check --fix`, `ty check`, `uv lock --check`, large-file guard |
| `commit-msg` | Conventional Commits validation                                |
| `pre-push`   | `pytest` (100% coverage gate) and `zizmor` on workflow changes  |

To bypass in an emergency: `git commit --no-verify`. CI will still catch it.

> **Windows on ARM64:** the `lefthook` PyPI wrapper picks its bundled binary
> from the *OS* architecture, so an x86-64 Python running under emulation looks
> for an `arm64` binary that the `win_amd64` wheel does not ship, and
> `uv run lefthook` fails. Install lefthook natively instead
> (`scoop install lefthook` or `winget install evilmartians.lefthook`) and drop
> the `uv run` prefix: `lefthook install`.

## Making a change

1. Branch off `main`: `git switch -c feat/my-change`.
2. Make the change **and its tests**. Coverage must stay at 100%.
3. Run the full local check:

   ```bash
   uv run ruff format . && uv run ruff check . && uv run ty check && uv run pytest
   ```

4. Push and open a pull request. All CI checks must pass before merge.

## Commit messages

We use [Conventional Commits](https://www.conventionalcommits.org/). The
`commit-msg` hook enforces this.

```
<type>[optional scope][!]: <description>
```

Valid types: `build`, `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`,
`revert`, `style`, `test`. Keep the subject under 72 characters. Use `!` (or a
`BREAKING CHANGE:` footer) for breaking changes.

Examples:

```
feat(tools): add list_groups
fix: handle 401 responses from GroupMe
docs: clarify Horizon entrypoint
```

## Code style

- Formatting and linting are **not** matters of taste here — `ruff` decides.
  Run it rather than arguing with it.
- Everything is type annotated; `ty` runs in strict-ish mode and CI fails on
  warnings.
- Public modules, classes, and functions need Google-style docstrings.
- `from __future__ import annotations` is required at the top of every module
  (ruff enforces this).
- No relative imports — always `from groupme_mcp_server.x import y`.

### One constraint worth knowing

`src/groupme_mcp_server/server.py` is the Prefect Horizon entrypoint. Horizon
loads that file directly. Keep it importable on its own and be deliberate about
what it pulls in at module scope.

## Tests

- Tests live in the top-level `tests/` directory, not inside the package.
- `pytest-asyncio` runs in `auto` mode — `async def test_*` just works, no
  decorator needed.
- Prefer FastMCP's in-memory `Client(mcp)` for end-to-end tool tests; it
  exercises the real MCP protocol without a subprocess.
- Warnings are errors (`filterwarnings = ["error"]`). If a dependency emits an
  unavoidable warning, add a targeted ignore with a comment, not a blanket one.

## Releasing

Maintainer only:

1. Bump the version: `uv version --bump patch|minor|major`.
2. Update `CHANGELOG.md`.
3. Commit, then tag: `git tag -a v0.1.1 -m "v0.1.1" && git push --follow-tags`.

The tag triggers `release.yml`, which verifies the tag matches the project
version, builds, attests provenance, publishes to PyPI via Trusted Publishing,
and cuts a GitHub release.
