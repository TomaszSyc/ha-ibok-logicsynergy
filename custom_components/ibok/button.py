"""Button that submits a meter reading, from a source entity or typed by hand."""

from __future__ import annotations

import logging
import math
from datetime import UTC, datetime, timedelta

from homeassistant.components.button import ButtonEntity
from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, UnitOfVolume
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from . import IbokConfigEntry
from .api import IbokOutcomeUnknownError
from .const import CONF_SOURCE_ENTITY_PREFIX, DOMAIN
from .coordinator import IbokCoordinator, fraction_digits, meter_id, meter_serial
from .entity import IbokEntity
from .ledger import reading_text
from .submit import async_submit, truncate_to_dial

_LOGGER = logging.getLogger(__name__)

CONFIRM_WINDOW = timedelta(seconds=60)
# A double click is one press, not an announcement read and then confirmed.
MIN_CONFIRM_GAP = timedelta(seconds=2)
# A radio overlay reports about every minute; a day of silence means the reader
# is dead and the entity is only showing its last value.
MAX_SOURCE_AGE = timedelta(hours=24)
# An overlay that reports every few minutes has not necessarily reported yet
# this soon after a restart; refusing on silence alone would then refuse a
# reader that is working fine.
RESTART_GRACE = timedelta(minutes=15)
# How long a state restored at startup keeps a last_reported close to the
# restart itself, before a real report would have moved it on.
RESTART_WINDOW = timedelta(minutes=5)

# Attributes an overlay may publish the meter's own read time under, tried in
# this order.
_TIMESTAMP_ATTRS = ("timestamp", "last_seen", "last_update")
# How far ahead of Home Assistant a reader's own clock may run before its
# timestamps stop being believed.
MAX_CLOCK_AHEAD = timedelta(minutes=5)

# (announced at, confirm by, value announced, user who pressed)
type Armed = tuple[datetime, datetime, float, str | None]


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


def source_timestamp(state: State, now: datetime) -> tuple[datetime, bool]:
    """When the source entity's value was actually read, and whether that is known.

    An overlay that publishes its own read time is more precise than Home
    Assistant's bookkeeping: ``last_reported`` also moves on a restart, when
    the entity's last state is merely restored, not freshly reported. Where
    no such attribute exists, ``last_reported`` is what there is. The same
    goes for a read time well in the future: the reader's clock is wrong,
    and trusting it would make a source that went silent look fresh.
    """
    for attr in _TIMESTAMP_ATTRS:
        raw = state.attributes.get(attr)
        if raw is None:
            continue
        parsed = dt_util.parse_datetime(str(raw))
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            if parsed - now > MAX_CLOCK_AHEAD:
                break
            return parsed, True
    return state.last_reported, False


def age_text(delta: timedelta) -> str:
    """How long ago a reading was reported, read out loud: "45 s", "3 min", "2 h".

    A reader whose clock runs slightly ahead reports from the future; that
    reads as just now.
    """
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return f"{seconds} s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min"
    return f"{seconds // 3600} h"


def source_reading(
    state: State | None, now: datetime, started_at: datetime | None
) -> tuple[float, datetime]:
    """The source entity's value in cubic metres and when it was read, or a refusal.

    Converted by its unit: a sensor in litres, or an m3 sensor whose display
    unit was switched to litres, would otherwise be sent 1000 times too high.
    Refused when stale: a dead radio overlay keeps showing its last value, and
    that would be filed as today's reading. Refused too, for a while after
    Home Assistant starts, when the only evidence of freshness is a
    ``last_reported`` sitting right at the restart: that is what a restored
    state looks like, indistinguishable from a reader that has stayed silent
    since. An overlay that timestamps its own reading is trusted as soon as it
    reports, restart or not.
    """
    if state is None or state.state in ("unknown", "unavailable"):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="source_unusable"
        )
    try:
        value = float(state.state)
    except ValueError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="source_not_a_number"
        ) from err
    if not math.isfinite(value):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="source_not_a_number"
        )

    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    try:
        cubic_metres = VolumeConverter.convert(value, unit, UnitOfVolume.CUBIC_METERS)
    except HomeAssistantError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="source_unit_not_volume",
            translation_placeholders={"unit": str(unit)},
        ) from err

    reported, from_attribute = source_timestamp(state, now)
    silent = now - reported
    if silent > MAX_SOURCE_AGE:
        hours = int(silent.total_seconds() // 3600)
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="source_stale",
            translation_placeholders={"hours": str(hours)},
        )
    if (
        not from_attribute
        and started_at is not None
        and now - started_at > RESTART_GRACE
        and started_at <= reported <= started_at + RESTART_WINDOW
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="source_silent_since_restart"
        )
    return cubic_metres, reported


def typed_reading(typed: tuple[float, datetime] | None, now: datetime) -> float:
    """The reading typed into the meter's field, or refuse it.

    Refused a day after it was typed, like a source that went silent: a value
    typed and forgotten last month must not go out as this month's reading.
    """
    if typed is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="type_reading_first"
        )
    value, typed_at = typed
    if now - typed_at > MAX_SOURCE_AGE:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="typed_reading_stale"
        )
    return value


async def async_setup_entry(
    hass: HomeAssistant, entry: IbokConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data

    # Every meter the portal accepts a reading for gets a button. With a source
    # entity assigned it sends that entity's value; without one, what was typed
    # into the meter's field. A household with a garden sub-meter typically
    # automates one and reads the other off the dial, so this is per meter.
    # Checked on every update, like the meter sensors: a meter the portal lists
    # only later still gets its button without a restart.
    known: set[str] = set()
    registry = er.async_get(hass)

    @callback
    def _add_new_buttons() -> None:
        entities = []
        for meter in coordinator.submittable_meters:
            serial = meter_serial(meter)
            mid = meter_id(meter)
            if mid is None or serial in known:
                continue
            known.add(serial)
            if entry.options.get(f"{CONF_SOURCE_ENTITY_PREFIX}{serial}"):
                _show_if_hidden_by_us(registry, f"{entry.entry_id}_{serial}_submit")
            entities.append(IbokSubmitButton(coordinator, meter, mid))
        if entities:
            async_add_entities(entities)

    _add_new_buttons()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_buttons))


def _show_if_hidden_by_us(registry: er.EntityRegistry, unique_id: str) -> None:
    """Show a button hidden while it waited for a typed reading.

    With a source entity it is the button it always was. Only the integration's
    own hiding is undone; one hidden by the user stays hidden.
    """
    entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
    registered = registry.async_get(entity_id) if entity_id else None
    if registered and registered.hidden_by is er.RegistryEntryHider.INTEGRATION:
        registry.async_update_entity(registered.entity_id, hidden_by=None)


class IbokSubmitButton(IbokEntity, ButtonEntity):
    """Sends a meter reading: the source entity's value, or the typed one.

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
    _modules = ()

    def __init__(
        self, coordinator: IbokCoordinator, meter: dict, meter_id: int
    ) -> None:
        self._meter_id = meter_id
        self._serial = meter_serial(meter)
        self._armed: Armed | None = None
        super().__init__(coordinator, f"{self._serial}_submit", self._serial)
        # Like its field, the button for a typed reading starts hidden. With a
        # source entity it is a deliberate setup and shows from the start.
        source = coordinator.config_entry.options.get(
            f"{CONF_SOURCE_ENTITY_PREFIX}{self._serial}"
        )
        self._attr_entity_registry_visible_default = bool(source)

    async def async_press(self) -> None:
        entry = self.coordinator.config_entry
        source = entry.options.get(f"{CONF_SOURCE_ENTITY_PREFIX}{self._serial}")
        now = dt_util.utcnow()
        if source:
            started_at = self.hass.data.get(DOMAIN, {}).get("started_at")
            value, reported = source_reading(
                self.hass.states.get(source), now, started_at
            )
            age = now - reported
        else:
            typed_entry = self.coordinator.typed_readings.get(self._meter_id)
            value = typed_reading(typed_entry, now)
            age = now - typed_entry[1]
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
                    "reading": reading_text(reading),
                    "serial": self._serial,
                    "meter_id": str(self._meter_id),
                    "seconds": str(int(CONFIRM_WINDOW.total_seconds())),
                    "age": age_text(age),
                },
            )

        self._armed = None

        # The field empties before sending, not after: sending and the refresh
        # behind it take several requests, and a press in the meantime would
        # otherwise announce and send the same value again. It comes back only
        # when nothing reached the portal.
        typed = (
            None
            if source
            else self.coordinator.typed_readings.pop(self._meter_id, None)
        )
        if typed:
            self.coordinator.async_update_listeners()
        try:
            # This button's own coordinator, not a lookup through the service:
            # with two accounts the service has to guess which one is meant,
            # while the button already knows. Validation stays in one place.
            # The serial and the value are what the announcement showed, so
            # a replaced meter or a changed dial precision is refused rather
            # than sent under what the user confirmed.
            await async_submit(
                self.coordinator,
                self._meter_id,
                reading,
                expected_serial=self._serial,
                announced=reading,
            )
        except HomeAssistantError as err:
            if typed and not isinstance(err.__cause__, IbokOutcomeUnknownError):
                self.coordinator.typed_readings.setdefault(self._meter_id, typed)
                self.coordinator.async_update_listeners()
            raise

    @property
    def fraction_digits(self) -> int:
        """Fractional digits the portal declares for this meter."""
        return fraction_digits(self.coordinator.meter_by_id(self._meter_id))
