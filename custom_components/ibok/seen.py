"""What the portal showed last, kept across restarts so a change fires once."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_VERSION = 1
# Written a moment after the last change: several meters changing in one
# refresh become one write, and Home Assistant flushes it when it stops.
_SAVE_DELAY = 1


class SeenStore:
    """The last submission seen for each meter, by serial, and the announcements.

    The data stays a plain dict and is saved whole, so a key this class does
    not know about survives a load and a save.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(
            hass, _VERSION, f"{DOMAIN}.{entry_id}"
        )
        self._data: dict[str, Any] = {}
        self._dirty = False

    async def async_load(self) -> None:
        loaded = await self._store.async_load()
        self._data = loaded if isinstance(loaded, dict) else {}

    def knows(self, serial: str) -> bool:
        """Whether the meter has been seen at all, with or without a submission."""
        return serial in self._data.get("submissions", {})

    def submission(self, serial: str) -> tuple[str, int] | None:
        """The (id, status) pair last seen for a meter; None if none was."""
        try:
            iid, ist = self._data.get("submissions", {})[serial]
            return str(iid), int(ist)
        except (KeyError, TypeError, ValueError):
            return None

    def set_submission(self, serial: str, pair: tuple[str, int] | None) -> None:
        """Remember what the meter showed; None means it showed no submission.

        That is not the same as never having seen the meter: a submission
        appearing after "none" is new, an old one found on the first look is not.
        """
        self._data.setdefault("submissions", {})[serial] = (
            None if pair is None else [pair[0], pair[1]]
        )
        self._dirty = True
        self._store.async_delay_save(lambda: self._data, _SAVE_DELAY)

    def announcements(self) -> set[str] | None:
        """The keys of the announcements last seen; None if never recorded."""
        keys = self._data.get("announcements")
        if not isinstance(keys, list):
            return None
        return {str(key) for key in keys}

    def set_announcements(self, keys: set[str]) -> None:
        """Remember which announcements the portal shows now.

        Written only when the set changed: most refreshes see the same ones.
        """
        if self.announcements() == keys:
            return
        self._data["announcements"] = sorted(keys)
        self._dirty = True
        self._store.async_delay_save(lambda: self._data, _SAVE_DELAY)

    async def async_flush(self) -> None:
        """Write a pending change now.

        A reload within the delay would otherwise load the old file into the
        new instance, and the change would fire a second time.
        """
        if self._dirty:
            self._dirty = False
            await self._store.async_save(self._data)


async def async_remove_seen(hass: HomeAssistant, entry_id: str) -> None:
    """Delete the stored data of an account that is going away."""
    await Store(hass, _VERSION, f"{DOMAIN}.{entry_id}").async_remove()
