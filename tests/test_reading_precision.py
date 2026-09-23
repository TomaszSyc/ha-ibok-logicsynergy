"""A reading sent at the wrong precision lands on somebody's bill."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.ibok.button import plan_press, press_confirms, truncate_to_dial


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
    ],
)
def test_truncate_to_dial(value: float, digits: int, expected: float) -> None:
    assert truncate_to_dial(value, digits) == pytest.approx(expected)


def test_press_confirms() -> None:
    """The second press sends only what the first press announced."""
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    deadline = now + timedelta(seconds=60)

    # Nothing announced yet.
    assert press_confirms(None, 48.0, now) is False
    # Announced, still in the window, same value.
    assert press_confirms((deadline, 48.0), 48.0, now) is True
    # Window elapsed.
    assert (
        press_confirms((deadline, 48.0), 48.0, deadline + timedelta(seconds=1))
        is False
    )
    # The meter moved between the two presses, so the value was never shown.
    assert press_confirms((deadline, 48.0), 49.0, now) is False


def test_plan_press_ignores_movement_below_the_dial() -> None:
    """A meter read in whole m3 does not re-ask because litres ticked over."""
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    deadline = now + timedelta(seconds=60)

    # First press: nothing is armed, so it announces what it would send.
    assert plan_press(48.002, 0, None, now) == ("announce", 48.0)

    # Second press after the overlay reported two more litres. The dial still
    # reads 48, so this confirms rather than starting over.
    assert plan_press(48.004, 0, (deadline, 48.0), now) == ("send", 48.0)

    # A whole cubic metre later is a different reading and has to be shown.
    assert plan_press(49.0, 0, (deadline, 48.0), now) == ("announce", 49.0)


def test_plan_press_honours_a_declared_fractional_part() -> None:
    """Where the portal reads litres, two litres are a different reading."""
    now = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
    deadline = now + timedelta(seconds=60)

    assert plan_press(48.004, 3, (deadline, 48.002), now) == ("announce", 48.004)
    assert plan_press(48.002, 3, (deadline, 48.002), now) == ("send", 48.002)
