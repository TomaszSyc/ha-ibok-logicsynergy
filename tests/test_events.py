"""The reading-submission event fires once per change of the form's submission."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import CoreState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
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
    # Two saves follow each other (submission, announcements), and the store
    # moves the write to the later one, which a mocked clock never reaches.
    await entry.runtime_data.seen.async_flush()
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


async def _start_again(hass, entry) -> None:
    """Sets the entry up as Home Assistant's start would, before automations arm."""
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    hass.set_state(CoreState.not_running)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _started(hass) -> None:
    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()


async def test_a_change_during_a_restart_waits_until_home_assistant_has_started(
    hass, portal, setup
) -> None:
    portal.notify[0].update(SUBMITTED, ist="1")
    entry = await setup()
    entity_id = _entity_id(hass, entry)
    fired = _collect(hass, entity_id)

    portal.notify[0].update(ist="4")
    await _start_again(hass, entry)
    # Automations arm their triggers only as the start completes.
    assert fired == []
    assert entry.runtime_data.seen.submission("12345678") == ("9001", 1)
    await _refresh(hass, entry)
    assert fired == []

    await _started(hass)
    assert [f["event_type"] for f in fired] == ["approved"]


async def test_a_failed_reading_form_fires_nothing(hass, portal, setup) -> None:
    portal.notify[0].update(SUBMITTED, ist="1")
    entry = await setup()
    entity_id = _entity_id(hass, entry)

    portal.notify[0].update(ist="3")
    portal.broken.add("NotifyReadout_v1")
    await _refresh(hass, entry)

    assert hass.states.get(entity_id).state == "unavailable"
    assert entry.runtime_data.seen.submission("12345678") == ("9001", 1)

    fired = _collect(hass, entity_id)
    portal.broken.clear()
    await _refresh(hass, entry)
    assert [f["event_type"] for f in fired] == ["rejected"]


async def test_a_row_without_a_submission_is_ignored(
    hass, hass_storage, portal, setup
) -> None:
    entry = await setup()
    await entry.runtime_data.seen.async_flush()

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


def _announcement_id(hass, entry) -> str:
    unique_id = f"{entry.entry_id}_announcement"
    entity_id = er.async_get(hass).async_get_entity_id("event", DOMAIN, unique_id)
    assert entity_id
    return entity_id


def _row(title: str, short: str = "S", kind: int = 1, full: str = "F") -> dict:
    return {"type": kind, "title": title, "short": short, "full": full}


def _collect(hass, entity_id) -> list:
    fired: list = []

    def _on_change(event) -> None:
        state = event.data["new_state"]
        if state and state.attributes.get("event_type"):
            fired.append(state.attributes)

    async_track_state_change_event(hass, entity_id, _on_change)
    return fired


async def test_the_first_announcements_are_remembered_silently(
    hass, hass_storage, portal, setup
) -> None:
    portal.announcements = [_row("Przerwa w dostawie wody")]
    entry = await setup()

    assert hass.states.get(_announcement_id(hass, entry)).state == "unknown"
    keys = entry.runtime_data.seen.announcements()
    assert keys and len(keys) == 1
    await entry.runtime_data.seen.async_flush()
    assert _store(hass_storage, entry)["announcements"] == sorted(keys)


async def test_a_new_announcement_fires_with_its_text(hass, portal, setup) -> None:
    entry = await setup()
    entity_id = _announcement_id(hass, entry)
    fired = _collect(hass, entity_id)

    portal.announcements = [
        _row("Przerwa w dostawie wody", "Jutro", 0, "<p>Od 8:00<br>do 12:00</p>")
    ]
    await _refresh(hass, entry)

    state = hass.states.get(entity_id)
    assert state.attributes["event_type"] == "news"
    assert state.attributes["title"] == "Przerwa w dostawie wody"
    assert state.attributes["short"] == "Jutro"
    assert state.attributes["text"] == "Od 8:00\ndo 12:00"
    assert len(fired) == 1


async def test_several_new_announcements_fire_in_portal_order(
    hass, portal, setup
) -> None:
    portal.announcements = [_row("Stare")]
    entry = await setup()
    fired = _collect(hass, _announcement_id(hass, entry))

    portal.announcements = [_row("Stare"), _row("B", kind=0), _row("A")]
    await _refresh(hass, entry)

    assert [(f["event_type"], f["title"]) for f in fired] == [
        ("news", "B"),
        ("announcement", "A"),
    ]


async def test_the_same_announcement_twice_in_one_answer_fires_once(
    hass, portal, setup
) -> None:
    portal.announcements = [_row("Stare")]
    entry = await setup()
    fired = _collect(hass, _announcement_id(hass, entry))

    portal.announcements = [_row("Stare"), _row("A"), _row("A")]
    await _refresh(hass, entry)

    assert [f["title"] for f in fired] == ["A"]


async def test_an_unchanged_refresh_fires_nothing_and_saves_nothing(
    hass, hass_storage, portal, setup
) -> None:
    portal.announcements = [_row("Stare")]
    entry = await setup()
    await _flush(hass)
    fired = _collect(hass, _announcement_id(hass, entry))
    store = entry.runtime_data.seen
    saves = []
    store._store.async_delay_save = lambda *a, **k: saves.append(a)

    await _refresh(hass, entry)

    assert fired == []
    assert saves == []


async def test_an_announcement_taken_down_and_put_back_fires_again(
    hass, portal, setup
) -> None:
    portal.announcements = [_row("Przerwa")]
    entry = await setup()
    fired = _collect(hass, _announcement_id(hass, entry))

    portal.announcements = []
    await _refresh(hass, entry)
    assert fired == []

    portal.announcements = [_row("Przerwa")]
    await _refresh(hass, entry)
    assert [f["title"] for f in fired] == ["Przerwa"]


async def test_a_failed_module_fires_nothing_and_keeps_the_store(
    hass, portal, setup
) -> None:
    portal.announcements = [_row("Stare")]
    entry = await setup()
    entity_id = _announcement_id(hass, entry)
    before = entry.runtime_data.seen.announcements()
    fired = _collect(hass, entity_id)

    portal.announcements = [_row("Stare"), _row("Nowe")]
    portal.broken.add("HomePage_v1")
    await _refresh(hass, entry)

    assert fired == []
    assert entry.runtime_data.seen.announcements() == before
    assert hass.states.get(entity_id).state == "unavailable"


async def test_a_reload_with_an_unchanged_portal_fires_nothing(
    hass, portal, setup
) -> None:
    portal.announcements = [_row("Stare")]
    entry = await setup()
    entity_id = _announcement_id(hass, entry)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "unknown"


async def test_an_announcement_that_came_during_a_restart_fires_once(
    hass, portal, setup
) -> None:
    portal.announcements = [_row("Stare")]
    entry = await setup()
    entity_id = _announcement_id(hass, entry)

    portal.announcements = [_row("Stare"), _row("Nowe")]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.attributes["title"] == "Nowe"
    fired = state.state

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == fired


async def test_an_announcement_during_a_restart_waits_until_home_assistant_has_started(
    hass, portal, setup
) -> None:
    portal.announcements = [_row("Stare")]
    entry = await setup()
    fired = _collect(hass, _announcement_id(hass, entry))

    portal.announcements = [_row("Stare"), _row("Nowe")]
    await _start_again(hass, entry)
    assert fired == []

    await _started(hass)
    assert [f["title"] for f in fired] == ["Nowe"]
