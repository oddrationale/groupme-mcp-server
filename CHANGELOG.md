# Changelog

This file is maintained by [Release Please](https://github.com/googleapis/release-please)
from [Conventional Commit](https://www.conventionalcommits.org/) subjects, and
this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Do not edit entries by hand — they are regenerated from commit history. See
[CONTRIBUTING.md](CONTRIBUTING.md#releasing) for how a release is cut.

## [0.1.0](https://github.com/oddrationale/groupme-mcp-server/releases/tag/v0.1.0) (2026-08-29)

Initial release. This is **scaffolding only** — the project structure, tooling,
deployment, and CI/CD are in place, but no GroupMe tools are implemented yet.
It was published to claim the name and to prove the release pipeline end to end.

### Features

* a `uv` src-layout package targeting Python 3.13+, with a FastMCP server
  instance at `src/groupme_mcp_server/server.py:mcp` exposing no tools yet
* a `groupme-mcp-server` console script that runs the server over stdio
* environment-driven configuration via `pydantic-settings` (`GROUPME_MCP_*`)
* deployment to Prefect Horizon at <https://groupme.fastmcp.app/mcp>, tracking
  `main` and deploying only after CI passes, with a preview deployment per pull
  request
* releases published to PyPI with Trusted Publishing (OIDC, no API tokens) and
  build provenance attestations

### Documentation

* README, contributing guide, security policy, Contributor Covenant code of
  conduct, and agent instructions
