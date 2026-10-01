"""Translations must cover exactly the same keys as strings.json.

A missing key does not fail anywhere at runtime -- Home Assistant quietly shows
the raw key instead, which is the kind of defect nobody reports.
"""

import ast
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


# Keys that must be found; if the collector below stops seeing any of them, it
# has gone blind to a way keys are passed, and the check would pass on nothing.
_MUST_FIND = {
    "press_again_to_send",
    "submit_in_progress",
    "reading_already_sent",
    "invalid_reading",
    "reading_below_min",
    "meter_serial_changed",
    "precision_changed_press_again",
    "outcome_unknown",
    "nothing_recorded",
    "nothing_sent_check_failed",
    "several_meters",
    "no_account",
    "submission_refused",
}


def _functions(tree: ast.AST):
    """Every function with the parameter names it takes, in call order."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            positional = [a.arg for a in [*args.posonlyargs, *args.args]]
            yield node, positional, {a.arg for a in args.kwonlyargs}


def _translation_keys() -> tuple[set[str], list[str]]:
    """Every translation key the code raises, and every one it cannot resolve.

    Read from the syntax tree, not by pattern: a key can reach
    ``translation_key=`` directly or through a helper that forwards one of its
    own parameters, possibly through another helper. Such helpers are found,
    not listed, so one added later is covered too. Anything else -- a key built
    at runtime, a variable -- is reported as unresolved and fails the test,
    rather than being skipped.
    """
    trees = {
        path.name: ast.parse(path.read_text(encoding="utf-8"))
        for path in ROOT.glob("*.py")
    }
    used: set[str] = set()
    unresolved: list[str] = []
    # helper name -> (position of the forwarded parameter or None, its name)
    helpers: dict[str, tuple[int | None, str]] = {}

    def resolve(value: ast.expr, where: str, enclosing) -> None:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            used.add(value.value)
            return
        if enclosing is not None and isinstance(value, ast.Name):
            func, positional, keyword_only = enclosing
            if value.id in positional or value.id in keyword_only:
                index = positional.index(value.id) if value.id in positional else None
                helpers.setdefault(func.name, (index, value.id))
                return
        unresolved.append(f"{where}: {ast.unparse(value)}")

    def enclosing_of(tree):
        parents = {}
        for fn in _functions(tree):
            for child in ast.walk(fn[0]):
                parents[child] = fn  # innermost wins: nested defs come later
        return parents

    # Until no new helper turns up: each round may reveal a helper that feeds
    # another one.
    while True:
        known_helpers = dict(helpers)
        used.clear()
        unresolved.clear()
        for name, tree in trees.items():
            parents = enclosing_of(tree)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                where = f"{name}:{node.lineno}"
                for kw in node.keywords:
                    if kw.arg == "translation_key":
                        resolve(kw.value, where, parents.get(node))
                callee = getattr(node.func, "id", None) or getattr(
                    node.func, "attr", None
                )
                if callee in known_helpers:
                    index, param = known_helpers[callee]
                    arg = next(
                        (kw.value for kw in node.keywords if kw.arg == param), None
                    )
                    if arg is None and index is not None and index < len(node.args):
                        arg = node.args[index]
                    if arg is not None:
                        resolve(arg, where, parents.get(node))
        if helpers == known_helpers:
            return used, unresolved


def test_every_exception_key_is_translated() -> None:
    """A translation_key raised in code needs a message in strings.json.

    Without one the user is shown the bare key -- "meter_serial_changed" --
    instead of what went wrong, and nothing fails until someone hits it.
    """
    strings = json.loads((ROOT / "strings.json").read_text(encoding="utf-8"))
    known = set(strings.get("exceptions", {})) | set(strings.get("issues", {}))
    used, unresolved = _translation_keys()

    assert not unresolved, f"translation keys that cannot be checked: {unresolved}"
    assert _MUST_FIND <= used, f"keys no longer found: {sorted(_MUST_FIND - used)}"
    assert used <= known, f"untranslated keys: {sorted(used - known)}"


def test_announcement_texts_are_translated() -> None:
    """Texts looked up by key at runtime, which the check above cannot see.

    The notification that a reading reached the portal is no exception, so its
    keys are never passed as ``translation_key``; the module lists them.
    """
    from custom_components.ibok.submit import TRANSLATED_TEXTS

    assert TRANSLATED_TEXTS
    for path in [
        ROOT / "strings.json",
        *sorted((ROOT / "translations").glob("*.json")),
    ]:
        exceptions = json.loads(path.read_text(encoding="utf-8"))["exceptions"]
        missing = set(TRANSLATED_TEXTS) - set(exceptions)
        assert not missing, f"{path.name}: untranslated texts {sorted(missing)}"


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
