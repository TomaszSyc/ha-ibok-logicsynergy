"""The transport against a real HTTP server: login, redirects, timeouts, submission.

Each test pins down a failure that was confirmed against the live portal or
the aiohttp that Home Assistant ships, not a hypothetical one.
"""

from __future__ import annotations

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
    return IbokApi(portal.base, USERNAME, password, session=http_session)


async def _submit(api: IbokApi, reading: float = 48.0) -> str:
    return await api.async_submit_reading(
        meter_id=10001, reading=reading, reading_date="2026-10-01", previous="45"
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
    api = _api(portal, http_session)
    await api.async_login()
    portal.expire_all()

    await _submit(api)

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


async def test_submission_posts_the_portal_form(portal, http_session) -> None:
    api = _api(portal, http_session)
    await api.async_login()

    await api.async_submit_reading(
        meter_id=10001,
        reading=48.05,
        reading_date="2026-10-01",
        previous="45",
        note="uwaga",
    )

    assert portal.submissions == [
        {
            "id_wodom": "10001",
            "odcz_poprz": "45",
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
