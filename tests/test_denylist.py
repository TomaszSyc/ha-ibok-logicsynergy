"""check-denylist.py keeps values from a private list out of every commit.

Run as a subprocess, the way pre-commit invokes it -- that also catches anything
that only breaks when the script runs stand-alone rather than imported.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check-denylist.py"


def _env(tmp_path: Path, **overrides: str) -> dict[str, str]:
    # HOME is overridden so the script's own default path never falls back to
    # whatever denylist the machine running the tests happens to have.
    env = dict(os.environ)
    env["HOME"] = str(tmp_path)
    env.pop("IBOK_DENYLIST", None)
    env.update(overrides)
    return env


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_no_denylist_passes(tmp_path: Path) -> None:
    target = tmp_path / "file.txt"
    target.write_text("nothing interesting here\n", encoding="utf-8")

    result = _run(
        [str(target)],
        _env(tmp_path, IBOK_DENYLIST=str(tmp_path / "missing")),
    )

    assert result.returncode == 0
    assert result.stdout == ""


def test_a_hit_fails_without_printing_the_pattern(tmp_path: Path) -> None:
    denylist = tmp_path / "denylist"
    denylist.write_text("secret-777\n", encoding="utf-8")
    target = tmp_path / "file.txt"
    target.write_text("this line carries secret-777 by mistake\n", encoding="utf-8")

    result = _run([str(target)], _env(tmp_path, IBOK_DENYLIST=str(denylist)))

    assert result.returncode == 1
    assert "secret-777" not in result.stdout
    assert "file.txt:1" in result.stdout


def test_comments_and_blank_lines_are_ignored(tmp_path: Path) -> None:
    # A blank line compiles to a pattern matching everything if it is not
    # filtered out, which would turn line 1 below into a false positive too.
    denylist = tmp_path / "denylist"
    denylist.write_text(
        "\n# secret-777, as a comment only\n\nsecret-777\n", encoding="utf-8"
    )
    target = tmp_path / "file.txt"
    target.write_text(
        "an ordinary first line\na line that leaks secret-777\n", encoding="utf-8"
    )

    result = _run([str(target)], _env(tmp_path, IBOK_DENYLIST=str(denylist)))

    assert result.returncode == 1
    assert "file.txt:1" not in result.stdout
    assert "file.txt:2" in result.stdout
