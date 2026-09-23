"""Button that submits the configured source entity's reading."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

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

CONFIRM_WINDOW = timedelta(seconds=60)


def _as_text(value: float) -> str:
    """A reading as a person reads it: no trailing .0 on a whole reading."""
    return str(int(value)) if value == int(value) else str(value)


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


def press_confirms(
    armed: tuple[datetime, float] | None, reading: float, now: datetime
) -> bool:
    """Whether this press confirms the value the previous one announced.

    A source entity keeps moving -- a radio overlay reports every minute.
    Confirming a value that has changed since it was announced would send a
    number nobody was shown, so a changed reading has to be announced again.
    """
    if armed is None:
        return False
    deadline, announced = armed
    return now <= deadline and announced == reading


def plan_press(
    source_value: float,
    digits: int,
    armed: tuple[datetime, float] | None,
    now: datetime,
) -> tuple[str, float]:
    """Decide what a press does: ``("send", value)`` or ``("announce", value)``.

    The source value is cut to the dial's precision *before* it is compared
    with the announced one, so what re-opens the question is a change the
    operator would see. On a meter read in whole cubic metres, 48.002 moving
    to 48.004 is the same reading and confirms; 48 moving to 49 does not.
    """
    reading = truncate_to_dial(source_value, digits)
    return ("send" if press_confirms(armed, reading, now) else "announce", reading)


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
    """Sends the current value of the source entity as a meter reading.

    A press is confirmed by a second press. Home Assistant has no confirmation
    for a button entity -- the dialog a dashboard card can show is a property
    of the card, so it does nothing on the device page, in a script or in voice
    control. The reading lands on somebody's bill, so the confirmation belongs
    to the entity and has to work wherever the entity is pressed.

    The first press answers with the value and the meter it would go to, which
    is the part a dashboard dialog cannot show: its text is static.
    """

    _attr_translation_key = "submit_reading"

    def __init__(self, coordinator: IbokCoordinator, meter: dict) -> None:
        self._meter_id = int(meter["id_wodom"])
        self._serial = str(meter.get("numer_fabryczny") or self._meter_id)
        self._armed: tuple[datetime, float] | None = None
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

        now = dt_util.utcnow()
        decision, reading = plan_press(reading, self.fraction_digits, self._armed, now)

        if decision == "announce":
            self._armed = (now + CONFIRM_WINDOW, reading)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="press_again_to_send",
                translation_placeholders={
                    "reading": _as_text(reading),
                    "serial": self._serial,
                    "meter_id": str(self._meter_id),
                    "seconds": str(int(CONFIRM_WINDOW.total_seconds())),
                },
            )

        self._armed = None

        # Routed through the service so that validation against the portal's
        # allowed range happens in exactly one place.
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_SUBMIT_READING,
            {ATTR_READING: reading, ATTR_METER_ID: self._meter_id},
            blocking=True,
        )

    @property
    def fraction_digits(self) -> int:
        """Fractional digits the portal declares for this meter."""
        meter: dict[str, Any] | None = self.coordinator.meter_by_id(self._meter_id)
        value = (meter or {}).get("l_cyfr_p")
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
