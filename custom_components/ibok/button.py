"""Button that submits the configured source entity's reading."""

from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import IbokConfigEntry
from .const import (
    ATTR_METER_ID,
    ATTR_READING,
    CONF_SOURCE_ENTITY_PREFIX,
    DOMAIN,
    SERVICE_SUBMIT_READING,
)
from .coordinator import IbokCoordinator
from .entity import IbokEntity

_LOGGER = logging.getLogger(__name__)


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


async def async_setup_entry(
    hass: HomeAssistant, entry: IbokConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    # A button appears only for meters that have a source entity assigned. A
    # household with a garden sub-meter typically automates one and reads the
    # other off the dial, so the buttons are decided per meter, not per account.
    entities = []
    for meter in coordinator.submittable_meters:
        if meter.get("id_wodom") is None:
            continue
        serial = str(meter.get("numer_fabryczny") or meter["id_wodom"])
        if entry.options.get(f"{CONF_SOURCE_ENTITY_PREFIX}{serial}"):
            entities.append(IbokSubmitButton(coordinator, meter))
    async_add_entities(entities)


class IbokSubmitButton(IbokEntity, ButtonEntity):
    """Sends the current value of the source entity as a meter reading."""

    _attr_translation_key = "submit_reading"

    def __init__(self, coordinator: IbokCoordinator, meter: dict) -> None:
        self._meter_id = int(meter["id_wodom"])
        self._serial = str(meter.get("numer_fabryczny") or self._meter_id)
        super().__init__(coordinator, f"{self._serial}_submit", self._serial)

    async def async_press(self) -> None:
        entry = self.coordinator.config_entry
        source = entry.options.get(f"{CONF_SOURCE_ENTITY_PREFIX}{self._serial}")
        state = self.hass.states.get(source) if source else None

        if state is None or state.state in ("unknown", "unavailable"):
            raise HomeAssistantError(
                f"Source entity {source} has no usable value right now"
            )

        try:
            reading = float(state.state)
        except ValueError as err:
            raise HomeAssistantError(
                f"Source entity {source} does not hold a number: {state.state}"
            ) from err

        reading = self.reading_to_submit(reading)

        # Routed through the service so that validation against the portal's
        # allowed range happens in exactly one place.
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_SUBMIT_READING,
            {ATTR_READING: reading, ATTR_METER_ID: self._meter_id},
            blocking=True,
        )

    def reading_to_submit(self, value: float) -> float:
        """The source value cut down to the precision this meter is read at."""
        return truncate_to_dial(value, self.fraction_digits)

    @property
    def fraction_digits(self) -> int:
        """Fractional digits the portal declares for this meter."""
        meter: dict[str, Any] | None = self.coordinator.meter_by_id(self._meter_id)
        value = (meter or {}).get("l_cyfr_p")
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
