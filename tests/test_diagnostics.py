"""Diagnostics show what the portal sends without showing anyone's data."""

from __future__ import annotations

import json

from fake_portal import METER, PASSWORD, USERNAME

from custom_components.ibok.api import IbokConnectionError
from custom_components.ibok.diagnostics import (
    async_get_config_entry_diagnostics,
    mask,
)

SERIAL = METER["numer_fabryczny"]


def test_masking_keeps_the_shape_and_hides_the_values() -> None:
    value = {
        "a": "Ab 12,5",
        "b": [1, 2, 3, 4, 5],
        "c": None,
        "d": True,
        "12345678": "x",
    }

    assert mask(value) == {
        "a": "xx 99,9",
        "b": ["9", "9", "9", "... 2 more"],
        "c": None,
        "d": True,
        # A key with the serial in it is data, not a field name.
        "99999999": "x",
    }


async def test_diagnostics_hide_every_value(hass, portal, setup) -> None:
    portal.readouts = [
        {"numer_fabryczny": SERIAL, "odczyty": [{"do": "2026-04-06", "sl": "48"}]}
    ]
    portal.meters = [{"numer_fabr": SERIAL, "leg_do": "2031-12-31"}]
    portal.invoices = [
        {
            "nw": "2026-04-10",
            "brutto": "150,00",
            "pw": [{"poz": [{"pn": "Dostawa wody", "pj": "m3", "pc": "6,00"}]}],
        }
    ]
    entry = await setup()

    result = await async_get_config_entry_diagnostics(hass, entry)

    dumped = json.dumps(result, ensure_ascii=False)
    for value in (
        USERNAME,
        PASSWORD,
        SERIAL,
        "2031-12-31",
        "150,00",
        "Dostawa wody",
        "sensor.woda",
    ):
        assert value not in dumped
    # What a fix depends on is still there: the fields and how they are written.
    assert result["data"]["meters"][0]["leg_do"] == "9999-99-99"
    assert result["data"]["invoices"][0]["brutto"] == "999,99"
    assert result["entry"]["options"] == {"source_entity_99999999": "xxxxxx.xxxx"}
    assert result["portal_modules"] == ["Meters_v1"]
    assert result["failed_modules"] == []


async def test_diagnostics_do_not_need_the_portal_to_answer(
    hass, portal, setup, monkeypatch
) -> None:
    entry = await setup()

    async def _refused():
        raise IbokConnectionError("connection refused")

    monkeypatch.setattr(entry.runtime_data.api, "async_menu", _refused)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["portal_modules"] == "not available: IbokConnectionError"
    assert result["data"] is not None
