"""State classes decide what the long-term statistics make of a sensor.

A per-period value presented as a running total poisons the statistics: a
smaller month is recorded as a negative change.
"""

from __future__ import annotations

from types import SimpleNamespace

from homeassistant.util import dt as dt_util

from custom_components.ibok.sensor import (
    IbokLastConsumptionSensor,
    IbokLastInvoiceSensor,
)

SERIAL = "12345678"


def _coordinator(readouts=None, invoices=None) -> SimpleNamespace:
    return SimpleNamespace(
        data={"readouts": readouts or [], "invoices": invoices or []},
        config_entry=SimpleNamespace(
            entry_id="e1", title="ibok.przyklad.pl", data={"base_url": "https://x"}
        ),
        hass=SimpleNamespace(config=SimpleNamespace(currency="PLN")),
        last_update_success=True,
    )


def _meter(*rows) -> list[dict]:
    return [{"numer_fabryczny": SERIAL, "odczyty": list(rows)}]


def test_consumption_resets_at_the_start_of_its_period() -> None:
    coordinator = _coordinator(
        _meter(
            {"do": "2026-03-04", "sl": "40", "zu": "7"},
            {"do": "2026-04-06", "sl": "48", "zu": "8"},
        )
    )
    sensor = IbokLastConsumptionSensor(coordinator, SERIAL)

    assert sensor.native_value == 8
    assert sensor.last_reset == dt_util.start_of_local_day(
        dt_util.parse_date("2026-03-04")
    )


def test_a_new_period_moves_the_reset() -> None:
    """The reset moving is what tells Home Assistant a new period began."""
    first = _meter(
        {"do": "2026-03-04", "zu": "7"},
        {"do": "2026-04-06", "zu": "8"},
    )
    later = _meter(
        {"do": "2026-03-04", "zu": "7"},
        {"do": "2026-04-06", "zu": "8"},
        {"do": "2026-05-05", "zu": "6"},
    )

    before = IbokLastConsumptionSensor(_coordinator(first), SERIAL).last_reset
    after = IbokLastConsumptionSensor(_coordinator(later), SERIAL).last_reset

    assert after > before


def test_the_invoice_is_not_recorded_as_a_running_total() -> None:
    sensor = IbokLastInvoiceSensor(_coordinator(invoices=[{"brutto": "216,35"}]))

    assert sensor.native_value == 216.35
    assert sensor.state_class is None
