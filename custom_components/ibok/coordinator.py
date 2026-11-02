"""Polling coordinator for the iBOK integration."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    IbokApi,
    IbokAuthError,
    IbokDisconnectedError,
    IbokError,
    IbokResponseError,
    IbokTimeoutError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .ledger import SubmissionLedger
from .seen import SeenStore

_LOGGER = logging.getLogger(__name__)

# id_wodom values already warned about, so a meter stuck with a bad one is not
# logged again on every poll.
_unusable_meter_ids: set[Any] = set()


def parse_number(value: Any) -> float | None:
    """A number as the portal writes it, wherever a field can hold one.

    Portal numbers can carry a decimal comma and thousands separators, plain
    space or non-breaking space alike -- ``\\s`` matches both in Python 3.
    ``None`` and anything that still does not parse come back as ``None``: a
    portal field is never guaranteed to hold a number.
    """
    if value is None:
        return None
    text = re.sub(r"\s", "", str(value)).replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def meter_id(row: Mapping[str, Any]) -> int | None:
    """A meter's internal id as an int, or ``None`` if the portal sent nothing usable.

    ``id_wodom`` is what a submission and a lookup key on; a meter reporting
    one that is not a plain number can still be listed and read, but nothing
    can key on it, so it is left out wherever an id is required.
    """
    value = row.get("id_wodom")
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        if value not in _unusable_meter_ids:
            _unusable_meter_ids.add(value)
            _LOGGER.warning(
                "Meter id %r from the portal is not a usable number, skipping it", value
            )
        return None


def meter_serial(row: Mapping[str, Any]) -> str:
    """A meter's serial, spelled the same way everywhere in the integration.

    The serial names devices and option keys, and three modules carry it: the
    readouts and the reading form as ``numer_fabryczny``, the meter list as
    ``numer_fabr``. Read differently, the same meter would become two devices.
    Where the form lists a meter without a serial, its internal id stands in,
    so it still gets a button; the other modules have no such id.
    """
    serial = str(row.get("numer_fabryczny") or row.get("numer_fabr") or "").strip()
    return serial or str(row.get("id_wodom") or "").strip()


DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S")


def parse_date(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            # A reading date is a civil date with no time of day. Attaching a
            # timezone to it would invent precision the portal never sent.
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def readouts_for(data: Mapping[str, Any], serial: str) -> list[dict[str, Any]]:
    """Readouts for one meter, oldest first.

    The portal does not promise an order, so they are sorted by date here
    rather than trusting the position in the list. The sort is stable, so
    rows sharing a day keep the order the portal gave them.
    """
    for row in data.get("readouts") or []:
        if meter_serial(row) != serial:
            continue
        rows = [r for r in row.get("odczyty") or [] if isinstance(r, dict)]
        return sorted(rows, key=lambda r: parse_date(r.get("do")) or date.min)
    return []


def fraction_digits(meter: Mapping[str, Any] | None) -> int:
    """Fractional digits the portal declares for a meter's dial; none if absent."""
    try:
        return max(0, int((meter or {}).get("l_cyfr_p")))
    except (TypeError, ValueError):
        return 0


def settlement_query(
    accountancy: Mapping[str, Any] | None,
) -> tuple[str, str, str] | None:
    """The document to ask the portal about: the newest one with a payment on it.

    The portal's own type for a settlement is the document's with ``Rozr`` in
    front, and the settlement query wants the document's.
    """
    newest: tuple[date, Mapping[str, Any]] | None = None
    for row in (accountancy or {}).get("pozycje") or []:
        if not isinstance(row, dict) or (parse_number(row.get("ma")) or 0) <= 0:
            continue
        paid_on = parse_date(row.get("dw"))
        if paid_on is not None and (newest is None or paid_on > newest[0]):
            newest = (paid_on, row)
    if newest is None:
        return None
    row = newest[1]
    return (
        str(row.get("idrec")),
        re.sub(r"^rozr", "", str(row.get("ty") or ""), flags=re.IGNORECASE),
        str(row.get("nr")),
    )


def last_payment(operations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The latest payment among a document's operations, as amount and date.

    Debit rows are charges, not payments. Only these two values are kept: the
    rest of the answer sits beside a name and an address.
    """
    newest: tuple[date, float] | None = None
    for row in operations:
        amount = parse_number(row.get("ma"))
        paid_on = parse_date(row.get("dataWplaty"))
        if amount is None or amount <= 0 or paid_on is None:
            continue
        if newest is None or paid_on > newest[0]:
            newest = (paid_on, amount)
    if newest is None:
        return None
    return {"amount": newest[1], "date": newest[0].isoformat()}


class IbokCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches each module the integration uses, each on its own.

    One module timing out must not take down the rest: an unanswered invoice
    list would otherwise make the balance, every meter and the submit button
    unavailable along with it. A module that fails keeps its last good data,
    and ``failed`` says which ones did, for the entities built on them.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: IbokApi,
        scan_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=scan_interval,
            config_entry=entry,
        )
        self.api = api
        self.failed: frozenset[str] = frozenset()
        # Readings typed by hand, by meter id, with the time they were typed,
        # until they are sent. Kept here rather than in the number entity's
        # state, whose timestamp a restart would renew: a value typed last week
        # would then look as fresh as one typed a minute ago.
        self.typed_readings: dict[int, tuple[float, datetime]] = {}
        # One ledger per account: what has been sent today and what is
        # still waiting on an answer from the portal.
        self.ledger = SubmissionLedger(hass, entry.entry_id)
        # The submission each meter's event entity last announced; loaded in setup.
        self.seen = SeenStore(hass, entry.entry_id)
        # Run with each successful refresh's data, e.g. to write statistics.
        # Set before the first refresh by whoever wants it.
        self.post_refresh: (
            Callable[[dict[str, Any], frozenset[str]], Awaitable[None]] | None
        ) = None

    async def _async_update_data(self) -> dict[str, Any]:
        previous = self.data or {}
        data: dict[str, Any] = {}
        errors: dict[str, IbokError] = {}
        # Modules that were put to the portal this refresh.
        asked = 0

        async def fetch_payment() -> dict[str, Any] | None:
            nonlocal asked
            # Without this refresh's accountancy there is nothing to ask about,
            # and the last payment stands.
            if "accountancy" in errors or data.get("accountancy") is None:
                asked -= 1
                return previous.get("payment")
            query = settlement_query(data["accountancy"])
            if query is None:
                return None
            answer = await self.api.async_settlement(*query)
            return last_payment(answer["operations"])

        fetchers: dict[str, Callable[[], Awaitable[Any]]] = {
            "readouts": self.api.async_readouts,
            "notify": self.api.async_notify_readout,
            # Legalisation dates, and each meter's share of water and sewage.
            "meters": self.api.async_meters,
            "accountancy": self.api.async_accountancy,
            # Asked per document, so it follows accountancy and needs its answer.
            "payment": fetch_payment,
            "invoices": self.api.async_invoices,
        }
        # One after another, not in parallel: the modules share one session,
        # and a login renewed halfway through must not race another request.
        for key, fetch in fetchers.items():
            asked += 1
            try:
                data[key] = await fetch()
            except IbokAuthError as err:
                # Surfaces the re-authentication flow instead of leaving every
                # entity unavailable with no explanation.
                raise ConfigEntryAuthFailed(str(err)) from err
            except (IbokTimeoutError, IbokDisconnectedError, IbokResponseError) as err:
                # The portal was reached; only this module's answer failed.
                errors[key] = err
                data[key] = previous.get(key)
            except IbokError as err:
                # Not reaching the portal at all is no module's fault. Stop here
                # rather than wait out the same failure once per module.
                raise UpdateFailed(str(err)) from err

        if len(errors) == asked:
            # Nothing answered: the portal is down, not a module.
            raise UpdateFailed(str(next(iter(errors.values()))))

        failed = frozenset(errors)
        for key in sorted(failed - self.failed):
            _LOGGER.warning(
                "The portal did not return %s, its entities are unavailable: %s",
                key,
                errors[key],
            )
        for key in sorted(self.failed - failed):
            _LOGGER.info("The portal returns %s again", key)
        self.failed = failed
        # A reading whose outcome was unknown may show up in this refresh.
        self.ledger.reconcile(data, dt_util.now().date())
        if self.post_refresh is not None:
            try:
                await self.post_refresh(data, failed)
            # A side effect, e.g. statistics, must never fail the refresh itself.
            except Exception:
                _LOGGER.warning("The post-refresh step failed", exc_info=True)
        return data

    def meter_by_id(self, wanted: int) -> dict[str, Any] | None:
        """Look up a meter in the submission dataset by its internal id.

        The id is read with ``meter_id``, as everywhere else, so a row whose
        ``id_wodom`` comes padded or as a string still matches.
        """
        for row in self.submittable_meters:
            if meter_id(row) == wanted:
                return row
        return None

    @property
    def submittable_meters(self) -> list[dict[str, Any]]:
        """Meters the portal currently accepts a reading for."""
        return list((self.data or {}).get("notify") or [])
