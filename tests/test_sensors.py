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
    IbokLastReadingSensor,
)

SERIAL = "12345678"


def _coordinator(
    readouts=None, invoices=None, meters=None, notify=None
) -> SimpleNamespace:
    return SimpleNamespace(
        data={
            "readouts": readouts or [],
            "invoices": invoices or [],
            "meters": meters or [],
            "notify": notify or [],
        },
        config_entry=SimpleNamespace(
            entry_id="e1", title="ibok.przyklad.pl", data={"base_url": "https://x"}
        ),
        hass=SimpleNamespace(config=SimpleNamespace(currency="PLN")),
        last_update_success=True,
        failed=frozenset(),
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


def test_a_correction_downwards_keeps_the_highest_reading() -> None:
    """A correction can lower the newest reading; the state must not drop."""
    coordinator = _coordinator(
        _meter(
            {"do": "2026-03-01", "sl": "110"},
            {"do": "2026-04-01", "sl": "105"},
        )
    )
    sensor = IbokLastReadingSensor(coordinator, SERIAL)

    assert sensor.native_value == 110
    assert sensor.extra_state_attributes["portal_reading"] == 105.0
    assert sensor.extra_state_attributes["corrected"] is True


def test_an_uncorrected_reading_is_not_flagged() -> None:
    coordinator = _coordinator(
        _meter(
            {"do": "2026-03-01", "sl": "45"},
            {"do": "2026-04-01", "sl": "48"},
        )
    )
    sensor = IbokLastReadingSensor(coordinator, SERIAL)

    assert sensor.native_value == 48
    assert sensor.extra_state_attributes["portal_reading"] == 48.0
    assert sensor.extra_state_attributes["corrected"] is False


def test_a_negative_consumption_stays() -> None:
    """A correction on the invoice can make a period's consumption negative."""
    coordinator = _coordinator(_meter({"do": "2026-04-06", "sl": "48", "zu": "-5"}))
    sensor = IbokLastConsumptionSensor(coordinator, SERIAL)

    assert sensor.native_value == -5.0


def test_the_invoice_is_not_recorded_as_a_running_total() -> None:
    sensor = IbokLastInvoiceSensor(_coordinator(invoices=[{"brutto": "216,35"}]))

    assert sensor.native_value == 216.35
    assert sensor.state_class is None


def test_the_newest_invoice_is_picked_by_its_issue_date() -> None:
    """The portal does not promise an order, so the position proves nothing."""
    invoices = [
        {"nw": "2026-02-10", "brutto": "100,00"},
        {"nw": "2026-04-12", "brutto": "150,00"},
        {"nw": "2026-03-11", "brutto": "120,00"},
    ]
    sensor = IbokLastInvoiceSensor(_coordinator(invoices=invoices))

    assert sensor.native_value == 150.0


def test_invoices_without_a_date_fall_back_to_the_first() -> None:
    invoices = [{"brutto": "100,00"}, {"brutto": "150,00"}]
    sensor = IbokLastInvoiceSensor(_coordinator(invoices=invoices))

    assert sensor.native_value == 100.0
