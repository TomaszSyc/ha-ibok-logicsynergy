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


def test_entity_names_have_no_unfilled_placeholders() -> None:
    """A `{placeholder}` in an entity name needs translation_placeholders.

    Without them Home Assistant renders the braces literally, so the entity
    shows up as "Wodomierz {serial} - stan". Nothing logs a warning.
    """
    # Specifically the entity attribute: the `translation_placeholders=` keyword
    # that a translated exception takes would otherwise satisfy this check while
    # entity names stayed broken.
    sources = "\n".join(path.read_text(encoding="utf-8") for path in ROOT.glob("*.py"))
    declared = "_attr_translation_placeholders" in sources

    for path in [
        ROOT / "strings.json",
        *sorted((ROOT / "translations").glob("*.json")),
    ]:
        entity = json.loads(path.read_text(encoding="utf-8")).get("entity", {})
        for platform, keys in entity.items():
            for key, fields in keys.items():
                name = fields.get("name", "")
                if "{" in name and not declared:
                    raise AssertionError(
                        f"{path.name}: {platform}/{key} name {name!r} has a "
                        "placeholder but no entity sets translation_placeholders"
                    )


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _strings(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _strings(value)


def test_no_angle_brackets_in_translations() -> None:
    """The frontend renders these as markdown, where `<name>` is an open tag.

    A placeholder written that way does not show up as text: the field renders
    "Translation error: UNCLOSED_TAG" instead of its description.
    """
    for path in [
        ROOT / "strings.json",
        *sorted((ROOT / "translations").glob("*.json")),
    ]:
        for text in _strings(json.loads(path.read_text(encoding="utf-8"))):
            assert "<" not in text, f"{path.name}: angle bracket in {text!r}"


def test_no_urls_in_translations() -> None:
    """hassfest rejects a URL in a string; it belongs in description_placeholders.

    The integration fails Home Assistant's own manifest validation over this,
    which is what HACS and any later submission run.
    """
    for path in [
        ROOT / "strings.json",
        *sorted((ROOT / "translations").glob("*.json")),
    ]:
        for text in _strings(json.loads(path.read_text(encoding="utf-8"))):
            assert "http://" not in text and "https://" not in text, (
                f"{path.name}: URL in {text!r}"
            )


def test_hacs_json_has_only_known_keys() -> None:
    """HACS validates hacs.json against a strict schema.

    An unknown key -- `render_readme` was one, and was removed from the schema --
    makes the whole file invalid, and HACS then cannot locate the integration.
    """
    znane = {
        "name",
        "content_in_root",
        "zip_release",
        "filename",
        "hide_default_branch",
        "country",
        "homeassistant",
        "hacs",
        "persistent_directory",
    }
    hacs = json.loads((ROOT.parent.parent / "hacs.json").read_text(encoding="utf-8"))
    assert hacs.get("name"), "hacs.json must name the repository"
    assert set(hacs) <= znane, f"unknown hacs.json keys: {sorted(set(hacs) - znane)}"
