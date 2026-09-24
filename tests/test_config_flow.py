"""The portal address the user types, and what the integration stores from it."""

from __future__ import annotations

import pytest

from custom_components.ibok.config_flow import (
    _AddressError,
    merge_options,
    normalise_address,
)


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
