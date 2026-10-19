"""Telling a replaced meter's readings from the old one's.

An operator who swaps a meter can keep its serial, so the portal files the old
and the new readings under one meter. Two signals mark the swap. A total that
falls more than a tenth below the highest so far is what Home Assistant itself
treats as a reset (``reset_detected``), so the two agree on every float. That
alone misses a small meter, such as one on a garden tap, whose new first
reading lands within a tenth of the old total; the portal's own removal entry
("wymontowanie", "demontaż") catches those, and the first reading after it
starts the new meter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_REPLACEMENT_CONFIRMED_PREFIX,
    CONF_SOURCE_ENTITY_PREFIX,
    DOMAIN,
)
from .coordinator import meter_serial, parse_number, readouts_for

NEW_METER_BELOW: Final = 0.9
REMOVAL_WORDS: Final = ("wymontowan", "demonta")
REPLACEMENT_ISSUE_PREFIX: Final = "meter_replaced_"


@dataclass(frozen=True)
class CurrentMeter:
    """The readings of the meter now installed."""

    rows: list[dict[str, Any]]
    # The raw ``do`` of its first row; None when the history holds no swap.
    replaced_on: str | None


def current_meter(rows: Sequence[Mapping[str, Any]]) -> CurrentMeter:
    """The readings of the latest meter in a history given oldest first."""
    start, highest, after_removal = 0, None, False
    for i, row in enumerate(rows):
        value = parse_number(row.get("sl"))
        if value is not None:
            if after_removal or (
                highest is not None and value < NEW_METER_BELOW * highest
            ):
                start, highest, after_removal = i, None, False
            highest = value if highest is None else max(highest, value)
        if any(word in str(row.get("typ") or "").lower() for word in REMOVAL_WORDS):
            after_removal = True
    return CurrentMeter(list(rows[start:]), rows[start].get("do") if start else None)


def unconfirmed_replacement(
    options: Mapping[str, Any], serial: str, replaced_on: str | None
) -> str | None:
    """The swap date still to confirm for a meter read from a source entity.

    Only a source has anything to confirm: its overlay was calibrated on the
    old meter and may still count it. A person typing a reading reads the new
    one off the dial. A confirmed date settles that swap only; a later one is
    a different date and needs confirming again.
    """
    if replaced_on is None or not options.get(f"{CONF_SOURCE_ENTITY_PREFIX}{serial}"):
        return None
    if options.get(f"{CONF_REPLACEMENT_CONFIRMED_PREFIX}{serial}") == replaced_on:
        return None
    return replaced_on


def replacement_issue_id(entry_id: str, serial: str) -> str:
    """The Repairs issue for one meter of one account.

    The entry id is part of it because two accounts can share a serial; each
    must get, and clear, only its own issue.
    """
    return f"{REPLACEMENT_ISSUE_PREFIX}{entry_id}_{serial}"


def sync_replacement_issues(
    hass: HomeAssistant,
    entry_id: str,
    options: Mapping[str, Any],
    data: Mapping[str, Any] | None,
) -> None:
    """Raise an issue for each swap still to confirm, and drop the settled ones.

    Run after every poll rather than once: a swap can show up in a later
    poll, and an issue for a meter that is no longer in the data (or whose
    source was removed) has nothing left to confirm. Without readouts (the
    portal's module failed) nothing is known, so the issues stand as they
    were: dropping them would lift the block on the source for as long as
    the module stays down. For the same reason they are persistent, so a
    restart in that state keeps them too.
    """
    if data is None or data.get("readouts") is None:
        return
    wanted: dict[str, str] = {}
    for row in data["readouts"]:
        serial = meter_serial(row)
        date = unconfirmed_replacement(
            options, serial, current_meter(readouts_for(data, serial)).replaced_on
        )
        if date is not None:
            wanted[replacement_issue_id(entry_id, serial)] = date
            ir.async_create_issue(
                hass,
                DOMAIN,
                replacement_issue_id(entry_id, serial),
                is_fixable=True,
                is_persistent=True,
                severity=ir.IssueSeverity.WARNING,
                translation_key="meter_replaced",
                translation_placeholders={"serial": serial, "date": date},
                data={"entry_id": entry_id, "serial": serial, "replaced_on": date},
            )
    for issue in _issues_of(hass, entry_id):
        if issue.issue_id not in wanted:
            ir.async_delete_issue(hass, DOMAIN, issue.issue_id)


def pending_replacement(
    hass: HomeAssistant,
    entry_id: str,
    options: Mapping[str, Any],
    data: Mapping[str, Any] | None,
    serial: str,
) -> str | None:
    """The swap date still to confirm for a meter, from the readouts or the issue.

    The readouts decide when there are any. Without them (the portal's
    Readouts module failed, say on the reload that follows a restart) the
    open Repairs issue is what remembers the swap: a reload while that module
    fails must not lift the block and let the source through.
    """
    if data is not None and data.get("readouts") is not None:
        replaced_on = current_meter(readouts_for(data, serial)).replaced_on
    else:
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, replacement_issue_id(entry_id, serial)
        )
        replaced_on = (issue.data or {}).get("replaced_on") if issue else None
    return unconfirmed_replacement(options, serial, replaced_on)


def _issues_of(hass: HomeAssistant, entry_id: str) -> list[ir.IssueEntry]:
    return [
        issue
        for issue in ir.async_get(hass).issues.values()
        if issue.domain == DOMAIN
        and issue.issue_id.startswith(REPLACEMENT_ISSUE_PREFIX)
        and (issue.data or {}).get("entry_id") == entry_id
    ]


def remove_replacement_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Delete every replacement issue of an account that is going away."""
    for issue in _issues_of(hass, entry_id):
        ir.async_delete_issue(hass, DOMAIN, issue.issue_id)
