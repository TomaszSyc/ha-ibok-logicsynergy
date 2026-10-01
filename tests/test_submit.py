"""The submission path: which account and meter, which checks, what reaches the portal.

A wrong reading lands on somebody's bill, so these tests pin down behaviour
rather than implementation: what gets sent, what gets refused before anything
is sent, and what the user is told when the outcome is unknown.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fake_portal import METER, PASSWORD, USERNAME
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ibok.api import (
    IbokApi,
    IbokOutcomeUnknownError,
    IbokResponseError,
)
from custom_components.ibok.const import CONF_BASE_URL, DOMAIN, SERVICE_SUBMIT_READING
from custom_components.ibok.ledger import SubmissionLedger, issue_id
from custom_components.ibok.submit import (
    Account,
    async_submit,
    resolve_target,
    validate_date,
    validate_range,
)

HOUSE = {"id_wodom": 10001, "numer_fabryczny": "12345678"}
GARDEN = {"id_wodom": 10002, "numer_fabryczny": "87654321"}


class _Coordinator:
    """Just what async_submit touches: the API, the ledger and a refresh."""

    def __init__(self, api: IbokApi) -> None:
        self.api = api
        self.ledger = SubmissionLedger(None, "e1")
        self.config_entry = SimpleNamespace(entry_id="e1")
        self.refreshes = 0

    async def async_refresh(self) -> None:
        self.refreshes += 1


# --- choosing the account and meter ---------------------------------------


def test_the_only_meter_needs_no_id() -> None:
    assert resolve_target([Account("a", "A", [HOUSE])], None, None) == ("a", 10001)


def test_a_meter_on_the_second_account_is_found() -> None:
    """The service used to look only at the first loaded account."""
    accounts = [Account("a", "A", [HOUSE]), Account("b", "B", [{"id_wodom": 20002}])]

    assert resolve_target(accounts, 20002, None) == ("b", 20002)


def test_the_same_meter_id_on_two_accounts_is_refused_without_an_account() -> None:
    """Two operators may number meters independently; never guess between them."""
    accounts = [Account("a", "A", [HOUSE]), Account("b", "B", [HOUSE])]

    with pytest.raises(ServiceValidationError) as info:
        resolve_target(accounts, 10001, None)

    assert info.value.translation_key == "several_meters"

    assert resolve_target(accounts, 10001, "b") == ("b", 10001)


def test_house_and_garden_without_an_id_are_refused_by_serial() -> None:
    with pytest.raises(ServiceValidationError) as info:
        resolve_target([Account("a", "A", [HOUSE, GARDEN])], None, None)

    # The internal ids alone would not say which is the garden meter.
    assert info.value.translation_key == "several_meters"
    choices = info.value.translation_placeholders["choices"]
    assert "12345678" in choices
    assert "87654321" in choices


@pytest.mark.parametrize(
    ("accounts", "meter_id", "entry_id", "key"),
    [
        ([Account("a", "A", [])], None, None, "no_meter_accepting"),
        ([Account("a", "A", [HOUSE])], 99999, None, "no_meter_with_id"),
        ([Account("a", "A", [HOUSE])], None, "missing", "no_account_with_id"),
    ],
)
def test_nothing_to_choose_is_refused(accounts, meter_id, entry_id, key) -> None:
    with pytest.raises(ServiceValidationError) as info:
        resolve_target(accounts, meter_id, entry_id)

    assert info.value.translation_key == key


# --- the portal's own limits ------------------------------------------------


def test_digit_count_sent_as_a_string_is_still_checked() -> None:
    """A PHP dataset may send "5"; the check used to skip anything but an int."""
    with pytest.raises(ServiceValidationError) as info:
        validate_range({"l_cyfr_l": "5"}, 100000)

    assert info.value.translation_key == "reading_too_many_digits"


def test_bounds_with_a_comma_and_spaces_are_parsed() -> None:
    meter = {"min_zakres": "45", "zakres": "99 999,5"}

    validate_range(meter, 99999.5)
    with pytest.raises(ServiceValidationError) as info:
        validate_range(meter, 99999.6)

    assert info.value.translation_key == "reading_above_max"


def test_the_minimum_itself_is_allowed() -> None:
    validate_range({"min_zakres": "45"}, 45)
    with pytest.raises(ServiceValidationError) as info:
        validate_range({"min_zakres": "45"}, 44.99)

    assert info.value.translation_key == "reading_below_min"


TODAY = date(2026, 4, 20)
PREVIOUS = {"do": "2026-04-06"}


def test_a_reading_dated_in_the_future_is_refused() -> None:
    with pytest.raises(ServiceValidationError) as info:
        validate_date(PREVIOUS, TODAY + timedelta(days=1), TODAY)

    assert info.value.translation_key == "reading_date_future"


def test_a_reading_dated_before_the_previous_one_is_refused() -> None:
    """It would be filed under a period that has already been billed."""
    with pytest.raises(ServiceValidationError) as info:
        validate_date(PREVIOUS, date(2026, 4, 5), TODAY)

    assert info.value.translation_key == "reading_date_before_previous"


@pytest.mark.parametrize("when", [date(2026, 4, 6), date(2026, 4, 12), TODAY])
def test_a_reading_dated_from_the_previous_one_to_today_is_allowed(when) -> None:
    validate_date(PREVIOUS, when, TODAY)


def test_without_a_previous_date_only_the_future_is_refused() -> None:
    validate_date({}, date(2020, 1, 1), TODAY)
    with pytest.raises(ServiceValidationError) as info:
        validate_date({"do": "brak"}, TODAY + timedelta(days=1), TODAY)

    assert info.value.translation_key == "reading_date_future"


# --- submitting through a real HTTP portal ----------------------------------


@pytest.fixture
def coordinator(portal, http_session) -> _Coordinator:
    return _Coordinator(
        IbokApi(
            portal.base,
            USERNAME,
            PASSWORD,
            session=http_session,
            timeout=http_session.timeout,
        )
    )


async def test_the_previous_reading_comes_from_the_portal_now(
    portal, coordinator
) -> None:
    """The snapshot can be hours old; the portal's current row is what counts."""
    portal.notify = [{**METER, "sl": "47"}]

    await async_submit(coordinator, 10001, 48)

    assert portal.submissions[0]["odcz_poprz"] == "47"
    assert coordinator.refreshes == 1


async def test_a_meter_that_stopped_accepting_is_refused_before_sending(
    portal, coordinator
) -> None:
    portal.notify = []

    with pytest.raises(ServiceValidationError) as info:
        await async_submit(coordinator, 10001, 48)

    assert info.value.translation_key == "meter_not_accepting"
    assert portal.submissions == []


async def test_a_reading_out_of_range_is_never_sent(portal, coordinator) -> None:
    with pytest.raises(ServiceValidationError):
        await async_submit(coordinator, 10001, 44)

    assert portal.submissions == []


async def test_an_expired_session_still_sends_exactly_once(portal, coordinator) -> None:
    await coordinator.api.async_login()
    portal.expire_all()

    await async_submit(coordinator, 10001, 48)

    assert len(portal.submissions) == 1
    assert portal.submissions[0]["id_wodom"] == "10001"


async def test_an_unknown_outcome_warns_against_sending_again(
    portal, coordinator
) -> None:
    portal.submit_delay = 2

    with pytest.raises(HomeAssistantError) as info:
        await async_submit(coordinator, 10001, 48)

    # Not a validation error: it did go out, and the portal has it.
    assert not isinstance(info.value, ServiceValidationError)
    assert info.value.translation_key == "outcome_unknown"
    assert len(portal.submissions) == 1


async def test_a_reading_dated_before_the_previous_one_is_never_sent(
    portal, coordinator
) -> None:
    portal.notify = [{**METER, "do": "2026-04-06"}]

    with pytest.raises(ServiceValidationError) as info:
        await async_submit(coordinator, 10001, 48, date(2026, 4, 5))

    assert info.value.translation_key == "reading_date_before_previous"
    assert portal.submissions == []


def _key(info: pytest.ExceptionInfo) -> str | None:
    return info.value.translation_key


async def test_the_same_value_twice_is_refused(portal, coordinator) -> None:
    """Sent once already today, it would be on the bill twice."""
    await async_submit(coordinator, 10001, 48)

    with pytest.raises(ServiceValidationError) as info:
        await async_submit(coordinator, 10001, 48)

    assert _key(info) == "reading_already_sent"
    assert len(portal.submissions) == 1


async def test_a_different_value_the_same_day_goes_out(portal, coordinator) -> None:
    """A correction of a mistyped reading is not a repeat."""
    await async_submit(coordinator, 10001, 48)
    await async_submit(coordinator, 10001, 49)

    assert [s["txtReadingNr"] for s in portal.submissions] == ["48", "49"]


async def test_the_service_truncates_to_the_dial(portal, coordinator) -> None:
    """48.6 on a meter read in whole cubic metres is a dial showing 48."""
    portal.notify = [{**METER, "l_cyfr_p": 0}]

    sent = await async_submit(coordinator, 10001, 48.6)

    assert sent == 48.0
    assert portal.submissions[0]["txtReadingNr"] == "48"
    assert portal.submissions[0]["txtReadingFrac"] == "000"


async def test_fresh_digits_win_over_the_announced_value(portal, coordinator) -> None:
    """The portal changed the dial's precision after the value was announced."""
    portal.notify = [{**METER, "l_cyfr_p": 0}]

    with pytest.raises(ServiceValidationError) as info:
        await async_submit(coordinator, 10001, 48.6, announced=48.6)

    assert _key(info) == "precision_changed_press_again"
    assert info.value.translation_placeholders == {
        "announced": "48.6",
        "reading": "48",
    }
    assert portal.submissions == []
    # So the next announcement is made at the precision the portal now uses.
    assert coordinator.refreshes == 1


async def test_a_different_serial_is_refused(portal, coordinator) -> None:
    """The portal reuses the internal id for a replaced meter."""
    with pytest.raises(ServiceValidationError) as info:
        await async_submit(coordinator, 10001, 48, expected_serial="87654321")

    assert _key(info) == "meter_serial_changed"
    assert info.value.translation_placeholders == {
        "expected": "87654321",
        "actual": "12345678",
    }
    assert portal.submissions == []


async def test_a_past_reading_date_is_checked_against_that_date(
    portal, coordinator
) -> None:
    """The portal has 48 for that day already; today's date is beside the point."""
    portal.notify = [{**METER, "do": "2026-04-10", "sl": "48"}]

    with pytest.raises(ServiceValidationError) as info:
        await async_submit(coordinator, 10001, 48, date(2026, 4, 10))

    assert _key(info) == "reading_already_recorded"
    assert portal.submissions == []


async def test_today_is_the_local_date(hass, portal, coordinator, freezer) -> None:
    """At 00:30 in Warsaw it is already the next day, whatever UTC says."""
    await hass.config.async_set_time_zone("Europe/Warsaw")
    freezer.move_to("2026-04-19T22:30:00+00:00")

    await async_submit(coordinator, 10001, 48)

    assert portal.submissions[0]["txtDateOfReading"] == "2026-04-20"


async def test_not_a_number_is_refused_before_asking_the_portal(
    portal, coordinator
) -> None:
    with pytest.raises(ServiceValidationError) as info:
        await async_submit(coordinator, 10001, float("nan"))

    assert _key(info) == "invalid_reading"
    assert portal.requests == []


async def test_a_failed_send_is_not_remembered(
    portal, coordinator, monkeypatch
) -> None:
    """Nothing reached the portal, so sending the same value again is fine."""

    async def _refused(**_kwargs) -> str:
        raise IbokResponseError("refused")

    monkeypatch.setattr(coordinator.api, "async_submit_reading", _refused)
    with pytest.raises(HomeAssistantError) as info:
        await async_submit(coordinator, 10001, 48)
    assert _key(info) == "nothing_recorded"

    monkeypatch.undo()
    await async_submit(coordinator, 10001, 48)

    assert len(portal.submissions) == 1


# --- through Home Assistant --------------------------------------------------


async def _call_service(hass, **data) -> None:
    await hass.services.async_call(
        DOMAIN, SERVICE_SUBMIT_READING, {"reading": 48, **data}, blocking=True
    )


async def test_two_concurrent_service_calls_send_once(
    hass, portal, setup, monkeypatch
) -> None:
    entry = await setup()
    api = entry.runtime_data.api
    submit, started, release = (
        api.async_submit_reading,
        asyncio.Event(),
        asyncio.Event(),
    )

    async def _held(**kwargs) -> str:
        started.set()
        await release.wait()
        return await submit(**kwargs)

    monkeypatch.setattr(api, "async_submit_reading", _held)
    first = hass.async_create_task(_call_service(hass))
    await started.wait()

    try:
        # Bounded, so a second call that waits for the first instead of
        # refusing fails the test rather than hanging it.
        with pytest.raises(ServiceValidationError) as info:
            await asyncio.wait_for(_call_service(hass), 5)
    finally:
        release.set()
        await first

    assert _key(info) == "submit_in_progress"
    assert len(portal.submissions) == 1


async def test_the_service_finds_a_meter_missing_from_the_snapshot(
    hass, portal, setup
) -> None:
    """The portal opened its reading window after the last poll."""
    portal.notify = []
    await setup()
    portal.notify = [dict(METER)]

    await _call_service(hass)

    assert len(portal.submissions) == 1


async def test_the_service_refuses_when_no_account_answers(hass, portal, setup) -> None:
    await setup()
    portal.broken.add("NotifyReadout_v1")

    with pytest.raises(HomeAssistantError) as info:
        await _call_service(hass)

    assert _key(info) == "nothing_sent_check_failed"
    assert portal.submissions == []


async def test_an_unknown_outcome_raises_a_repair_and_blocks_the_next_call(
    hass, portal, setup, monkeypatch
) -> None:
    entry = await setup()

    async def _lost(**_kwargs) -> str:
        raise IbokOutcomeUnknownError("no answer")

    monkeypatch.setattr(entry.runtime_data.api, "async_submit_reading", _lost)
    with pytest.raises(HomeAssistantError) as info:
        await async_submit(entry.runtime_data, 10001, 48)
    assert _key(info) == "outcome_unknown"

    issue = ir.async_get(hass).async_get_issue(DOMAIN, issue_id(entry.entry_id, 10001))
    assert issue is not None

    with pytest.raises(ServiceValidationError) as info:
        await async_submit(entry.runtime_data, 10001, 50)
    assert _key(info) == "submit_outcome_pending"


async def test_a_meter_id_shared_with_an_account_that_did_not_answer_stays_ambiguous(
    hass, portal, other_site, setup
) -> None:
    """The reading must not quietly go to the account that happened to answer."""
    await setup()
    second = MockConfigEntry(
        domain=DOMAIN,
        title="drugi.przyklad.pl",
        data={
            CONF_BASE_URL: other_site.base,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
        },
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()
    other_site.broken.add("NotifyReadout_v1")

    try:
        with pytest.raises(ServiceValidationError) as info:
            await _call_service(hass)
    finally:
        await hass.config_entries.async_unload(second.entry_id)

    assert _key(info) == "several_meters"
    assert portal.submissions == other_site.submissions == []
