"""Translations must cover exactly the same keys as strings.json.

A missing key does not fail anywhere at runtime -- Home Assistant quietly shows
the raw key instead, which is the kind of defect nobody reports.
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "ibok"


def _keys(obj, prefix: str = "") -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}/{key}"
            found.add(path)
            found |= _keys(value, path)
    return found


def test_translations_match_strings() -> None:
    expected = _keys(json.loads((ROOT / "strings.json").read_text(encoding="utf-8")))
    for path in sorted((ROOT / "translations").glob("*.json")):
        actual = _keys(json.loads(path.read_text(encoding="utf-8")))
        assert actual == expected, (
            f"{path.name} differs from strings.json: "
            f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}"
        )


def test_polish_translation_exists() -> None:
    assert (ROOT / "translations" / "pl.json").is_file()
