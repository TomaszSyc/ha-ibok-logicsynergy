#!/usr/bin/env python3
"""Refuse a commit that touches a value from a private denylist.

Patterns live outside this repository: a public repo cannot itself hold the list of
values it should never contain. This reads $IBOK_DENYLIST, or
~/.config/ibok-logicsynergy/denylist if that is unset, and treats each non-blank,
non-comment line as a regular expression to search staged files for. Without that
file, the check passes without doing anything. A hit never prints the pattern or the
matched text -- only where it was found -- because both are the private values this
guards.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _denylist_path() -> Path:
    configured = os.environ.get("IBOK_DENYLIST")
    if configured:
        return Path(configured)
    return Path.home() / ".config" / "ibok-logicsynergy" / "denylist"


def _patterns(path: Path) -> list[re.Pattern[str]]:
    if not path.is_file():
        return []
    patterns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(re.compile(line))
    return patterns


def main(argv: list[str]) -> int:
    patterns = _patterns(_denylist_path())
    if not patterns:
        return 0

    hit = False
    for filename in argv:
        try:
            text = Path(filename).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            # Not text we can search -- most likely a binary file staged alongside.
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in patterns):
                print(f"{filename}:{lineno}: matches a private pattern")
                hit = True

    return 1 if hit else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
