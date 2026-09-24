"""The integration set up in Home Assistant, against the stand-in portal."""

from __future__ import annotations

import aiohttp
import pytest
from fake_portal import METER, PASSWORD, USERNAME
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ibok.api import IbokApi
from custom_components.ibok.const import (
    CONF_BASE_URL,
    CONF_SOURCE_ENTITY_PREFIX,
    DOMAIN,
)

SERIAL = METER["numer_fabryczny"]
GARDEN = "87654321"


def _readouts(*serials: str) -> list[dict]:
    return [
        {"numer_fabryczny": s, "odczyty": [{"do": "2026-04-06", "sl": "48", "zu": "8"}]}
        for s in serials
    ]


@pytest.fixture
async def setup(hass, enable_custom_integrations, portal, monkeypatch):
    """Returns a function that sets the entry up once the portal is prepared."""
    # The integration's own cookie jar refuses cookies from a bare IP address,
    # which is all the stand-in portal has.
    monkeypatch.setattr(
        "custom_components.ibok.IbokApi",
        lambda base, user, password: IbokApi(
            base,
            user,
            password,
            session=aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)),
        ),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ibok.przyklad.pl",
        data={
            CONF_BASE_URL: portal.base,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
        },
        options={f"{CONF_SOURCE_ENTITY_PREFIX}{SERIAL}": "sensor.woda"},
    )
    entry.add_to_hass(hass)

    async def _setup() -> MockConfigEntry:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    yield _setup

    if entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


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
