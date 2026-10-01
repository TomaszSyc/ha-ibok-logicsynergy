"""What a press of the button sends: the precision, the confirmation, the source."""

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import State
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.ibok.button import (
    MIN_CONFIRM_GAP,
    age_text,
    plan_press,
    press_confirms,
    source_reading,
)
from custom_components.ibok.submit import truncate_to_dial

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
ME = "user-1"


def _armed(reading: float, *, at: datetime = NOW, by: str | None = ME):
    """What the first press leaves behind: announced at `at`, by `by`."""
    return (at, at + timedelta(seconds=60), reading, by)


LATER = NOW + MIN_CONFIRM_GAP


@pytest.mark.parametrize(
    ("value", "digits", "expected"),
    [
        # Whole cubic metres: what the dial shows, not the nearest value.
        (48.394, 0, 48.0),
        (48.6, 0, 48.0),
        (48.999, 0, 48.0),
        (48.0, 0, 48.0),
        (0.4, 0, 0.0),
        # Meters the portal declares a fractional part for.
        (48.394, 1, 48.3),
        (48.394, 3, 48.394),
        (48.9999, 2, 48.99),
        # Values binary floating point cannot hold exactly: multiplying by a
        # power of ten and flooring sends these one unit below the dial.
        (0.29, 2, 0.29),
        (0.57, 2, 0.57),
        (1.001, 3, 1.001),
        # Five digits: the naive floor gives 10000.04, within pytest.approx's
        # default tolerance -- which is why the comparison below is exact.
        (10000.05, 2, 10000.05),
    ],
)
def test_truncate_to_dial(value: float, digits: int, expected: float) -> None:
    # Exact: a quantised Decimal converted to float is the float of that value.
    assert truncate_to_dial(value, digits) == expected


# --- the second press -------------------------------------------------------


def test_nothing_announced_does_not_confirm() -> None:
    assert press_confirms(None, 48.0, LATER, ME) is False


def test_the_same_value_confirms_after_a_moment() -> None:
    assert press_confirms(_armed(48.0), 48.0, LATER, ME) is True


def test_a_double_click_does_not_confirm() -> None:
    """Both clicks arrive within milliseconds; nobody read the announcement."""
    assert (
        press_confirms(_armed(48.0), 48.0, NOW + timedelta(milliseconds=150), ME)
        is False
    )


def test_someone_else_cannot_confirm() -> None:
    """Whoever saw the announcement is the one who confirms it."""
    assert press_confirms(_armed(48.0), 48.0, LATER, "user-2") is False


def test_the_window_closes() -> None:
    assert press_confirms(_armed(48.0), 48.0, NOW + timedelta(seconds=61), ME) is False


def test_a_moved_meter_is_announced_again() -> None:
    assert press_confirms(_armed(48.0), 49.0, LATER, ME) is False


def test_plan_press_ignores_movement_below_the_dial() -> None:
    """A meter read in whole m3 does not re-ask because litres ticked over."""
    assert plan_press(48.002, 0, None, NOW, ME) == ("announce", 48.0)
    assert plan_press(48.004, 0, _armed(48.0), LATER, ME) == ("send", 48.0)
    assert plan_press(49.0, 0, _armed(48.0), LATER, ME) == ("announce", 49.0)


def test_plan_press_honours_a_declared_fractional_part() -> None:
    """Where the portal reads litres, two litres are a different reading."""
    assert plan_press(48.004, 3, _armed(48.002), LATER, ME) == ("announce", 48.004)
    assert plan_press(48.002, 3, _armed(48.002), LATER, ME) == ("send", 48.002)


# --- the source entity -------------------------------------------------------


def _state(
    value: str,
    unit: str | None = "m³",
    reported: datetime = NOW,
    **extra_attributes: str,
) -> State:
    attributes = {"unit_of_measurement": unit} if unit else {}
    attributes.update(extra_attributes)
    return State(
        "sensor.woda",
        value,
        attributes,
        last_changed=reported,
        last_reported=reported,
        last_updated=reported,
    )


def test_a_source_in_litres_is_converted() -> None:
    """Sent as-is, 48394 L would be filed as 48394 m3."""
    value, reported = source_reading(_state("48394", "L"), NOW, None)
    assert value == pytest.approx(48.394)
    assert reported == NOW


@pytest.mark.parametrize("unit", [None, "kWh"])
def test_a_source_without_a_volume_unit_is_refused(unit) -> None:
    with pytest.raises(ServiceValidationError) as info:
        source_reading(_state("48.4", unit), NOW, None)
    assert info.value.translation_key == "source_unit_not_volume"


def test_a_source_that_went_silent_is_refused() -> None:
    """A dead radio overlay keeps showing its last value."""
    stale = _state("48.4", reported=NOW - timedelta(days=3))

    with pytest.raises(ServiceValidationError) as info:
        source_reading(stale, NOW, None)
    assert info.value.translation_key == "source_stale"


@pytest.mark.parametrize("value", ["unavailable", "unknown", "abc"])
def test_a_source_without_a_number_is_refused(value) -> None:
    with pytest.raises(ServiceValidationError):
        source_reading(_state(value), NOW, None)


def test_inf_is_refused() -> None:
    with pytest.raises(ServiceValidationError) as info:
        source_reading(_state("inf"), NOW, None)
    assert info.value.translation_key == "source_not_a_number"


def test_an_attribute_timestamp_wins() -> None:
    """An overlay's own read time beats Home Assistant's last_reported."""
    stale_reading = NOW - timedelta(hours=30)
    state = _state("48.4", reported=NOW, timestamp=stale_reading.isoformat())

    with pytest.raises(ServiceValidationError) as info:
        source_reading(state, NOW, None)
    assert info.value.translation_key == "source_stale"


def test_an_offsetless_attribute_timestamp_is_read_as_utc() -> None:
    """A bare ISO string with no offset means UTC, never the local zone.

    Warsaw is UTC+2 in September: reading the same string as local time
    would put the report two hours earlier in UTC, tipping it from 22.5 h
    old (accepted) to 24.5 h old (refused as stale). The two interpretations
    must not agree by accident, so the default time zone is set away from
    UTC for the call.
    """
    naive = "2026-09-22T13:30:00"
    state = _state("48.4", reported=NOW, timestamp=naive)

    original_tz = dt_util.get_default_time_zone()
    dt_util.set_default_time_zone(dt_util.get_time_zone("Europe/Warsaw"))
    try:
        value, reported = source_reading(state, NOW, None)
    finally:
        dt_util.set_default_time_zone(original_tz)

    assert value == pytest.approx(48.4)
    assert reported == datetime(2026, 9, 22, 13, 30, tzinfo=UTC)


def test_silent_since_restart_is_refused() -> None:
    """last_reported this close to the restart is what a restored state looks like."""
    started = NOW - timedelta(minutes=20)
    state = _state("48.4", reported=started + timedelta(minutes=2))

    with pytest.raises(ServiceValidationError) as info:
        source_reading(state, NOW, started)
    assert info.value.translation_key == "source_silent_since_restart"


def test_a_later_report_passes() -> None:
    """A report well after the restart proves the reader is alive."""
    started = NOW - timedelta(minutes=20)
    state = _state("48.4", reported=NOW - timedelta(minutes=1))

    value, reported = source_reading(state, NOW, started)
    assert value == pytest.approx(48.4)
    assert reported == NOW - timedelta(minutes=1)


def test_right_after_a_restart_passes() -> None:
    """Inside the grace period, a state that looks restored is not yet refused."""
    started = NOW - timedelta(minutes=10)
    state = _state("48.4", reported=started + timedelta(minutes=1))

    value, _reported = source_reading(state, NOW, started)
    assert value == pytest.approx(48.4)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(45, "45 s"), (200, "3 min"), (7300, "2 h")],
)
def test_age_text(seconds: int, text: str) -> None:
    assert age_text(timedelta(seconds=seconds)) == text


def test_an_attribute_timestamp_far_in_the_future_is_not_trusted() -> None:
    """A clock gone wrong on the reader must not make a silent source look fresh."""
    ahead = NOW + timedelta(hours=2)
    state = _state(
        "48.4", reported=NOW - timedelta(hours=30), timestamp=ahead.isoformat()
    )

    with pytest.raises(ServiceValidationError) as info:
        source_reading(state, NOW, None)
    assert info.value.translation_key == "source_stale"


def test_an_attribute_timestamp_slightly_ahead_is_kept() -> None:
    """A reader whose clock runs a minute or two fast is still read by its own time."""
    ahead = NOW + timedelta(minutes=2)
    state = _state(
        "48.4", reported=NOW - timedelta(hours=1), timestamp=ahead.isoformat()
    )

    _value, reported = source_reading(state, NOW, None)
    assert reported == ahead


def test_an_age_below_zero_reads_as_zero() -> None:
    assert age_text(timedelta(minutes=-2)) == "0 s"
