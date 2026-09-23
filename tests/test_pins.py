"""Every pin in requirements-dev.txt must agree with the Home Assistant test plugin.

pytest-homeassistant-custom-component pins exact versions of Home Assistant and of
pytest itself. Naming a different one anywhere makes pip refuse to resolve the
environment, which fails the CI install with a wall of text rather than a useful
message -- and it is not obvious from reading requirements-dev.txt why.
"""

import re
from importlib.metadata import requires, version
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements-dev.txt"
HELPER = "pytest-homeassistant-custom-component"


def _our_pins() -> dict[str, str]:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    return dict(re.findall(r"^([A-Za-z0-9_.-]+)==([^\s#]+)", text, re.MULTILINE))


def _helper_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for dep in requires(HELPER) or []:
        match = re.match(r"^([A-Za-z0-9_.-]+)==([^\s;]+)", dep)
        if match:
            pins[match.group(1).lower()] = match.group(2)
    return pins


def test_no_pin_contradicts_the_helper() -> None:
    ours = _our_pins()
    theirs = _helper_pins()
    assert theirs, f"{HELPER} no longer pins anything exactly"

    clashes = {
        name: (ours[name], theirs[name.lower()])
        for name in ours
        if name.lower() in theirs and ours[name] != theirs[name.lower()]
    }
    assert not clashes, f"pins disagree with {HELPER}: {clashes}"


def test_pinned_homeassistant_is_the_installed_one() -> None:
    assert _our_pins()["homeassistant"] == version("homeassistant")
