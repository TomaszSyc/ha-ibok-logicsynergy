"""Shared entity base for the iBOK integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IbokCoordinator


class IbokEntity(CoordinatorEntity[IbokCoordinator]):
    """Base for every entity in this integration.

    Account-wide entities (balance, invoices) sit on one device. Each meter gets
    a device of its own, so a household with a garden sub-meter sees two clearly
    separated groups rather than one list where the serial is the only clue.

    The devices are deliberately not linked with ``via_device``: that parameter
    was dropped from DeviceInfo in Home Assistant 2026.8 and raises at runtime
    in 2026.9, which is exactly how the Tauron integration lost an entity.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: IbokCoordinator, key: str, meter_serial: str | None = None
    ) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"

        if meter_serial:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, f"{entry.entry_id}_meter_{meter_serial}")},
                name=f"{entry.title} \u2014 {meter_serial}",
                manufacturer="LogicSynergy",
                model="Wodomierz",
                serial_number=meter_serial,
            )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                name=entry.title,
                manufacturer="LogicSynergy",
                model="iBOK",
                configuration_url=entry.data.get("base_url"),
            )
