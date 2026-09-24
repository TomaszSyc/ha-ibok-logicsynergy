"""Sensors for the iBOK integration."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import IbokConfigEntry
from .coordinator import IbokCoordinator
from .entity import IbokEntity

_LOGGER = logging.getLogger(__name__)

_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S")


async def async_setup_entry(
    hass: HomeAssistant, entry: IbokConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        IbokBalanceSensor(coordinator),
        IbokLastInvoiceSensor(coordinator),
    ]
    for meter in coordinator.data.get("readouts", []):
        serial = str(meter.get("numer_fabryczny") or "").strip()
        if serial:
            entities.append(IbokLastReadingSensor(coordinator, serial))
            entities.append(IbokLastConsumptionSensor(coordinator, serial))
    async_add_entities(entities)


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


def _readouts_for(coordinator: IbokCoordinator, serial: str) -> list[dict[str, Any]]:
    """Readouts for one meter, oldest first.

    The portal does not promise an order, so they are sorted by date here
    rather than trusting the position in the list.
    """
    for row in coordinator.data.get("readouts", []):
        if str(row.get("numer_fabryczny") or "").strip() != serial:
            continue
        rows = [r for r in row.get("odczyty", []) if isinstance(r, dict)]
        return sorted(rows, key=lambda r: _to_date(r.get("do")) or date.min)
    return []


class IbokBalanceSensor(IbokEntity, SensorEntity):
    """Account balance, as the portal's accountancy module reports it."""

    _attr_translation_key = "balance"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL

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
    # No state class. Each value is one document, not a running total, and
    # Home Assistant allows only TOTAL for money: without a reset it would read
    # a smaller next bill as a negative change in the long-term statistics.

    def __init__(self, coordinator: IbokCoordinator) -> None:
        super().__init__(coordinator, "last_invoice")
        self._attr_native_unit_of_measurement = coordinator.hass.config.currency

    def _latest(self) -> dict[str, Any] | None:
        invoices = self.coordinator.data.get("invoices") or []
        return invoices[0] if invoices else None

    @property
    def native_value(self) -> float | None:
        invoice = self._latest()
        return _to_float(invoice.get("brutto")) if invoice else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        invoice = self._latest() or {}
        return {"net": invoice.get("netto"), "vat": invoice.get("vat")}


class _MeterSensor(IbokEntity, SensorEntity):
    """Base for per-meter sensors."""

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
