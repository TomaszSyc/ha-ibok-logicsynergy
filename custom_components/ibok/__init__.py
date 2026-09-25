"""The iBOK (LogicSynergy) integration."""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    Platform,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType

from .api import IbokApi
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_METER_ID,
    ATTR_NOTE,
    ATTR_READING,
    ATTR_READING_DATE,
    CONF_BASE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_READING,
    SERVICE_SUBMIT_READING,
)
from .coordinator import IbokCoordinator
from .submit import Account, async_submit, resolve_target

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON, Platform.NUMBER]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SUBMIT_READING_SCHEMA = vol.Schema(
    {
        # The upper bound also rejects nan and inf, which float() accepts.
        vol.Required(ATTR_READING): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=MAX_READING)
        ),
        vol.Optional(ATTR_METER_ID): vol.Coerce(int),
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_READING_DATE): cv.date,
        vol.Optional(ATTR_NOTE, default=""): cv.string,
    }
)

type IbokConfigEntry = ConfigEntry[IbokCoordinator]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the service independently of any account.

    Registered here rather than in async_setup_entry, so it exists even while
    the portal is down when Home Assistant starts. It is an admin service: a
    reading goes onto the account holder's bill.
    """

    async def _submit(call: ServiceCall) -> None:
        await _async_submit_reading(hass, call)

    async_register_admin_service(
        hass, DOMAIN, SERVICE_SUBMIT_READING, _submit, schema=SUBMIT_READING_SCHEMA
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: IbokConfigEntry) -> bool:
    """Set up one iBOK account."""
    api = IbokApi(
        entry.data[CONF_BASE_URL],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    # Before the first refresh, which can fail. Home Assistant runs on_unload
    # callbacks after a failed setup too; without this every retry during an
    # outage would leave a session open.
    entry.async_on_unload(api.async_close)

    hours = entry.options.get(CONF_SCAN_INTERVAL)
    interval = timedelta(hours=hours) if hours else DEFAULT_SCAN_INTERVAL

    coordinator = IbokCoordinator(hass, entry, api, interval)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IbokConfigEntry) -> bool:
    """Unload one iBOK account. The session closes through on_unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: IbokConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_submit_reading(hass: HomeAssistant, call: ServiceCall) -> None:
    """Find the account and meter the call means, then submit through it."""
    entries: list[IbokConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("No iBOK account is set up")

    accounts = [
        Account(e.entry_id, e.title, e.runtime_data.submittable_meters) for e in entries
    ]
    entry_id, meter_id = resolve_target(
        accounts, call.data.get(ATTR_METER_ID), call.data.get(ATTR_CONFIG_ENTRY_ID)
    )
    entry = next(e for e in entries if e.entry_id == entry_id)

    await async_submit(
        entry.runtime_data,
        meter_id,
        float(call.data[ATTR_READING]),
        call.data.get(ATTR_READING_DATE),
        call.data.get(ATTR_NOTE, ""),
    )
