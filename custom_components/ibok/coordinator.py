"""Polling coordinator for the iBOK integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    IbokApi,
    IbokAuthError,
    IbokDisconnectedError,
    IbokError,
    IbokResponseError,
    IbokTimeoutError,
)
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


def meter_serial(row: Mapping[str, Any]) -> str:
    """A meter's serial, spelled the same way everywhere in the integration.

    The serial names devices and option keys, and three modules carry it: the
    readouts and the reading form as ``numer_fabryczny``, the meter list as
    ``numer_fabr``. Read differently, the same meter would become two devices.
    Where the form lists a meter without a serial, its internal id stands in,
    so it still gets a button; the other modules have no such id.
    """
    serial = str(row.get("numer_fabryczny") or row.get("numer_fabr") or "").strip()
    return serial or str(row.get("id_wodom") or "").strip()


def fraction_digits(meter: Mapping[str, Any] | None) -> int:
    """Fractional digits the portal declares for a meter's dial; none if absent."""
    try:
        return max(0, int((meter or {}).get("l_cyfr_p")))
    except (TypeError, ValueError):
        return 0


class IbokCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches each module the integration uses, each on its own.

    One module timing out must not take down the rest: an unanswered invoice
    list would otherwise make the balance, every meter and the submit button
    unavailable along with it. A module that fails keeps its last good data,
    and ``failed`` says which ones did, for the entities built on them.
    """

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
        self.failed: frozenset[str] = frozenset()
        # Readings typed by hand, by meter id, with the time they were typed,
        # until they are sent. Kept here rather than in the number entity's
        # state, whose timestamp a restart would renew: a value typed last week
        # would then look as fresh as one typed a minute ago.
        self.typed_readings: dict[int, tuple[float, datetime]] = {}

    async def _async_update_data(self) -> dict[str, Any]:
        fetchers: dict[str, Callable[[], Awaitable[Any]]] = {
            "readouts": self.api.async_readouts,
            "notify": self.api.async_notify_readout,
            # Legalisation dates, and each meter's share of water and sewage.
            "meters": self.api.async_meters,
            "accountancy": self.api.async_accountancy,
            "invoices": self.api.async_invoices,
        }
        previous = self.data or {}
        data: dict[str, Any] = {}
        errors: dict[str, IbokError] = {}

        # One after another, not in parallel: the modules share one session,
        # and a login renewed halfway through must not race another request.
        for key, fetch in fetchers.items():
            try:
                data[key] = await fetch()
            except IbokAuthError as err:
                # Surfaces the re-authentication flow instead of leaving every
                # entity unavailable with no explanation.
                raise ConfigEntryAuthFailed(str(err)) from err
            except (IbokTimeoutError, IbokDisconnectedError, IbokResponseError) as err:
                # The portal was reached; only this module's answer failed.
                errors[key] = err
                data[key] = previous.get(key)
            except IbokError as err:
                # Not reaching the portal at all is no module's fault. Stop here
                # rather than wait out the same failure once per module.
                raise UpdateFailed(str(err)) from err

        if len(errors) == len(fetchers):
            # Nothing answered: the portal is down, not a module.
            raise UpdateFailed(str(next(iter(errors.values()))))

        failed = frozenset(errors)
        for key in sorted(failed - self.failed):
            _LOGGER.warning(
                "The portal did not return %s, its entities are unavailable: %s",
                key,
                errors[key],
            )
        for key in sorted(self.failed - failed):
            _LOGGER.info("The portal returns %s again", key)
        self.failed = failed
        return data

    def meter_by_id(self, meter_id: int) -> dict[str, Any] | None:
        """Look up a meter in the submission dataset by its internal id."""
        for row in self.submittable_meters:
            if str(row.get("id_wodom")) == str(meter_id):
                return row
        return None

    @property
    def submittable_meters(self) -> list[dict[str, Any]]:
        """Meters the portal currently accepts a reading for."""
        return list((self.data or {}).get("notify") or [])
