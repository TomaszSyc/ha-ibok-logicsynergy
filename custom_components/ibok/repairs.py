"""Repairs for the iBOK integration: an unknown submission outcome, a replaced meter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import CONF_REPLACEMENT_CONFIRMED_PREFIX, DOMAIN
from .replacement import REPLACEMENT_ISSUE_PREFIX

if TYPE_CHECKING:
    from homeassistant.components.repairs import RepairsFlowResult


class OutcomeUnknownRepairFlow(ConfirmRepairFlow):
    """The user has checked the portal; lift the block on the meter."""

    def __init__(self, entry_id: str | None, meter_id: int | None) -> None:
        super().__init__()
        self._entry_id = entry_id
        self._meter_id = meter_id

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            entry = (
                self.hass.config_entries.async_get_entry(self._entry_id)
                if self._entry_id
                else None
            )
            # An entry that is not loaded has no ledger to clear: it rebuilds
            # its blocks from the issues on setup, and this one is about to go.
            if (
                entry is not None
                and entry.state is ConfigEntryState.LOADED
                and self._meter_id is not None
            ):
                entry.runtime_data.ledger.clear_unknown(self._meter_id)
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        return await super().async_step_confirm(user_input)


class MeterReplacedRepairFlow(ConfirmRepairFlow):
    """The user has moved the overlay to the new meter; let the source through."""

    def __init__(self, entry_id: str | None, serial: str, replaced_on: str) -> None:
        super().__init__()
        self._entry_id = entry_id
        self._serial = serial
        self._replaced_on = replaced_on

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        if user_input is not None:
            entry = (
                self.hass.config_entries.async_get_entry(self._entry_id)
                if self._entry_id
                else None
            )
            # Writing the options reloads the entry, which syncs the issues
            # again; the delete below covers an entry that is not loaded.
            if entry is not None:
                self.hass.config_entries.async_update_entry(
                    entry,
                    options={
                        **entry.options,
                        f"{CONF_REPLACEMENT_CONFIRMED_PREFIX}{self._serial}": (
                            self._replaced_on
                        ),
                    },
                )
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """A replaced meter, or else an unknown submission outcome."""
    data = data or {}
    if issue_id.startswith(REPLACEMENT_ISSUE_PREFIX):
        return MeterReplacedRepairFlow(
            data.get("entry_id"), str(data["serial"]), str(data["replaced_on"])
        )
    meter_id = data.get("meter_id")
    return OutcomeUnknownRepairFlow(
        data.get("entry_id"), int(meter_id) if meter_id is not None else None
    )
