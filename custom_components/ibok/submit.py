"""Submitting a meter reading, the one path in this integration that lands on a bill.

Shared by the submit_reading service and the button, so both go through the
same checks against the portal's current data.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util

from .api import IbokError, IbokOutcomeUnknownError

if TYPE_CHECKING:
    from .coordinator import IbokCoordinator


@dataclass(frozen=True)
class Account:
    """What choosing a meter needs to know about one loaded config entry."""

    entry_id: str
    title: str
    meters: list[dict[str, Any]]


def resolve_target(
    accounts: Iterable[Account], meter_id: int | None, entry_id: str | None
) -> tuple[str, int]:
    """Pick the one account and meter a service call means, or refuse.

    Every loaded account is searched, not only the first. A meter id is only
    ambiguous when two portals happen to use the same one, and then the caller
    has to name the account rather than have one picked for them.
    """
    pool = [a for a in accounts if entry_id is None or a.entry_id == entry_id]
    if entry_id is not None and not pool:
        raise ServiceValidationError(f"No loaded iBOK account has entry id {entry_id}")

    matches = [
        (account, meter)
        for account in pool
        for meter in account.meters
        if meter_id is None or str(meter.get("id_wodom")) == str(meter_id)
    ]
    if len(matches) == 1:
        account, meter = matches[0]
        return account.entry_id, int(meter["id_wodom"])

    if not matches:
        if meter_id is None:
            raise ServiceValidationError(
                "The portal is not accepting a reading for any meter right now"
            )
        raise ServiceValidationError(
            f"No meter with id {meter_id} is accepting a reading right now"
        )

    choices = ", ".join(
        f"{meter.get('id_wodom')} (meter {_serial(meter)}, {account.title})"
        for account, meter in matches
    )
    raise ServiceValidationError(
        "Several meters match -- pass meter_id, and config_entry_id if two "
        f"accounts share it: {choices}"
    )


def validate_range(meter: dict[str, Any], reading: float) -> None:
    """Reject readings the portal itself would not accept.

    NotifyReadout_v1 publishes the allowed window, which is the cheapest
    safeguard available: a typo caught here never reaches the operator.
    """
    low = _as_float(meter.get("min_zakres"))
    high = _as_float(meter.get("zakres"))

    if low is not None and reading < low:
        raise ServiceValidationError(
            f"Reading {reading} is below the portal's minimum of {low}"
        )
    if high is not None and high > 0 and reading > high:
        raise ServiceValidationError(
            f"Reading {reading} is above the portal's maximum of {high}"
        )

    # Parsed like its sibling fields: a PHP dataset may send the count as a
    # string, and an isinstance check on int would then skip the check silently.
    digits = _as_int(meter.get("l_cyfr_l"))
    if digits is not None and digits > 0 and int(reading) >= 10**digits:
        raise ServiceValidationError(
            f"Reading {reading} has more than {digits} digits before the decimal point"
        )


async def async_submit(
    coordinator: IbokCoordinator,
    meter_id: int,
    reading: float,
    reading_date: date | None = None,
    note: str = "",
) -> None:
    """Check a reading against the portal's current data, then send it.

    The portal is asked again right before sending instead of trusting the
    coordinator's snapshot, which can be hours old. The allowed range and the
    previous reading come from that answer, and fetching it also renews a
    session that expired since the last poll.
    """
    if reading < 0:
        raise ServiceValidationError("A meter reading cannot be negative")

    try:
        rows = await coordinator.api.async_notify_readout()
    except IbokError as err:
        raise HomeAssistantError(
            f"Could not check the meter with the portal, nothing was sent: {err}"
        ) from err

    meter = next((r for r in rows if str(r.get("id_wodom")) == str(meter_id)), None)
    if meter is None:
        raise ServiceValidationError(
            f"The portal is not accepting a reading for meter {meter_id} right now"
        )

    validate_range(meter, reading)
    when = reading_date or dt_util.now().date()

    try:
        await coordinator.api.async_submit_reading(
            meter_id=int(meter["id_wodom"]),
            reading=reading,
            reading_date=when.strftime("%Y-%m-%d"),
            previous=str(meter.get("sl", "") or ""),
            note=note,
        )
    except IbokOutcomeUnknownError as err:
        raise HomeAssistantError(
            "The reading went out but the portal's answer never arrived, so it "
            "may have been recorded. Check the portal before sending it again, "
            "or it will be on the bill twice."
        ) from err
    except IbokError as err:
        raise HomeAssistantError(
            f"The portal did not take the reading, nothing was recorded: {err}"
        ) from err

    # A real refresh: async_request_refresh is debounced and may return
    # without asking the portal at all.
    await coordinator.async_refresh()


def _serial(meter: dict[str, Any]) -> str:
    return str(meter.get("numer_fabryczny") or "").strip() or "?"


def _as_float(value: Any) -> float | None:
    # Portal numbers can carry a decimal comma and thousands separators. In
    # Python 3, \s also matches the non-breaking space.
    text = re.sub(r"\s", "", str(value)).replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _as_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
