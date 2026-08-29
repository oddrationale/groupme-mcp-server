"""Validate a commit message against the Conventional Commits specification.

Usage:
    python scripts/check_commit_msg.py <path-to-commit-message-file>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TYPES = (
    "build",
    "chore",
    "ci",
    "docs",
    "feat",
    "fix",
    "perf",
    "refactor",
    "revert",
    "style",
    "test",
)

PATTERN = re.compile(
    rf"^(?:{'|'.join(TYPES)})(?:\([\w./-]+\))?!?: .+",
)

MAX_SUBJECT_LENGTH = 72

USAGE = "usage: check_commit_msg.py <path-to-commit-message-file>"


def _first_meaningful_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped
    return ""


def check(subject: str) -> list[str]:
    """Return a list of problems with ``subject``; empty means it is valid."""
    problems: list[str] = []
    if not subject:
        problems.append("commit message is empty")
        return problems
    if subject.startswith(("Merge ", "Revert ", "fixup!", "squash!")):
        return problems
    if not PATTERN.match(subject):
        problems.append(
            f"subject must match '<type>[optional scope][!]: <description>' "
            f"where <type> is one of: {', '.join(TYPES)}"
        )
    if len(subject) > MAX_SUBJECT_LENGTH:
        problems.append(f"subject is {len(subject)} characters (max {MAX_SUBJECT_LENGTH})")
    return problems


def main(argv: list[str]) -> int:
    """Entry point. Returns a process exit code."""
    if len(argv) != 2:  # noqa: PLR2004
        sys.stderr.write(f"{USAGE}\n")
        return 2
    subject = _first_meaningful_line(Path(argv[1]).read_text(encoding="utf-8"))
    problems = check(subject)
    if problems:
        sys.stderr.write(f"\nInvalid commit message: {subject!r}\n")
        for problem in problems:
            sys.stderr.write(f"  - {problem}\n")
        sys.stderr.write(
            "\nExamples:\n  feat(tools): add list_groups\n  fix: handle 401 from GroupMe\n"
        )
        sys.stderr.write("\nSee https://www.conventionalcommits.org/\n\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
