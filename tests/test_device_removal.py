"""Removing the device of a meter the portal no longer lists."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest
from fake_portal import METER
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from custom_components.ibok import async_remove_config_entry_device
from custom_components.ibok.const import (
    CONF_REPLACEMENT_CONFIRMED_PREFIX,
    CONF_SOURCE_ENTITY_PREFIX,
    DOMAIN,
)
from custom_components.ibok.ledger import SubmissionLedger, issue_id
from custom_components.ibok.replacement import replacement_issue_id

SERIAL = METER["numer_fabryczny"]
GARDEN = "87654321"
GARDEN_ID = 10002


def _readouts(*serials: str) -> list[dict]:
    return [
        {"numer_fabryczny": s, "odczyty": [{"do": "2026-04-06", "sl": "48", "zu": "8"}]}
        for s in serials
    ]


def _notify(*serials: str) -> list[dict]:
    rows = {SERIAL: dict(METER), GARDEN: {**METER, "id_wodom": GARDEN_ID}}
    return [{**rows[s], "numer_fabryczny": s} for s in serials]


def _meters(*serials: str) -> list[dict]:
    return [{"numer_fabr": s} for s in serials]


async def _poll(hass, entry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


def _device(hass, entry, identifier: str):
    """The entry's device with this identifier; the lookup by identifier alone
    is deprecated, and the entry's own device list works on every version."""
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        if (DOMAIN, identifier) in device.identifiers:
            return device
    return None


def _meter_device(hass, entry, serial: str):
    return _device(hass, entry, f"{entry.entry_id}_meter_{serial}")


@pytest.fixture
async def two_meters(hass, portal, setup):
    """An account with both meters, each reading from a source entity."""
    portal.readouts = _readouts(SERIAL, GARDEN)
    portal.notify = _notify(SERIAL, GARDEN)
    portal.meters = _meters(SERIAL, GARDEN)
    entry = await setup(
        {
            f"{CONF_SOURCE_ENTITY_PREFIX}{SERIAL}": "sensor.woda",
            f"{CONF_SOURCE_ENTITY_PREFIX}{GARDEN}": "sensor.ogrod",
            f"{CONF_REPLACEMENT_CONFIRMED_PREFIX}{GARDEN}": "2026-01-01",
        }
    )
    assert _meter_device(hass, entry, GARDEN) is not None
    return entry


async def _refused(hass, entry, device) -> str:
    """The translation key of the refusal; the device must be left as it is."""
    with pytest.raises(ServiceValidationError) as refused:
        await async_remove_config_entry_device(hass, entry, device)
    assert refused.value.translation_domain == DOMAIN
    return refused.value.translation_key


def _gone(portal) -> None:
    portal.readouts = _readouts(SERIAL)
    portal.notify = _notify(SERIAL)
    portal.meters = _meters(SERIAL)


async def test_a_meter_gone_from_the_portal_can_be_removed(
    hass, portal, two_meters
) -> None:
    entry = two_meters
    device = _meter_device(hass, entry, GARDEN)
    _gone(portal)
    await _poll(hass, entry)

    assert await async_remove_config_entry_device(hass, entry, device) is True
    await hass.async_block_till_done()

    assert f"{CONF_SOURCE_ENTITY_PREFIX}{GARDEN}" not in entry.options
    assert f"{CONF_REPLACEMENT_CONFIRMED_PREFIX}{GARDEN}" not in entry.options
    assert f"{CONF_SOURCE_ENTITY_PREFIX}{SERIAL}" in entry.options


async def test_a_meter_still_on_the_portal_cannot_be_removed(
    hass, portal, two_meters
) -> None:
    entry = two_meters
    device = _meter_device(hass, entry, GARDEN)
    await _poll(hass, entry)

    assert await _refused(hass, entry, device) == "remove_meter_on_portal"
    assert f"{CONF_SOURCE_ENTITY_PREFIX}{GARDEN}" in entry.options


async def test_a_meter_only_on_the_reading_form_cannot_be_removed(
    hass, portal, two_meters
) -> None:
    entry = two_meters
    device = _meter_device(hass, entry, GARDEN)
    portal.readouts = _readouts(SERIAL)
    portal.meters = _meters(SERIAL)
    await _poll(hass, entry)

    assert await _refused(hass, entry, device) == "remove_meter_on_portal"


async def test_the_account_device_cannot_be_removed(hass, portal, two_meters) -> None:
    entry = two_meters
    account = _device(hass, entry, entry.entry_id)
    assert account is not None
    _gone(portal)
    await _poll(hass, entry)

    assert await _refused(hass, entry, account) == "remove_account_device"


@pytest.mark.parametrize(
    ("module", "key"),
    [
        ("Readouts_v1", "readouts"),
        ("NotifyReadout_v1", "notify"),
        ("Meters_v1", "meters"),
    ],
)
async def test_no_removal_while_a_module_failed(
    hass, portal, two_meters, module, key
) -> None:
    """The coordinator keeps a failed module's old data, so absence proves nothing."""
    entry = two_meters
    device = _meter_device(hass, entry, GARDEN)
    _gone(portal)
    portal.broken = {module}
    await _poll(hass, entry)

    assert key in entry.runtime_data.failed
    assert await _refused(hass, entry, device) == "remove_meter_unknown"
    assert f"{CONF_SOURCE_ENTITY_PREFIX}{GARDEN}" in entry.options


@pytest.mark.parametrize("key", ["readouts", "notify", "meters"])
async def test_no_removal_while_a_module_has_no_data(
    hass, portal, two_meters, key
) -> None:
    entry = two_meters
    device = _meter_device(hass, entry, GARDEN)
    _gone(portal)
    await _poll(hass, entry)
    entry.runtime_data.data[key] = None

    assert await _refused(hass, entry, device) == "remove_meter_unknown"
    assert f"{CONF_SOURCE_ENTITY_PREFIX}{GARDEN}" in entry.options


async def test_no_removal_while_the_account_is_not_loaded(
    hass, portal, two_meters
) -> None:
    entry = two_meters
    device = _meter_device(hass, entry, GARDEN)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert await _refused(hass, entry, device) == "remove_meter_unknown"


async def test_no_removal_when_the_portal_listed_no_meter_at_all(
    hass, portal, two_meters
) -> None:
    """An empty answer is no failure, yet it does not show the meter left."""
    entry = two_meters
    device = _meter_device(hass, entry, GARDEN)
    portal.readouts, portal.notify, portal.meters = [], [], []
    await _poll(hass, entry)
    assert entry.runtime_data.data["readouts"] == []
    options = dict(entry.options)

    assert await _refused(hass, entry, device) == "remove_meter_none_listed"
    assert dict(entry.options) == options


async def test_a_device_of_another_kind_is_left_to_home_assistant(
    hass, portal, two_meters
) -> None:
    entry = two_meters
    stranger = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("other", "x")}
    )

    assert await async_remove_config_entry_device(hass, entry, stranger) is False


async def test_the_refusal_reaches_the_user_through_the_websocket(
    hass, portal, two_meters, hass_ws_client
) -> None:
    assert await async_setup_component(hass, "config", {})
    entry = two_meters
    device = _meter_device(hass, entry, GARDEN)
    client = await hass_ws_client()
    if HA_VERSION.startswith("2026.1."):
        command = {
            "type": "config/device_registry/remove_config_entry",
            "config_entry_id": entry.entry_id,
            "device_id": device.id,
        }
    else:
        command = {"type": "config/device_registry/remove", "device_id": device.id}

    await client.send_json_auto_id(command)
    reply = await client.receive_json()

    assert reply["success"] is False
    assert reply["error"]["translation_key"] == "remove_meter_on_portal"
    assert reply["error"]["translation_domain"] == DOMAIN
    assert GARDEN in reply["error"]["message"]
    assert "rejected by integration" not in reply["error"]["message"]
    assert _meter_device(hass, entry, GARDEN) is not None


async def test_removal_without_options_to_drop_leaves_the_entry_alone(
    hass, portal, setup
) -> None:
    portal.readouts = _readouts(SERIAL, GARDEN)
    portal.notify = _notify(SERIAL, GARDEN)
    portal.meters = _meters(SERIAL, GARDEN)
    options = {f"{CONF_SOURCE_ENTITY_PREFIX}{SERIAL}": "sensor.woda"}
    entry = await setup(options)
    device = _meter_device(hass, entry, GARDEN)
    _gone(portal)
    await _poll(hass, entry)
    before = entry.options

    with patch.object(
        hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
    ) as reload:
        assert await async_remove_config_entry_device(hass, entry, device) is True
        await hass.async_block_till_done()

    assert entry.options is before
    reload.assert_not_called()


async def test_removal_clears_the_meters_open_repairs(hass, portal, two_meters) -> None:
    entry = two_meters
    coordinator = entry.runtime_data
    device = _meter_device(hass, entry, GARDEN)
    day = date(2026, 4, 6)
    coordinator.ledger.record_unknown(
        GARDEN_ID,
        GARDEN,
        48.0,
        day,
        day,
        datetime(2026, 4, 6, 12, 0, tzinfo=dt_util.UTC),
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        replacement_issue_id(entry.entry_id, GARDEN),
        is_fixable=True,
        is_persistent=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="meter_replaced",
        data={
            "entry_id": entry.entry_id,
            "serial": GARDEN,
            "replaced_on": "2026-01-01",
        },
    )
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, issue_id(entry.entry_id, GARDEN_ID))
    _gone(portal)
    await _poll(hass, entry)

    assert await async_remove_config_entry_device(hass, entry, device) is True
    await hass.async_block_till_done()

    ledger = entry.runtime_data.ledger
    assert ledger.pending(GARDEN_ID) is None
    assert registry.async_get_issue(DOMAIN, issue_id(entry.entry_id, GARDEN_ID)) is None
    assert (
        registry.async_get_issue(DOMAIN, replacement_issue_id(entry.entry_id, GARDEN))
        is None
    )


def test_forget_serial_drops_only_that_meter() -> None:
    ledger = SubmissionLedger(None, "e")
    day = date(2026, 4, 6)
    at = datetime(2026, 4, 6, 12, 0, tzinfo=dt_util.UTC)
    ledger.record_unknown(1, "A", 48.0, day, day, at)
    ledger.record_unknown(2, "B", 48.0, day, day, at)
    ledger.record_sent(3, "A", 50.0, day, day)
    ledger.record_sent(4, "B", 50.0, day, day)

    ledger.forget_serial("A")

    assert ledger.pending(1) is None
    assert ledger.pending(2) is not None
    # Sent memory, through what it refuses: B's repeat still, A's no longer.
    row_a = {"id_wodom": 3, "numer_fabryczny": "A"}
    row_b = {"id_wodom": 4, "numer_fabryczny": "B"}
    ledger.check(row_a, 50.0, day, day)
    with pytest.raises(ServiceValidationError) as refused:
        ledger.check(row_b, 50.0, day, day)
    assert refused.value.translation_key == "reading_already_sent"


async def test_forget_serial_sweeps_issues_missing_from_memory(hass) -> None:
    """After a restart the issue can outlive any entry in the ledger's memory."""
    day = date(2026, 4, 6)
    at = datetime(2026, 4, 6, 12, 0, tzinfo=dt_util.UTC)
    before_restart = SubmissionLedger(hass, "e")
    before_restart.record_unknown(1, "A", 48.0, day, day, at)
    before_restart.record_unknown(2, "B", 48.0, day, day, at)
    registry = ir.async_get(hass)

    after_restart = SubmissionLedger(hass, "e")
    assert after_restart.pending(1) is None

    after_restart.forget_serial("A")

    assert registry.async_get_issue(DOMAIN, issue_id("e", 1)) is None
    assert registry.async_get_issue(DOMAIN, issue_id("e", 2)) is not None
