"""Diagnostics: what the portal sends, in shape only.

A report from another operator's portal is useful only with the data that
portal sends, and that data is a household's bills, meters and address. So
every value is masked, letters to x and digits to 9, with everything else
kept. That keeps what a fix depends on: which fields exist, how a date or a
decimal is written, whether a list is empty.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import IbokConfigEntry
from .api import IbokError

# Enough items of a list to show its shape; the rest are only counted.
_MAX_ITEMS = 3
# Four digits or more in a key are data rather than part of a field name: the
# option keys carry the meter serial, for one. Only that part is masked.
_DATA_IN_KEY = re.compile(r"\d{4,}")
# Asking the portal for its menu must not hold the download for the full
# request timeout when the portal is down.
_MENU_TIMEOUT = 10


def mask(value: Any) -> Any:
    """The value with its letters and digits replaced, structure kept."""
    if isinstance(value, dict):
        return {_mask_key(str(key)): mask(item) for key, item in value.items()}
    if isinstance(value, list):
        shown = [mask(item) for item in value[:_MAX_ITEMS]]
        hidden = len(value) - _MAX_ITEMS
        return shown + [f"... {hidden} more"] if hidden > 0 else shown
    if value is None or isinstance(value, bool):
        return value
    return re.sub(r"\d", "9", re.sub(r"[^\W\d_]", "x", str(value)))


def _mask_key(key: str) -> str:
    return _DATA_IN_KEY.sub(lambda digits: "9" * len(digits.group()), key)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: IbokConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    try:
        async with asyncio.timeout(_MENU_TIMEOUT):
            # Module names are the portal's own and say nothing about anyone.
            modules: Any = await coordinator.api.async_menu()
    except (IbokError, TimeoutError) as err:
        modules = f"not available: {type(err).__name__}"

    options = {
        _mask_key(key): value if key == CONF_SCAN_INTERVAL else mask(value)
        for key, value in entry.options.items()
    }
    return {
        # The portal address stays: it names the operator, which a report
        # needs, and the issue form asks for it anyway.
        "entry": {
            "data": async_redact_data(dict(entry.data), {CONF_USERNAME, CONF_PASSWORD}),
            "options": options,
        },
        "portal_modules": modules,
        "failed_modules": sorted(coordinator.failed),
        "last_update_success": coordinator.last_update_success,
        "data": mask(coordinator.data),
    }
