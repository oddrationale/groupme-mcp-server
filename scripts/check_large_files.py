"""Reject staged files above a size threshold.

Usage:
    python scripts/check_large_files.py [paths...]
"""

from __future__ import annotations

import sys
from pathlib import Path

MAX_BYTES = 512 * 1024


def main(argv: list[str]) -> int:
    """Entry point. Returns a process exit code."""
    offenders = [
        (path, size)
        for path in map(Path, argv[1:])
        if path.is_file() and (size := path.stat().st_size) > MAX_BYTES
    ]
    for path, size in offenders:
        sys.stderr.write(f"{path} is {size / 1024:.0f} KiB (limit {MAX_BYTES // 1024} KiB)\n")
    if offenders:
        sys.stderr.write("Commit rejected. Use Git LFS or keep large assets out of the repo.\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
