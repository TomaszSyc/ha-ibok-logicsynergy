"""The manifest and hacs.json are what HACS and Home Assistant read first."""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "ibok" / "manifest.json"


def test_manifest_has_required_keys() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for key in ("domain", "name", "version", "documentation", "codeowners"):
        assert manifest.get(key), f"manifest is missing {key}"
    assert manifest["domain"] == "ibok"


def test_manifest_declares_no_requirements() -> None:
    """Dependencies come from Home Assistant; a requirement here would be a mistake."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest.get("requirements") == []


def test_hacs_json_parses() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert hacs.get("name")


def test_version_is_semver() -> None:
    """HACS sorts releases by semver and installs the newest.

    A version it cannot parse is not treated as newer than anything, so a
    malformed number here means users silently never get the update. A beta
    carries a semver pre-release part, which sorts below the release itself.
    """
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = manifest["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+(-(beta|rc)\.\d+)?", version), version
