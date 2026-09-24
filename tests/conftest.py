"""Fixtures serving the stand-in portal. The portal itself is in fake_portal.py."""

from __future__ import annotations

import aiohttp
import pytest
from aiohttp import web
from fake_portal import FakePortal


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
