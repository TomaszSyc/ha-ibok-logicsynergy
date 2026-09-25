"""The integration set up in Home Assistant, against the stand-in portal."""

from __future__ import annotations

from fake_portal import METER
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er

from custom_components.ibok.const import DOMAIN

SERIAL = METER["numer_fabryczny"]
GARDEN = "87654321"


def _readouts(*serials: str) -> list[dict]:
    return [
        {"numer_fabryczny": s, "odczyty": [{"do": "2026-04-06", "sl": "48", "zu": "8"}]}
        for s in serials
    ]


def _entity_id(hass, platform: str, entry, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )


async def _poll(hass, entry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_a_meter_listed_later_gets_its_sensors(hass, portal, setup) -> None:
    """A replaced meter comes back under a new serial; no restart needed."""
    portal.readouts = _readouts(SERIAL)
    entry = await setup()
    assert _entity_id(hass, "sensor", entry, f"{GARDEN}_last_reading") is None

    portal.readouts = _readouts(SERIAL, GARDEN)
    await _poll(hass, entry)

    entity_id = _entity_id(hass, "sensor", entry, f"{GARDEN}_last_reading")
    assert entity_id is not None
    assert hass.states.get(entity_id).state == "48.0"


async def test_a_failing_module_takes_down_only_its_entities(
    hass, portal, setup
) -> None:
    portal.readouts = _readouts(SERIAL)
    portal.invoices = [{"nw": "2026-04-12", "brutto": "150,00"}]
    entry = await setup()

    portal.broken = {"Invoices_v1"}
    await _poll(hass, entry)

    def state(platform: str, key: str) -> str:
        return hass.states.get(_entity_id(hass, platform, entry, key)).state

    assert state("sensor", "last_invoice") == STATE_UNAVAILABLE
    assert state("sensor", "balance") != STATE_UNAVAILABLE
    assert state("sensor", f"{SERIAL}_last_reading") == "48.0"
    # Pressing it asks the portal again anyway.
    assert state("button", f"{SERIAL}_submit") != STATE_UNAVAILABLE


async def test_the_button_appears_when_its_meter_does(hass, portal, setup) -> None:
    portal.notify = []
    entry = await setup()
    assert _entity_id(hass, "button", entry, f"{SERIAL}_submit") is None

    portal.notify = [dict(METER)]
    await _poll(hass, entry)

    assert _entity_id(hass, "button", entry, f"{SERIAL}_submit") is not None


async def test_one_meter_is_one_device_however_its_serial_is_spaced(
    hass, portal, setup
) -> None:
    """The sensors read the serial from one module, the button from another."""
    portal.readouts = _readouts(SERIAL)
    portal.notify = [{**METER, "numer_fabryczny": f" {SERIAL} "}]
    entry = await setup()

    registry = er.async_get(hass)
    sensor = registry.async_get(
        _entity_id(hass, "sensor", entry, f"{SERIAL}_last_reading")
    )
    button = registry.async_get(_entity_id(hass, "button", entry, f"{SERIAL}_submit"))
    assert button.device_id == sensor.device_id


async def test_the_button_survives_a_failed_poll_of_the_reading_form(
    hass, portal, setup
) -> None:
    """A press fetches the form again before sending, so one failed poll is moot."""
    entry = await setup()

    portal.broken = {"NotifyReadout_v1"}
    await _poll(hass, entry)

    entity_id = _entity_id(hass, "button", entry, f"{SERIAL}_submit")
    assert hass.states.get(entity_id).state != STATE_UNAVAILABLE


METER_ROW = {
    "numer_fabr": SERIAL,
    "procent_w": "100",
    "procent_k": "100",
    "leg_do": "2031-12-31",
    "ew_prz": "2025-06-01",
}
PRICED_INVOICE = {
    "nw": "2026-04-10",
    "nt": "2026-04-24",
    "brutto": "150,00",
    "pw": [
        {
            "poz": [
                {"pn": "Dostawa wody", "pj": "m3", "pc": "6,00", "pt": "8%"},
                {"pn": "Odprowadzanie ścieków", "pj": "m3", "pc": "10,00", "pt": "8%"},
            ]
        }
    ],
}


async def test_each_meter_gets_its_price_and_legalisation(hass, portal, setup) -> None:
    portal.readouts = _readouts(SERIAL)
    portal.meters = [METER_ROW]
    portal.invoices = [PRICED_INVOICE]
    entry = await setup()
    registry = er.async_get(hass)

    def entity(key: str):
        entity_id = _entity_id(hass, "sensor", entry, key)
        return registry.async_get(entity_id), hass.states.get(entity_id)

    reading, _ = entity(f"{SERIAL}_last_reading")
    price, price_state = entity(f"{SERIAL}_price")
    legalised, legalised_state = entity(f"{SERIAL}_legalised_until")
    _, due_state = entity("payment_due")

    assert price_state.state == "17.28"
    assert price_state.attributes["unit_of_measurement"] == f"{hass.config.currency}/m³"
    assert legalised_state.state == "2031-12-31"
    assert due_state.state == "2026-04-24"
    # One device per meter, whichever module a sensor reads from.
    assert price.device_id == legalised.device_id == reading.device_id


async def test_the_price_depends_on_the_meter_list_as_well(hass, portal, setup) -> None:
    """Without the meter list the shares are unknown, so the price is too."""
    portal.readouts = _readouts(SERIAL)
    portal.meters = [METER_ROW]
    portal.invoices = [PRICED_INVOICE]
    entry = await setup()

    portal.broken = {"Meters_v1"}
    await _poll(hass, entry)

    def state(key: str) -> str:
        return hass.states.get(_entity_id(hass, "sensor", entry, key)).state

    assert state(f"{SERIAL}_price") == STATE_UNAVAILABLE
    assert state(f"{SERIAL}_last_reading") == "48.0"
