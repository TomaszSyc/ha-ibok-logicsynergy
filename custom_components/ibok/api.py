"""Transport layer for iBOK portals built by LogicSynergy.

Everything that knows how the portal speaks lives here. The rest of the
integration only sees plain dictionaries, so swapping this for the mobile
application's API later means replacing one module, not rewriting the
integration.

The portal renders its UI client side from JSON datasets (MooTools + Spry),
which is why plain ``?module=<Name>`` requests return JSON rather than HTML.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal
from typing import Any, NamedTuple

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


class _Answer(NamedTuple):
    """The portal's final answer to one request, after any redirects."""

    status: int
    body: str
    # Whether the answer came from a page the portal redirected to, rather
    # than from the request itself.
    redirected: bool


class IbokError(Exception):
    """Base error for this integration."""

    # Whether the request that raised this error had already reached the
    # portal. Set by ``_request``; ``_post_submission`` uses it to tell a
    # submission that never left the machine from one whose outcome is
    # simply unknown.
    delivered = False


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
        timeout: aiohttp.ClientTimeout = _TIMEOUT,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._origin = URL(self._base).origin()
        self._username = username
        self._password = password
        self._session = session
        # Applied to every request rather than only to a session made here:
        # Home Assistant's session carries aiohttp's default of five minutes,
        # and a login stuck that long would hold every other request behind
        # the login lock.
        self._timeout = timeout
        # A session handed in belongs to whoever made it, Home Assistant
        # included, so only one made here is closed here.
        self._owns_session = session is None
        self._logged_in = False
        # One login at a time, and a count of the logins that succeeded. A
        # request that finds the session expired logs in again only if no
        # other request has done so since it went out; otherwise several
        # requests failing together would each log in, and every login would
        # throw away the session the one before it had just set up.
        self._login_lock = asyncio.Lock()
        self._generation = 0

    async def async_close(self) -> None:
        """Release the session. Called when the config entry unloads."""
        if self._owns_session:
            if self._session is not None and not self._session.closed:
                await self._session.close()
            self._session = None
        self._logged_in = False

    def _ensure_session(self) -> aiohttp.ClientSession:
        # A private session, and therefore a private cookie jar: the portal
        # identifies the account purely by PHPSESSID, so sharing Home
        # Assistant's shared session would let two config entries for two
        # different accounts overwrite each other's login. A session handed in
        # is expected to come with its own jar for the same reason.
        if self._owns_session and (self._session is None or self._session.closed):
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,
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
    ) -> _Answer:
        """One request, following redirects only within the portal's origin.

        Redirects are followed by hand because aiohttp re-sends the body of a
        307/308 to wherever it points, another host included -- for the login
        that body is the password. A hop that leaves the portal is refused
        before anything is sent to it.

        ``data`` is a factory rather than a value: a form has to be built again
        for every hop that re-sends it.

        Every raised ``IbokError`` carries ``delivered``: whether the request
        this call was asked to make had already reached the portal by the
        time things went wrong. That is true from the second hop onward --
        the first hop's response is what produced the redirect -- and true at
        the first hop for a timeout or a broken connection, since the request
        itself had already gone out. Only a connection refused or a connect
        timeout on the very first hop means nothing was sent at all.
        """
        session = self._ensure_session()
        target = URL(url, encoded=True)

        for hop in range(_MAX_REDIRECTS + 1):
            if target.origin() != self._origin:
                err = IbokResponseError(
                    f"refused to follow a redirect away from the portal, to {target.origin()}"
                )
                err.delivered = hop > 0
                raise err
            try:
                async with session.request(
                    method,
                    target,
                    data=data() if data is not None else None,
                    headers={**_HEADERS, **(headers or {})},
                    timeout=self._timeout,
                    allow_redirects=False,
                ) as response:
                    location = response.headers.get("Location")
                    if response.status in _REDIRECTS and location:
                        if response.status in (301, 302, 303):
                            method, data = "GET", None
                        target = target.join(URL(location))
                        continue
                    return _Answer(response.status, await response.text(), hop > 0)
            # Order matters. A refused connection and a connect timeout mean the
            # request never went out; any other failure may come after the
            # portal already has it.
            except aiohttp.ClientConnectorError as err:
                wrapped = IbokConnectionError(str(err))
                wrapped.delivered = hop > 0
                raise wrapped from err
            except aiohttp.ConnectionTimeoutError as err:
                wrapped = IbokConnectionError(str(err))
                wrapped.delivered = hop > 0
                raise wrapped from err
            except TimeoutError as err:
                # aiohttp raises the builtin TimeoutError for a total timeout,
                # which is not an aiohttp.ClientError.
                wrapped = IbokTimeoutError("the portal did not answer in time")
                wrapped.delivered = True
                raise wrapped from err
            except aiohttp.ClientError as err:
                wrapped = IbokDisconnectedError(str(err))
                wrapped.delivered = True
                raise wrapped from err

        err = IbokResponseError("the portal redirected too many times")
        err.delivered = True
        raise err

    async def async_login(self) -> None:
        """Establish a session. Raises IbokAuthError on bad credentials."""
        async with self._login_lock:
            await self._login()

    async def _ensure_logged_in(self) -> None:
        if self._logged_in:
            return
        async with self._login_lock:
            # Another request may have logged in while this one waited.
            if not self._logged_in:
                await self._login()

    async def _relogin(self, generation: int) -> None:
        """Log in again after a request made at ``generation`` found the session gone."""
        async with self._login_lock:
            if self._generation == generation:
                await self._login()

    async def _login(self) -> None:
        """The whole login sequence, tried a second time on a fresh cookie jar.

        The live portal now and then answers a correct password with the login
        page again. Asking for a new password then would be wrong, so a first
        refusal only discards the session cookie and starts over; a second
        one is taken at its word. The caller holds ``_login_lock``.
        """
        try:
            await self._login_once()
        except IbokAuthError:
            _LOGGER.debug("The portal refused the login, trying once more")
            self._ensure_session().cookie_jar.clear()
            await self._login_once()
        self._generation += 1

    async def _login_once(self) -> None:
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
        # The menu does: it lists modules only to a session that is logged in,
        # and answers any other session with an empty body -- but with HTTP
        # 200. An error status is the portal or a proxy in front of it failing,
        # often with no body either, and no reason to doubt the password.
        status, body, _redirected = await self._request(
            "GET", f"{self._base}/?module={MODULE_MENU}", headers=_XHR
        )
        if status >= 400:
            raise IbokResponseError(f"the portal answered the login with HTTP {status}")
        if not body.strip() or _session_expired(body):
            raise IbokAuthError("the portal did not accept the login")
        try:
            json.loads(body)
        except ValueError as err:
            raise IbokResponseError("there is no iBOK portal at this address") from err

        self._logged_in = True

    async def async_module(self, module: str) -> Any:
        """Fetch one module's dataset, logging in again if the session expired."""
        await self._ensure_logged_in()

        generation = self._generation
        body = await self._get_module(module)
        if body is _SESSION_EXPIRED:
            await self._relogin(generation)
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
        status, body, _redirected = await self._request(
            "GET", f"{self._base}/?module={module}", headers=_XHR
        )
        if _session_expired(body):
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

    async def async_menu(self) -> list[str]:
        """Names of the modules this portal offers, as its own menu lists them."""
        rows = _as_list(await self.async_module(MODULE_MENU))
        return [str(row["nameid"]) for row in rows if row.get("nameid")]

    async def async_submit_reading(
        self,
        meter_id: int,
        reading: float,
        reading_date: str,
        previous_reading_id: str = "0",
        note: str = "",
    ) -> str:
        """Submit a reading and return the portal's answer.

        ``previous_reading_id`` is the id of the reading this one follows, not
        its value: the portal reads whatever is there as a reading's number.
        Its own form sends ``0`` for a meter with no reading yet.

        The portal's own form posts into a hidden iframe, so the HTTP status
        alone does not prove the reading was accepted. What this does establish
        is that it was not refused outright: an expired session's answer means
        nothing was taken, which is the one case safe to resend.
        """
        whole, litres = split_reading(reading)

        def form() -> aiohttp.FormData:
            # The portal's own form is multipart/form-data.
            data = aiohttp.FormData(default_to_multipart=True)
            data.add_field("id_wodom", str(meter_id))
            data.add_field("odcz_poprz", str(previous_reading_id))
            data.add_field("txtDateOfReading", reading_date)
            data.add_field("txtReadingNr", whole)
            data.add_field("txtReadingFrac", litres)
            data.add_field("txtDesc", note)
            return data

        await self._ensure_logged_in()

        url = f"{self._base}/?module={MODULE_NOTIFY_READOUT}&action=add"
        generation = self._generation
        status, body, redirected = await self._post_submission(url, form)
        if _not_taken(status, body, redirected):
            # The session expired since the last request -- polling leaves hours
            # between them. An expired session's answer means the reading was
            # not taken, so one resend on a fresh session cannot duplicate it.
            await self._relogin(generation)
            status, body, redirected = await self._post_submission(url, form)
            if _not_taken(status, body, redirected):
                raise IbokResponseError("the portal refused the submission twice")

        # An error from a page the portal redirected to says nothing about the
        # submission itself: the redirect was the portal's answer to it, so
        # the reading may well be recorded.
        if status >= 500 or (status >= 400 and redirected):
            raise IbokOutcomeUnknownError(f"the portal answered HTTP {status}")
        if status >= 400:
            raise IbokResponseError(f"the portal refused the submission: HTTP {status}")
        if redirected and _session_expired(body):
            raise IbokOutcomeUnknownError(
                "the portal took the submission to a page of an expired session"
            )

        # Kept at debug: the answer carries no verdict -- the reading form,
        # read again afterwards, is what shows whether the portal has it.
        _LOGGER.debug("Portal answer to a submission: %s", body[:500])
        return body

    async def _post_submission(
        self, url: str, form: Callable[[], aiohttp.FormData]
    ) -> _Answer:
        try:
            return await self._request("POST", url, data=form)
        except IbokError as err:
            if err.delivered:
                raise IbokOutcomeUnknownError(str(err)) from err
            raise


_SESSION_EXPIRED = object()


def _session_expired(body: str) -> bool:
    """Whether the portal answered as it does to a session that is not logged in.

    The live portal answers a module request on such a session with HTTP 200
    and ``{"sessionExpired":true}``; it has also been seen to send the login
    page instead. Both mean the request was not acted on.
    """
    if _LOGIN_FORM in body:
        return True
    # Checked before parsing so a module's full dataset is not parsed twice.
    if "sessionExpired" not in body:
        return False
    try:
        data = json.loads(body)
    except ValueError:
        return False
    return isinstance(data, dict) and bool(data.get("sessionExpired"))


def _not_taken(status: int, body: str, redirected: bool) -> bool:
    """Whether a submission's answer proves the reading was not recorded.

    Only the POST's own successful answer can say that. Behind a redirect the
    portal had already acted on the form, and an error status says nothing
    about how far it got, so either way the reading may be on the bill and
    resending it could put it there twice.
    """
    return not redirected and status < 400 and _session_expired(body)


def _as_list(data: Any) -> list[dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []
