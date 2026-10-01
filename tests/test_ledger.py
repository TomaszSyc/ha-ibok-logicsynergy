"""SubmissionLedger: refuse a repeat reading, remember what is still unanswered.

Pure logic, no Home Assistant -- the ledger under test is built with
``hass=None`` throughout. Turning an unknown outcome into a Repairs issue is
not exercised here; only the in-memory block it sets is asserted.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from fake_portal import METER
from homeassistant.exceptions import ServiceValidationError

from custom_components.ibok.ledger import SubmissionLedger

TODAY = date(2026, 4, 20)
ROW = {**METER, "do": "2026-04-06"}
AT = datetime(2026, 4, 20, 12, 0, tzinfo=UTC)


def _key(fn, *args) -> str | None:
    """The translation key of the ServiceValidationError fn(*args) raises, if any."""
    try:
        fn(*args)
    except ServiceValidationError as err:
        return err.translation_key
    return None


def test_a_value_sent_today_is_refused() -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_sent(10001, "12345678", 48.0, TODAY, TODAY)

    assert _key(ledger.check, ROW, 48.0, TODAY, TODAY) == "reading_already_sent"


def test_another_value_the_same_day_passes() -> None:
    """A correction: a different reading the same day is not a repeat."""
    ledger = SubmissionLedger(None, "e1")
    ledger.record_sent(10001, "12345678", 48.0, TODAY, TODAY)

    ledger.check(ROW, 49.0, TODAY, TODAY)  # no raise


def test_yesterdays_record_is_forgotten() -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_sent(10001, "12345678", 48.0, TODAY, TODAY - timedelta(days=1))

    ledger.check(ROW, 48.0, TODAY, TODAY)  # no raise


def test_the_portal_already_has_it() -> None:
    """The portal writes a decimal comma; the value to compare is a plain float."""
    ledger = SubmissionLedger(None, "e1")
    row = {**METER, "do": "2026-04-20", "sl": "48,000"}

    assert _key(ledger.check, row, 48.0, TODAY, TODAY) == "reading_already_recorded"


def test_nbsp_in_portal_value() -> None:
    """A non-breaking space as the thousands separator in the portal's value."""
    ledger = SubmissionLedger(None, "e1")
    row = {**METER, "do": "2026-04-20", "sl": "1\xa0048"}

    assert _key(ledger.check, row, 1048.0, TODAY, TODAY) == "reading_already_recorded"


def test_a_pending_unknown_blocks_everything() -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT)

    assert _key(ledger.check, ROW, 50.0, TODAY, TODAY) == "submit_outcome_pending"


def test_pending_takes_priority_over_the_portal_row() -> None:
    """Order matters: submit_outcome_pending fires even when the row itself matches."""
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT)
    row = {**METER, "do": "2026-04-20", "sl": "48"}

    assert _key(ledger.check, row, 48.0, TODAY, TODAY) == "submit_outcome_pending"


def test_a_row_with_no_usable_meter_id_is_never_pending() -> None:
    ledger = SubmissionLedger(None, "e1")

    ledger.check({**ROW, "id_wodom": "abc"}, 48.0, TODAY, TODAY)  # no raise


def test_reconcile_confirms_from_readouts() -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT)
    data = {
        "readouts": [
            {
                "numer_fabryczny": "12345678",
                "odczyty": [{"do": "2026-04-20", "sl": "48"}],
            }
        ],
        "notify": [],
    }

    ledger.reconcile(data, TODAY)

    assert ledger.pending(10001) is None
    assert _key(ledger.check, ROW, 48.0, TODAY, TODAY) == "reading_already_sent"


def test_reconcile_confirms_from_notify() -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT)
    data = {"readouts": [], "notify": [{**METER, "do": "2026-04-20", "sl": "48"}]}

    ledger.reconcile(data, TODAY)

    assert ledger.pending(10001) is None
    assert _key(ledger.check, ROW, 48.0, TODAY, TODAY) == "reading_already_sent"


def test_reconcile_leaves_an_unmatched_pending_attempt_alone() -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT)
    data = {"readouts": [], "notify": [{**METER, "do": "2026-04-19", "sl": "47"}]}

    ledger.reconcile(data, TODAY)

    assert ledger.pending(10001) is not None


def test_clear_unknown_lifts_the_block() -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT)

    ledger.clear_unknown(10001)

    assert ledger.pending(10001) is None
    ledger.check(ROW, 50.0, TODAY, TODAY)  # no raise


async def test_a_second_lock_is_refused_at_once() -> None:
    ledger = SubmissionLedger(None, "e1")

    async with ledger.lock(10001):
        with pytest.raises(ServiceValidationError) as info:
            async with ledger.lock(10001):
                pass
        assert info.value.translation_key == "submit_in_progress"


async def test_the_lock_is_free_again_after_release() -> None:
    ledger = SubmissionLedger(None, "e1")

    async with ledger.lock(10001):
        pass

    async with ledger.lock(10001):
        pass  # no raise: the first lock released cleanly


async def test_locks_are_per_meter() -> None:
    ledger = SubmissionLedger(None, "e1")

    async with ledger.lock(10001), ledger.lock(10002):
        pass  # no raise: a different meter is never busy


# --- the portal's own record of a submission ---------------------------------
#
# The reading form keeps a meter's latest submission apart from the operator's
# reading: ``ido`` its date, ``isl`` its value, ``ist`` its status (1 waiting,
# 2 in progress, 3 refused, 4 accepted). ``do`` and ``sl`` change only once the
# operator approves it.


def _held(ist, ido: str = "2026-04-20", isl: str = "48") -> dict:
    return {**ROW, "ido": ido, "isl": isl, "ist": ist}


@pytest.mark.parametrize(
    "ist", [1, 2, 4, "2", " 4 "], ids=["1", "2", "4", "str", "padded"]
)
def test_a_submission_the_portal_holds_is_refused(ist) -> None:
    ledger = SubmissionLedger(None, "e1")

    assert _key(ledger.check, _held(ist), 48.0, TODAY, TODAY) == (
        "reading_already_recorded"
    )


def test_a_held_submission_is_compared_as_a_number() -> None:
    ledger = SubmissionLedger(None, "e1")

    assert _key(ledger.check, _held(1, isl="48,000"), 48.0, TODAY, TODAY) == (
        "reading_already_recorded"
    )


@pytest.mark.parametrize(
    "row",
    [
        _held(3),
        _held(4, ido="2026-04-19"),
        _held(4, isl="47"),
        _held(3, isl="47"),
        _held(None),
        _held("brak"),
    ],
    ids=[
        "refused",
        "another-day",
        "another-value",
        "refused-another-value",
        "no-status",
        "bad-status",
    ],
)
def test_a_submission_the_portal_does_not_hold_passes(row) -> None:
    """A refused one may be sent again; another day or value is another reading."""
    ledger = SubmissionLedger(None, "e1")

    ledger.check(row, 48.0, TODAY, TODAY)  # no raise


@pytest.mark.parametrize("ist", [1, 2, "2"], ids=["waiting", "in-progress", "str"])
@pytest.mark.parametrize(
    ("ido", "isl"),
    [("2026-04-20", "47"), ("2026-04-19", "48")],
    ids=["another-value", "another-day"],
)
def test_a_reading_while_another_waits_is_refused(ist, ido, isl) -> None:
    """The portal's own form offers only editing it, or nothing, until it is decided."""
    ledger = SubmissionLedger(None, "e1")
    row = _held(ist, ido=ido, isl=isl)

    with pytest.raises(ServiceValidationError) as info:
        ledger.check(row, 48.0, TODAY, TODAY)

    assert info.value.translation_key == "submission_waiting"
    assert info.value.translation_placeholders == {
        "serial": "12345678",
        "reading": isl,
        "date": ido,
    }


@pytest.mark.parametrize("ist", [1, 2, 4])
def test_reconcile_confirms_from_the_submission_record(ist) -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT)

    ledger.reconcile({"readouts": [], "notify": [_held(ist)]}, TODAY)

    assert ledger.pending(10001) is None
    assert _key(ledger.check, ROW, 48.0, TODAY, TODAY) == "reading_already_sent"


def test_reconcile_lifts_the_block_of_a_refused_submission() -> None:
    """Refused, it is not on the bill: no block, and nothing counts as sent."""
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT)

    ledger.reconcile({"readouts": [], "notify": [{**_held(3), "iid": "8002"}]}, TODAY)

    assert ledger.pending(10001) is None
    ledger.check(ROW, 48.0, TODAY, TODAY)  # no raise


@pytest.mark.parametrize("seen", ["8001", None], ids=["same-id", "no-ids"])
def test_reconcile_keeps_the_block_over_the_refusal_seen_before_sending(seen) -> None:
    """The form still showing that refusal says nothing about what was sent since."""
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT, seen=seen)
    row = {**_held(3), "iid": seen} if seen else _held(3)

    ledger.reconcile({"readouts": [], "notify": [row]}, TODAY)

    assert ledger.pending(10001) is not None


def test_reconcile_confirms_a_held_submission_whatever_was_seen() -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT, seen="8001")

    ledger.reconcile({"readouts": [], "notify": [{**_held(1), "iid": "8001"}]}, TODAY)

    assert ledger.pending(10001) is None


def test_reconcile_ignores_a_submission_of_another_reading() -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT)

    ledger.reconcile({"readouts": [], "notify": [_held(3, isl="47")]}, TODAY)

    assert ledger.pending(10001) is not None


@pytest.mark.parametrize("stamp", ["2026-04-20 00:00:00", "2026-04-20T12:00:00"])
def test_a_portal_date_with_a_time_is_read_by_its_day(stamp) -> None:
    ledger = SubmissionLedger(None, "e1")

    assert _key(ledger.check, _held(4, ido=stamp), 48.0, TODAY, TODAY) == (
        "reading_already_recorded"
    )
    row = {**METER, "do": stamp, "sl": "48"}
    assert _key(ledger.check, row, 48.0, TODAY, TODAY) == "reading_already_recorded"


def test_reconcile_reads_a_submission_date_with_a_time() -> None:
    ledger = SubmissionLedger(None, "e1")
    ledger.record_unknown(10001, "12345678", 48.0, TODAY, TODAY, AT)

    ledger.reconcile(
        {"readouts": [], "notify": [_held(1, ido="2026-04-20 00:00:00")]}, TODAY
    )

    assert ledger.pending(10001) is None
