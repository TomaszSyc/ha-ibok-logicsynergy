"""A field to type a reading into, for a meter read off the dial."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfVolume
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from . import IbokConfigEntry
from .button import MAX_SOURCE_AGE
from .const import CONF_SOURCE_ENTITY_PREFIX, DOMAIN, MAX_READING
from .coordinator import IbokCoordinator, fraction_digits, meter_id, meter_serial
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
            mid = meter_id(meter)
            if mid is None or serial in known:
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
            unique_id = f"{entry.entry_id}_{serial}_{_KEY}"
            # Whether this is a genuinely new field, or one back from the
            # tombstone Home Assistant kept from before a source removed it --
            # as opposed to one that already existed and is merely reloading.
            just_registered = (
                registry.async_get_entity_id("number", DOMAIN, unique_id) is None
            )
            entities.append(
                IbokTypedReadingNumber(
                    coordinator,
                    meter,
                    mid,
                    _button_visible(registry, entry, serial),
                    just_registered,
                )
            )
        if entities:
            async_add_entities(entities)

    _add_new_fields()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_fields))


def _button_visible(
    registry: er.EntityRegistry, entry: IbokConfigEntry, serial: str
) -> bool:
    """Whether the submit button next to this field is currently shown.

    A field is created either on first setup, when the button is not
    registered yet, or again after a source is unassigned, when the button
    has been sitting there all along. In the second case the field should not
    fall back to hidden if the household already unhid the button.
    """
    unique_id = f"{entry.entry_id}_{serial}_submit"
    entity_id = registry.async_get_entity_id("button", DOMAIN, unique_id)
    if entity_id is None:
        return False
    registered = registry.async_get(entity_id)
    return registered is not None and registered.hidden_by is None


class IbokTypedReadingNumber(IbokEntity, NumberEntity):
    """The reading read off the dial, waiting for the button to send it.

    Not restored after a restart: a reading is meant to go out right after it
    is typed, and the button refuses one typed more than a day ago anyway.
    """

    _attr_translation_key = _KEY
    _attr_device_class = NumberDeviceClass.WATER
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_READING

    def __init__(
        self,
        coordinator: IbokCoordinator,
        meter: dict,
        meter_id: int,
        visible: bool,
        just_registered: bool,
    ) -> None:
        self._meter_id = meter_id
        self._visible = visible
        self._just_registered = just_registered
        self._expire_unsub: CALLBACK_TYPE | None = None
        serial = meter_serial(meter)
        super().__init__(coordinator, f"{serial}_{_KEY}", serial)
        # Hidden until wanted: most households report on the portal itself and
        # would only see a field they never use. A field re-created after a
        # source is unassigned instead matches the button next to it, so a
        # household that already unhid that button does not lose the field to
        # a fresh, hidden default.
        self._attr_entity_registry_visible_default = visible

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self._just_registered:
            self._match_visibility()
        self._schedule_expiry()

    def _match_visibility(self) -> None:
        """Align a field just (re)created with the button it belongs to.

        Only called for a field that did not already exist in the registry --
        brand new, or restored from the tombstone Home Assistant kept from
        before a source removed it, which also restores how it was hidden
        back then rather than leaving ``entity_registry_visible_default`` in
        charge. A plain reload of a field that was already there never runs
        this: whatever the household chose about it, hidden or shown, is left
        alone. The integration only ever undoes or reapplies its own hiding,
        so a field the household hid itself (``hidden_by`` is ``USER``) is
        left untouched here too, exactly like the button already is.
        """
        registered = self.registry_entry
        if registered is None or registered.hidden_by is er.RegistryEntryHider.USER:
            return
        wanted = None if self._visible else er.RegistryEntryHider.INTEGRATION
        if registered.hidden_by != wanted:
            er.async_get(self.hass).async_update_entity(
                self.entity_id, hidden_by=wanted
            )

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self._cancel_expiry()

    def _cancel_expiry(self) -> None:
        if self._expire_unsub is not None:
            self._expire_unsub()
            self._expire_unsub = None

    def _schedule_expiry(self) -> None:
        """(Re)arm the timer that empties the field once its value goes stale.

        The value itself lives on the coordinator, not on the entity, so this
        timer only has to prompt a state write at the moment it expires --
        ``native_value`` already stops returning it by then. The value only
        counts as stale once it is strictly older than the limit, so the
        timer runs a second past it; firing exactly on the limit would write
        the same value again and leave it on screen with no timer left.
        """
        self._cancel_expiry()
        typed = self.coordinator.typed_readings.get(self._meter_id)
        if typed is None:
            return
        _value, typed_at = typed
        remaining = max(typed_at + MAX_SOURCE_AGE - dt_util.utcnow(), timedelta(0))
        remaining += timedelta(seconds=1)
        self._expire_unsub = async_call_later(self.hass, remaining, self._async_expired)

    @callback
    def _async_expired(self, _now: datetime) -> None:
        self._expire_unsub = None
        self.async_write_ha_state()

    @property
    def native_step(self) -> float:
        """As fine as the dial: whole cubic metres unless the portal says more."""
        digits = fraction_digits(self.coordinator.meter_by_id(self._meter_id))
        return 10**-digits if digits else 1

    @property
    def native_value(self) -> float | None:
        typed = self.coordinator.typed_readings.get(self._meter_id)
        if typed is None:
            return None
        value, typed_at = typed
        if dt_util.utcnow() - typed_at > MAX_SOURCE_AGE:
            return None
        return value

    async def async_set_native_value(self, value: float) -> None:
        # number.set_value lets "nan" through its range check.
        if not math.isfinite(value):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_reading"
            )
        self.coordinator.typed_readings[self._meter_id] = (value, dt_util.utcnow())
        self._schedule_expiry()
        self.async_write_ha_state()
