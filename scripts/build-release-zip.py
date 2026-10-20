#!/usr/bin/env python3
"""Build the release asset HACS installs: ibok.zip.

With ``zip_release`` in hacs.json, HACS downloads this asset from the release and unpacks
it straight into ``custom_components/ibok/``, so the integration's files go at the root of
the archive. Entries are sorted and carry a fixed timestamp, so the same tag always gives
the same bytes and a published asset can be checked against a rebuild.

Usage: build-release-zip.py OUTPUT [--source DIR]
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / "custom_components" / "ibok"
# The earliest timestamp a zip entry can hold.
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def _included(path: Path, source: Path) -> bool:
    relative = path.relative_to(source)
    return (
        path.is_file() and "__pycache__" not in relative.parts and path.suffix != ".pyc"
    )


def build(source: Path, output: Path) -> list[str]:
    files = sorted(
        (path for path in source.rglob("*") if _included(path, source)),
        key=lambda path: path.relative_to(source).as_posix(),
    )
    if not (source / "manifest.json").is_file():
        raise SystemExit(
            f"{source} has no manifest.json - not an integration directory"
        )

    names = []
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            name = path.relative_to(source).as_posix()
            entry = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, path.read_bytes())
            names.append(name)
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()

    names = build(args.source, args.output)
    print(f"{args.output}: {len(names)} files")


if __name__ == "__main__":
    main()
