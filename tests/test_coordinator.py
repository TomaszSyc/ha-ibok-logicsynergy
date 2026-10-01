"""One portal module failing must not take the whole account down with it."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fake_portal import METER, PASSWORD, USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ibok.api import IbokApi, IbokConnectionError
from custom_components.ibok.const import CONF_BASE_URL, DOMAIN
from custom_components.ibok.coordinator import (
    IbokCoordinator,
    meter_id,
    meter_serial,
    parse_number,
)

INVOICE = {"nw": "2026-04-12", "brutto": "150,00"}
EVERY_MODULE = {
    "Readouts_v1",
    "NotifyReadout_v1",
    "Meters_v1",
    "Accountancy_v4",
    "Invoices_v1",
}


def _coordinator(hass, portal, http_session, password=PASSWORD) -> IbokCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN, title="ibok.przyklad.pl", data={CONF_BASE_URL: portal.base}
    )
    entry.add_to_hass(hass)
    api = IbokApi(
        portal.base,
        USERNAME,
        password,
        session=http_session,
        timeout=http_session.timeout,
    )
    return IbokCoordinator(hass, entry, api)


@pytest.fixture
def coordinator(hass, portal, http_session) -> IbokCoordinator:
    return _coordinator(hass, portal, http_session)


async def test_a_failing_module_leaves_the_others(portal, coordinator) -> None:
    portal.broken = {"Invoices_v1"}

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.failed == {"invoices"}
    assert coordinator.data["notify"] == [METER]


async def test_a_failed_module_keeps_its_last_answer(portal, coordinator) -> None:
    portal.invoices = [INVOICE]
    await coordinator.async_refresh()

    portal.broken = {"Invoices_v1"}
    await coordinator.async_refresh()

    assert coordinator.data["invoices"] == [INVOICE]
    assert coordinator.failed == {"invoices"}


async def test_a_module_that_does_not_answer_in_time_is_left_out(
    portal, coordinator
) -> None:
    """The client gives up after a second; this module takes two."""
    portal.slow = {"Invoices_v1": 2}

    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.failed == {"invoices"}


async def test_an_unreachable_portal_is_given_up_at_once(coordinator) -> None:
    """A refused connection is the portal's, not a module's: no retry per module."""
    attempts = []

    async def _refused():
        attempts.append(1)
        raise IbokConnectionError("connection refused")

    for name in (
        "async_readouts",
        "async_notify_readout",
        "async_meters",
        "async_accountancy",
        "async_invoices",
    ):
        setattr(coordinator.api, name, _refused)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success
    assert len(attempts) == 1


async def test_nothing_answering_is_an_outage(portal, coordinator) -> None:
    portal.broken = set(EVERY_MODULE)

    await coordinator.async_refresh()

    assert not coordinator.last_update_success


async def test_a_rejected_password_asks_for_it_again(
    hass, portal, http_session
) -> None:
    coordinator = _coordinator(hass, portal, http_session, password="inne-haslo")

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_a_failing_module_is_reported_once(portal, coordinator, caplog) -> None:
    """A warning when it stops, one line when it is back, nothing every poll."""
    portal.broken = {"Invoices_v1"}
    with caplog.at_level(logging.INFO, logger="custom_components.ibok.coordinator"):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        portal.broken = set()
        await coordinator.async_refresh()

    messages = [record.getMessage() for record in caplog.records]
    assert sum("did not return invoices" in m for m in messages) == 1
    assert sum("returns invoices again" in m for m in messages) == 1


@pytest.mark.parametrize(
    ("row", "serial"),
    [
        ({"numer_fabryczny": "12345678"}, "12345678"),
        ({"numer_fabryczny": " 12345678 "}, "12345678"),
        ({"numer_fabryczny": 12345678}, "12345678"),
        # The reading form lists a meter without a serial under its id.
        ({"numer_fabryczny": "", "id_wodom": 10001}, "10001"),
        # A readouts row has no id to fall back on.
        ({"numer_fabryczny": None}, ""),
        # The meter list spells the field differently.
        ({"numer_fabr": " 12345678"}, "12345678"),
    ],
)
def test_meter_serial(row: dict, serial: str) -> None:
    assert meter_serial(row) == serial


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 234,56", 1234.56),
        ("1\xa0234,56", 1234.56),
        (" 45 ", 45.0),
        ("abc", None),
        (None, None),
        (7, 7.0),
    ],
)
def test_parse_number(raw, expected) -> None:
    assert parse_number(raw) == expected


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"id_wodom": 10001}, 10001),
        ({"id_wodom": "10001"}, 10001),
        ({"id_wodom": "W-1"}, None),
        ({}, None),
    ],
)
def test_meter_id(row, expected) -> None:
    assert meter_id(row) == expected


@pytest.mark.parametrize("raw", [10001, "10001", " 10001 ", "010001"])
def test_meter_by_id_reads_the_id_as_a_number(raw) -> None:
    """The lookup reads an id the same way every other lookup does."""
    row = {**METER, "id_wodom": raw}
    coordinator = SimpleNamespace(submittable_meters=[row])

    assert IbokCoordinator.meter_by_id(coordinator, 10001) is row
