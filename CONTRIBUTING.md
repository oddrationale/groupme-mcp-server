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

`src/groupme_mcp_server/server.py` is the Prefect Horizon entrypoint, and
Horizon builds with `uv sync --frozen --no-dev`. Two consequences:

- Commit the refreshed `uv.lock` whenever you touch dependencies — `--frozen`
  fails the deploy on a stale lockfile.
- Runtime dependencies belong in `[project.dependencies]`; the `dev` group is
  not installed in production.

## Tests

- Tests live in the top-level `tests/` directory, not inside the package.
- `pytest-asyncio` runs in `auto` mode — `async def test_*` just works, no
  decorator needed.
- Prefer FastMCP's in-memory `Client(mcp)` for end-to-end tool tests; it
  exercises the real MCP protocol without a subprocess.
- Warnings are errors (`filterwarnings = ["error"]`). If a dependency emits an
  unavoidable warning, add a targeted ignore with a comment, not a blanket one.

## Releasing

Releases are automated by [Release Please](https://github.com/googleapis/release-please).
You do not bump versions, write changelog entries, or push tags by hand.

### How it works

1. You merge ordinary pull requests to `main` with Conventional Commit titles.
2. Release Please keeps a **release pull request** open, titled something like
   `chore(main): release 0.2.0`. It bumps the version in `pyproject.toml` and
   `uv.lock`, and writes the `CHANGELOG.md` entry from the commit subjects since
   the last release. It rewrites that PR on every push to `main`.
3. **Merging the release pull request is the act of releasing.** Release Please
   then creates the `v0.2.0` tag and the GitHub release.
4. That tag triggers `release.yml`, which builds, publishes to PyPI via Trusted
   Publishing, and attaches the distributions to the release.

So: merge the release PR when you want to ship, and ignore it otherwise. It is
safe to leave open indefinitely.

### What decides the version

| Commit type | Effect while pre-1.0 |
| ----------- | -------------------- |
| `fix:` | patch — `0.1.0` → `0.1.1` |
| `feat:` | minor — `0.1.0` → `0.2.0` |
| `feat!:` or a `BREAKING CHANGE:` footer | minor — `0.2.0` → `0.3.0` |
| `docs:`, `perf:`, `revert:` | patch, and appear in the changelog |
| `chore:`, `ci:`, `build:`, `refactor:`, `test:`, `style:` | patch, hidden from the changelog |

`bump-minor-pre-major` is set, so a breaking change bumps the minor version
rather than jumping to 1.0.0. Remove that from `release-please-config.json`
when you are ready to commit to a stable API.

To force a specific version, add a `Release-As: 1.0.0` footer to a commit on
`main`.

### Never edit these by hand

`CHANGELOG.md`, the `version` in `pyproject.toml`, and the `groupme-mcp-server`
version in `uv.lock` are all generated. Hand edits will be overwritten by the
next release pull request.

### Rehearsing a release

Run the `Release` workflow manually (`workflow_dispatch`, from `main`) to
publish to **TestPyPI** instead. That exercises the same build and the same
OIDC handshake against a throwaway index, and re-runs are safe because the
TestPyPI upload uses `skip-existing`.

### Notes on the machinery

There are **no PyPI API tokens** in this project. PyPI authenticates the
workflow with a short-lived OIDC token scoped to this repository, this workflow
file, and the `pypi` GitHub environment — which only `v*` tags may deploy to.

Release Please signs its tag push with a **GitHub App token**, not
`GITHUB_TOKEN`. GitHub deliberately refuses to trigger workflows from events
created by `GITHUB_TOKEN`, so without the App token the tag would appear and
`release.yml` would never run.

**PyPI never permits re-uploading a version number**, even after you delete the
release. If a broken artifact reaches PyPI, yank it and let Release Please cut
the next patch.
