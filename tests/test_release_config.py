"""release-please owns the version: it bumps manifest.json and CHANGELOG.md in a release PR.

The release workflow publishes whatever release-please decides, so a wrong setting here
ships a wrong version to every HACS installation instead of failing loudly.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION_MANIFEST = "custom_components/ibok/manifest.json"


def _config() -> dict:
    return json.loads((ROOT / "release-please-config.json").read_text(encoding="utf-8"))


def _package() -> dict:
    return _config()["packages"]["."]


def _integration_version() -> str:
    manifest = json.loads((ROOT / INTEGRATION_MANIFEST).read_text(encoding="utf-8"))
    return manifest["version"]


def test_release_please_bumps_the_integration_manifest() -> None:
    """Home Assistant shows the manifest's version, so it must move with each release."""
    package = _package()

    assert package["release-type"] == "simple"
    assert {
        "type": "json",
        "path": INTEGRATION_MANIFEST,
        "jsonpath": "$.version",
    } in package["extra-files"]


def test_release_please_starts_from_the_installed_version() -> None:
    """A version changed by hand would make the next release PR count from the wrong one."""
    manifest = json.loads(
        (ROOT / ".release-please-manifest.json").read_text(encoding="utf-8")
    )

    assert manifest == {".": _integration_version()}


def test_releases_wait_as_drafts_with_their_tag() -> None:
    """HACS must never see a release before ibok.zip is attached to it.

    Without the tag a draft has none until it is published, and release-please then
    cannot find the previous release when it prepares the next one.
    """
    package = _package()

    assert package["draft"] is True
    assert package["force-tag-creation"] is True
    assert package["include-component-in-tag"] is False


def test_betas_continue_but_never_start_on_their_own() -> None:
    """A beta series is started and ended with Release-As; everything else stays stable."""
    package = _package()

    assert package["versioning"] == "prerelease"
    assert package["prerelease"] is True
    # A prerelease-type would turn every stable release into a beta.
    assert "prerelease-type" not in package


def test_changelog_leads_with_the_current_version() -> None:
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()

    assert lines[0] == "# Changelog"
    first = next(line for line in lines if line.startswith("## "))
    assert first.startswith(f"## [{_integration_version()}](")
