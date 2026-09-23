"""A reading sent at the wrong precision lands on somebody's bill."""

import pytest

from custom_components.ibok.button import truncate_to_dial


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
