"""The reading-submission event fires once per change of the form's submission."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.ibok.const import DOMAIN, SERVICE_SUBMIT_READING
from custom_components.ibok.seen import SeenStore, async_remove_seen

SUBMITTED = {"iid": "9001", "ist": "4", "isl": "48.0", "ido": "2026-04-06"}


def _entity_id(hass, entry) -> str:
    unique_id = f"{entry.entry_id}_12345678_submission"
    entity_id = er.async_get(hass).async_get_entity_id("event", DOMAIN, unique_id)
    assert entity_id
    return entity_id


def _store(hass_storage, entry) -> dict:
    return hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]


async def _flush(hass) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()


async def _refresh(hass, entry) -> None:
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()


async def test_first_run_announces_nothing(hass, hass_storage, portal, setup) -> None:
    portal.notify[0].update(SUBMITTED)
    entry = await setup()

    assert hass.states.get(_entity_id(hass, entry)).state == "unknown"
    assert entry.runtime_data.seen.submission("12345678") == ("9001", 4)
    await _flush(hass)
    assert _store(hass_storage, entry)["submissions"] == {"12345678": ["9001", 4]}


async def test_a_reading_sent_from_home_assistant_fires_waiting(
    hass, portal, setup
) -> None:
    # An earlier submission is the silent baseline the new one is compared with.
    portal.notify[0].update(iid="8999", ist="4")
    entry = await setup()
    entity_id = _entity_id(hass, entry)
    assert hass.states.get(entity_id).state == "unknown"

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUBMIT_READING,
        {"reading": 48, "reading_date": "2026-04-06"},
        blocking=True,
    )
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state != "unknown"
    assert state.attributes["event_type"] == "waiting"
    assert state.attributes["reading"] == 48.0
    assert state.attributes["reading_date"] == "2026-04-06"
    assert state.attributes["reason"] is None


async def test_a_status_change_fires_once(hass, portal, setup) -> None:
    portal.notify[0].update(SUBMITTED, ist="1")
    entry = await setup()
    entity_id = _entity_id(hass, entry)

    portal.notify[0].update(ist="3", tkom=" Odczyt niezgodny z poprzednim ")
    await _refresh(hass, entry)

    state = hass.states.get(entity_id)
    assert state.attributes["event_type"] == "rejected"
    assert state.attributes["reason"] == "Odczyt niezgodny z poprzednim"
    changed = state.last_changed

    await _refresh(hass, entry)
    assert hass.states.get(entity_id).last_changed == changed


async def test_a_change_during_a_restart_fires_once_and_an_unchanged_form_not_at_all(
    hass, portal, setup
) -> None:
    portal.notify[0].update(SUBMITTED, ist="1")
    entry = await setup()
    entity_id = _entity_id(hass, entry)
    assert hass.states.get(entity_id).state == "unknown"

    portal.notify[0].update(ist="4")
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state.attributes["event_type"] == "approved"
    fired = state.state

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    # The event's state is its timestamp: unchanged means nothing fired again.
    assert hass.states.get(entity_id).state == fired


async def test_a_failed_reading_form_fires_nothing(hass, portal, setup) -> None:
    portal.notify[0].update(SUBMITTED, ist="1")
    entry = await setup()
    entity_id = _entity_id(hass, entry)

    portal.notify[0].update(ist="3")
    portal.broken.add("NotifyReadout_v1")
    await _refresh(hass, entry)

    assert hass.states.get(entity_id).state == "unavailable"
    assert entry.runtime_data.seen.submission("12345678") == ("9001", 1)


async def test_a_row_without_a_submission_is_ignored(
    hass, hass_storage, portal, setup
) -> None:
    entry = await setup()
    await _flush(hass)

    assert hass.states.get(_entity_id(hass, entry)).state == "unknown"
    assert entry.runtime_data.seen.submission("12345678") is None
    assert entry.runtime_data.seen.knows("12345678")
    assert _store(hass_storage, entry)["submissions"] == {"12345678": None}


async def test_the_first_submission_on_a_meter_fires_waiting(
    hass, portal, setup
) -> None:
    entry = await setup()
    entity_id = _entity_id(hass, entry)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SUBMIT_READING,
        {"reading": 48, "reading_date": "2026-04-06"},
        blocking=True,
    )
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).attributes["event_type"] == "waiting"


async def test_unknown_keys_of_the_store_survive(hass, hass_storage) -> None:
    hass_storage[f"{DOMAIN}.abc"] = {
        "version": 1,
        "key": f"{DOMAIN}.abc",
        "data": {"announcements": {"x": 1}, "submissions": {"1": ["7", 2]}},
    }
    seen = SeenStore(hass, "abc")
    await seen.async_load()
    assert seen.submission("1") == ("7", 2)

    seen.set_submission("2", ("8", 3))
    await _flush(hass)

    assert hass_storage[f"{DOMAIN}.abc"]["data"] == {
        "announcements": {"x": 1},
        "submissions": {"1": ["7", 2], "2": ["8", 3]},
    }

    await async_remove_seen(hass, "abc")
    assert f"{DOMAIN}.abc" not in hass_storage
