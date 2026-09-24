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
from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal
from typing import Any

import aiohttp
from yarl import URL

from .const import (
    MODULE_ACCOUNTANCY,
    MODULE_INVOICES,
    MODULE_MENU,
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
_XHR = {"X-Requested-With": "XMLHttpRequest"}

_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5
_LOGIN_FORM = 'id="frmLogin"'


class IbokError(Exception):
    """Base error for this integration."""


class IbokConnectionError(IbokError):
    """The portal could not be reached; nothing was sent."""


class IbokTimeoutError(IbokConnectionError):
    """The request went out and no answer arrived in time."""


class IbokDisconnectedError(IbokConnectionError):
    """The connection broke after the request went out."""


class IbokAuthError(IbokError):
    """The portal rejected the credentials."""


class IbokResponseError(IbokError):
    """The portal answered with something unexpected."""


class IbokOutcomeUnknownError(IbokError):
    """A submission went out and its answer never came back.

    The portal may have recorded it. Sending it again blindly would put the
    same reading on the bill twice, so the caller has to say so rather than
    retry.
    """


def split_reading(reading: float) -> tuple[str, str]:
    """Split a reading into the portal's two fields: cubic metres and litres.

    Litres are padded to three digits, so the value reads the same whether the
    portal takes the field as a number of litres or as the digits after the
    decimal point. Truncated, never rounded: 48.9996 is 48 m3 and 999 litres,
    because 1000 litres would already be the next cubic metre.
    """
    value = Decimal(str(reading))
    if value < 0:
        raise ValueError("a meter reading cannot be negative")
    whole = int(value)
    litres = int(((value - whole) * 1000).to_integral_value(rounding=ROUND_DOWN))
    return str(whole), f"{litres:03d}"


class IbokApi:
    """Talks to a single iBOK instance on behalf of one account."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._origin = URL(self._base).origin()
        self._username = username
        self._password = password
        self._session = session
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

    async def _request(
        self,
        method: str,
        url: str,
        *,
        data: Callable[[], Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        """One request, following redirects only within the portal's origin.

        Redirects are followed by hand because aiohttp re-sends the body of a
        307/308 to wherever it points, another host included -- for the login
        that body is the password. A hop that leaves the portal is refused
        before anything is sent to it.

        ``data`` is a factory rather than a value: a form has to be built again
        for every hop that re-sends it.
        """
        session = self._ensure_session()
        target = URL(url, encoded=True)

        for _ in range(_MAX_REDIRECTS + 1):
            if target.origin() != self._origin:
                raise IbokResponseError(
                    f"refused to follow a redirect away from the portal, to {target.origin()}"
                )
            try:
                async with session.request(
                    method,
                    target,
                    data=data() if data is not None else None,
                    headers={**_HEADERS, **(headers or {})},
                    allow_redirects=False,
                ) as response:
                    location = response.headers.get("Location")
                    if response.status in _REDIRECTS and location:
                        if response.status in (301, 302, 303):
                            method, data = "GET", None
                        target = target.join(URL(location))
                        continue
                    return response.status, await response.text()
            # Order matters. A refused connection and a connect timeout mean the
            # request never went out; any other failure may come after the
            # portal already has it.
            except aiohttp.ClientConnectorError as err:
                raise IbokConnectionError(str(err)) from err
            except aiohttp.ConnectionTimeoutError as err:
                raise IbokConnectionError(str(err)) from err
            except TimeoutError as err:
                # aiohttp raises the builtin TimeoutError for a total timeout,
                # which is not an aiohttp.ClientError.
                raise IbokTimeoutError("the portal did not answer in time") from err
            except aiohttp.ClientError as err:
                raise IbokDisconnectedError(str(err)) from err

        raise IbokResponseError("the portal redirected too many times")

    async def async_login(self) -> None:
        """Establish a session. Raises IbokAuthError on bad credentials."""
        self._logged_in = False

        # The login form only works once the session cookie exists.
        await self._request("GET", f"{self._base}/")

        # Field names are a trap: the account name goes into "user", while
        # "login" is the submit button. Sending the account name as "login"
        # bounces silently back to the login page with no error message.
        payload = {
            "user": self._username,
            "pass": self._password,
            "login": "Zaloguj",
            "JSTest": "ok",
        }
        await self._request("POST", f"{self._base}/?", data=lambda: dict(payload))

        # The portal answers the login POST with a redirect and an empty body
        # whether or not the password was right, so that answer proves nothing.
        # A module returns JSON only to a session that is logged in.
        _status, body = await self._request(
            "GET", f"{self._base}/?module={MODULE_MENU}", headers=_XHR
        )
        if _LOGIN_FORM in body:
            raise IbokAuthError("the portal did not accept the login")
        try:
            json.loads(body)
        except ValueError as err:
            raise IbokResponseError("there is no iBOK portal at this address") from err

        self._logged_in = True

    async def async_module(self, module: str) -> Any:
        """Fetch one module's dataset, logging in again if the session expired."""
        if not self._logged_in:
            await self.async_login()

        body = await self._get_module(module)
        if body is _SESSION_EXPIRED:
            await self.async_login()
            body = await self._get_module(module)
            if body is _SESSION_EXPIRED:
                # The login itself just succeeded, so this is the portal
                # misbehaving, not a wrong password: no re-authentication.
                raise IbokResponseError(f"{module}: the portal refused a fresh session")

        if not body.strip():
            # Modules the operator has disabled answer HTTP 200 with an empty
            # body rather than 404, so this is not an error.
            return None

        try:
            return json.loads(body)
        except ValueError as err:
            raise IbokResponseError(f"{module}: {err}") from err

    async def _get_module(self, module: str) -> Any:
        status, body = await self._request(
            "GET", f"{self._base}/?module={module}", headers=_XHR
        )
        if _LOGIN_FORM in body:
            return _SESSION_EXPIRED
        if status >= 400:
            raise IbokResponseError(f"{module}: HTTP {status}")
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
        reading: float,
        reading_date: str,
        previous: str = "",
        note: str = "",
    ) -> str:
        """Submit a reading and return the portal's answer.

        The portal's own form posts into a hidden iframe, so the HTTP status
        alone does not prove the reading was accepted. What this does establish
        is that it was not refused outright: a login page means the session had
        died and nothing was taken, which is the one case safe to resend.
        """
        whole, litres = split_reading(reading)

        def form() -> aiohttp.FormData:
            # The portal's own form is multipart/form-data.
            data = aiohttp.FormData(default_to_multipart=True)
            data.add_field("id_wodom", str(meter_id))
            data.add_field("odcz_poprz", str(previous))
            data.add_field("txtDateOfReading", reading_date)
            data.add_field("txtReadingNr", whole)
            data.add_field("txtReadingFrac", litres)
            data.add_field("txtDesc", note)
            return data

        if not self._logged_in:
            await self.async_login()

        url = f"{self._base}/?module={MODULE_NOTIFY_READOUT}&action=add"
        status, body = await self._post_submission(url, form)
        if _LOGIN_FORM in body:
            # The session expired since the last request -- polling leaves hours
            # between them. The login page means the reading was not taken, so
            # one resend on a fresh session cannot duplicate it.
            await self.async_login()
            status, body = await self._post_submission(url, form)
            if _LOGIN_FORM in body:
                raise IbokResponseError("the portal refused the submission twice")

        if status >= 500:
            raise IbokOutcomeUnknownError(f"the portal answered HTTP {status}")
        if status >= 400:
            raise IbokResponseError(f"the portal refused the submission: HTTP {status}")

        # Kept at debug: this is what the first real submission has to show,
        # to learn how the portal words an accepted reading.
        _LOGGER.debug("Portal answer to a submission: %s", body[:500])
        return body

    async def _post_submission(
        self, url: str, form: Callable[[], aiohttp.FormData]
    ) -> tuple[int, str]:
        try:
            return await self._request("POST", url, data=form)
        except (IbokTimeoutError, IbokDisconnectedError) as err:
            raise IbokOutcomeUnknownError(str(err)) from err


_SESSION_EXPIRED = object()


def _as_list(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []
