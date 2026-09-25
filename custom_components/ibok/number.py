"""A field to type a reading into, for a meter read off the dial."""

from __future__ import annotations

import math

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import IbokConfigEntry
from .const import CONF_SOURCE_ENTITY_PREFIX, DOMAIN, MAX_READING
from .coordinator import IbokCoordinator, fraction_digits, meter_serial
from .entity import IbokEntity

_KEY = "typed_reading"


async def async_setup_entry(
    hass: HomeAssistant, entry: IbokConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    registry = er.async_get(hass)
    known: set[str] = set()

    # Only meters without a source entity get a field; the others read theirs.
    @callback
    def _add_new_fields() -> None:
        entities = []
        for meter in coordinator.submittable_meters:
            serial = meter_serial(meter)
            if meter.get("id_wodom") is None or serial in known:
                continue
            known.add(serial)
            if entry.options.get(f"{CONF_SOURCE_ENTITY_PREFIX}{serial}"):
                # A source was assigned since: a field left behind would only
                # suggest that typing there still does something.
                unique_id = f"{entry.entry_id}_{serial}_{_KEY}"
                if entity_id := registry.async_get_entity_id(
                    "number", DOMAIN, unique_id
                ):
                    registry.async_remove(entity_id)
                continue
            entities.append(IbokTypedReadingNumber(coordinator, meter))
        if entities:
            async_add_entities(entities)

    _add_new_fields()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_fields))


class IbokTypedReadingNumber(IbokEntity, NumberEntity):
    """The reading read off the dial, waiting for the button to send it.

    Not restored after a restart: a reading is meant to go out right after it
    is typed, and the button refuses one typed more than a day ago anyway.
    """

    _attr_translation_key = _KEY
    # Hidden until wanted: most households report on the portal itself and
    # would only see a field they never use. It stays on the device page,
    # one switch away, and works hidden or not.
    _attr_entity_registry_visible_default = False
    _attr_device_class = NumberDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_READING

    def __init__(self, coordinator: IbokCoordinator, meter: dict) -> None:
        self._meter_id = int(meter["id_wodom"])
        serial = meter_serial(meter)
        super().__init__(coordinator, f"{serial}_{_KEY}", serial)

    @property
    def native_step(self) -> float:
        """As fine as the dial: whole cubic metres unless the portal says more."""
        digits = fraction_digits(self.coordinator.meter_by_id(self._meter_id))
        return 10**-digits if digits else 1

    @property
    def native_value(self) -> float | None:
        typed = self.coordinator.typed_readings.get(self._meter_id)
        return typed[0] if typed else None

    async def async_set_native_value(self, value: float) -> None:
        # number.set_value lets "nan" through its range check.
        if not math.isfinite(value):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="type_reading_first"
            )
        self.coordinator.typed_readings[self._meter_id] = (value, dt_util.utcnow())
        self.async_write_ha_state()
