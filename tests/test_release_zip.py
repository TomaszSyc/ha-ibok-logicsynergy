"""ibok.zip is what HACS downloads and unpacks straight into custom_components/ibok/.

With ``zip_release`` in hacs.json, HACS fetches the release asset named in ``filename``
and extracts it into the integration directory. The archive therefore has to hold the
integration's files at its root: one that wraps them in ``custom_components/ibok/``
installs a nested copy Home Assistant never loads. The asset's download count is also
the only usage figure the repository gets, so every release has to carry it.

Run as a subprocess, the way the release workflow runs it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build-release-zip.py"
SOURCE = ROOT / "custom_components" / "ibok"
ASSET = "ibok.zip"


def _build(target: Path, *extra: str) -> list[str]:
    done = subprocess.run(
        [sys.executable, str(SCRIPT), str(target), *extra],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    with zipfile.ZipFile(target) as archive:
        return archive.namelist()


def _files(directory: Path) -> set[str]:
    return {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    }


def test_the_integration_files_sit_at_the_root_of_the_zip(tmp_path: Path) -> None:
    names = _build(tmp_path / ASSET)

    assert "manifest.json" in names
    assert not [name for name in names if name.startswith("custom_components/")]


def test_the_zip_carries_every_file_of_the_integration(tmp_path: Path) -> None:
    names = set(_build(tmp_path / ASSET))

    assert names == _files(SOURCE)
    assert {"brand/icon.png", "brand/icon@2x.png", "translations/pl.json"} <= names


def test_bytecode_caches_stay_out(tmp_path: Path) -> None:
    source = tmp_path / "ibok"
    shutil.copytree(
        SOURCE, source, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "sensor.cpython-314.pyc").write_bytes(b"\0")
    (source / "stray.pyc").write_bytes(b"\0")

    names = _build(tmp_path / ASSET, "--source", str(source))

    assert not [
        name for name in names if "__pycache__" in name or name.endswith(".pyc")
    ]


def test_the_zip_manifest_is_the_release_version(tmp_path: Path) -> None:
    target = tmp_path / ASSET
    _build(target)

    with zipfile.ZipFile(target) as archive:
        packed = json.loads(archive.read("manifest.json"))
    assert (
        packed["version"]
        == json.loads((SOURCE / "manifest.json").read_text())["version"]
    )


def test_building_twice_gives_the_same_archive(tmp_path: Path) -> None:
    # Fixed entry order and timestamps: the asset can be rebuilt from the tag and compared.
    first, second = tmp_path / "a" / ASSET, tmp_path / "b" / ASSET
    first.parent.mkdir()
    second.parent.mkdir()
    _build(first)
    _build(second)

    assert first.read_bytes() == second.read_bytes()


def test_hacs_json_points_at_the_zip_the_release_builds() -> None:
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))

    assert hacs["zip_release"] is True
    assert hacs["filename"] == ASSET


def test_the_release_workflow_builds_and_attaches_the_zip() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )

    assert f"scripts/build-release-zip.py {ASSET}" in workflow
    create = [line for line in workflow.splitlines() if "gh release create" in line]
    assert create, "release workflow no longer creates the release"
    assert all(ASSET in line for line in create), create
