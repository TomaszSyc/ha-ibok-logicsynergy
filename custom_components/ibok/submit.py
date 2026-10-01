"""Submitting a meter reading, the one path in this integration that lands on a bill.

Shared by the submit_reading service and the button, so both go through the
same checks against the portal's current data.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .api import IbokError, IbokOutcomeUnknownError
from .const import DOMAIN
from .coordinator import fraction_digits, meter_id, meter_serial, parse_number
from .ledger import reading_text

if TYPE_CHECKING:
    from .coordinator import IbokCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Account:
    """What choosing a meter needs to know about one loaded config entry."""

    entry_id: str
    title: str
    meters: list[dict[str, Any]]


def _refuse(key: str, **placeholders: str) -> ServiceValidationError:
    """A refusal the user can act on; nothing was sent."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _failure(key: str, **placeholders: str) -> HomeAssistantError:
    """A failure of the portal or of the connection to it."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def truncate_to_dial(value: float, digits: int) -> float:
    """Cut a source value down to the precision the meter is read at.

    A source entity can be far more precise than the dial -- a radio overlay
    reports litres. The operator records what the dial shows, so the extra
    digits are not a better reading, they are a different one.

    How many digits the dial shows is the portal's own answer: it publishes a
    fractional digit count per meter and omits it for meters read in whole
    cubic metres. Truncation, not rounding: 48.6 is a dial still showing 48.

    Decimal, because ``floor(value * 10**digits)`` is wrong for values binary
    floating point cannot hold: 0.29 * 100 is 28.999999999999996, which
    truncates to 0.28 and sends a reading one unit below the dial.
    """
    quantum = Decimal(1).scaleb(-digits) if digits > 0 else Decimal(1)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_DOWN))


def resolve_target(
    accounts: Iterable[Account], target_id: int | None, entry_id: str | None
) -> tuple[str, int]:
    """Pick the one account and meter a service call means, or refuse.

    Every loaded account is searched, not only the first. A meter id is only
    ambiguous when two portals happen to use the same one, and then the caller
    has to name the account rather than have one picked for them. A meter
    whose own id is not a usable number is never a candidate, named or not.
    """
    pool = [a for a in accounts if entry_id is None or a.entry_id == entry_id]
    if entry_id is not None and not pool:
        raise _refuse("no_account_with_id", entry_id=entry_id)

    matches = [
        (account, meter, mid)
        for account in pool
        for meter in account.meters
        if (mid := meter_id(meter)) is not None
        and (target_id is None or mid == target_id)
    ]
    if len(matches) == 1:
        account, _meter, mid = matches[0]
        return account.entry_id, mid

    if not matches:
        if target_id is None:
            raise _refuse("no_meter_accepting")
        raise _refuse("no_meter_with_id", meter_id=str(target_id))

    # Numbers and names only: the message around them is translated, and a
    # word in here would stay in English.
    choices = ", ".join(
        f"{mid} ({meter_serial(meter) or '?'}, {account.title})"
        for account, meter, mid in matches
    )
    raise _refuse("several_meters", choices=choices)


def validate_range(meter: dict[str, Any], reading: float) -> None:
    """Reject readings the portal itself would not accept.

    NotifyReadout_v1 publishes the allowed window, which is the cheapest
    safeguard available: a typo caught here never reaches the operator.
    """
    if reading < 0:
        raise _refuse("reading_negative")

    low = parse_number(meter.get("min_zakres"))
    high = parse_number(meter.get("zakres"))
    text = reading_text(reading)

    if low is not None and reading < low:
        raise _refuse("reading_below_min", reading=text, min=reading_text(low))
    if high is not None and high > 0 and reading > high:
        raise _refuse("reading_above_max", reading=text, max=reading_text(high))

    # Parsed like its sibling fields: a PHP dataset may send the count as a
    # string, and an isinstance check on int would then skip the check silently.
    digits = _as_int(meter.get("l_cyfr_l"))
    if digits is not None and digits > 0 and int(reading) >= 10**digits:
        raise _refuse("reading_too_many_digits", reading=text, digits=str(digits))


def validate_date(meter: dict[str, Any], when: date, today: date) -> None:
    """Reject a reading date that cannot be right.

    A date in the future, or one before the reading the portal already has,
    would file the reading under the wrong period of the bill. ``do`` is the
    date of that previous reading, the one ``sl`` holds the value of.
    """
    if when > today:
        raise _refuse("reading_date_future", date=when.isoformat())
    previous = dt_util.parse_date(str(meter.get("do") or "").strip())
    if previous is not None and when < previous:
        raise _refuse(
            "reading_date_before_previous",
            date=when.isoformat(),
            previous=previous.isoformat(),
        )


async def async_submit(
    coordinator: IbokCoordinator,
    target_id: int,
    reading: float,
    reading_date: date | None = None,
    note: str = "",
    *,
    expected_serial: str | None = None,
    announced: float | None = None,
) -> float:
    """Check a reading against the portal's current data, send it, return what went.

    The portal is asked again right before sending instead of trusting the
    coordinator's snapshot, which can be hours old. The meter's serial, the
    dial's precision, the allowed range, the previous reading and its date all
    come from that answer, and fetching it also renews a session that expired
    since the last poll.

    ``expected_serial`` is the meter the caller showed the user; the portal can
    hand the same internal id to a replacement meter. ``announced`` is the
    value the caller showed; if the portal's precision has changed since, the
    value that would go out is a different one, and the user has to see it.

    Only one submission per meter runs at a time, and the ledger refuses one
    the portal already has, one already sent today and any while the outcome
    of an earlier one is still unknown.
    """
    if not math.isfinite(reading):
        raise _refuse("invalid_reading")
    if reading < 0:
        raise _refuse("reading_negative")

    ledger = coordinator.ledger
    async with ledger.lock(target_id):
        try:
            rows = await coordinator.api.async_notify_readout()
        except IbokError as err:
            raise _failure("nothing_sent_check_failed", error=str(err)) from err

        row = next((r for r in rows if meter_id(r) == target_id), None)
        if row is None:
            raise _refuse("meter_not_accepting", meter_id=str(target_id))

        serial = meter_serial(row)
        if expected_serial is not None and serial != expected_serial:
            raise _refuse(
                "meter_serial_changed", expected=expected_serial, actual=serial
            )

        value = truncate_to_dial(reading, fraction_digits(row))
        if value != reading:
            _LOGGER.info(
                "Reading %s for meter %s cut to %s, the precision its dial is read at",
                reading_text(reading),
                serial,
                reading_text(value),
            )
        if announced is not None and announced != value:
            # The announcement came from the coordinator's snapshot. Refreshed
            # here, the next one is made at the precision the portal uses now;
            # otherwise every press would announce the same stale value again.
            await coordinator.async_refresh()
            raise _refuse(
                "precision_changed_press_again",
                announced=reading_text(announced),
                reading=reading_text(value),
            )

        today = dt_util.now().date()
        when = reading_date or today
        validate_range(row, value)
        validate_date(row, when, today)
        ledger.check(row, value, when, today)

        sent_at = dt_util.utcnow()
        try:
            await coordinator.api.async_submit_reading(
                meter_id=target_id,
                reading=value,
                reading_date=when.isoformat(),
                previous=str(row.get("sl", "") or ""),
                note=note,
            )
        except IbokOutcomeUnknownError as err:
            ledger.record_unknown(target_id, serial, value, when, today, sent_at)
            raise _failure(
                "outcome_unknown", serial=serial, reading=reading_text(value)
            ) from err
        except IbokError as err:
            raise _failure("nothing_recorded", error=str(err)) from err

        ledger.record_sent(target_id, serial, value, when, today)
        # A real refresh: async_request_refresh is debounced and may return
        # without asking the portal at all.
        await coordinator.async_refresh()
    return value


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
