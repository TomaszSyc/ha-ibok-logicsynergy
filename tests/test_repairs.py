"""An unknown submission outcome as a Repairs issue, and the ways it goes away.

The ledger is driven directly: what matters here is the issue it raises, the
block that comes with it, and each way both are lifted -- the portal showing
the reading, the user confirming the repair, the entry being removed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from fake_portal import METER, PASSWORD, USERNAME
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ibok import repairs
from custom_components.ibok.api import IbokOutcomeUnknownError
from custom_components.ibok.const import CONF_BASE_URL, DOMAIN
from custom_components.ibok.ledger import issue_id
from custom_components.ibok.submit import async_submit

SERIAL = "12345678"


def _today() -> date:
    return dt_util.now().date()


def _record(entry: MockConfigEntry, value: float = 48.0) -> None:
    today = _today()
    at = datetime(
        today.year,
        today.month,
        today.day,
        12,
        5,
        tzinfo=dt_util.get_default_time_zone(),
    )
    entry.runtime_data.ledger.record_unknown(10001, SERIAL, value, today, today, at)


def _row(entry: MockConfigEntry) -> dict:
    return entry.runtime_data.meter_by_id(10001)


def _issue(hass, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id, 10001))


async def _confirm(hass, iid: str) -> None:
    issue = ir.async_get(hass).async_get_issue(DOMAIN, iid)
    flow = await repairs.async_create_fix_flow(hass, iid, issue.data)
    flow.hass = hass
    flow.issue_id = iid
    await flow.async_step_init()
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_recording_an_unknown_outcome_raises_an_issue_and_blocks(
    hass, portal, setup
) -> None:
    entry = await setup()

    _record(entry)

    issue = _issue(hass, entry)
    assert issue is not None
    assert issue.is_fixable and issue.is_persistent
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "outcome_unknown"
    assert issue.translation_placeholders == {
        "serial": SERIAL,
        "reading": "48",
        "date": _today().isoformat(),
        "sent_date": _today().isoformat(),
        "time": "12:05",
    }
    assert issue.data == {
        "entry_id": entry.entry_id,
        "meter_id": 10001,
        "serial": SERIAL,
        "value": 48.0,
        "day": _today().isoformat(),
        "seen": None,
    }
    with pytest.raises(ServiceValidationError) as err:
        entry.runtime_data.ledger.check(_row(entry), 50.0, _today(), _today())
    assert err.value.translation_key == "submit_outcome_pending"


async def test_the_issue_says_when_it_was_sent_not_only_the_reading_date(
    hass, portal, setup
) -> None:
    """A reading dated days back went out today, late in the local evening."""
    entry = await setup()
    today = _today()
    reading_day = today - timedelta(days=3)
    local = datetime(
        today.year,
        today.month,
        today.day,
        23,
        30,
        tzinfo=dt_util.get_default_time_zone(),
    )

    entry.runtime_data.ledger.record_unknown(
        10001, SERIAL, 48.0, reading_day, today, dt_util.as_utc(local)
    )

    placeholders = _issue(hass, entry).translation_placeholders
    assert placeholders["date"] == reading_day.isoformat()
    assert placeholders["sent_date"] == today.isoformat()
    assert placeholders["time"] == "23:30"


async def test_the_issue_clears_when_the_portal_shows_the_reading(
    hass, portal, setup
) -> None:
    entry = await setup()
    _record(entry)

    portal.readouts = [
        {
            "numer_fabryczny": SERIAL,
            "odczyty": [{"do": _today().isoformat(), "sl": "48,000"}],
        }
    ]
    await entry.runtime_data.async_refresh()

    assert _issue(hass, entry) is None
    assert entry.runtime_data.ledger.pending(10001) is None


async def test_the_issue_clears_when_the_portal_holds_the_submission(
    hass, portal, setup
) -> None:
    """The operator's readouts change only on approval; the form shows it first."""
    entry = await setup()
    _record(entry)

    portal.notify = [
        {**portal.notify[0], "ido": _today().isoformat(), "isl": "48", "ist": 1}
    ]
    await entry.runtime_data.async_refresh()

    assert _issue(hass, entry) is None
    assert entry.runtime_data.ledger.pending(10001) is None


async def test_the_issue_clears_when_the_portal_refused_the_submission(
    hass, portal, setup
) -> None:
    entry = await setup()
    _record(entry)

    portal.notify = [
        {
            **portal.notify[0],
            "ido": _today().isoformat(),
            "isl": "48",
            "ist": 3,
            "iid": "8002",
        }
    ]
    await entry.runtime_data.async_refresh()

    assert _issue(hass, entry) is None
    assert entry.runtime_data.ledger.pending(10001) is None


def _refused_earlier() -> dict:
    """The meter's row still showing an earlier refusal of the very same reading."""
    return {
        **METER,
        "ido": _today().isoformat(),
        "isl": "48.000",
        "ist": "3",
        "iid": "8001",
        "tkom": "Odczyt niezgodny z poprzednim",
    }


async def _still_blocked(hass, entry) -> None:
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.ledger.pending(10001) is not None
    assert _issue(hass, entry) is not None


@pytest.mark.parametrize("lost", [False, True], ids=["not-shown", "no-answer"])
async def test_an_earlier_refusal_does_not_lift_the_block(
    hass, portal, setup, monkeypatch, lost
) -> None:
    """Neither on the next poll nor after a reload: it is not this one's verdict."""
    portal.notify = [_refused_earlier()]
    portal.verdict = "ignore"
    entry = await setup()
    if lost:

        async def _lost(**_kwargs) -> str:
            raise IbokOutcomeUnknownError("no answer")

        monkeypatch.setattr(entry.runtime_data.api, "async_submit_reading", _lost)

    with pytest.raises(HomeAssistantError) as info:
        await async_submit(entry.runtime_data, 10001, 48)
    assert info.value.translation_key == "outcome_unknown"

    await _still_blocked(hass, entry)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await _still_blocked(hass, entry)


async def test_confirming_the_repair_unblocks(hass, portal, setup) -> None:
    entry = await setup()
    _record(entry)

    await _confirm(hass, issue_id(entry.entry_id, 10001))

    assert _issue(hass, entry) is None
    entry.runtime_data.ledger.check(_row(entry), 48.0, _today(), _today())


async def test_the_block_survives_a_reload(hass, portal, setup) -> None:
    entry = await setup()
    _record(entry)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    pending = entry.runtime_data.ledger.pending(10001)
    assert pending is not None
    assert (pending.serial, pending.value, pending.day) == (SERIAL, 48.0, _today())
    with pytest.raises(ServiceValidationError):
        entry.runtime_data.ledger.check(_row(entry), 50.0, _today(), _today())


async def test_two_accounts_keep_separate_issues(hass, portal, setup) -> None:
    """Two accounts on one portal can both have a meter with the same id."""
    first = await setup()
    second = MockConfigEntry(
        domain=DOMAIN,
        title="drugie konto",
        data={
            CONF_BASE_URL: portal.base,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
        },
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    _record(second)

    assert issue_id(first.entry_id, 10001) != issue_id(second.entry_id, 10001)
    assert _issue(hass, first) is None
    assert _issue(hass, second) is not None
    first.runtime_data.ledger.check(_row(first), 50.0, _today(), _today())
    with pytest.raises(ServiceValidationError):
        second.runtime_data.ledger.check(_row(second), 50.0, _today(), _today())

    # A reload of the first account must not pick up the second one's block.
    assert await hass.config_entries.async_reload(first.entry_id)
    await hass.async_block_till_done()
    assert first.runtime_data.ledger.pending(10001) is None

    await hass.config_entries.async_unload(second.entry_id)
    await hass.async_block_till_done()


async def test_confirming_after_the_entry_is_unloaded(hass, portal, setup) -> None:
    entry = await setup()
    _record(entry)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    # Unloading keeps the issue: the outcome is still unknown.
    assert _issue(hass, entry) is not None

    await _confirm(hass, issue_id(entry.entry_id, 10001))

    assert _issue(hass, entry) is None
    # Nothing is left to restore once the entry comes back.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.ledger.pending(10001) is None


async def test_removing_the_entry_removes_its_issues(hass, portal, setup) -> None:
    entry = await setup()
    _record(entry)

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _issue(hass, entry) is None
