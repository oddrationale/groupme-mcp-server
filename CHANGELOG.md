# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial project scaffolding: `uv` src-layout package, ruff, ty, pytest with a
  100% coverage gate, and lefthook git hooks.
- FastMCP server instance at `src/groupme_mcp_server/server.py:mcp`, ready for
  Prefect Horizon.
- CI/CD: lint, type check, cross-platform test matrix, build, CodeQL, OpenSSF
  Scorecard, zizmor, dependency review, autofix.ci, and a tag-triggered PyPI
  release via Trusted Publishing.

[Unreleased]: https://github.com/oddrationale/groupme-mcp-server/commits/main
