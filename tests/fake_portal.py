"""A stand-in iBOK portal that the tests serve over real HTTP on localhost.

The transport has to be tested against a real server rather than a mocked
session: what matters is how aiohttp actually behaves on a redirect or a slow
answer, and a mock would only repeat what the test already assumes.

The behaviour mirrors the live portal as observed: the login POST answers
302 with an empty body back to the start page, and a submission is posted as
multipart/form-data. A submission it takes shows up in the meter's row of the
reading form as ``ido``/``isl``/``ist``; the operator's own ``do``/``sl`` stay
as they were until somebody approves it. To a session that is not logged in,
the start page is the login page, the menu is an empty body, and every other
module -- the submission included -- answers HTTP 200 with
``{"sessionExpired":true}``.
The portal has also been seen to answer those with the login page instead,
which ``FakePortal.expired_answer`` switches to.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from aiohttp import web

LOGIN_PAGE = '<html><form id="frmLogin" method="post" action="?"></form></html>'
SESSION_EXPIRED = '{"sessionExpired":true}'
# A page that always answers as to a dead session, for a submission to be
# redirected to.
_EXPIRED_PAGE = "Wygasla"
START_PAGE = '<html><a href="?logout">Wyloguj</a></html>'

USERNAME = "jan"
PASSWORD = "haslo-testowe"

METER = {
    "id_wodom": 10001,
    "numer_fabryczny": "12345678",
    "sl": "45",
    # The previous reading's own id, which a submission refers back to.
    "io": "1234567",
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
        self.readouts: list[dict[str, Any]] = []
        self.meters: list[dict[str, Any]] = []
        self.accountancy: list[dict[str, Any]] = []
        self.invoices: list[dict[str, Any]] = []
        # Modules that answer HTTP 500, as a portal does when one of its
        # back-end queries fails while the rest keep working.
        self.broken: set[str] = set()
        # The body of that HTTP 500. A proxy in front of a portal that is down
        # often sends none at all.
        self.broken_body = "Internal Server Error"
        # Modules that answer as to a dead session even right after a login
        # succeeded, as a misbehaving portal would.
        self.expired_modules: set[str] = set()
        # Modules that answer only after this many seconds.
        self.slow: dict[str, float] = {}
        self.login_status = 302
        self.login_location = "/"
        self.module_delay = 0.0
        self.submit_delay = 0.0
        self.not_an_ibok = False
        # How a module answers a session that is not logged in: "json" as the
        # live portal does now, or "login_page" as it has also been seen to.
        self.expired_answer = "json"
        # Accepted login POSTs, counted to prove concurrent requests share one.
        self.logins = 0
        # Upcoming accepted logins that still leave the session logged out, so
        # the next module request is answered as a dead session's -- as the
        # live portal now and then does right after a correct password.
        self.bounce_logins = 0
        # How a submission is answered once it has been recorded:
        # "ok" | "redirect_away" (303 to `away`) | "redirect_loop" (303 to
        # itself) | "redirect_to_missing" (303 to a page of the portal that
        # answers 404) | "redirect_to_expired" (303 to a page that answers as
        # to a dead session) | "error_expired" (HTTP 500 with a dead session's
        # answer as its body).
        self.submit_answer = "ok"
        self.away = ""
        # What the portal makes of a submission it has recorded: "accept" files
        # it in the meter's row with `accepted_status`, "refuse" files it as
        # refused (status 3) with `refusal_comment`, "ignore" leaves no trace
        # of it in the reading form at all.
        self.verdict = "accept"
        self.accepted_status = 1
        self.refusal_comment = "Odczyt niezgodny z poprzednim"

    def expire_all(self) -> None:
        """Drop every login, as the portal does once PHPSESSID times out."""
        for sid in self.sessions:
            self.sessions[sid] = False

    def _file(self, form: dict[str, str]) -> None:
        """Show a recorded submission in its meter's row, as the live portal does."""
        if self.verdict == "ignore":
            return
        for row in self.notify:
            if str(row.get("id_wodom")) != form.get("id_wodom"):
                continue
            if str(row.get("ist", "")) in ("1", "2"):
                # The live form offers no new reading while one waits or is in
                # progress; one sent anyway is not filed.
                continue
            row["ido"] = form["txtDateOfReading"]
            row["isl"] = f"{form['txtReadingNr']}.{form['txtReadingFrac']}"
            row["iid"] = str(9000 + len(self.submissions))
            if self.verdict == "refuse":
                row["ist"] = "3"
                row["tkom"] = self.refusal_comment
            else:
                row["ist"] = str(self.accepted_status)
                row["tkom"] = ""

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
                self.logins += 1
                if self.bounce_logins:
                    self.bounce_logins -= 1
                else:
                    self.sessions[sid] = True
            return web.Response(
                status=self.login_status, headers={"Location": self.login_location}
            )

        if module is None:
            page = START_PAGE if logged_in else LOGIN_PAGE
            return web.Response(text=page, content_type="text/html")

        if not logged_in or module in self.expired_modules or module == _EXPIRED_PAGE:
            if self.expired_answer == "login_page":
                return web.Response(text=LOGIN_PAGE, content_type="text/html")
            if module == "Menu":
                return web.Response(text="", content_type="text/html")
            return web.Response(text=SESSION_EXPIRED, content_type="text/html")

        if module == "NotifyReadout_v1" and request.query.get("action") == "add":
            if request.method != "POST":
                # Only a "redirect_loop" answer sends a client back here, and
                # it must keep looping rather than record a second submission.
                return web.Response(
                    status=303, headers={"Location": str(request.rel_url)}
                )
            self.submission_content_types.append(request.content_type)
            form = await request.post()
            self.submissions.append({k: str(v) for k, v in form.items()})
            self._file(self.submissions[-1])
            if self.submit_delay:
                await asyncio.sleep(self.submit_delay)
            if self.submit_answer == "redirect_away":
                return web.Response(status=303, headers={"Location": f"{self.away}/"})
            if self.submit_answer == "redirect_loop":
                return web.Response(
                    status=303, headers={"Location": str(request.rel_url)}
                )
            if self.submit_answer == "redirect_to_missing":
                return web.Response(status=303, headers={"Location": "/brak"})
            if self.submit_answer == "redirect_to_expired":
                return web.Response(
                    status=303, headers={"Location": f"/?module={_EXPIRED_PAGE}"}
                )
            if self.submit_answer == "error_expired":
                return web.Response(
                    status=500, text=SESSION_EXPIRED, content_type="text/html"
                )
            return web.Response(text="<script>ok</script>", content_type="text/html")

        if self.module_delay:
            await asyncio.sleep(self.module_delay)
        if module in self.slow:
            await asyncio.sleep(self.slow[module])
        if module in self.broken:
            return web.Response(status=500, text=self.broken_body)
        if module == "Menu":
            return web.json_response([{"nameid": "Meters_v1"}])
        datasets = {
            "NotifyReadout_v1": self.notify,
            "Readouts_v1": self.readouts,
            "Meters_v1": self.meters,
            "Accountancy_v4": self.accountancy,
            "Invoices_v1": self.invoices,
        }
        return web.json_response(datasets.get(module, []))
