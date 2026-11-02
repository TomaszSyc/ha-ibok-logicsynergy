"""The price of a cubic metre, the payment deadline and the legalisation date."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from custom_components.ibok.sensor import (
    IbokLegalisationSensor,
    IbokPaymentDueSensor,
    IbokPriceSensor,
    IbokStandingChargeSensor,
    current_standing_charges,
    current_unit_prices,
    invoiced_water,
    matching_settlement,
    meter_price,
    standing_charges,
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


def _line(name: str, unit: str, qty, net_price: str, vat: str) -> dict:
    return {
        "pn": name,
        "pj": unit,
        "pi": str(qty).replace(".", ","),
        "pc": net_price,
        "pt": vat,
    }


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


def test_an_unreadable_vat_makes_the_price_unknown() -> None:
    line = {**WATER, "pt": "?"}

    assert unit_prices(_invoice("2026-04-10", line)) is None


def test_distinct_lines_of_one_kind_add_up() -> None:
    sewage = {"pn": "Ścieki", "pj": "m3", "pc": "10,00", "pt": "8%"}
    rainfall = {
        "pn": "Wody opadowe odprowadzanie",
        "pj": "m3",
        "pc": "2,00",
        "pt": "8%",
    }

    prices = unit_prices(_invoice("2026-04-10", sewage, rainfall))

    assert prices == pytest.approx({"sewage": 12.96})


def test_the_same_line_twice_takes_the_last() -> None:
    first = {**WATER, "pc": "5,00"}
    second = {**WATER, "pc": "6,00"}

    prices = unit_prices(_invoice("2026-04-10", first, second))

    assert prices == pytest.approx({"water": 6.48})


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


def test_an_unreadable_vat_on_the_newest_invoice_does_not_fall_back() -> None:
    """An older tariff is not the current price just because it is readable."""
    invoices = [
        _invoice("2026-04-10", {**WATER, "pc": "5,00", "pt": "8"}),
        _invoice("2026-05-10", {**WATER, "pc": "6,00", "pt": "np."}),
    ]

    assert current_unit_prices(invoices) is None


def test_a_correction_after_an_unreadable_invoice_does_not_revive_the_older_one() -> (
    None
):
    invoices = [
        _invoice("2026-04-10", {**WATER, "pc": "5,00"}),
        _invoice("2026-05-10", {**WATER, "pt": "np."}),
        _invoice("2026-05-20", *STANDING),
    ]

    assert current_unit_prices(invoices) is None


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


def test_shares_assumed_full_when_the_list_is_empty() -> None:
    coordinator = _coordinator([_invoice("2026-04-10", WATER, SEWAGE)])
    sensor = IbokPriceSensor(coordinator, SERIAL)

    assert sensor._shares() == (100.0, 100.0, "assumed_full")
    assert sensor.extra_state_attributes["shares"] == "assumed_full"


def test_price_unknown_when_the_meter_is_missing_from_a_list(caplog) -> None:
    """A non-empty meter list that skips this serial means its shares are unknown."""
    coordinator = _coordinator(
        [_invoice("2026-04-10", WATER, SEWAGE)],
        [{"numer_fabr": "87654321", "procent_w": "100", "procent_k": "100"}],
    )
    sensor = IbokPriceSensor(coordinator, SERIAL)

    assert sensor._shares() is None
    assert sensor.native_value is None
    assert "shares" not in sensor.extra_state_attributes

    with caplog.at_level("WARNING"):
        _ = sensor.native_value
        _ = sensor.native_value

    warnings = [r for r in caplog.records if SERIAL in r.getMessage()]
    assert len(warnings) == 1


def test_price_unknown_and_warned_once_when_the_newest_vat_is_unreadable(
    caplog,
) -> None:
    coordinator = _coordinator(
        [
            _invoice("2026-04-10", {**WATER, "pc": "5,00", "pt": "8"}),
            _invoice("2026-05-10", {**WATER, "pc": "6,00", "pt": "np."}),
        ]
    )
    sensor = IbokPriceSensor(coordinator, SERIAL)

    with caplog.at_level("WARNING"):
        assert sensor.native_value is None
        assert "invoice_date" not in sensor.extra_state_attributes
        # The next refresh brings the same invoices back.
        coordinator.data = {
            **coordinator.data,
            "invoices": list(coordinator.data["invoices"]),
        }
        assert sensor.native_value is None
        assert "water" not in sensor.extra_state_attributes

    warnings = [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and SERIAL in r.getMessage()
    ]
    assert len(warnings) == 1


def test_the_payment_deadline_is_the_newest_invoices() -> None:
    coordinator = _coordinator(
        [
            _invoice("2026-03-10", due="2026-03-24"),
            _invoice("2026-04-10", due="2026-04-24"),
        ]
    )

    assert IbokPaymentDueSensor(coordinator).native_value == date(2026, 4, 24)


def test_payment_due_skips_a_correction() -> None:
    """A correction's gross amount can be zero and carries no due date to trust."""
    invoices = [
        _invoice("2026-03-10", due="2026-03-24"),
        {**_invoice("2026-04-10", due="2026-04-24"), "brutto": "0,00"},
    ]

    assert IbokPaymentDueSensor(_coordinator(invoices)).native_value == date(
        2026, 3, 24
    )


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


# --- the standing charge -------------------------------------------------------


def test_standing_charge_sums_distinct_names_gross() -> None:
    inv = _invoice(
        "2026-03-02",
        _line("Opłata abonamentowa woda", "mies", 1, "4,00", "8"),
        _line("Opłata abonamentowa ścieki", "mies", 1, "5,00", "8"),
        _line("Sprzedaż wody", "m3", 7, "5,00", "8"),
    )

    assert standing_charges(inv) == pytest.approx({"water": 4.32, "sewage": 5.40})


def test_standing_charge_multi_period_invoice_is_not_tripled() -> None:
    """The same names repeat per period; the last set is the tariff in force."""
    name = "Opłata abonamentowa woda"
    inv = _invoice(
        "2026-03-02",
        _line(name, "mies", 0.5, "3,00", "8"),
        _line(name, "mies", 1, "3,50", "8"),
        _line(name, "mies", 1.5, "4,00", "8"),
    )

    assert standing_charges(inv)["water"] == pytest.approx(4.00 * 1.08)


def test_standing_charge_unreadable_vat_is_unknown() -> None:
    inv = _invoice(
        "2026-03-02", _line("Opłata abonamentowa woda", "mies", 1, "4,00", "??")
    )

    assert standing_charges(inv) is None


@pytest.mark.parametrize(
    "unit", ["mies", "mies.", "miesiąc", "miesiac", "m-c", " M-C "]
)
def test_the_monthly_unit_can_be_written_several_ways(unit: str) -> None:
    inv = _invoice(
        "2026-03-02", _line("Opłata abonamentowa woda", unit, 1, "4,00", "8")
    )

    assert standing_charges(inv) == pytest.approx({"water": 4.32})


def test_current_standing_charges_skips_invoice_without_monthly_lines() -> None:
    invoices = [
        _invoice(
            "2026-03-02", _line("Opłata abonamentowa woda", "mies", 1, "4,00", "8")
        ),
        _invoice("2026-04-02", _line("Korekta wody", "m3", 1, "5,00", "8")),
    ]

    charges, invoice = current_standing_charges(invoices)

    assert invoice["nw"] == "2026-03-02"
    assert charges == pytest.approx({"water": 4.32})


def test_current_standing_charges_does_not_fall_back_past_an_unreadable_vat() -> None:
    invoices = [
        _invoice(
            "2026-03-02", _line("Opłata abonamentowa woda", "mies", 1, "4,00", "8")
        ),
        _invoice(
            "2026-04-02", _line("Opłata abonamentowa woda", "mies", 1, "4,00", "np.")
        ),
    ]

    assert current_standing_charges(invoices) is None


def test_unit_prices_ignore_monthly_lines() -> None:
    inv = _invoice(
        "2026-03-02",
        _line("Opłata abonamentowa woda", "mies", 1, "4,00", "8"),
        _line("Sprzedaż wody", "m3", 7, "5,00", "8"),
    )

    assert unit_prices(inv) == pytest.approx({"water": 5.40})


def test_invoiced_water_sums_water_lines_only() -> None:
    inv = _invoice(
        "2026-03-02",
        _line("Sprzedaż wody", "m3", 5, "5,00", "8"),
        _line("Sprzedaż wody", "m3", 3.5, "5,00", "8"),
        _line("Odprowadzenie ścieków", "m3", 8.5, "6,00", "8"),
        _line("Opłata abonamentowa woda", "mies", 1, "4,00", "8"),
    )

    assert invoiced_water(inv) == pytest.approx(8.5)


def test_invoiced_water_skips_an_unreadable_quantity_and_defaults_to_zero() -> None:
    bad = _line("Sprzedaż wody", "m3", "?", "5,00", "8")

    assert invoiced_water(_invoice("2026-03-02", bad)) == 0.0
    assert invoiced_water(_invoice("2026-03-02")) == 0.0


def test_matching_settlement_by_id_then_number() -> None:
    accountancy = {
        "pozycje": [
            {"id": 501, "nr": "FV/5/2026", "ro": "12,30"},
            {"id": 502, "nr": "FV/6/2026", "ro": "0,00"},
        ]
    }

    assert (
        matching_settlement(accountancy, {"id": "502", "nd": "FV/5/2026"})["id"] == 502
    )
    assert (
        matching_settlement(accountancy, {"id": "999", "nd": "FV/5/2026"})["id"] == 501
    )
    assert matching_settlement(accountancy, {"nd": "FV/6/2026"})["id"] == 502
    assert matching_settlement(accountancy, {"id": "999", "nd": "FV/9/2026"}) is None
    assert matching_settlement(None, {"id": "501"}) is None


def test_the_standing_charge_sensor_state_and_parts() -> None:
    coordinator = _coordinator(
        [
            _invoice(
                "2026-03-02",
                _line("Opłata abonamentowa woda", "mies", 1, "4,00", "8"),
                _line("Opłata abonamentowa ścieki", "mies", 1, "5,00", "8"),
            )
        ]
    )
    sensor = IbokStandingChargeSensor(coordinator)

    assert sensor.native_value == pytest.approx(9.72)
    assert sensor.native_unit_of_measurement == "PLN"
    assert sensor.state_class is None
    assert sensor.extra_state_attributes == {
        "water": 4.32,
        "sewage": 5.40,
        "other": None,
    }


def test_the_standing_charge_is_unknown_without_monthly_lines() -> None:
    sensor = IbokStandingChargeSensor(_coordinator([_invoice("2026-03-02", WATER)]))

    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {
        "water": None,
        "sewage": None,
        "other": None,
    }
