"""Button that submits the configured source entity's reading."""

from __future__ import annotations

import logging

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

        # Routed through the service so that validation against the portal's
        # allowed range happens in exactly one place.
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_SUBMIT_READING,
            {ATTR_READING: reading, ATTR_METER_ID: self._meter_id},
            blocking=True,
        )
