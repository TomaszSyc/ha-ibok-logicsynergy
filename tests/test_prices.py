"""The price of a cubic metre, the payment deadline and the legalisation date."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from custom_components.ibok.sensor import (
    IbokLegalisationSensor,
    IbokPaymentDueSensor,
    IbokPriceSensor,
    current_unit_prices,
    meter_price,
    unit_prices,
)

SERIAL = "12345678"
WATER = {"pn": "Dostawa wody", "pj": "m3   ", "pi": "8", "pc": "6,00", "pt": "8%    "}
SEWAGE = {
    "pn": "Odprowadzanie ścieków",
    "pj": "m3",
    "pi": "8",
    "pc": "10,00",
    "pt": "8%",
}
STANDING = [
    # Billed per month: no part of a price per cubic metre, name or not.
    {
        "pn": "Opłata abonamentowa woda",
        "pj": "szt.",
        "pi": "1",
        "pc": "5,00",
        "pt": "8%",
    },
    {
        "pn": "Opłata abonamentowa ścieki",
        "pj": "m-c",
        "pi": "1",
        "pc": "4,00",
        "pt": "8%",
    },
]


def _invoice(issued: str, *lines: dict, due: str = "") -> dict:
    return {"nw": issued, "nt": due, "brutto": "150,00", "pw": [{"poz": list(lines)}]}


def _coordinator(invoices=(), meters=()) -> SimpleNamespace:
    return SimpleNamespace(
        data={"invoices": list(invoices), "meters": list(meters)},
        config_entry=SimpleNamespace(
            entry_id="e1", title="ibok.przyklad.pl", data={"base_url": "https://x"}
        ),
        hass=SimpleNamespace(config=SimpleNamespace(currency="PLN")),
        last_update_success=True,
        failed=frozenset(),
    )


# --- reading the invoice lines ---------------------------------------------


def test_a_cubic_metre_is_priced_with_its_vat() -> None:
    prices = unit_prices(_invoice("2026-04-10", WATER, SEWAGE, *STANDING))

    assert prices == pytest.approx({"water": 6.48, "sewage": 10.8})


@pytest.mark.parametrize("unit", ["m3", "m³", "M3", " m 3 "])
def test_the_unit_can_be_written_several_ways(unit: str) -> None:
    assert "water" in unit_prices(_invoice("2026-04-10", {**WATER, "pj": unit}))


def test_sewage_is_recognised_before_water() -> None:
    """The name of this sewage line also mentions water."""
    line = {**SEWAGE, "pn": "Odprowadzanie wód opadowych"}

    assert set(unit_prices(_invoice("2026-04-10", line))) == {"sewage"}


def test_a_line_named_neither_way_is_kept_apart() -> None:
    line = {**WATER, "pn": "Usługa dodatkowa"}

    assert set(unit_prices(_invoice("2026-04-10", line))) == {"other"}


def test_a_line_without_a_vat_rate_is_priced_net() -> None:
    line = {**WATER, "pt": "zw"}

    assert unit_prices(_invoice("2026-04-10", line)) == {"water": 6.0}


def test_a_correction_without_such_lines_does_not_hide_the_price() -> None:
    """A correction invoice can be the newest and bill no cubic metres at all."""
    invoices = [
        _invoice("2026-04-10", WATER, SEWAGE),
        _invoice("2026-04-20", *STANDING),
    ]

    prices, invoice = current_unit_prices(invoices)

    assert invoice["nw"] == "2026-04-10"
    assert prices == pytest.approx({"water": 6.48, "sewage": 10.8})


def test_the_newest_invoice_sets_the_price() -> None:
    invoices = [
        _invoice("2026-04-10", {**WATER, "pc": "6,00"}),
        _invoice("2026-05-10", {**WATER, "pc": "7,00"}),
    ]

    prices, _ = current_unit_prices(invoices)

    assert prices["water"] == pytest.approx(7.56)


def test_no_invoice_billing_by_the_cubic_metre_means_no_price() -> None:
    assert current_unit_prices([_invoice("2026-04-10", *STANDING)]) is None
    assert current_unit_prices([]) is None


@pytest.mark.parametrize(
    ("water", "sewage", "price"),
    [(100, 100, 17.28), (100, 0, 6.48), (50, 50, 8.64)],
)
def test_a_meter_pays_its_shares(water: float, sewage: float, price: float) -> None:
    prices = {"water": 6.48, "sewage": 10.8}

    assert meter_price(prices, water, sewage) == pytest.approx(price)


# --- the sensors -------------------------------------------------------------


def test_a_garden_meter_costs_the_water_alone() -> None:
    coordinator = _coordinator(
        [_invoice("2026-04-10", WATER, SEWAGE)],
        [{"numer_fabr": SERIAL, "procent_w": "100", "procent_k": "0"}],
    )
    sensor = IbokPriceSensor(coordinator, SERIAL)

    assert sensor.native_value == pytest.approx(6.48)
    assert sensor.native_unit_of_measurement == "PLN/m³"
    assert sensor.extra_state_attributes["sewage_share"] == 0
    assert sensor.extra_state_attributes["invoice_date"] == "2026-04-10"


def test_a_meter_missing_from_the_list_is_billed_in_full() -> None:
    coordinator = _coordinator([_invoice("2026-04-10", WATER, SEWAGE)])

    assert IbokPriceSensor(coordinator, SERIAL).native_value == pytest.approx(17.28)


def test_the_payment_deadline_is_the_newest_invoices() -> None:
    coordinator = _coordinator(
        [
            _invoice("2026-03-10", due="2026-03-24"),
            _invoice("2026-04-10", due="2026-04-24"),
        ]
    )

    assert IbokPaymentDueSensor(coordinator).native_value == date(2026, 4, 24)


def test_the_legalisation_date_is_read_per_meter() -> None:
    coordinator = _coordinator(
        meters=[
            {"numer_fabr": "87654321", "leg_do": "2029-01-31"},
            {"numer_fabr": SERIAL, "leg_do": "2031-12-31", "ew_prz": "2025-06-01"},
        ]
    )
    sensor = IbokLegalisationSensor(coordinator, SERIAL)

    assert sensor.native_value == date(2031, 12, 31)
    assert sensor.extra_state_attributes == {"installed": "2025-06-01"}
