"""Transport layer for iBOK portals built by LogicSynergy.

Everything that knows how the portal speaks lives here. The rest of the
integration only sees plain dictionaries, so swapping this for the mobile
application's API later means replacing one module, not rewriting the
integration.

The portal renders its UI client side from JSON datasets (MooTools + Spry),
which is why plain ``?module=<Name>`` requests return JSON rather than HTML.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp

from .const import (
    MODULE_ACCOUNTANCY,
    MODULE_INVOICES,
    MODULE_METERS,
    MODULE_NOTIFY_READOUT,
    MODULE_READOUTS,
)

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=45)
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0",
    "Accept-Language": "pl-PL,pl;q=0.9",
}


class IbokError(Exception):
    """Base error for this integration."""


class IbokConnectionError(IbokError):
    """The portal could not be reached."""


class IbokAuthError(IbokError):
    """The portal rejected the credentials."""


class IbokResponseError(IbokError):
    """The portal answered with something unexpected."""


class IbokApi:
    """Talks to a single iBOK instance on behalf of one account."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self._base = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._session: aiohttp.ClientSession | None = None
        self._logged_in = False

    async def async_close(self) -> None:
        """Release the session. Called when the config entry unloads."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._logged_in = False

    def _ensure_session(self) -> aiohttp.ClientSession:
        # A private session, and therefore a private cookie jar: the portal
        # identifies the account purely by PHPSESSID, so sharing Home
        # Assistant's shared session would let two config entries for two
        # different accounts overwrite each other's login.
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=_TIMEOUT,
                headers=_HEADERS,
                cookie_jar=aiohttp.CookieJar(unsafe=False),
            )
        return self._session

    async def async_login(self) -> None:
        """Establish a session. Raises IbokAuthError on bad credentials."""
        session = self._ensure_session()
        try:
            # The login form only works once the session cookie exists.
            async with session.get(f"{self._base}/") as response:
                await response.text()

            # Field names are a trap: the account name goes into "user", while
            # "login" is the submit button. Sending the account name as "login"
            # bounces silently back to the login page with no error message.
            payload = {
                "user": self._username,
                "pass": self._password,
                "login": "Zaloguj",
                "JSTest": "ok",
            }
            async with session.post(f"{self._base}/?", data=payload) as response:
                body = await response.text()
        except aiohttp.ClientError as err:
            raise IbokConnectionError(str(err)) from err

        if 'id="frmLogin"' in body and "wyloguj" not in body.lower():
            raise IbokAuthError("portal returned the login page again")

        self._logged_in = True

    async def async_module(self, module: str) -> Any:
        """Fetch one module's dataset, logging in again if the session expired."""
        if not self._logged_in:
            await self.async_login()

        body = await self._get_module(module)
        if body is _SESSION_EXPIRED:
            self._logged_in = False
            await self.async_login()
            body = await self._get_module(module)
            if body is _SESSION_EXPIRED:
                raise IbokAuthError("session could not be re-established")

        if not body.strip():
            # Modules the operator has disabled answer HTTP 200 with an empty
            # body rather than 404, so this is not an error.
            return None

        try:
            return json.loads(body)
        except ValueError as err:
            raise IbokResponseError(f"{module}: {err}") from err

    async def _get_module(self, module: str) -> Any:
        session = self._ensure_session()
        try:
            async with session.get(
                f"{self._base}/?module={module}",
                headers={"X-Requested-With": "XMLHttpRequest"},
            ) as response:
                body = await response.text()
        except aiohttp.ClientError as err:
            raise IbokConnectionError(str(err)) from err

        if 'id="frmLogin"' in body:
            return _SESSION_EXPIRED
        return body

    async def async_meters(self) -> list[dict[str, Any]]:
        return _as_list(await self.async_module(MODULE_METERS))

    async def async_readouts(self) -> list[dict[str, Any]]:
        return _as_list(await self.async_module(MODULE_READOUTS))

    async def async_notify_readout(self) -> list[dict[str, Any]]:
        """Meters that currently accept a reading, with the allowed range."""
        return _as_list(await self.async_module(MODULE_NOTIFY_READOUT))

    async def async_accountancy(self) -> dict[str, Any] | None:
        data = await self.async_module(MODULE_ACCOUNTANCY)
        if isinstance(data, list):
            return data[0] if data else None
        return data

    async def async_invoices(self) -> list[dict[str, Any]]:
        return _as_list(await self.async_module(MODULE_INVOICES))

    async def async_submit_reading(
        self,
        meter_id: int,
        whole: int,
        fraction: int,
        reading_date: str,
        previous: str = "",
        note: str = "",
    ) -> str:
        """Submit a reading. Returns the raw response for the caller to verify.

        The portal's own form posts into a hidden iframe, so the HTTP status
        says nothing about whether the reading was accepted. The caller is
        expected to confirm by re-reading the readouts rather than trusting
        this response.
        """
        if not self._logged_in:
            await self.async_login()

        form = aiohttp.FormData()
        form.add_field("id_wodom", str(meter_id))
        form.add_field("odcz_poprz", str(previous))
        form.add_field("txtDateOfReading", reading_date)
        form.add_field("txtReadingNr", str(whole))
        form.add_field("txtReadingFrac", str(fraction))
        form.add_field("txtDesc", note)

        session = self._ensure_session()
        url = f"{self._base}/?module={MODULE_NOTIFY_READOUT}&action=add"
        try:
            async with session.post(url, data=form) as response:
                return await response.text()
        except aiohttp.ClientError as err:
            raise IbokConnectionError(str(err)) from err


_SESSION_EXPIRED = object()


def _as_list(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []
