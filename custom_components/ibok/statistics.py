"""Invoice history as long-term statistics, one row per month.

The portal gives no billing period for an invoice, so the month it belongs to
is recovered from the meter readings: the cubic metres on the invoice must
equal the use between two readings. An invoice is counted whole in the month
holding most days of that period.
"""

from __future__ import annotations

import hashlib
import logging
from calendar import monthrange
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify
from yarl import URL

from .const import CONF_BASE_URL, DOMAIN
from .coordinator import parse_date, parse_number

_LOGGER = logging.getLogger(__name__)

# Tolerance when comparing cubic metres: the portal writes them to three places.
_TOLERANCE = 0.001
# A multi-period invoice covers a handful of readings, never a year's worth.
_MAX_READINGS = 6

KIND_AMOUNT = "invoiced_amount"
KIND_WATER = "invoiced_water"


def _invoiced_water(invoice: Mapping[str, Any]) -> float:
    # Imported here: the sensor platform imports the package, which imports this
    # module, so a top-level import would be circular.
    from .sensor import invoiced_water

    return invoiced_water(invoice)


def billing_period(
    invoice: Mapping[str, Any], chains: Iterable[Sequence[Mapping[str, Any]]]
) -> tuple[date, date] | None:
    """The dates between which the invoice's water was used, or ``None``.

    ``chains`` holds each meter's readings, oldest first. The first meter whose
    consecutive uses add up to the invoiced cubic metres decides.
    """
    wanted = _invoiced_water(invoice)
    if wanted <= 0:
        return None
    sale = parse_date(invoice.get("ns")) or parse_date(invoice.get("nw"))
    if sale is None:
        return None
    for chain in chains:
        dates = [parse_date(row.get("do")) for row in chain]
        ends = [i for i, day in enumerate(dates) if day is not None and day <= sale]
        if not ends:
            continue
        end = ends[-1]
        total = 0.0
        for count in range(1, min(_MAX_READINGS, end + 1) + 1):
            use = parse_number(chain[end - count + 1].get("zu"))
            if use is None:
                break
            total += use
            if abs(total - wanted) < _TOLERANCE:
                before = dates[end - count] if end - count >= 0 else None
                if before:
                    return before, dates[end]  # type: ignore[return-value]
                # No reading before the first one summed: this meter has no start.
                break
            if total > wanted + _TOLERANCE:
                break
    return None


def dominant_month(start: date, end: date) -> date:
    """First day of the month with most days in (start, end]; a tie goes to the later."""
    days: dict[date, int] = {}
    day = start + timedelta(days=1)
    while day <= end:
        first = day.replace(day=1)
        # Whole months at a time: a period can run to a year or two of days.
        last = min(end, day.replace(day=monthrange(day.year, day.month)[1]))
        days[first] = (last - day).days + 1
        day = last + timedelta(days=1)
    if not days:
        return end.replace(day=1)
    return max(days, key=lambda month: (days[month], month))


def invoice_month(
    invoice: Mapping[str, Any], chains: Iterable[Sequence[Mapping[str, Any]]]
) -> date | None:
    """The month an invoice is booked to."""
    period = billing_period(invoice, chains)
    if period is not None:
        return dominant_month(*period)
    # Invoices are issued early in the month for the month before.
    sale = parse_date(invoice.get("ns")) or parse_date(invoice.get("nw"))
    if sale is None:
        return None
    return (sale.replace(day=1) - timedelta(days=1)).replace(day=1)


def monthly_changes(
    invoices: Iterable[Mapping[str, Any]],
    chains: Iterable[Sequence[Mapping[str, Any]]],
) -> tuple[dict[date, float], dict[date, float]]:
    """Gross amount and cubic metres of water per month, invoices added up."""
    chains = list(chains)
    amounts: dict[date, float] = {}
    water: dict[date, float] = {}
    for invoice in invoices:
        month = invoice_month(invoice, chains)
        if month is None:
            continue
        gross = parse_number(invoice.get("brutto"))
        if gross is not None:
            amounts[month] = amounts.get(month, 0.0) + gross
        cubic = _invoiced_water(invoice)
        if cubic:
            water[month] = water.get(month, 0.0) + cubic
    return amounts, water


def cumulative_rows(
    changes: Mapping[datetime, float], stored: Mapping[datetime, float]
) -> list[tuple[datetime, float, float]]:
    """``(start, state, sum)`` for every month from the oldest change on.

    A stored month the invoices no longer mention is rewritten with a change of
    zero, so its old sum cannot outlive what came before it. Nothing earlier
    than the oldest change is touched: invoices the portal has dropped stay.
    """
    if not changes:
        return []
    first = min(changes)
    total = next(
        (stored[start] for start in sorted(stored, reverse=True) if start < first),
        0.0,
    )
    rows: list[tuple[datetime, float, float]] = []
    for start in sorted(set(changes) | {s for s in stored if s >= first}):
        state = changes.get(start, 0.0)
        total += state
        rows.append((start, state, total))
    return rows


def statistic_id(entry: ConfigEntry, kind: str) -> str:
    # Built from the account, not the entry id: removing and adding the
    # integration again must continue the same history. The login goes in as a
    # hash so it stays out of the recorder and out of dashboard YAML people paste
    # into issues.
    host = URL(entry.data[CONF_BASE_URL]).host or ""
    digest = hashlib.sha256((entry.unique_id or "").encode()).hexdigest()[:8]
    return f"{DOMAIN}:{slugify(host)}_{digest}_{kind}"


def _month_start(month: date) -> datetime:
    # Whole hours only: the recorder rejects a start that is not on the hour,
    # which a zone such as +05:30 would otherwise produce. Rounded up, so the row
    # still falls on the first day of the month in local time.
    start = dt_util.as_utc(dt_util.start_of_local_day(month))
    whole = start.replace(minute=0, second=0, microsecond=0)
    return whole if whole == start else whole + timedelta(hours=1)


async def async_import_invoice_statistics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    data: Mapping[str, Any],
    failed: frozenset[str],
) -> None:
    """Recompute the two statistics from this refresh's invoices and readings.

    Skipped when either module failed: an invoice would then be matched against
    stale readings and could jump to a wrong month.
    """
    if "invoices" in failed or "readouts" in failed:
        return
    invoices = [row for row in data.get("invoices") or [] if isinstance(row, dict)]
    if not invoices:
        return
    if "recorder" not in hass.config.components:
        _LOGGER.debug("The recorder is not loaded, invoice statistics are skipped")
        return

    chains: list[list[Mapping[str, Any]]] = []
    for row in data.get("readouts") or []:
        if not isinstance(row, dict):
            continue
        dated = [
            (parse_date(r.get("do")), r)
            for r in row.get("odczyty") or []
            if isinstance(r, dict)
        ]
        # Stable, so readings sharing a day keep the portal's order.
        chains.append(
            [r for _, r in sorted((p for p in dated if p[0]), key=lambda p: p[0])]
        )

    amounts, water = monthly_changes(invoices, chains)
    series = (
        (KIND_AMOUNT, amounts, hass.config.currency, None),
        (KIND_WATER, water, "m³", "volume"),
    )
    for kind, by_month, unit, unit_class in series:
        changes = {_month_start(month): value for month, value in by_month.items()}
        if not changes:
            continue
        identifier = statistic_id(entry, kind)
        found = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            datetime.fromtimestamp(0, UTC),
            None,
            {identifier},
            "hour",
            None,
            {"sum"},
        )
        stored = {
            dt_util.utc_from_timestamp(row["start"]): row["sum"]
            for row in found.get(identifier, [])
            if row.get("sum") is not None
        }
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{entry.title} {kind.replace('_', ' ')}",
            source=DOMAIN,
            statistic_id=identifier,
            unit_class=unit_class,
            unit_of_measurement=unit,
        )
        async_add_external_statistics(
            hass,
            metadata,
            [
                StatisticData(start=start, state=state, sum=total)
                for start, state, total in cumulative_rows(changes, stored)
            ],
        )
