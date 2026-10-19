"""A replaced meter: what the integration does until the swap is confirmed."""

from __future__ import annotations

import pytest
from fake_portal import METER
from homeassistant.core import Context
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.ibok.const import (
    CONF_REPLACEMENT_CONFIRMED_PREFIX,
    CONF_SOURCE_ENTITY_PREFIX,
    DOMAIN,
)
from custom_components.ibok.repairs import async_create_fix_flow
from custom_components.ibok.replacement import (
    replacement_issue_id,
    sync_replacement_issues,
)

SERIAL = METER["numer_fabryczny"]
REMOVAL = "Rozliczenie wg średniej - wymontowanie wodomierza"


def _replaced() -> list[dict]:
    """The portal's readouts for a meter swapped on 2026-06-03; fresh each call."""
    return [
        {
            "numer_fabryczny": SERIAL,
            "odczyty": [
                {"do": "2026-05-04", "sl": "305"},
                {"do": "2026-06-02", "sl": "312", "typ": REMOVAL},
                {"do": "2026-06-03", "sl": "0"},
                {"do": "2026-07-01", "sl": "6"},
            ],
        }
    ]


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


async def test_a_replaced_meter_refuses_its_source(
    hass, portal, setup, hass_admin_user
):
    portal.readouts = _replaced()
    hass.states.async_set("sensor.woda", "51.0", {"unit_of_measurement": "m³"})
    entry = await setup()

    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, entry, hass_admin_user)

    assert err.value.translation_key == "meter_replaced_confirm_source"
    assert err.value.translation_placeholders == {
        "serial": SERIAL,
        "date": "2026-06-03",
    }
    assert portal.submissions == []


async def test_a_typed_reading_is_not_blocked(hass, portal, setup, hass_admin_user):
    portal.readouts = _replaced()
    entry = await setup(options={})
    await _type(hass, entry, 7)

    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, entry, hass_admin_user)

    assert err.value.translation_key == "press_again_to_send"


async def test_a_confirmed_replacement_lets_the_source_through(
    hass, portal, setup, hass_admin_user
):
    portal.readouts = _replaced()
    hass.states.async_set("sensor.woda", "51.0", {"unit_of_measurement": "m³"})
    entry = await setup(
        options={
            f"{CONF_SOURCE_ENTITY_PREFIX}{SERIAL}": "sensor.woda",
            f"{CONF_REPLACEMENT_CONFIRMED_PREFIX}{SERIAL}": "2026-06-03",
        }
    )

    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, entry, hass_admin_user)

    assert err.value.translation_key == "press_again_to_send"


def _issue(hass, entry):
    return ir.async_get(hass).async_get_issue(
        DOMAIN, replacement_issue_id(entry.entry_id, SERIAL)
    )


async def _confirm(hass, iid: str) -> None:
    issue = ir.async_get(hass).async_get_issue(DOMAIN, iid)
    flow = await async_create_fix_flow(hass, iid, issue.data)
    flow.hass = hass
    flow.issue_id = iid
    await flow.async_step_init()
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_an_unconfirmed_replacement_raises_a_fixable_issue(hass, portal, setup):
    portal.readouts = _replaced()
    entry = await setup()

    issue = _issue(hass, entry)

    assert issue.translation_key == "meter_replaced"
    # Persistent so that a restart while the portal's Readouts module fails
    # does not lose the block.
    assert issue.is_fixable and issue.is_persistent
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_placeholders == {"serial": SERIAL, "date": "2026-06-03"}


async def test_no_issue_without_a_source(hass, portal, setup):
    portal.readouts = _replaced()
    entry = await setup(options={})

    assert _issue(hass, entry) is None


async def test_confirming_stores_the_date_and_lets_the_source_through(
    hass, portal, setup, hass_admin_user
):
    portal.readouts = _replaced()
    hass.states.async_set("sensor.woda", "51.0", {"unit_of_measurement": "m³"})
    entry = await setup()

    await _confirm(hass, replacement_issue_id(entry.entry_id, SERIAL))
    await hass.async_block_till_done()

    assert entry.options[f"{CONF_REPLACEMENT_CONFIRMED_PREFIX}{SERIAL}"] == "2026-06-03"
    assert _issue(hass, entry) is None
    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, entry, hass_admin_user)
    assert err.value.translation_key == "press_again_to_send"


async def test_a_later_replacement_needs_confirming_again(hass, portal, setup):
    portal.readouts = _replaced()
    entry = await setup(
        options={
            f"{CONF_SOURCE_ENTITY_PREFIX}{SERIAL}": "sensor.woda",
            f"{CONF_REPLACEMENT_CONFIRMED_PREFIX}{SERIAL}": "2026-06-03",
        }
    )
    assert _issue(hass, entry) is None

    portal.readouts[0]["odczyty"] += [
        {"do": "2026-08-10", "sl": "0"},
        {"do": "2026-08-11", "sl": "4"},
    ]
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert _issue(hass, entry).translation_placeholders["date"] == "2026-08-10"


async def test_removing_the_entry_removes_the_issue(hass, portal, setup):
    portal.readouts = _replaced()
    entry = await setup()
    assert _issue(hass, entry) is not None

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _issue(hass, entry) is None


async def test_no_readouts_raise_nothing(hass):
    sync_replacement_issues(
        hass,
        "e1",
        {f"{CONF_SOURCE_ENTITY_PREFIX}12345678": "sensor.woda"},
        {"readouts": None},
    )

    assert not [i for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN]


async def test_a_reload_while_readouts_fail_keeps_the_issue(hass, portal, setup):
    portal.readouts = _replaced()
    entry = await setup()
    assert _issue(hass, entry) is not None

    portal.broken = {"Readouts_v1"}
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.runtime_data.data["readouts"] is None
    assert _issue(hass, entry) is not None


async def test_a_press_is_refused_while_readouts_are_unavailable(
    hass, portal, setup, hass_admin_user
):
    portal.readouts = _replaced()
    hass.states.async_set("sensor.woda", "51.0", {"unit_of_measurement": "m³"})
    entry = await setup()
    portal.broken = {"Readouts_v1"}
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, entry, hass_admin_user)

    assert err.value.translation_key == "meter_replaced_confirm_source"
    assert err.value.translation_placeholders == {
        "serial": SERIAL,
        "date": "2026-06-03",
    }
    assert portal.submissions == []


async def test_a_confirmation_settles_the_issue_while_readouts_are_unavailable(
    hass, portal, setup, hass_admin_user
):
    portal.readouts = _replaced()
    hass.states.async_set("sensor.woda", "51.0", {"unit_of_measurement": "m³"})
    entry = await setup()
    await _confirm(hass, replacement_issue_id(entry.entry_id, SERIAL))
    await hass.async_block_till_done()
    portal.broken = {"Readouts_v1"}
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError) as err:
        await _press(hass, entry, hass_admin_user)

    assert err.value.translation_key == "press_again_to_send"
