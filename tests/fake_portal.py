"""A stand-in iBOK portal that the tests serve over real HTTP on localhost.

The transport has to be tested against a real server rather than a mocked
session: what matters is how aiohttp actually behaves on a redirect or a slow
answer, and a mock would only repeat what the test already assumes.

The behaviour mirrors the live portal as observed: the login POST answers
302 with an empty body back to the start page, a module returns the login page
to a session that is not logged in, and a submission is posted as
multipart/form-data.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from aiohttp import web

LOGIN_PAGE = '<html><form id="frmLogin" method="post" action="?"></form></html>'
START_PAGE = '<html><a href="?logout">Wyloguj</a></html>'

USERNAME = "jan"
PASSWORD = "haslo-testowe"

METER = {
    "id_wodom": 10001,
    "numer_fabryczny": "12345678",
    "sl": "45",
    "min_zakres": "45",
    "zakres": "99999",
    "l_cyfr_l": 5,
}


class FakePortal:
    """One iBOK instance: cookie sessions, JSON modules and submissions."""

    def __init__(self) -> None:
        self.base = ""
        self.sessions: dict[str, bool] = {}
        self.requests: list[tuple[str, str]] = []
        self.submissions: list[dict[str, str]] = []
        self.submission_content_types: list[str] = []
        self.notify: list[dict[str, Any]] = [dict(METER)]
        self.login_status = 302
        self.login_location = "/"
        self.module_delay = 0.0
        self.submit_delay = 0.0
        self.not_an_ibok = False

    def expire_all(self) -> None:
        """Drop every login, as the portal does once PHPSESSID times out."""
        for sid in self.sessions:
            self.sessions[sid] = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_route("*", "/", self._handle)
        return app

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        self.requests.append((request.method, str(request.rel_url)))

        sid = request.cookies.get("PHPSESSID")
        new_session = sid is None or sid not in self.sessions
        if new_session:
            sid = secrets.token_hex(8)
            self.sessions[sid] = False

        response = await self._answer(request, sid)
        if new_session:
            response.set_cookie("PHPSESSID", sid)
        return response

    async def _answer(self, request: web.Request, sid: str) -> web.StreamResponse:
        if self.not_an_ibok:
            return web.Response(
                text="<html>Strona firmy</html>", content_type="text/html"
            )

        module = request.query.get("module")
        logged_in = self.sessions[sid]

        if request.method == "POST" and module is None:
            form = await request.post()
            if (
                form.get("user") == USERNAME
                and form.get("pass") == PASSWORD
                and form.get("login") == "Zaloguj"
            ):
                self.sessions[sid] = True
            return web.Response(
                status=self.login_status, headers={"Location": self.login_location}
            )

        if module is None:
            page = START_PAGE if logged_in else LOGIN_PAGE
            return web.Response(text=page, content_type="text/html")

        if not logged_in:
            return web.Response(text=LOGIN_PAGE, content_type="text/html")

        if module == "NotifyReadout_v1" and request.query.get("action") == "add":
            self.submission_content_types.append(request.content_type)
            form = await request.post()
            self.submissions.append({k: str(v) for k, v in form.items()})
            if self.submit_delay:
                await asyncio.sleep(self.submit_delay)
            return web.Response(text="<script>ok</script>", content_type="text/html")

        if self.module_delay:
            await asyncio.sleep(self.module_delay)
        if module == "Menu":
            return web.json_response([{"nameid": "Meters_v1"}])
        if module == "NotifyReadout_v1":
            return web.json_response(self.notify)
        return web.json_response([])
