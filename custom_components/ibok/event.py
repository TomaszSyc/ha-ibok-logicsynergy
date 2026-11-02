"""Event entities: a submitted reading's fate and the operator's announcements."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.start import async_at_started

from . import IbokConfigEntry
from .announcements import ANNOUNCEMENT, NEWS, announcement_key
from .coordinator import IbokCoordinator, meter_id, meter_serial, parse_number
from .entity import IbokEntity
from .ledger import form_status, portal_date, submission_id

# Updates are pushed from the coordinator, never polled per entity.
PARALLEL_UPDATES = 0

# The reading form's status for a submission, as an event type.
SUBMISSION_EVENTS = {1: "waiting", 2: "in_progress", 3: "rejected", 4: "approved"}


async def async_setup_entry(
    hass: HomeAssistant, entry: IbokConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    # Checked on every update, like the buttons: a meter the portal lists only
    # later still gets its event without a restart.
    @callback
    def _add_new_events() -> None:
        entities = []
        for meter in coordinator.submittable_meters:
            serial = meter_serial(meter)
            mid = meter_id(meter)
            if mid is None or serial in known:
                continue
            known.add(serial)
            entities.append(IbokSubmissionEvent(coordinator, serial, mid))
        if entities:
            async_add_entities(entities)

    async_add_entities([IbokAnnouncementEvent(coordinator)])
    _add_new_events()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_events))


class _IbokEvent(IbokEntity, EventEntity):
    """An event entity that compares only once Home Assistant has started.

    Automations arm their triggers as the start completes, so an event fired
    earlier (a change that happened while Home Assistant was down) would reach
    none of them, and the store would already have used it up.
    """

    _started = False

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_at_started(self.hass, self._async_started))

    @callback
    def _async_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        self._check()
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._started:
            self._check()
        self.async_write_ha_state()

    def _check(self) -> None:
        raise NotImplementedError


class IbokSubmissionEvent(_IbokEvent):
    """Fires when the reading form's submission for a meter changes.

    The form keeps one submission per meter, so its (id, status) pair is all
    there is to compare. The last pair seen is stored, which is how a change
    that happened while Home Assistant was down fires once on the next start.
    A meter never seen before gets its pair stored without an event: an old
    submission must not be announced as if it had just happened. A meter seen
    with no submission stores that too, so the first one to appear fires.
    """

    _modules = ("notify",)
    _attr_translation_key = "submission"
    _attr_event_types: ClassVar[list[str]] = list(SUBMISSION_EVENTS.values())

    def __init__(self, coordinator: IbokCoordinator, serial: str, mid: int) -> None:
        super().__init__(coordinator, f"{serial}_submission", serial)
        self._serial = serial
        self._meter_id = mid

    def _check(self) -> None:
        # A module that failed keeps its previous rows, which prove nothing.
        if "notify" in self.coordinator.failed:
            return
        row = next(
            (
                r
                for r in self.coordinator.submittable_meters
                if meter_id(r) == self._meter_id and meter_serial(r) == self._serial
            ),
            None,
        )
        if row is None:
            return
        iid, ist = submission_id(row), form_status(row)
        seen = self.coordinator.seen
        known = seen.knows(self._serial)
        if iid is None:
            if not known:
                seen.set_submission(self._serial, None)
            return
        if ist not in SUBMISSION_EVENTS:
            return
        previous = seen.submission(self._serial)
        if previous == (iid, ist):
            return
        seen.set_submission(self._serial, (iid, ist))
        if not known:
            return
        day = portal_date(row.get("ido"))
        self._trigger_event(
            SUBMISSION_EVENTS[ist],
            {
                "reading": parse_number(row.get("isl")),
                "reading_date": day.isoformat() if day else None,
                "reason": str(row.get("tkom") or "").strip() or None,
            },
        )


class IbokAnnouncementEvent(_IbokEvent):
    """Fires for each announcement the operator puts up.

    An announcement is the same one again when its title and short text are, and
    only those digests are stored. What is stored is what the portal shows now,
    so one taken down and put up again fires a second time, on purpose. With
    nothing stored yet the announcements shown are stored without an event, so
    old ones are not announced as new.
    """

    _modules = ("announcements",)
    _attr_translation_key = "announcement"
    _attr_event_types: ClassVar[list[str]] = [NEWS, ANNOUNCEMENT]

    def __init__(self, coordinator: IbokCoordinator) -> None:
        super().__init__(coordinator, "announcement")

    def _check(self) -> None:
        # A module that failed keeps its previous answer, which proves nothing.
        if "announcements" in self.coordinator.failed:
            return
        items = (self.coordinator.data or {}).get("announcements") or []
        keys = {announcement_key(item) for item in items}
        seen = self.coordinator.seen
        previous = seen.announcements()
        seen.set_announcements(keys)
        if previous is None:
            return
        for item in items:
            key = announcement_key(item)
            if key in previous:
                continue
            # The same announcement listed twice is still one announcement.
            previous.add(key)
            self._trigger_event(
                item["type"],
                {k: item[k] for k in ("title", "short", "text")},
            )
            self.async_write_ha_state()
