"""The submission path: which account and meter, which checks, what reaches the portal.

A wrong reading lands on somebody's bill, so these tests pin down behaviour
rather than implementation: what gets sent, what gets refused before anything
is sent, and what the user is told when the outcome is unknown.
"""

from __future__ import annotations

import pytest
from fake_portal import METER, PASSWORD, USERNAME
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.ibok.api import IbokApi
from custom_components.ibok.submit import (
    Account,
    async_submit,
    resolve_target,
    validate_range,
)

HOUSE = {"id_wodom": 10001, "numer_fabryczny": "12345678"}
GARDEN = {"id_wodom": 10002, "numer_fabryczny": "87654321"}


class _Coordinator:
    """Just what async_submit touches: the API and a refresh."""

    def __init__(self, api: IbokApi) -> None:
        self.api = api
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

    with pytest.raises(ServiceValidationError, match="config_entry_id"):
        resolve_target(accounts, 10001, None)

    assert resolve_target(accounts, 10001, "b") == ("b", 10001)


def test_house_and_garden_without_an_id_are_refused_by_serial() -> None:
    with pytest.raises(ServiceValidationError) as info:
        resolve_target([Account("a", "A", [HOUSE, GARDEN])], None, None)

    # The internal ids alone would not say which is the garden meter.
    assert "12345678" in str(info.value)
    assert "87654321" in str(info.value)


@pytest.mark.parametrize(
    ("accounts", "meter_id", "entry_id"),
    [
        ([Account("a", "A", [])], None, None),
        ([Account("a", "A", [HOUSE])], 99999, None),
        ([Account("a", "A", [HOUSE])], None, "missing"),
    ],
)
def test_nothing_to_choose_is_refused(accounts, meter_id, entry_id) -> None:
    with pytest.raises(ServiceValidationError):
        resolve_target(accounts, meter_id, entry_id)


# --- the portal's own limits ------------------------------------------------


def test_digit_count_sent_as_a_string_is_still_checked() -> None:
    """A PHP dataset may send "5"; the check used to skip anything but an int."""
    with pytest.raises(ServiceValidationError, match="digits"):
        validate_range({"l_cyfr_l": "5"}, 100000)


def test_bounds_with_a_comma_and_spaces_are_parsed() -> None:
    meter = {"min_zakres": "45", "zakres": "99 999,5"}

    validate_range(meter, 99999.5)
    with pytest.raises(ServiceValidationError, match="maximum"):
        validate_range(meter, 99999.6)


def test_the_minimum_itself_is_allowed() -> None:
    validate_range({"min_zakres": "45"}, 45)
    with pytest.raises(ServiceValidationError, match="minimum"):
        validate_range({"min_zakres": "45"}, 44.99)


# --- submitting through a real HTTP portal ----------------------------------


@pytest.fixture
def coordinator(portal, http_session) -> _Coordinator:
    return _Coordinator(IbokApi(portal.base, USERNAME, PASSWORD, session=http_session))


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

    with pytest.raises(ServiceValidationError):
        await async_submit(coordinator, 10001, 48)

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


async def test_an_unknown_outcome_warns_against_sending_again(
    portal, coordinator
) -> None:
    portal.submit_delay = 2

    with pytest.raises(HomeAssistantError, match="twice") as info:
        await async_submit(coordinator, 10001, 48)

    # Not a validation error: it did go out, and the portal has it.
    assert not isinstance(info.value, ServiceValidationError)
    assert len(portal.submissions) == 1
