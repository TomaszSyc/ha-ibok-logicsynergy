"""Repairs for the iBOK integration: confirming an unknown submission outcome."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

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


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Every issue this integration raises is an unknown submission outcome."""
    data = data or {}
    meter_id = data.get("meter_id")
    return OutcomeUnknownRepairFlow(
        data.get("entry_id"), int(meter_id) if meter_id is not None else None
    )
