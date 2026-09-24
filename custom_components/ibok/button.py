"""Button that submits the configured source entity's reading."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, UnitOfVolume
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from . import IbokConfigEntry
from .const import CONF_SOURCE_ENTITY_PREFIX, DOMAIN
from .coordinator import IbokCoordinator, meter_serial
from .entity import IbokEntity
from .submit import async_submit

_LOGGER = logging.getLogger(__name__)

CONFIRM_WINDOW = timedelta(seconds=60)
# A double click is one press, not an announcement read and then confirmed.
MIN_CONFIRM_GAP = timedelta(seconds=2)
# A radio overlay reports about every minute; a day of silence means the reader
# is dead and the entity is only showing its last value.
MAX_SOURCE_AGE = timedelta(hours=24)

# (announced at, confirm by, value announced, user who pressed)
type Armed = tuple[datetime, datetime, float, str | None]


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
    armed: Armed | None, reading: float, now: datetime, user_id: str | None
) -> bool:
    """Whether this press confirms the value the previous one announced.

    A source entity keeps moving -- a radio overlay reports every minute.
    Confirming a value that has changed since it was announced would send a
    number nobody was shown, so a changed reading has to be announced again.
    The confirmation has to come from whoever saw the announcement, and not in
    the same instant: a double click would otherwise confirm unread.
    """
    if armed is None:
        return False
    announced_at, deadline, announced, announced_by = armed
    return (
        now - announced_at >= MIN_CONFIRM_GAP
        and now <= deadline
        and announced == reading
        and user_id == announced_by
    )


def plan_press(
    source_value: float,
    digits: int,
    armed: Armed | None,
    now: datetime,
    user_id: str | None = None,
) -> tuple[str, float]:
    """Decide what a press does: ``("send", value)`` or ``("announce", value)``.

    The source value is cut to the dial's precision *before* it is compared
    with the announced one, so what re-opens the question is a change the
    operator would see. On a meter read in whole cubic metres, 48.002 moving
    to 48.004 is the same reading and confirms; 48 moving to 49 does not.
    """
    reading = truncate_to_dial(source_value, digits)
    confirmed = press_confirms(armed, reading, now, user_id)
    return ("send" if confirmed else "announce", reading)


def source_reading(state: State | None, now: datetime) -> float:
    """The source entity's value in cubic metres, or refuse it.

    Converted by its unit: a sensor in litres, or an m3 sensor whose display
    unit was switched to litres, would otherwise be sent 1000 times too high.
    Refused when stale: a dead radio overlay keeps showing its last value, and
    that would be filed as today's reading.
    """
    if state is None or state.state in ("unknown", "unavailable"):
        raise ServiceValidationError("The source entity has no usable value right now")
    try:
        value = float(state.state)
    except ValueError as err:
        raise ServiceValidationError(
            f"The source entity does not hold a number: {state.state}"
        ) from err

    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    try:
        cubic_metres = VolumeConverter.convert(value, unit, UnitOfVolume.CUBIC_METERS)
    except HomeAssistantError as err:
        raise ServiceValidationError(
            f"The source entity's unit {unit!r} is not a volume"
        ) from err

    silent = now - state.last_reported
    if silent > MAX_SOURCE_AGE:
        hours = int(silent.total_seconds() // 3600)
        raise ServiceValidationError(
            f"The source entity has not reported for {hours} h, so its value may "
            "be old -- check the meter reader before sending"
        )
    return cubic_metres


async def async_setup_entry(
    hass: HomeAssistant, entry: IbokConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    # A button appears only for meters that have a source entity assigned. A
    # household with a garden sub-meter typically automates one and reads the
    # other off the dial, so the buttons are decided per meter, not per account.
    # Checked on every update, like the meter sensors: a meter the portal lists
    # only later still gets its button without a restart.
    known: set[str] = set()

    @callback
    def _add_new_buttons() -> None:
        entities = []
        for meter in coordinator.submittable_meters:
            serial = meter_serial(meter)
            if meter.get("id_wodom") is None or serial in known:
                continue
            if entry.options.get(f"{CONF_SOURCE_ENTITY_PREFIX}{serial}"):
                known.add(serial)
                entities.append(IbokSubmitButton(coordinator, meter))
        if entities:
            async_add_entities(entities)

    _add_new_buttons()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_buttons))


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
    # Tied to no module: a press asks the portal again before sending, so one
    # failed poll of the reading form does not have to make the button unusable.
    _module = None

    def __init__(self, coordinator: IbokCoordinator, meter: dict) -> None:
        self._meter_id = int(meter["id_wodom"])
        self._serial = meter_serial(meter)
        self._armed: Armed | None = None
        super().__init__(coordinator, f"{self._serial}_submit", self._serial)

    async def async_press(self) -> None:
        entry = self.coordinator.config_entry
        source = entry.options.get(f"{CONF_SOURCE_ENTITY_PREFIX}{self._serial}")
        state = self.hass.states.get(source) if source else None
        now = dt_util.utcnow()
        value = source_reading(state, now)
        user_id = self._context.user_id if self._context else None

        decision, reading = plan_press(
            value, self.fraction_digits, self._armed, now, user_id
        )

        if decision == "announce":
            self._armed = (now, now + CONFIRM_WINDOW, reading, user_id)
            # A validation error, not a failure: it is the expected answer to a
            # first press, and Home Assistant logs only real failures with a
            # full traceback.
            raise ServiceValidationError(
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

        # This button's own coordinator, not a lookup through the service: with
        # two accounts the service has to guess which one is meant, while the
        # button already knows. Validation stays in one place either way.
        await async_submit(self.coordinator, self._meter_id, reading)

    @property
    def fraction_digits(self) -> int:
        """Fractional digits the portal declares for this meter."""
        meter: dict[str, Any] | None = self.coordinator.meter_by_id(self._meter_id)
        value = (meter or {}).get("l_cyfr_p")
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0
