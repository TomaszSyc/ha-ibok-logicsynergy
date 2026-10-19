"""The rule that tells a replaced meter's readings from the old one's."""

from custom_components.ibok.coordinator import readouts_for
from custom_components.ibok.replacement import (
    CurrentMeter,
    current_meter,
    unconfirmed_replacement,
)

REMOVAL = "Rozliczenie wg średniej - wymontowanie wodomierza"


def _row(day, value, kind="Odczyt z licznika"):
    return {"do": day, "sl": value, "typ": kind}


def test_a_history_without_a_swap_is_one_meter():
    rows = [_row("2026-03-01", "40"), _row("2026-04-01", "48")]
    assert current_meter(rows) == CurrentMeter(rows, None)


def test_a_drop_to_zero_starts_a_new_meter():
    rows = [
        _row("2026-05-04", "305"),
        _row("2026-06-02", "312", REMOVAL),
        _row("2026-06-03", "0"),
        _row("2026-07-01", "6"),
    ]
    meter = current_meter(rows)
    assert meter.replaced_on == "2026-06-03"
    assert meter.rows == rows[2:]


def test_a_small_correction_is_the_same_meter():
    rows = [_row("2026-03-01", "110"), _row("2026-04-01", "105")]
    assert current_meter(rows).replaced_on is None


def test_a_drop_of_exactly_a_tenth_is_a_correction():
    rows = [_row("2026-03-01", "100"), _row("2026-04-01", "90")]
    assert current_meter(rows).replaced_on is None


def test_the_removal_entry_marks_a_swap_the_drop_would_miss():
    # A garden meter: the new one's first reading is within a tenth of the old total.
    rows = [
        _row("2026-05-01", "14"),
        _row("2026-06-01", "15", REMOVAL),
        _row("2026-07-01", "14,2"),
    ]
    meter = current_meter(rows)
    assert meter.replaced_on == "2026-07-01"
    assert meter.rows == rows[2:]


def test_a_removal_entry_with_nothing_after_it_is_still_the_old_meter():
    rows = [_row("2026-05-01", "14"), _row("2026-06-01", "15", REMOVAL)]
    assert current_meter(rows).replaced_on is None


def test_the_marker_ignores_case_and_covers_demontaz():
    rows = [
        _row("2026-05-01", "14"),
        _row("2026-06-01", "15", "DEMONTAŻ WODOMIERZA"),
        _row("2026-07-01", "14,5"),
    ]
    assert current_meter(rows).replaced_on == "2026-07-01"


def test_a_row_without_a_value_after_the_removal_does_not_start_the_meter():
    rows = [
        _row("2026-05-01", "14"),
        _row("2026-06-01", "15", REMOVAL),
        _row("2026-06-15", None),
        _row("2026-07-01", "14,5"),
    ]
    assert current_meter(rows).replaced_on == "2026-07-01"


def test_the_same_day_keeps_the_portal_order():
    rows = [
        _row("2026-05-04", "305"),
        _row("2026-06-02", "312", REMOVAL),
        _row("2026-06-02", "0"),
    ]
    data = {"readouts": [{"numer_fabryczny": "12345678", "odczyty": rows}]}
    assert readouts_for(data, "12345678") == rows
    assert current_meter(rows).replaced_on == "2026-06-02"


def test_the_latest_of_two_swaps_wins():
    rows = [
        _row("2025-01-10", "480"),
        _row("2025-02-01", "0"),
        _row("2025-06-01", "12"),
        _row("2026-01-05", "0"),
        _row("2026-02-01", "2"),
    ]
    assert current_meter(rows).replaced_on == "2026-01-05"


SRC = {"source_entity_12345678": "sensor.woda"}


def test_only_a_meter_with_a_source_has_anything_to_confirm():
    assert unconfirmed_replacement({}, "12345678", "2026-06-03") is None
    assert unconfirmed_replacement(SRC, "12345678", None) is None
    assert unconfirmed_replacement(SRC, "12345678", "2026-06-03") == "2026-06-03"


def test_a_confirmed_swap_is_settled_and_a_later_one_is_not():
    done = {**SRC, "replacement_confirmed_12345678": "2026-06-03"}
    assert unconfirmed_replacement(done, "12345678", "2026-06-03") is None
    assert unconfirmed_replacement(done, "12345678", "2026-09-01") == "2026-09-01"
