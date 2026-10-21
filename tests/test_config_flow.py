"""The portal address the user types, and what the integration stores from it."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from fake_portal import PASSWORD, USERNAME
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    SOURCE_USER,
    ConfigEntryState,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import ibok
from custom_components.ibok.api import IbokAuthError
from custom_components.ibok.config_flow import (
    _AddressError,
    _is_default_title,
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

    async def _logged_in(hass, data) -> None:
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


async def test_a_wrong_password_is_reported_as_one(
    hass, enable_custom_integrations, portal, monkeypatch
) -> None:
    """The portal answers a wrong password's menu request with an empty body."""

    def _session(hass, cookie_jar):
        # The stand-in portal is a bare IP address, whose cookies the jar the
        # flow makes would refuse.
        return async_create_clientsession(
            hass, cookie_jar=aiohttp.CookieJar(unsafe=True)
        )

    monkeypatch.setattr(
        "custom_components.ibok.config_flow.async_create_clientsession", _session
    )
    # The flow takes HTTPS only; the stand-in portal speaks plain HTTP.
    monkeypatch.setattr(
        "custom_components.ibok.config_flow.normalise_address", lambda raw: raw
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_BASE_URL: portal.base, CONF_USERNAME: USERNAME, CONF_PASSWORD: "zle"},
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


def test_the_options_form_keeps_a_confirmation():
    existing = {
        "source_entity_12345678": "sensor.woda",
        "replacement_confirmed_12345678": "2026-06-03",
    }

    merged = merge_options(
        existing, ["source_entity_12345678"], {"source_entity_12345678": "sensor.woda"}
    )

    assert merged["replacement_confirmed_12345678"] == "2026-06-03"


@pytest.mark.parametrize(
    ("title", "default"),
    [
        ("ibok.przyklad.pl", True),
        ("ibok.przyklad.pl (2)", True),
        ("ibok.przyklad.pl (13)", True),
        ("ibok.przyklad.pl (1)", False),
        ("ibok.przyklad.pl (x)", False),
        ("Woda dom", False),
        ("ebok.przyklad.pl", False),
    ],
)
def test_only_an_address_title_counts_as_the_default_one(title, default) -> None:
    assert _is_default_title(title, "ibok.przyklad.pl") is default


@pytest.fixture
def validated(monkeypatch) -> list[dict]:
    """Credentials the flow tried to log in with; the portal accepts them."""
    seen: list[dict] = []

    async def _logged_in(hass, data) -> None:
        seen.append(dict(data))

    async def _set_up(hass, entry) -> bool:
        return True

    monkeypatch.setattr(
        "custom_components.ibok.config_flow._async_validate", _logged_in
    )
    # Updating an entry reloads it, which would connect.
    monkeypatch.setattr("custom_components.ibok.async_setup_entry", _set_up)
    return seen


def _account(hass, **overrides) -> MockConfigEntry:
    fields = {
        "domain": DOMAIN,
        "title": "ibok.przyklad.pl",
        "unique_id": "ibok.przyklad.pl:jan",
        "data": {
            CONF_BASE_URL: "https://ibok.przyklad.pl",
            CONF_USERNAME: "jan",
            CONF_PASSWORD: "stare",
        },
        **overrides,
    }
    entry = MockConfigEntry(**fields)
    entry.add_to_hass(hass)
    return entry


async def _reconfigure(hass, entry, user_input):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_reconfigure_moves_the_account_to_a_new_address(
    hass, enable_custom_integrations, validated
) -> None:
    entry = _account(hass)

    result = await _reconfigure(
        hass, entry, {CONF_BASE_URL: "ebok.przyklad.pl", CONF_PASSWORD: ""}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_BASE_URL] == "https://ebok.przyklad.pl"
    assert entry.data[CONF_PASSWORD] == "stare"
    assert entry.data[CONF_USERNAME] == "jan"
    assert entry.unique_id == "ebok.przyklad.pl:jan"
    assert entry.title == "ebok.przyklad.pl"
    assert validated == [
        {
            CONF_BASE_URL: "https://ebok.przyklad.pl",
            CONF_USERNAME: "jan",
            CONF_PASSWORD: "stare",
        }
    ]


async def test_reconfigure_keeps_a_name_the_user_gave(
    hass, enable_custom_integrations, validated
) -> None:
    entry = _account(hass, title="Woda dom")

    await _reconfigure(
        hass, entry, {CONF_BASE_URL: "ebok.przyklad.pl", CONF_PASSWORD: ""}
    )

    assert entry.title == "Woda dom"


async def test_reconfigure_renumbers_a_default_numbered_name(
    hass, enable_custom_integrations, validated
) -> None:
    entry = _account(hass, title="ibok.przyklad.pl (2)")
    MockConfigEntry(
        domain=DOMAIN, title="ebok.przyklad.pl", unique_id="ebok.przyklad.pl:ola"
    ).add_to_hass(hass)

    await _reconfigure(
        hass, entry, {CONF_BASE_URL: "ebok.przyklad.pl", CONF_PASSWORD: ""}
    )

    assert entry.title == "ebok.przyklad.pl (2)"


async def test_reconfigure_with_only_a_new_password_is_not_a_collision(
    hass, enable_custom_integrations, validated
) -> None:
    entry = _account(hass)

    result = await _reconfigure(
        hass, entry, {CONF_BASE_URL: "https://ibok.przyklad.pl", CONF_PASSWORD: "nowe"}
    )

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PASSWORD] == "nowe"
    assert validated[-1][CONF_PASSWORD] == "nowe"
    assert entry.unique_id == "ibok.przyklad.pl:jan"
    assert entry.title == "ibok.przyklad.pl"


async def test_reconfigure_onto_another_entrys_account_is_refused(
    hass, enable_custom_integrations, validated
) -> None:
    entry = _account(hass)
    MockConfigEntry(
        domain=DOMAIN, title="ebok.przyklad.pl", unique_id="ebok.przyklad.pl:jan"
    ).add_to_hass(hass)

    result = await _reconfigure(
        hass, entry, {CONF_BASE_URL: "ebok.przyklad.pl", CONF_PASSWORD: ""}
    )

    assert result["reason"] == "already_configured"
    assert entry.data[CONF_BASE_URL] == "https://ibok.przyklad.pl"
    assert validated == []


async def test_reconfigure_reports_a_rejected_login(
    hass, enable_custom_integrations, monkeypatch
) -> None:
    async def _rejected(hass, data) -> None:
        raise IbokAuthError

    monkeypatch.setattr("custom_components.ibok.config_flow._async_validate", _rejected)
    entry = _account(hass)

    result = await _reconfigure(
        hass, entry, {CONF_BASE_URL: "ibok.przyklad.pl", CONF_PASSWORD: "nowe"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "stare"


async def test_reconfigure_refuses_plain_http(
    hass, enable_custom_integrations, validated
) -> None:
    entry = _account(hass)

    result = await _reconfigure(
        hass, entry, {CONF_BASE_URL: "http://ebok.przyklad.pl", CONF_PASSWORD: ""}
    )

    assert result["errors"] == {CONF_BASE_URL: "insecure_url"}


async def test_the_reconfigure_form_does_not_offer_the_login(
    hass, enable_custom_integrations
) -> None:
    entry = _account(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )

    assert {str(k) for k in result["data_schema"].schema} == {
        CONF_BASE_URL,
        CONF_PASSWORD,
    }


@pytest.fixture
def portal_login_ok(monkeypatch) -> None:
    """The flow accepts the stand-in portal's plain-HTTP address and login."""

    async def _logged_in(hass, data) -> None:
        return None

    monkeypatch.setattr(
        "custom_components.ibok.config_flow._async_validate", _logged_in
    )
    monkeypatch.setattr(
        "custom_components.ibok.config_flow.normalise_address", lambda raw: raw
    )


def _counting_setup():
    """Wraps the real setup, so a loaded entry is set up through the real code."""
    return patch(
        "custom_components.ibok.async_setup_entry", wraps=ibok.async_setup_entry
    )


async def _reauth(hass, entry, password):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: password}
    )


async def test_reconfiguring_a_loaded_account_sets_it_up_once(
    hass, portal, other_site, setup, portal_login_ok, caplog
) -> None:
    entry = await setup()
    assert entry.state is ConfigEntryState.LOADED

    with _counting_setup() as set_up:
        result = await _reconfigure(
            hass, entry, {CONF_BASE_URL: other_site.base, CONF_PASSWORD: ""}
        )
        await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_BASE_URL] == other_site.base
    assert set_up.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert "has an update listener" not in caplog.text


async def test_signing_in_again_with_a_new_password_sets_up_once(
    hass, portal, setup, portal_login_ok, caplog
) -> None:
    entry = await setup()

    with _counting_setup() as set_up:
        result = await _reauth(hass, entry, PASSWORD + "-nowe")
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == PASSWORD + "-nowe"
    assert set_up.call_count == 1
    assert "has an update listener" not in caplog.text


async def test_signing_in_again_with_the_same_password_still_reloads_once(
    hass, portal, setup, portal_login_ok, caplog
) -> None:
    """Nothing changes, so no update listener fires; a failed login must retry."""
    entry = await setup()

    with _counting_setup() as set_up:
        result = await _reauth(hass, entry, PASSWORD)
        await hass.async_block_till_done()

    assert result["reason"] == "reauth_successful"
    assert set_up.call_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert "has an update listener" not in caplog.text


async def test_reconfiguring_an_account_that_is_not_loaded_sets_it_up_again(
    hass, portal, setup, portal_login_ok
) -> None:
    portal.broken, portal.broken_body = {"Menu"}, ""
    (entry,) = hass.config_entries.async_entries(DOMAIN)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    portal.broken = set()

    with _counting_setup() as set_up:
        await _reconfigure(hass, entry, {CONF_BASE_URL: portal.base, CONF_PASSWORD: ""})
        await hass.async_block_till_done()

    assert set_up.call_count == 1
    assert entry.state is ConfigEntryState.LOADED


def _suggested(result, field: str):
    return next(
        key.description["suggested_value"]
        for key in result["data_schema"].schema
        if str(key) == field
    )


async def test_the_reconfigure_form_keeps_what_was_typed_after_an_error(
    hass, enable_custom_integrations, validated
) -> None:
    entry = _account(hass)

    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
    )
    assert _suggested(first, CONF_BASE_URL) == "https://ibok.przyklad.pl"

    result = await hass.config_entries.flow.async_configure(
        first["flow_id"],
        {CONF_BASE_URL: "http://ebok.przyklad.pl", CONF_PASSWORD: "nowe"},
    )

    assert result["errors"] == {CONF_BASE_URL: "insecure_url"}
    assert _suggested(result, CONF_BASE_URL) == "http://ebok.przyklad.pl"
    assert all(
        str(key) != CONF_PASSWORD or not key.description
        for key in result["data_schema"].schema
    )
