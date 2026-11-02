"""Every third-party action runs pinned to a commit.

A tag can be moved to other code at any time; a commit cannot. Dependabot bumps a pin
only when the version comment follows it, so a pin without one would silently age.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted(
    (Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml")
)
PINNED = re.compile(r"uses: [\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d\S*")
# Validators that must check against today's rules, not a pinned copy (see hacs.yml).
ON_THEIR_BRANCH = {
    "uses: hacs/action@main",
    "uses: home-assistant/actions/hassfest@master",
}


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda path: path.name)
def test_actions_are_pinned_to_commits(workflow: Path) -> None:
    uses = [
        line.strip().removeprefix("- ")
        for line in workflow.read_text(encoding="utf-8").splitlines()
        if line.strip().removeprefix("- ").startswith("uses:")
    ]
    unpinned = [
        line
        for line in uses
        if not line.startswith("uses: ./")
        and line not in ON_THEIR_BRANCH
        and not PINNED.fullmatch(line)
    ]

    assert not unpinned, unpinned
