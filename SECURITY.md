# Security Policy

## Supported versions

This project is pre-1.0. Only the latest release receives security fixes.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | ✅        |

## Reporting a vulnerability

**Please do not open a public issue, pull request, or discussion for a security
vulnerability.**

Report it privately through GitHub's
[private vulnerability reporting](https://github.com/oddrationale/groupme-mcp-server/security/advisories/new).
This creates a private advisory visible only to the maintainer.

Please include:

- A description of the issue and its impact
- Steps to reproduce, or a proof of concept
- The affected version and environment
- Any suggested mitigation

### What to expect

- **Acknowledgement:** within 7 days.
- **Assessment:** within 14 days, with a severity judgement and a rough fix
  timeline.
- **Fix and disclosure:** coordinated with you. A GitHub Security Advisory and a
  CVE will be published where warranted, and you will be credited unless you
  prefer otherwise.

This is a solo-maintained hobby project — there is no bug bounty, and response
times are best effort.

## Scope

In scope:

- The `groupme_mcp_server` package and its published distributions
- The GitHub Actions workflows and release/supply-chain configuration in this
  repository

Out of scope:

- Vulnerabilities in GroupMe's own API or services — report those to
  [Microsoft/GroupMe](https://dev.groupme.com/)
- Vulnerabilities in upstream dependencies — report those upstream, though
  please do tell us if this project's usage makes one exploitable
- Findings from automated scanners without a demonstrated impact
- Social engineering, physical access, or denial of service through resource
  exhaustion

## Handling credentials

This server talks to GroupMe on a user's behalf and therefore touches access
tokens and private message content. When reporting or reproducing an issue:

- **Never** include a real GroupMe access token in a report, log, or test
  fixture. Redact it.
- **Never** include real message content, phone numbers, or user IDs.
- Credentials are supplied through environment variables only — they are never
  read from committed files, and `.env` is git-ignored.

## Security measures in this repository

- CodeQL code scanning (`security-extended` + `security-and-quality`) on every
  push, pull request, and weekly
- Secret scanning with push protection
- Dependabot alerts and weekly dependency updates
- Dependency review on pull requests, failing on moderate or higher severity
- `zizmor` static analysis of GitHub Actions workflows
- OpenSSF Scorecard, published weekly
- All GitHub Actions pinned to full commit SHAs
- Workflows default to `permissions: {}` and opt into the minimum needed
- PyPI publishing via Trusted Publishing (OIDC) with build provenance
  attestations — no long-lived API tokens exist for this project
