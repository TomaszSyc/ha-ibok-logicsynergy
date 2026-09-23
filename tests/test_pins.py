"""The two Home Assistant pins in requirements-dev.txt have to agree.

pytest-homeassistant-custom-component pins exactly one Home Assistant release.
If requirements-dev.txt names a different one, pip resolves it silently and the
test suite then runs against a version nobody chose.
"""

import re
from importlib.metadata import requires, version
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements-dev.txt"


def _pinned(package: str) -> str | None:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(package)}==([^\s#]+)", text, re.MULTILINE)
    return match.group(1) if match else None


def test_requirements_pin_matches_installed_homeassistant() -> None:
    assert _pinned("homeassistant") == version("homeassistant")


def test_helper_pins_the_same_homeassistant() -> None:
    deps = requires("pytest-homeassistant-custom-component") or []
    wanted = next((d for d in deps if d.lower().startswith("homeassistant==")), None)
    assert wanted, "pytest-homeassistant-custom-component no longer pins homeassistant"
    assert wanted.split("==", 1)[1].strip() == version("homeassistant")
