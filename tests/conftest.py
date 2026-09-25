"""Fixtures serving the stand-in portal. The portal itself is in fake_portal.py."""

from __future__ import annotations

import aiohttp
import pytest
from aiohttp import web
from fake_portal import METER, PASSWORD, USERNAME, FakePortal
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ibok.api import IbokApi
from custom_components.ibok.const import (
    CONF_BASE_URL,
    CONF_SOURCE_ENTITY_PREFIX,
    DOMAIN,
)


async def _serve(portal: FakePortal) -> web.AppRunner:
    runner = web.AppRunner(portal.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    portal.base = f"http://127.0.0.1:{runner.addresses[0][1]}"
    return runner


@pytest.fixture
async def portal(socket_enabled) -> FakePortal:
    # socket_enabled lifts the HA test plugin's socket block for this test
    # only; connections stay limited to 127.0.0.1.
    fake = FakePortal()
    runner = await _serve(fake)
    yield fake
    await runner.cleanup()


@pytest.fixture
async def other_site(socket_enabled) -> FakePortal:
    """A second origin -- somewhere a redirect must never lead the password."""
    fake = FakePortal()
    runner = await _serve(fake)
    yield fake
    await runner.cleanup()


@pytest.fixture
async def http_session() -> aiohttp.ClientSession:
    # unsafe=True only because the test server is an IP address; the
    # integration's own jar refuses cookies from bare IPs.
    session = aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True),
        timeout=aiohttp.ClientTimeout(total=1),
    )
    yield session
    await session.close()


@pytest.fixture
async def setup(hass, enable_custom_integrations, portal, monkeypatch):
    """Returns a function that sets the entry up once the portal is prepared."""
    # The integration's own cookie jar refuses cookies from a bare IP address,
    # which is all the stand-in portal has.
    monkeypatch.setattr(
        "custom_components.ibok.IbokApi",
        lambda base, user, password: IbokApi(
            base,
            user,
            password,
            session=aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)),
        ),
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="ibok.przyklad.pl",
        data={
            CONF_BASE_URL: portal.base,
            CONF_USERNAME: USERNAME,
            CONF_PASSWORD: PASSWORD,
        },
    )
    entry.add_to_hass(hass)

    async def _setup(options: dict | None = None) -> MockConfigEntry:
        """Without options, the meter reads from a source entity."""
        if options is None:
            options = {
                f"{CONF_SOURCE_ENTITY_PREFIX}{METER['numer_fabryczny']}": "sensor.woda"
            }
        hass.config_entries.async_update_entry(entry, options=options)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry

    yield _setup

    if entry.state is ConfigEntryState.LOADED:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
