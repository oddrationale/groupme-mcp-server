# Changelog

This file is maintained by [Release Please](https://github.com/googleapis/release-please)
from [Conventional Commit](https://www.conventionalcommits.org/) subjects, and
this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Do not edit entries by hand — they are regenerated from commit history. See
[CONTRIBUTING.md](CONTRIBUTING.md#releasing) for how a release is cut.

## [0.2.1](https://github.com/oddrationale/groupme-mcp-server/compare/v0.2.0...v0.2.1) (2026-08-29)


### Documentation

* point the Scorecard badge at the canonical api.scorecard.dev host ([#20](https://github.com/oddrationale/groupme-mcp-server/issues/20)) ([17b3140](https://github.com/oddrationale/groupme-mcp-server/commit/17b31405572ee7098d675a6746b342559a680a4b))

## [0.2.0](https://github.com/oddrationale/groupme-mcp-server/compare/v0.1.0...v0.2.0) (2026-08-29)


### Features

* add read tools for conversations, messages, and group context ([5e4f453](https://github.com/oddrationale/groupme-mcp-server/commit/5e4f4533569a9b02e2801fd27552b1bf90ef1413))
* add search_messages and get_highlights agentic tools ([c1bdb46](https://github.com/oddrationale/groupme-mcp-server/commit/c1bdb4658c99ea127669735ac20e00de1fd93478))
* add send_message and react_to_message tools ([26f8edc](https://github.com/oddrationale/groupme-mcp-server/commit/26f8edcfea74c174d50cb43954b91245e868925a))
* add typed GroupMe client, settings, and observability foundation ([83fbc71](https://github.com/oddrationale/groupme-mcp-server/commit/83fbc7160a4bf9330197494b2f36889bd87fccae))


### Bug Fixes

* harden token validation and OTel header sanitization against leaks ([7bc74fe](https://github.com/oddrationale/groupme-mcp-server/commit/7bc74feb19524f8963e80e8bd9897cffd75630ac))


### Documentation

* document usage, auth model, configuration, and observability ([13b9143](https://github.com/oddrationale/groupme-mcp-server/commit/13b914324a826705ab03319742fee211f0bb3418))
* record why three Scorecard checks are dismissed ([#10](https://github.com/oddrationale/groupme-mcp-server/issues/10)) ([6d91c9b](https://github.com/oddrationale/groupme-mcp-server/commit/6d91c9b02daa1e0075d719e8bfc140f660caf3de))

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
