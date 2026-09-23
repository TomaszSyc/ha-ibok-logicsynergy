"""The iBOK (LogicSynergy) integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .api import IbokApi, IbokError
from .const import (
    ATTR_METER_ID,
    ATTR_NOTE,
    ATTR_READING,
    ATTR_READING_DATE,
    CONF_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SERVICE_SUBMIT_READING,
)
from .coordinator import IbokCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

SUBMIT_READING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_READING): vol.Coerce(float),
        vol.Optional(ATTR_METER_ID): vol.Coerce(int),
        vol.Optional(ATTR_READING_DATE): cv.date,
        vol.Optional(ATTR_NOTE, default=""): cv.string,
    }
)

type IbokConfigEntry = ConfigEntry[IbokCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: IbokConfigEntry) -> bool:
    """Set up one iBOK account."""
    api = IbokApi(
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )

    hours = entry.options.get(CONF_SCAN_INTERVAL)
    interval = timedelta(hours=hours) if hours else DEFAULT_SCAN_INTERVAL

    coordinator = IbokCoordinator(hass, entry, api, interval)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IbokConfigEntry) -> bool:
    """Unload one iBOK account."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.api.async_close()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: IbokConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SUBMIT_READING):
        return

    async def _submit(call: ServiceCall) -> None:
        await _async_submit_reading(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_SUBMIT_READING, _submit, schema=SUBMIT_READING_SCHEMA
    )


async def _async_submit_reading(hass: HomeAssistant, call: ServiceCall) -> None:
    """Validate a reading and send it to the portal.

    Submitting a wrong reading is not a cosmetic mistake -- it lands on the
    customer's bill and has to be corrected with the operator. Everything the
    portal tells us about the meter is therefore checked before anything is
    sent, and the result is confirmed by re-reading rather than assumed.
    """
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("No iBOK account is set up")

    coordinator: IbokCoordinator = entries[0].runtime_data
    meters = coordinator.submittable_meters
    if not meters:
        raise ServiceValidationError(
            "The portal is not accepting a reading for any meter right now"
        )

    meter_id = call.data.get(ATTR_METER_ID)
    if meter_id is None:
        if len(meters) > 1:
            ids = ", ".join(str(m.get("id_wodom")) for m in meters)
            raise ServiceValidationError(
                f"Several meters accept a reading -- pass meter_id (one of: {ids})"
            )
        meter = meters[0]
    else:
        meter = coordinator.meter_by_id(meter_id)
        if meter is None:
            raise ServiceValidationError(f"Unknown meter_id: {meter_id}")

    reading = float(call.data[ATTR_READING])
    if reading < 0:
        raise ServiceValidationError("A meter reading cannot be negative")

    _validate_range(meter, reading)

    whole = int(reading)
    fraction = round((reading - whole) * 1000)

    when = call.data.get(ATTR_READING_DATE) or dt_util.now().date()

    try:
        await coordinator.api.async_submit_reading(
            meter_id=int(meter["id_wodom"]),
            whole=whole,
            fraction=fraction,
            reading_date=when.strftime("%Y-%m-%d"),
            previous=str(meter.get("sl", "") or ""),
            note=call.data.get(ATTR_NOTE, ""),
        )
    except IbokError as err:
        raise HomeAssistantError(f"Submitting the reading failed: {err}") from err

    # The portal answers into a hidden iframe, so the HTTP status proves
    # nothing. Refresh and let the caller see the result in the entities.
    await coordinator.async_request_refresh()


def _validate_range(meter: dict[str, Any], reading: float) -> None:
    """Reject readings the portal itself would not accept.

    NotifyReadout_v1 publishes the allowed window, which is the cheapest
    safeguard available: a typo caught here never reaches the operator.
    """
    low = _as_float(meter.get("min_zakres"))
    high = _as_float(meter.get("zakres"))

    if low is not None and reading < low:
        raise ServiceValidationError(
            f"Reading {reading} is below the portal's minimum of {low}"
        )
    if high is not None and high > 0 and reading > high:
        raise ServiceValidationError(
            f"Reading {reading} is above the portal's maximum of {high}"
        )

    digits = meter.get("l_cyfr_l")
    if isinstance(digits, int) and digits > 0 and int(reading) >= 10**digits:
        raise ServiceValidationError(
            f"Reading {reading} has more than {digits} digits before the decimal point"
        )


def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return None
