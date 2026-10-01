"""The iBOK (LogicSynergy) integration."""

from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
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
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .api import IbokApi, IbokError
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
from .ledger import remove_issues
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
    # When this instance came up, for the button's restart rule: a source
    # entity's state can be merely restored rather than freshly reported.
    hass.data.setdefault(DOMAIN, {})["started_at"] = dt_util.utcnow()

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
        # Home Assistant's session with a jar of this entry's own: the portal
        # knows the account only by its cookie, so two accounts must never
        # share one. Home Assistant lets go of the session itself when the
        # entry unloads, a failed setup included.
        session=async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar()),
    )
    # Before the first refresh, which can fail. Home Assistant runs on_unload
    # callbacks after a failed setup too, and a retry must start logged out.
    entry.async_on_unload(api.async_close)

    hours = entry.options.get(CONF_SCAN_INTERVAL)
    interval = timedelta(hours=hours) if hours else DEFAULT_SCAN_INTERVAL

    coordinator = IbokCoordinator(hass, entry, api, interval)
    # Before the first refresh, so a reading the portal now shows can lift a
    # block that was carried over from before the restart.
    coordinator.ledger.restore()
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: IbokConfigEntry) -> bool:
    """Unload one iBOK account. The login is dropped through on_unload."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: IbokConfigEntry) -> None:
    """Drop the removed account's Repairs issues; nothing is left to confirm."""
    remove_issues(hass, entry.entry_id)


async def _async_reload(hass: HomeAssistant, entry: IbokConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _async_submit_reading(hass: HomeAssistant, call: ServiceCall) -> None:
    """Find the account and meter the call means, then submit through it.

    The meter is looked up in each account's reading form as the portal shows
    it now, not in the last poll: the portal may have opened the reading window
    since, and a meter missing from an hours-old snapshot would be refused. One
    request per account is cheap for a service called once a month.
    """
    entries: list[IbokConfigEntry] = hass.config_entries.async_loaded_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_account"
        )

    wanted = call.data.get(ATTR_CONFIG_ENTRY_ID)
    candidates = [e for e in entries if wanted is None or e.entry_id == wanted]
    accounts: list[Account] = []
    answered = 0
    error: IbokError | None = None
    for entry in candidates:
        coordinator = entry.runtime_data
        try:
            meters = await coordinator.api.async_notify_readout()
        except IbokError as err:
            _LOGGER.warning(
                "Could not ask %s which meters accept a reading: %s", entry.title, err
            )
            error = err
            # Still counted, from the last poll: a meter id this account shares
            # with another one must stay ambiguous rather than quietly go to the
            # other account. If it is the one chosen, async_submit asks the
            # portal again and refuses when that fails too.
            meters = coordinator.submittable_meters
        else:
            answered += 1
        accounts.append(Account(entry.entry_id, entry.title, meters))

    if error is not None and not answered:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="nothing_sent_check_failed",
            translation_placeholders={"error": str(error)},
        ) from error

    entry_id, target_id = resolve_target(accounts, call.data.get(ATTR_METER_ID), wanted)
    entry = next(e for e in entries if e.entry_id == entry_id)

    await async_submit(
        entry.runtime_data,
        target_id,
        float(call.data[ATTR_READING]),
        call.data.get(ATTR_READING_DATE),
        call.data.get(ATTR_NOTE, ""),
    )
