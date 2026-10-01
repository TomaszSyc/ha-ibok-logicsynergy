"""The transport against a real HTTP server: login, redirects, timeouts, submission.

Each test pins down a failure that was confirmed against the live portal or
the aiohttp that Home Assistant ships, not a hypothetical one.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aiohttp
import pytest
from fake_portal import PASSWORD, USERNAME

from custom_components.ibok.api import (
    IbokApi,
    IbokAuthError,
    IbokConnectionError,
    IbokOutcomeUnknownError,
    IbokResponseError,
    split_reading,
)


def _api(portal, http_session, password: str = PASSWORD) -> IbokApi:
    return IbokApi(
        portal.base,
        USERNAME,
        password,
        session=http_session,
        timeout=http_session.timeout,
    )


async def _submit(api: IbokApi, reading: float = 48.0) -> str:
    return await api.async_submit_reading(
        meter_id=10001,
        reading=reading,
        reading_date="2026-10-01",
        previous_reading_id="1234567",
    )


async def test_login_follows_the_portal_redirect(portal, http_session) -> None:
    """The live portal answers the login POST with 302 and an empty body."""
    await _api(portal, http_session).async_login()

    assert ("GET", "/?module=Menu") in portal.requests


async def test_wrong_password_is_an_auth_error(portal, http_session) -> None:
    # A wrong password gets the same 302 as a right one, so the answer to the
    # POST cannot tell them apart -- only a module request can.
    with pytest.raises(IbokAuthError):
        await _api(portal, http_session, password="zle").async_login()


async def test_a_bounced_login_is_retried_once(portal, http_session) -> None:
    """A correct password sent back to the login page once is not a wrong one."""
    portal.bounce_logins = 1

    await _api(portal, http_session).async_login()

    assert portal.logins == 2


@pytest.mark.parametrize("answer", ["json", "login_page"])
async def test_concurrent_expiries_log_in_once(portal, http_session, answer) -> None:
    """Requests that all find the session expired share one fresh login."""
    portal.expired_answer = answer
    api = _api(portal, http_session)
    await api.async_login()
    portal.expire_all()
    before = portal.logins

    await asyncio.gather(api.async_readouts(), api.async_menu(), api.async_invoices())

    assert portal.logins == before + 1


async def test_a_portal_error_on_the_menu_is_not_a_wrong_password(
    portal, http_session
) -> None:
    """A proxy in front of a portal that is down answers 5xx with no body."""
    portal.broken, portal.broken_body = {"Menu"}, ""

    with pytest.raises(IbokResponseError):
        await _api(portal, http_session).async_login()


async def test_a_module_expired_again_after_a_fresh_login_is_a_portal_fault(
    portal, http_session
) -> None:
    """The login just succeeded, so asking for the password again would be wrong."""
    api = _api(portal, http_session)
    await api.async_login()
    portal.expired_modules = {"Readouts_v1"}
    before = portal.logins

    with pytest.raises(IbokResponseError):
        await api.async_readouts()

    assert portal.logins == before + 1


async def test_a_given_session_outlives_close(portal, http_session) -> None:
    api = _api(portal, http_session)
    await api.async_login()

    await api.async_close()

    assert not http_session.closed
    assert not api._logged_in


async def test_a_given_session_still_gets_the_45_s_timeout(portal, monkeypatch) -> None:
    """Home Assistant's session would otherwise wait out aiohttp's 300 s default."""
    seen = []
    request = aiohttp.ClientSession.request

    def spy(self, *args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return request(self, *args, **kwargs)

    monkeypatch.setattr(aiohttp.ClientSession, "request", spy)
    async with aiohttp.ClientSession(
        cookie_jar=aiohttp.CookieJar(unsafe=True)
    ) as session:
        await IbokApi(portal.base, USERNAME, PASSWORD, session=session).async_login()

    assert seen
    assert all(timeout == aiohttp.ClientTimeout(total=45) for timeout in seen)


async def test_address_that_is_not_a_portal_is_refused(portal, http_session) -> None:
    portal.not_an_ibok = True

    with pytest.raises(IbokResponseError):
        await _api(portal, http_session).async_login()


@pytest.mark.parametrize("status", [302, 307, 308])
async def test_redirect_away_from_the_portal_is_never_followed(
    portal, other_site, http_session, status
) -> None:
    """On 307/308 aiohttp re-sends the login form, password included, anywhere."""
    portal.login_status = status
    portal.login_location = f"{other_site.base}/"

    with pytest.raises(IbokResponseError):
        await _api(portal, http_session).async_login()

    assert other_site.requests == []


async def test_timeout_is_a_connection_error(portal, http_session) -> None:
    # aiohttp raises the builtin TimeoutError for a total timeout, which is not
    # an aiohttp.ClientError and used to escape every handler.
    api = _api(portal, http_session)
    await api.async_login()
    portal.module_delay = 2

    with pytest.raises(IbokConnectionError):
        await api.async_module("Meters_v1")


async def test_submission_on_an_expired_session_is_sent_exactly_once(
    portal, http_session
) -> None:
    """The session dies between polls; the POST then lands on the login page."""
    portal.expired_answer = "login_page"
    api = _api(portal, http_session)
    await api.async_login()
    portal.expire_all()

    await _submit(api)

    assert len(portal.submissions) == 1


async def test_a_submission_answered_session_expired_is_resent_once(
    portal, http_session
) -> None:
    """The live portal answers a dead session's POST with {"sessionExpired":true}."""
    api = _api(portal, http_session)
    await api.async_login()
    portal.expire_all()
    before = portal.logins

    answer = await _submit(api)

    assert _submission_posts(portal) == 2
    assert portal.logins == before + 1
    assert len(portal.submissions) == 1
    assert "sessionExpired" not in answer


def _submission_posts(portal) -> int:
    return sum(
        1 for method, url in portal.requests if method == "POST" and "action=add" in url
    )


async def test_a_submission_expired_again_after_a_fresh_login_is_refused(
    portal, http_session
) -> None:
    api = _api(portal, http_session)
    await api.async_login()
    portal.expired_modules = {"NotifyReadout_v1"}

    with pytest.raises(IbokResponseError):
        await _submit(api)

    assert _submission_posts(portal) == 2
    assert portal.submissions == []


@pytest.mark.parametrize("mode", ["redirect_to_expired", "error_expired"])
async def test_a_redirected_or_failed_submission_is_never_resent(
    portal, http_session, mode
) -> None:
    """Only the POST's own answer can say the reading was not taken."""
    portal.submit_answer = mode
    api = _api(portal, http_session)
    await api.async_login()

    with pytest.raises(IbokOutcomeUnknownError):
        await _submit(api)

    assert _submission_posts(portal) == 1
    assert len(portal.submissions) == 1


async def test_submission_timeout_is_reported_as_outcome_unknown(
    portal, http_session
) -> None:
    """The portal took the reading but the answer never came back."""
    api = _api(portal, http_session)
    await api.async_login()
    portal.submit_delay = 2

    with pytest.raises(IbokOutcomeUnknownError):
        await _submit(api)

    assert len(portal.submissions) == 1


@pytest.mark.parametrize(
    "mode", ["redirect_away", "redirect_loop", "redirect_to_missing"]
)
async def test_a_failure_after_delivery_is_an_unknown_outcome(
    portal, other_site, http_session, mode
) -> None:
    """The portal already has the reading; whatever went wrong after that is unknown, not lost."""
    portal.submit_answer, portal.away = mode, other_site.base
    api = _api(portal, http_session)

    with pytest.raises(IbokOutcomeUnknownError):
        await _submit(api)

    assert len(portal.submissions) == 1


class _RedirectThenRefuse:
    """A session whose first request is redirected within the portal and whose
    second cannot connect at all.

    A real server cannot refuse the second connection on the same origin
    after accepting the first, so this one hop is stubbed.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def request(self, method, target, **_kwargs):
        self.calls.append((method, str(target)))
        if len(self.calls) > 1:
            key = SimpleNamespace(host="ibok.przyklad.pl", port=443, ssl=True)
            raise aiohttp.ClientConnectorError(key, OSError(111, "refused"))
        response = SimpleNamespace(status=303, headers={"Location": "/?gotowe"})
        return _Opened(response)


class _Opened:
    def __init__(self, response) -> None:
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *_exc) -> None:
        return None


async def test_a_refused_connection_after_a_redirect_is_an_unknown_outcome() -> None:
    """The redirect was the portal's answer to the POST, so the POST got there."""
    session = _RedirectThenRefuse()
    api = IbokApi("https://ibok.przyklad.pl", USERNAME, PASSWORD, session=session)
    api._logged_in = True

    with pytest.raises(IbokOutcomeUnknownError):
        await _submit(api)

    assert [method for method, _url in session.calls] == ["POST", "GET"]


async def test_a_refused_connection_is_still_nothing_sent(
    socket_enabled, unused_tcp_port
) -> None:
    """Nothing ever left the machine, so this is the one case still safe to retry blindly."""
    api = IbokApi(f"http://127.0.0.1:{unused_tcp_port}", USERNAME, "x")
    api._logged_in = True

    with pytest.raises(IbokConnectionError) as info:
        await _submit(api)

    assert not isinstance(info.value, IbokOutcomeUnknownError)
    await api.async_close()


async def test_submission_posts_the_portal_form(portal, http_session) -> None:
    api = _api(portal, http_session)
    await api.async_login()

    await api.async_submit_reading(
        meter_id=10001,
        reading=48.05,
        reading_date="2026-10-01",
        previous_reading_id="1234567",
        note="uwaga",
    )

    assert portal.submissions == [
        {
            "id_wodom": "10001",
            "odcz_poprz": "1234567",
            "txtDateOfReading": "2026-10-01",
            "txtReadingNr": "48",
            "txtReadingFrac": "050",
            "txtDesc": "uwaga",
        }
    ]
    # The portal's own form is multipart/form-data.
    assert portal.submission_content_types == ["multipart/form-data"]


@pytest.mark.parametrize(
    ("reading", "expected"),
    [
        (48.0, ("48", "000")),
        (48.05, ("48", "050")),
        (45.29, ("45", "290")),
        # Truncated, never rounded: 1000 litres would be one more cubic metre.
        (48.9996, ("48", "999")),
        (0.001, ("0", "001")),
        (99999.999, ("99999", "999")),
    ],
)
def test_split_reading(reading: float, expected: tuple[str, str]) -> None:
    assert split_reading(reading) == expected


def test_split_reading_refuses_a_negative_value() -> None:
    with pytest.raises(ValueError):
        split_reading(-1.0)
