"""The portal address the user types, and what the integration stores from it."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ibok.config_flow import (
    _AddressError,
    entry_title,
    merge_options,
    normalise_address,
)
from custom_components.ibok.const import CONF_BASE_URL, DOMAIN


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("ibok.przyklad.pl", "https://ibok.przyklad.pl"),
        ("  https://IBOK.Przyklad.pl/  ", "https://ibok.przyklad.pl"),
        ("https://ibok.przyklad.pl:8443/ibok/", "https://ibok.przyklad.pl:8443/ibok"),
    ],
)
def test_address_is_stored_in_one_form(typed: str, stored: str) -> None:
    # One form, so the same account typed two ways is still one entry.
    assert normalise_address(typed) == stored


def test_plain_http_is_refused() -> None:
    """Over HTTP the password would travel unencrypted on every login."""
    with pytest.raises(_AddressError, match="insecure_url"):
        normalise_address("http://ibok.przyklad.pl")


@pytest.mark.parametrize(
    "typed",
    [
        # The address becomes the entry title and every device name, so a
        # password written into it would end up on every screen.
        "https://jan:haslo@ibok.przyklad.pl",
        "https://ibok.przyklad.pl/?module=Menu",
        "https://ibok.przyklad.pl/#logowanie",
        "https://",
    ],
)
def test_address_with_more_than_the_portal_is_refused(typed: str) -> None:
    with pytest.raises(_AddressError, match="invalid_url"):
        normalise_address(typed)


def test_saving_options_keeps_meters_the_form_did_not_show() -> None:
    """The form lists only meters accepting a reading now, none while unloaded."""
    existing = {
        "scan_interval": 6,
        "source_entity_12345678": "sensor.dom",
        "source_entity_87654321": "sensor.ogrod",
    }

    merged = merge_options(existing, ["scan_interval"], {"scan_interval": 12})

    assert merged == {
        "scan_interval": 12,
        "source_entity_12345678": "sensor.dom",
        "source_entity_87654321": "sensor.ogrod",
    }


def test_emptying_a_shown_field_removes_that_assignment_only() -> None:
    existing = {"source_entity_12345678": "sensor.dom", "source_entity_87654321": "x"}

    merged = merge_options(
        existing,
        ["scan_interval", "source_entity_12345678"],
        {"scan_interval": 6, "source_entity_12345678": ""},
    )

    assert merged == {"scan_interval": 6, "source_entity_87654321": "x"}


@pytest.mark.parametrize(
    ("taken", "title"),
    [
        ([], "ibok.przyklad.pl"),
        (["inny.przyklad.pl"], "ibok.przyklad.pl"),
        (["ibok.przyklad.pl"], "ibok.przyklad.pl (2)"),
        (["ibok.przyklad.pl", "ibok.przyklad.pl (2)"], "ibok.przyklad.pl (3)"),
        # A removed first entry frees its title again.
        (["ibok.przyklad.pl (2)"], "ibok.przyklad.pl"),
    ],
)
def test_a_second_account_on_one_portal_is_numbered(taken, title) -> None:
    assert entry_title("ibok.przyklad.pl", taken) == title


async def test_adding_a_second_account_names_it_apart(
    hass, enable_custom_integrations, monkeypatch
) -> None:
    """Both accounts would otherwise name every device and entity the same."""

    async def _logged_in(data) -> None:
        return None

    async def _set_up(hass, entry) -> bool:
        return True

    monkeypatch.setattr(
        "custom_components.ibok.config_flow._async_validate", _logged_in
    )
    # Home Assistant sets a new entry up straight away, which would connect.
    monkeypatch.setattr("custom_components.ibok.async_setup_entry", _set_up)
    MockConfigEntry(
        domain=DOMAIN, title="ibok.przyklad.pl", unique_id="ibok.przyklad.pl:jan"
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BASE_URL: "ibok.przyklad.pl", CONF_USERNAME: "ola", CONF_PASSWORD: "x"},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "ibok.przyklad.pl (2)"
