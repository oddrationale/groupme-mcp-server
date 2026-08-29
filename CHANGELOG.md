# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-29

Initial release. This is **scaffolding only** — the project structure, tooling,
deployment, and CI/CD are in place, but no GroupMe tools are implemented yet.
It is published to claim the name and to prove the release pipeline end to end.

### Added

- A `uv` src-layout package targeting Python 3.13+, with a FastMCP server
  instance at `src/groupme_mcp_server/server.py:mcp` exposing no tools yet.
- A `groupme-mcp-server` console script that runs the server over stdio.
- Environment-driven configuration via `pydantic-settings` (`GROUPME_MCP_*`).
- Tooling: `ruff` for linting and formatting, `ty` for type checking, `pytest`
  with a hard 100% branch-coverage gate, and `lefthook` git hooks.
- Deployment to Prefect Horizon at <https://groupme.fastmcp.app/mcp>, tracking
  `main` and deploying only after CI passes, with preview deployments per
  pull request.
- CI across Python 3.13 and 3.14 on Linux, macOS, and Windows, plus a job that
  runs `fastmcp inspect` against the Horizon entrypoint.
- Security: CodeQL, OpenSSF Scorecard, zizmor, dependency review, secret
  scanning with push protection, and Dependabot. All GitHub Actions are pinned
  to full commit SHAs and every workflow starts from `permissions: {}`.
- Releases published to PyPI with Trusted Publishing (OIDC, no API tokens) and
  build provenance attestations.

[Unreleased]: https://github.com/oddrationale/groupme-mcp-server/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/oddrationale/groupme-mcp-server/releases/tag/v0.1.0
