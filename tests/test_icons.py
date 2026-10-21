"""Icons must stay in step with the entities and services they describe.

An icon filed under a key that no entity uses is silently ignored, and a service
left out falls back to the generic icon -- neither shows up as an error anywhere.
"""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "ibok"
ICONS = ROOT / "icons.json"
STRINGS = ROOT / "strings.json"
SERVICES = ROOT / "services.yaml"

EXPECTED = {
    ("sensor", "last_reading"): "mdi:counter",
    ("sensor", "last_invoice"): "mdi:receipt-text",
    ("sensor", "payment_due"): "mdi:calendar-clock",
    ("sensor", "legalised_until"): "mdi:calendar-check",
    ("sensor", "price"): "mdi:cash",
    ("button", "submit_reading"): "mdi:send",
    ("number", "typed_reading"): "mdi:pencil",
}


def test_entities_get_the_agreed_icons() -> None:
    icons = json.loads(ICONS.read_text(encoding="utf-8"))["entity"]
    assert {
        (p, k): v["default"] for p, keys in icons.items() for k, v in keys.items()
    } == EXPECTED


def test_every_icon_belongs_to_a_translated_entity() -> None:
    icons = json.loads(ICONS.read_text(encoding="utf-8"))["entity"]
    strings = json.loads(STRINGS.read_text(encoding="utf-8"))["entity"]
    translated = {(p, k) for p, keys in strings.items() for k in keys}
    assert {(p, k) for p, keys in icons.items() for k in keys} <= translated


def test_every_service_has_an_icon() -> None:
    services = yaml.safe_load(SERVICES.read_text(encoding="utf-8"))
    icons = json.loads(ICONS.read_text(encoding="utf-8"))["services"]
    assert services
    for name in services:
        assert icons[name]["service"].startswith("mdi:"), name
