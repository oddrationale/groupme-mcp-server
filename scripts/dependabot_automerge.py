"""Decide whether a Dependabot pull request is low-risk enough to auto-merge.

Reads the ``updated-dependencies-json`` output of ``dependabot/fetch-metadata``
from the ``UPDATED_DEPENDENCIES`` environment variable and writes
``automerge=true`` or ``automerge=false`` to ``$GITHUB_OUTPUT`` (or stdout when
run outside Actions). The reasons an update needs a human go to stderr.

Every dependency in the pull request must pass; anything unrecognised fails
closed. Auto-merge only arms GitHub's native auto-merge, so the required
checks still have to pass before anything lands.

Usage:
    UPDATED_DEPENDENCIES='[...]' python scripts/dependabot_automerge.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PATCH = "version-update:semver-patch"
MINOR = "version-update:semver-minor"

# Actions that only run while cutting a release (release.yml and
# release-please.yml). Pull request CI never executes them, so a green PR says
# nothing about whether an update to one of them still works.
RELEASE_ONLY_ACTIONS = frozenset(
    {
        "actions/attest-build-provenance",
        "actions/create-github-app-token",
        "googleapis/release-please-action",
        "pypa/gh-action-pypi-publish",
    }
)


def _action_blocker(dependency: dict[str, object]) -> str | None:
    if dependency.get("dependencyName") in RELEASE_ONLY_ACTIONS:
        return "only runs during a release, which pull request CI does not exercise"
    return None


def _uv_blocker(dependency: dict[str, object]) -> str | None:
    dependency_type = dependency.get("dependencyType")
    if dependency_type in ("direct:development", "indirect"):
        # Dev tools are exactly what CI runs; indirect (transitive) runtime
        # packages are only bumped by security updates.
        return None
    if dependency_type == "direct:production":
        if dependency.get("updateType") == PATCH:
            return None
        return "minor update of a runtime dependency"
    return f"unknown dependency type {dependency_type!r}"


def _blocker(dependency: dict[str, object]) -> str | None:
    """Return why one updated dependency needs review, or ``None`` if it is low-risk."""
    update_type = dependency.get("updateType")
    if update_type not in (PATCH, MINOR):
        return f"{update_type or 'unknown'} update"
    ecosystem = dependency.get("packageEcosystem")
    if ecosystem == "github_actions":
        return _action_blocker(dependency)
    if ecosystem == "uv":
        return _uv_blocker(dependency)
    return f"unsupported ecosystem {ecosystem!r}"


def blockers(dependencies: list[dict[str, object]]) -> list[str]:
    """Return why the pull request needs a human; an empty list means auto-merge it."""
    if not dependencies:
        return ["no dependency metadata"]
    problems: list[str] = []
    for dependency in dependencies:
        problem = _blocker(dependency)
        if problem is not None:
            problems.append(f"{dependency.get('dependencyName', '<unnamed>')}: {problem}")
    return problems


def _parse(raw: str) -> list[dict[str, object]]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def main(environ: dict[str, str]) -> int:
    """Entry point. Returns a process exit code."""
    problems = blockers(_parse(environ.get("UPDATED_DEPENDENCIES", "")))
    verdict = "false" if problems else "true"
    for problem in problems:
        sys.stderr.write(f"needs review: {problem}\n")
    line = f"automerge={verdict}\n"
    output = environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(line)
    else:
        sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(dict(os.environ)))
