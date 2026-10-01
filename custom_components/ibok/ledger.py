"""Per-account bookkeeping for meter-reading submissions.

One instance lives on the coordinator (``IbokCoordinator.ledger``) and answers
one question before anything reaches the portal: has this exact reading
already gone out, or is a submission for this meter still open? The checks
here are pure and testable without Home Assistant; only turning an unknown
outcome into a Repairs issue needs ``hass``. Built with ``hass=None``, the
ledger keeps its blocks in memory alone.

The issue is what carries a block across a restart: it is persistent, and
``restore`` reads the blocks back from it when the entry is set up again.

``coordinator.py`` constructs a ``SubmissionLedger``, so this module must not
import from it at module level -- that would be a straight import cycle. The
handful of helpers it needs (``meter_id``, ``meter_serial``, ``parse_number``)
are imported inside the methods that use them instead: by the time a method
runs, both modules have finished loading regardless of which one Python
happened to import first.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_ISSUE_PREFIX = "outcome_unknown_"


def issue_id(entry_id: str, meter_id: int) -> str:
    """The Repairs issue for one meter of one account.

    The entry id is part of it because two accounts can have meters with the
    same id; each must get, and clear, only its own issue.
    """
    return f"{_ISSUE_PREFIX}{entry_id}_{meter_id}"


def _issues_of(hass: HomeAssistant, entry_id: str) -> list[ir.IssueEntry]:
    """This account's unknown-outcome issues, as the issue registry holds them."""
    return [
        issue
        for issue in ir.async_get(hass).issues.values()
        if issue.domain == DOMAIN
        and issue.issue_id.startswith(_ISSUE_PREFIX)
        and (issue.data or {}).get("entry_id") == entry_id
    ]


def remove_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Delete every unknown-outcome issue of an account that is going away."""
    for issue in _issues_of(hass, entry_id):
        ir.async_delete_issue(hass, DOMAIN, issue.issue_id)


def reading_text(value: float) -> str:
    """A reading as a person reads it: no trailing .0 on a whole reading."""
    return str(int(value)) if value == int(value) else str(value)


@dataclass(frozen=True)
class Attempt:
    """One reading the ledger has recorded, sent or still waiting on an answer."""

    meter_id: int
    serial: str
    value: float
    day: date
    recorded_on: date


class SubmissionLedger:
    """Remembers what has been sent for one account, and refuses a repeat.

    Two lookups back this. ``_sent`` holds attempts a submission actually
    completed; it is forgotten once the calendar day changes -- a repeat a
    day later is a correction, not a duplicate, and none of it is meant to
    survive a restart. ``_pending`` holds attempts whose outcome the portal
    never confirmed, keyed by meter id; these block that meter until the
    pending outcome is cleared or ``reconcile`` sees the portal has the
    reading after all.
    """

    def __init__(self, hass: HomeAssistant | None, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._sent: list[Attempt] = []
        self._pending: dict[int, Attempt] = {}
        self._busy: set[int] = set()

    @asynccontextmanager
    async def lock(self, meter_id: int) -> AsyncIterator[None]:
        """Hold the one submission in flight for a meter; refuse a second at once.

        The check and the add to ``_busy`` have no ``await`` between them, so
        on the single-threaded event loop nothing can interleave -- a plain
        set is enough here, an ``asyncio.Lock`` would only add a wait this is
        meant to avoid.
        """
        if meter_id in self._busy:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="submit_in_progress"
            )
        self._busy.add(meter_id)
        try:
            yield
        finally:
            self._busy.discard(meter_id)

    def check(
        self, row: Mapping[str, Any], value: float, day: date, today: date
    ) -> None:
        """Refuse a reading the portal already has or the ledger already sent.

        Checked in this order: a pending unknown outcome blocks everything
        for its meter, whatever is being sent; then the portal's own row, in
        case it already carries this exact reading; then this ledger's own
        memory of what went out today.
        """
        from .coordinator import meter_id, parse_number

        self._forget_stale(today)
        mid = meter_id(row)

        pending = self._pending.get(mid)
        if pending is not None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="submit_outcome_pending",
                translation_placeholders={"serial": pending.serial},
            )

        previous_date = dt_util.parse_date(str(row.get("do") or "").strip())
        previous_value = parse_number(row.get("sl"))
        if previous_date == day and previous_value == value:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="reading_already_recorded",
                translation_placeholders={
                    "reading": reading_text(value),
                    "date": str(day),
                },
            )

        if any(
            a.meter_id == mid
            and a.day == day
            and a.value == value
            and a.recorded_on == today
            for a in self._sent
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="reading_already_sent",
                translation_placeholders={
                    "reading": reading_text(value),
                    "date": str(day),
                },
            )

    def record_sent(
        self, meter_id: int, serial: str, value: float, day: date, today: date
    ) -> None:
        """Remember a reading that went out, so a repeat today is refused."""
        self._forget_stale(today)
        self._sent.append(Attempt(meter_id, serial, value, day, today))

    def record_unknown(
        self,
        meter_id: int,
        serial: str,
        value: float,
        day: date,
        today: date,
        at: datetime,
    ) -> None:
        """Block a meter whose last submission never got a confirmed outcome.

        The block itself lives in memory, so ``check`` refuses further
        submissions for this meter until it is cleared. With ``hass``, a
        persistent Repairs issue tells the user what went out and when --
        ``at`` is the moment of sending -- and keeps the block across a
        restart.
        """
        self._pending[meter_id] = Attempt(meter_id, serial, value, day, today)
        if self._hass is None:
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id(self._entry_id, meter_id),
            is_fixable=True,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="outcome_unknown",
            translation_placeholders={
                "serial": serial,
                "reading": reading_text(value),
                "date": day.isoformat(),
                # The day it went out, which for a reading dated back is not
                # the reading's own date.
                "sent_date": dt_util.as_local(at).date().isoformat(),
                "time": dt_util.as_local(at).strftime("%H:%M"),
            },
            # Plain JSON types only: the issue registry stores this as is.
            data={
                "entry_id": self._entry_id,
                "meter_id": meter_id,
                "serial": serial,
                "value": float(value),
                "day": day.isoformat(),
            },
        )

    def pending(self, meter_id: int) -> Attempt | None:
        """The attempt still blocking this meter, if any."""
        return self._pending.get(meter_id)

    def clear_unknown(self, meter_id: int) -> None:
        """Lift the block once the pending outcome is confirmed, with its issue."""
        self._pending.pop(meter_id, None)
        self._delete_issue(meter_id)

    def reconcile(self, data: Mapping[str, Any], today: date) -> None:
        """Turn a pending attempt into a sent one once the portal shows it.

        A reading whose outcome was unknown counts as sent the moment the
        readouts or the reading form show its exact ``(day, value)`` for that
        meter, whichever the portal updates first.
        """
        from .coordinator import meter_id, meter_serial, parse_number

        readouts = data.get("readouts") or []
        notify = data.get("notify") or []

        for mid, attempt in list(self._pending.items()):
            confirmed = any(
                dt_util.parse_date(str(entry.get("do") or "").strip()) == attempt.day
                and parse_number(entry.get("sl")) == attempt.value
                for row in readouts
                if meter_serial(row) == attempt.serial
                for entry in row.get("odczyty") or []
            )
            if not confirmed:
                confirmed = any(
                    meter_id(row) == mid
                    and dt_util.parse_date(str(row.get("do") or "").strip())
                    == attempt.day
                    and parse_number(row.get("sl")) == attempt.value
                    for row in notify
                )
            if confirmed:
                del self._pending[mid]
                self._delete_issue(mid)
                self._sent.append(
                    Attempt(
                        attempt.meter_id,
                        attempt.serial,
                        attempt.value,
                        attempt.day,
                        today,
                    )
                )

    def restore(self) -> None:
        """Rebuild pending blocks from this account's issues after a restart.

        Only issues whose ``data`` names this entry are taken, so an account
        never inherits a block from another one with the same meter id. An
        issue whose ``data`` cannot be read back is skipped rather than
        allowed to stop the entry from setting up.
        """
        if self._hass is None:
            return
        today = dt_util.now().date()
        for issue in _issues_of(self._hass, self._entry_id):
            data = issue.data or {}
            try:
                mid = int(data["meter_id"])
                attempt = Attempt(
                    mid,
                    str(data["serial"]),
                    float(data["value"]),
                    date.fromisoformat(str(data["day"])),
                    today,
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._pending[mid] = attempt

    def _delete_issue(self, meter_id: int) -> None:
        """Drop this meter's issue; deleting one that is already gone is fine."""
        if self._hass is not None:
            ir.async_delete_issue(
                self._hass, DOMAIN, issue_id(self._entry_id, meter_id)
            )

    def _forget_stale(self, today: date) -> None:
        """Drop sent attempts recorded on any day but today."""
        self._sent = [a for a in self._sent if a.recorded_on == today]
