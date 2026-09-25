"""A reading typed by hand, for a meter nobody reads automatically."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fake_portal import METER
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.ibok.api import IbokOutcomeUnknownError
from custom_components.ibok.button import typed_reading
from custom_components.ibok.const import CONF_SOURCE_ENTITY_PREFIX, DOMAIN

SERIAL = METER["numer_fabryczny"]
NOW = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)


# --- the rule ----------------------------------------------------------------


def test_nothing_typed_is_refused() -> None:
    with pytest.raises(ServiceValidationError) as info:
        typed_reading(None, NOW)

    assert info.value.translation_key == "type_reading_first"


def test_a_reading_typed_over_a_day_ago_is_refused() -> None:
    """Typed for last month and forgotten, it must not go out as this month's."""
    with pytest.raises(ServiceValidationError) as info:
        typed_reading((48.0, NOW - timedelta(hours=25)), NOW)

    assert info.value.translation_key == "typed_reading_stale"


def test_a_fresh_reading_is_taken() -> None:
    assert typed_reading((48.0, NOW - timedelta(hours=23)), NOW) == 48.0


# --- in Home Assistant -------------------------------------------------------


def _entity_id(hass, platform: str, entry, key: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{SERIAL}_{key}"
    )


async def _press(hass, entry, user) -> None:
    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": _entity_id(hass, "button", entry, "submit")},
        blocking=True,
        context=Context(user_id=user.id),
    )


async def _type(hass, entry, value: float) -> None:
    await hass.services.async_call(
        "number",
        "set_value",
        {
            "entity_id": _entity_id(hass, "number", entry, "typed_reading"),
            "value": value,
        },
        blocking=True,
    )


async def test_a_typed_reading_goes_out_on_the_second_press(
    hass, portal, setup, hass_admin_user, freezer
) -> None:
    entry = await setup(options={})
    await _type(hass, entry, 48)

    with pytest.raises(ServiceValidationError) as info:
        await _press(hass, entry, hass_admin_user)
    assert info.value.translation_key == "press_again_to_send"
    assert portal.submissions == []

    freezer.tick(timedelta(seconds=3))
    await _press(hass, entry, hass_admin_user)
    await hass.async_block_till_done()

    assert len(portal.submissions) == 1
    assert portal.submissions[0]["txtReadingNr"] == "48"
    # The field empties, so the same value cannot go out twice.
    field = _entity_id(hass, "number", entry, "typed_reading")
    assert hass.states.get(field).state == STATE_UNKNOWN


async def _send(hass, entry, user, freezer) -> None:
    with pytest.raises(ServiceValidationError):
        await _press(hass, entry, user)
    freezer.tick(timedelta(seconds=3))
    await _press(hass, entry, user)


async def test_pressing_again_after_sending_sends_nothing(
    hass, portal, setup, hass_admin_user, freezer
) -> None:
    entry = await setup(options={})
    await _type(hass, entry, 48)
    await _send(hass, entry, hass_admin_user, freezer)

    for _ in range(2):
        freezer.tick(timedelta(seconds=3))
        with pytest.raises(ServiceValidationError) as info:
            await _press(hass, entry, hass_admin_user)
        assert info.value.translation_key == "type_reading_first"

    assert len(portal.submissions) == 1


async def test_a_press_while_sending_does_not_send_again(
    hass, portal, setup, hass_admin_user, freezer, monkeypatch
) -> None:
    entry = await setup(options={})
    await _type(hass, entry, 48)
    api = entry.runtime_data.api
    submit, started, release = (
        api.async_submit_reading,
        asyncio.Event(),
        asyncio.Event(),
    )

    async def _held(**kwargs) -> None:
        started.set()
        await release.wait()
        await submit(**kwargs)

    monkeypatch.setattr(api, "async_submit_reading", _held)
    with pytest.raises(ServiceValidationError):
        await _press(hass, entry, hass_admin_user)
    freezer.tick(timedelta(seconds=3))
    sending = hass.async_create_task(_press(hass, entry, hass_admin_user))
    await started.wait()

    freezer.tick(timedelta(seconds=3))
    with pytest.raises(ServiceValidationError) as info:
        await _press(hass, entry, hass_admin_user)
    release.set()
    await sending

    assert info.value.translation_key == "type_reading_first"
    assert len(portal.submissions) == 1


async def test_a_send_that_never_reached_the_portal_keeps_the_value(
    hass, portal, setup, hass_admin_user, freezer
) -> None:
    entry = await setup(options={})
    await _type(hass, entry, 48)
    portal.broken.add("NotifyReadout_v1")

    with pytest.raises(HomeAssistantError):
        await _send(hass, entry, hass_admin_user, freezer)

    assert portal.submissions == []
    field = _entity_id(hass, "number", entry, "typed_reading")
    assert float(hass.states.get(field).state) == 48


async def test_a_send_with_an_unknown_outcome_empties_the_field(
    hass, portal, setup, hass_admin_user, freezer, monkeypatch
) -> None:
    """It may be on the bill already, so another press must not repeat it."""
    entry = await setup(options={})
    await _type(hass, entry, 48)

    async def _lost(**_kwargs) -> None:
        raise IbokOutcomeUnknownError("no answer")

    monkeypatch.setattr(entry.runtime_data.api, "async_submit_reading", _lost)
    with pytest.raises(HomeAssistantError):
        await _send(hass, entry, hass_admin_user, freezer)

    field = _entity_id(hass, "number", entry, "typed_reading")
    assert hass.states.get(field).state == STATE_UNKNOWN


async def test_not_a_number_is_refused(hass, portal, setup) -> None:
    entry = await setup(options={})

    with pytest.raises(ServiceValidationError):
        await _type(hass, entry, float("nan"))

    field = _entity_id(hass, "number", entry, "typed_reading")
    assert hass.states.get(field).state == STATE_UNKNOWN


async def test_pressing_with_nothing_typed_sends_nothing(
    hass, portal, setup, hass_admin_user
) -> None:
    entry = await setup(options={})

    with pytest.raises(ServiceValidationError) as info:
        await _press(hass, entry, hass_admin_user)

    assert info.value.translation_key == "type_reading_first"
    assert portal.submissions == []


async def test_a_reading_typed_yesterday_is_not_sent(
    hass, portal, setup, hass_admin_user, freezer
) -> None:
    entry = await setup(options={})
    await _type(hass, entry, 48)

    freezer.tick(timedelta(hours=25))
    with pytest.raises(ServiceValidationError) as info:
        await _press(hass, entry, hass_admin_user)

    assert info.value.translation_key == "typed_reading_stale"
    assert portal.submissions == []


async def test_the_field_steps_as_finely_as_the_dial(hass, portal, setup) -> None:
    portal.notify = [{**METER, "l_cyfr_p": 3}]
    entry = await setup(options={})

    field = _entity_id(hass, "number", entry, "typed_reading")
    assert hass.states.get(field).attributes["step"] == 0.001


async def test_a_meter_with_a_source_gets_no_field(hass, portal, setup) -> None:
    entry = await setup()

    assert _entity_id(hass, "number", entry, "typed_reading") is None
    assert _entity_id(hass, "button", entry, "submit") is not None


async def test_assigning_a_source_removes_the_field(hass, portal, setup) -> None:
    """A field left behind would suggest that typing there still does something."""
    entry = await setup(options={})
    assert _entity_id(hass, "number", entry, "typed_reading") is not None

    hass.config_entries.async_update_entry(
        entry, options={f"{CONF_SOURCE_ENTITY_PREFIX}{SERIAL}": "sensor.woda"}
    )
    await hass.async_block_till_done()

    assert _entity_id(hass, "number", entry, "typed_reading") is None


# --- hidden until wanted -----------------------------------------------------


def _registered(hass, entry, platform: str, key: str):
    return er.async_get(hass).async_get(_entity_id(hass, platform, entry, key))


async def test_the_typed_reading_pair_starts_hidden(hass, portal, setup) -> None:
    """Most households report on the portal and would never use the pair."""
    entry = await setup(options={})

    for platform, key in (("number", "typed_reading"), ("button", "submit")):
        hider = _registered(hass, entry, platform, key).hidden_by
        assert hider is er.RegistryEntryHider.INTEGRATION


async def test_a_button_with_a_source_shows(hass, portal, setup) -> None:
    entry = await setup()

    assert _registered(hass, entry, "button", "submit").hidden_by is None


async def test_assigning_a_source_shows_the_button(hass, portal, setup) -> None:
    entry = await setup(options={})

    hass.config_entries.async_update_entry(
        entry, options={f"{CONF_SOURCE_ENTITY_PREFIX}{SERIAL}": "sensor.woda"}
    )
    await hass.async_block_till_done()

    assert _registered(hass, entry, "button", "submit").hidden_by is None


async def test_a_button_the_user_hid_stays_hidden(hass, portal, setup) -> None:
    entry = await setup(options={})
    button = _entity_id(hass, "button", entry, "submit")
    er.async_get(hass).async_update_entity(button, hidden_by=er.RegistryEntryHider.USER)

    hass.config_entries.async_update_entry(
        entry, options={f"{CONF_SOURCE_ENTITY_PREFIX}{SERIAL}": "sensor.woda"}
    )
    await hass.async_block_till_done()

    assert er.async_get(hass).async_get(button).hidden_by is er.RegistryEntryHider.USER
