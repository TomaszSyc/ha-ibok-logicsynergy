"""Polling coordinator for the iBOK integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import IbokApi, IbokAuthError, IbokError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class IbokCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches every module the integration uses in one pass."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: IbokApi,
        scan_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=scan_interval,
            config_entry=entry,
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return {
                "meters": await self.api.async_meters(),
                "readouts": await self.api.async_readouts(),
                "notify": await self.api.async_notify_readout(),
                "accountancy": await self.api.async_accountancy(),
                "invoices": await self.api.async_invoices(),
            }
        except IbokAuthError as err:
            # Surfaces the re-authentication flow instead of leaving every
            # entity unavailable with no explanation.
            raise ConfigEntryAuthFailed(str(err)) from err
        except IbokError as err:
            raise UpdateFailed(str(err)) from err

    def meter_by_id(self, meter_id: int) -> dict[str, Any] | None:
        """Look up a meter in the submission dataset by its internal id."""
        for row in self.data.get("notify", []) if self.data else []:
            if str(row.get("id_wodom")) == str(meter_id):
                return row
        return None

    @property
    def submittable_meters(self) -> list[dict[str, Any]]:
        """Meters the portal currently accepts a reading for."""
        return list(self.data.get("notify", [])) if self.data else []
