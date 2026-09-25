"""Sensors for the iBOK integration."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import IbokConfigEntry
from .coordinator import IbokCoordinator, meter_serial
from .entity import IbokEntity

_LOGGER = logging.getLogger(__name__)

_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S")

# Invoice lines are told apart by name. A sewage line can mention water, as in
# "odprowadzanie wód opadowych", so sewage is looked for first.
_SEWAGE_WORDS = ("ściek", "sciek", "kanaliz", "odprowadz")
_WATER_WORDS = ("wod", "wód")
_CUBIC_METRE = re.compile(r"m\s*[3³]")


async def async_setup_entry(
    hass: HomeAssistant, entry: IbokConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            IbokBalanceSensor(coordinator),
            IbokLastInvoiceSensor(coordinator),
            IbokPaymentDueSensor(coordinator),
        ]
    )

    # Meters are added whenever they appear, not only at start-up: a replaced
    # meter comes back under a new serial, and a readouts module that failed
    # at start-up would otherwise leave every meter out until a restart. The
    # meter list is a module of its own, so its sensors are tracked apart.
    known: set[tuple[str, str]] = set()

    @callback
    def _add_new_meters() -> None:
        entities: list[SensorEntity] = []
        for row in coordinator.data.get("readouts") or []:
            serial = meter_serial(row)
            if serial and ("readouts", serial) not in known:
                known.add(("readouts", serial))
                entities.append(IbokLastReadingSensor(coordinator, serial))
                entities.append(IbokLastConsumptionSensor(coordinator, serial))
                entities.append(IbokPriceSensor(coordinator, serial))
        for row in coordinator.data.get("meters") or []:
            serial = meter_serial(row)
            if serial and ("meters", serial) not in known:
                known.add(("meters", serial))
                entities.append(IbokLegalisationSensor(coordinator, serial))
        if entities:
            async_add_entities(entities)

    _add_new_meters()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_meters))


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            # A reading date is a civil date with no time of day. Attaching a
            # timezone to it would invent precision the portal never sent.
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def latest_invoice(invoices: Iterable[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The invoice issued last, by its issue date and not its list position.

    The portal does not promise an order, the same reason the readouts are
    sorted. ``nw`` is the issue date: the portal's own label for the field is
    InvoiceCreateDate. With no date to go by, the first one is taken.
    """
    invoices = list(invoices)
    if not invoices:
        return None
    return max(invoices, key=lambda row: _to_date(row.get("nw")) or date.min)


def unit_prices(invoice: Mapping[str, Any]) -> dict[str, float]:
    """Gross price of a cubic metre on one invoice: water, sewage, other.

    Only lines billed per cubic metre count; a standing charge is billed per
    month and has no place in a price per volume. A line with neither word in
    its name is kept as "other" rather than guessed at.
    """
    prices: dict[str, float] = {}
    for block in invoice.get("pw") or []:
        for line in (block.get("poz") or []) if isinstance(block, dict) else []:
            if not isinstance(line, dict):
                continue
            unit = str(line.get("pj") or "").strip().lower()
            net = _to_float(line.get("pc"))
            if not _CUBIC_METRE.fullmatch(unit) or net is None:
                continue
            vat = _to_float(str(line.get("pt") or "").replace("%", "")) or 0.0
            # Assumed: a period split by a tariff change is billed on two lines
            # of one kind, the newer price last, so the last one is in force.
            prices[_line_kind(str(line.get("pn") or ""))] = net * (1 + vat / 100)
    return prices


def current_unit_prices(
    invoices: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, float], Mapping[str, Any]] | None:
    """Prices from the newest invoice that bills by the cubic metre.

    A correction invoice can come last and carry no such line, so the search
    goes back until one does.
    """
    newest_first = sorted(
        invoices, key=lambda row: _to_date(row.get("nw")) or date.min, reverse=True
    )
    for invoice in newest_first:
        if prices := unit_prices(invoice):
            return prices, invoice
    return None


def meter_price(prices: Mapping[str, float], water: float, sewage: float) -> float:
    """What a cubic metre through one meter costs, given its billed shares.

    The meter list gives each meter a share of water and of sewage billed, in
    percent: a garden sub-meter usually pays no sewage at all.
    """
    return (
        prices.get("water", 0.0) * water / 100
        + prices.get("sewage", 0.0) * sewage / 100
        + prices.get("other", 0.0)
    )


def _line_kind(name: str) -> str:
    text = name.lower()
    if any(word in text for word in _SEWAGE_WORDS):
        return "sewage"
    if any(word in text for word in _WATER_WORDS):
        return "water"
    return "other"


def _share(value: Any) -> float:
    """A billed share in percent; missing means billed in full."""
    number = _to_float(value)
    return 100.0 if number is None else number


def _meter_row(coordinator: IbokCoordinator, serial: str) -> dict[str, Any] | None:
    for row in coordinator.data.get("meters") or []:
        if meter_serial(row) == serial:
            return row
    return None


def _readouts_for(coordinator: IbokCoordinator, serial: str) -> list[dict[str, Any]]:
    """Readouts for one meter, oldest first.

    The portal does not promise an order, so they are sorted by date here
    rather than trusting the position in the list.
    """
    for row in coordinator.data.get("readouts") or []:
        if meter_serial(row) != serial:
            continue
        rows = [r for r in row.get("odczyty", []) if isinstance(r, dict)]
        return sorted(rows, key=lambda r: _to_date(r.get("do")) or date.min)
    return []


class IbokBalanceSensor(IbokEntity, SensorEntity):
    """Account balance, as the portal's accountancy module reports it."""

    _attr_translation_key = "balance"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _modules = ("accountancy",)

    def __init__(self, coordinator: IbokCoordinator) -> None:
        super().__init__(coordinator, "balance")
        self._attr_native_unit_of_measurement = coordinator.hass.config.currency

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data.get("accountancy") or {}
        return _to_float(data.get("roznica"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data.get("accountancy") or {}
        return {"debit": data.get("sumawn"), "credit": data.get("sumama")}


class IbokLastInvoiceSensor(IbokEntity, SensorEntity):
    """Gross amount of the most recent invoice."""

    _attr_translation_key = "last_invoice"
    _attr_device_class = SensorDeviceClass.MONETARY
    _modules = ("invoices",)
    # No state class. Each value is one document, not a running total, and
    # Home Assistant allows only TOTAL for money: without a reset it would read
    # a smaller next bill as a negative change in the long-term statistics.

    def __init__(self, coordinator: IbokCoordinator) -> None:
        super().__init__(coordinator, "last_invoice")
        self._attr_native_unit_of_measurement = coordinator.hass.config.currency

    def _latest(self) -> Mapping[str, Any] | None:
        return latest_invoice(self.coordinator.data.get("invoices") or [])

    @property
    def native_value(self) -> float | None:
        invoice = self._latest()
        return _to_float(invoice.get("brutto")) if invoice else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        invoice = self._latest() or {}
        return {"net": invoice.get("netto"), "vat": invoice.get("vat")}


class IbokPaymentDueSensor(IbokEntity, SensorEntity):
    """The payment deadline of the newest invoice.

    With the balance it is all a reminder needs: a balance above zero and the
    deadline a few days away. ``nt`` is labelled DateOfPayment by the portal.
    """

    _attr_translation_key = "payment_due"
    _attr_device_class = SensorDeviceClass.DATE
    _modules = ("invoices",)

    def __init__(self, coordinator: IbokCoordinator) -> None:
        super().__init__(coordinator, "payment_due")

    @property
    def native_value(self) -> date | None:
        invoice = latest_invoice(self.coordinator.data.get("invoices") or [])
        return _to_date(invoice.get("nt")) if invoice else None


class _MeterSensor(IbokEntity, SensorEntity):
    """Base for per-meter sensors."""

    _modules = ("readouts",)

    def __init__(self, coordinator: IbokCoordinator, serial: str, key: str) -> None:
        super().__init__(coordinator, f"{serial}_{key}", serial)
        self._serial = serial

    def _last(self) -> dict[str, Any] | None:
        rows = _readouts_for(self.coordinator, self._serial)
        return rows[-1] if rows else None


class IbokLastReadingSensor(_MeterSensor):
    """Meter reading as registered by the operator."""

    _attr_translation_key = "last_reading"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = "m³"

    def __init__(self, coordinator: IbokCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "last_reading")

    @property
    def native_value(self) -> float | None:
        last = self._last()
        return _to_float(last.get("sl")) if last else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        last = self._last() or {}
        return {
            "meter": self._serial,
            "reading_date": last.get("do"),
            "type": last.get("typ"),
        }


class IbokLastConsumptionSensor(_MeterSensor):
    """Consumption billed in the most recent settlement period."""

    _attr_translation_key = "last_consumption"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "m³"

    def __init__(self, coordinator: IbokCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "last_consumption")

    @property
    def native_value(self) -> float | None:
        last = self._last()
        return _to_float(last.get("zu")) if last else None

    @property
    def last_reset(self) -> datetime | None:
        """Start of the period this consumption covers: the reading before it.

        Without a reset, Home Assistant takes 9, 7, 8 m3 for a running
        total, and every smaller month becomes a negative change in the
        long-term statistics.
        """
        rows = _readouts_for(self.coordinator, self._serial)
        if not rows:
            return None
        start = rows[-2] if len(rows) > 1 else rows[-1]
        day = _to_date(start.get("do"))
        return dt_util.start_of_local_day(day) if day else None


class IbokPriceSensor(_MeterSensor):
    """The price of a cubic metre through this meter, water and sewage together.

    For the energy dashboard, whose water cost takes an entity holding the
    current price. It comes from the newest invoice, so a new tariff shows once
    the first invoice under it does.
    """

    _attr_translation_key = "price"
    _attr_suggested_display_precision = 2
    _modules = ("invoices", "meters")

    def __init__(self, coordinator: IbokCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "price")
        currency = coordinator.hass.config.currency
        self._attr_native_unit_of_measurement = (
            f"{currency}/{UnitOfVolume.CUBIC_METERS}"
        )

    def _shares(self) -> tuple[float, float]:
        row = _meter_row(self.coordinator, self._serial) or {}
        return _share(row.get("procent_w")), _share(row.get("procent_k"))

    @property
    def native_value(self) -> float | None:
        current = current_unit_prices(self.coordinator.data.get("invoices") or [])
        if current is None:
            return None
        return round(meter_price(current[0], *self._shares()), 4)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        current = current_unit_prices(self.coordinator.data.get("invoices") or [])
        if current is None:
            return {}
        prices, invoice = current
        water, sewage = self._shares()
        return {
            **{kind: round(price, 4) for kind, price in prices.items()},
            "water_share": water,
            "sewage_share": sewage,
            "invoice_date": invoice.get("nw"),
        }


class IbokLegalisationSensor(_MeterSensor):
    """When the meter's legalisation runs out, and the operator replaces it.

    A reader fitted onto the meter has to move to the new one then, and starts
    counting from zero again, so the date is worth a reminder well ahead.
    """

    _attr_translation_key = "legalised_until"
    _attr_device_class = SensorDeviceClass.DATE
    _modules = ("meters",)

    def __init__(self, coordinator: IbokCoordinator, serial: str) -> None:
        super().__init__(coordinator, serial, "legalised_until")

    @property
    def native_value(self) -> date | None:
        row = _meter_row(self.coordinator, self._serial) or {}
        return _to_date(row.get("leg_do"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        row = _meter_row(self.coordinator, self._serial) or {}
        return {"installed": row.get("ew_prz")}
