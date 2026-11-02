"""Invoice history in long-term statistics: the month an invoice belongs to, and the import."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.ibok.const import CONF_BASE_URL
from custom_components.ibok.statistics import (
    async_import_invoice_statistics,
    billing_period,
    cumulative_rows,
    dominant_month,
    invoice_month,
    monthly_changes,
    statistic_id,
)

ENTRY = SimpleNamespace(
    unique_id="ibok.przyklad.pl:12345678",
    title="Woda dom",
    data={CONF_BASE_URL: "https://ibok.przyklad.pl"},
)
AMOUNT_ID = "ibok:ibok_przyklad_pl_78b1f8fe_invoiced_amount"
WATER_ID = "ibok:ibok_przyklad_pl_78b1f8fe_invoiced_water"
WARSAW = ZoneInfo("Europe/Warsaw")


def _chain(*readings: tuple[str, object]) -> list[dict]:
    return [{"do": day, "zu": str(use), "typ": "Odczyt"} for day, use in readings]


def _invoice(sale: str | None, water: float = 0, gross: object = "0", **extra) -> dict:
    lines = (
        [{"pn": "Dostawa wody", "pj": "m3", "pi": str(water), "pc": "6,00"}]
        if water
        else []
    )
    invoice = {"brutto": str(gross), "pw": [{"poz": lines}], **extra}
    if sale is not None:
        invoice["ns"] = sale
    return invoice


# Four periods of a household's year, invented: July, August, September.
READINGS = _chain(
    ("2026-06-01", 3), ("2026-07-01", 4), ("2026-08-01", 6), ("2026-09-01", 7)
)
JULY = _invoice("2026-08-02", water=6, gross="100,00", nd="FV/1/2026")
AUGUST = _invoice("2026-09-03", water=7, gross="150,00", nd="FV/2/2026")
SEPTEMBER = _invoice("2026-10-02", water=5, gross="200,00", nd="FV/3/2026")
READINGS_TO_OCTOBER = [*READINGS, *_chain(("2026-10-01", 5))]


def test_billing_period_matches_one_reading() -> None:
    chain = _chain(("2026-08-03", 6), ("2026-09-01", 7), ("2026-10-01", 5))
    invoice = _invoice("2026-10-02", water=5)

    assert billing_period(invoice, [chain]) == (date(2026, 9, 1), date(2026, 10, 1))


def test_billing_period_sums_consecutive_readings() -> None:
    chain = _chain(
        ("2025-10-20", 1),
        ("2025-11-20", 7),
        ("2025-12-20", 2),
        ("2026-01-05", 3),
        ("2026-02-03", 9),
    )
    invoice = _invoice("2026-01-08", water=12)

    assert billing_period(invoice, [chain]) == (date(2025, 10, 20), date(2026, 1, 5))


def test_billing_period_none_when_the_sum_never_matches() -> None:
    chain = _chain(("2026-08-03", 6), ("2026-09-01", 7), ("2026-10-01", 5))

    assert billing_period(_invoice("2026-10-02", water=4), [chain]) is None


def test_billing_period_none_without_water() -> None:
    chain = _chain(("2026-08-03", 6), ("2026-09-01", 7))

    assert billing_period(_invoice("2026-09-03", water=0), [chain]) is None


def test_billing_period_none_without_a_reading_before_the_first_summed() -> None:
    chain = _chain(("2026-09-01", 7), ("2026-10-01", 5))

    assert billing_period(_invoice("2026-10-02", water=12), [chain]) is None


def test_billing_period_none_when_a_use_cannot_be_read() -> None:
    chain = [
        {"do": "2026-08-03", "zu": "6"},
        {"do": "2026-09-01", "zu": ""},
        {"do": "2026-10-01", "zu": "5"},
    ]

    assert billing_period(_invoice("2026-10-02", water=5), [chain]) == (
        date(2026, 9, 1),
        date(2026, 10, 1),
    )
    assert billing_period(_invoice("2026-10-02", water=12), [chain]) is None


def test_billing_period_looks_back_at_most_six_readings() -> None:
    chain = _chain(*[(f"2026-0{m}-01", 1) for m in range(1, 9)])

    assert billing_period(_invoice("2026-08-02", water=7), [chain]) is None
    assert billing_period(_invoice("2026-08-02", water=6), [chain]) == (
        date(2026, 2, 1),
        date(2026, 8, 1),
    )


def test_billing_period_without_a_start_moves_on_to_the_next_meter() -> None:
    short = _chain(("2026-09-01", 7), ("2026-10-01", 5))
    house = _chain(("2026-08-03", 6), ("2026-09-01", 7), ("2026-10-01", 5))

    assert billing_period(_invoice("2026-10-02", water=12), [short, house]) == (
        date(2026, 8, 3),
        date(2026, 10, 1),
    )


def test_billing_period_tries_every_meter() -> None:
    house = _chain(("2026-08-03", 6), ("2026-09-01", 7), ("2026-10-01", 5))
    garden = _chain(("2026-08-03", 1), ("2026-09-01", 2), ("2026-10-01", 3))

    assert billing_period(_invoice("2026-10-02", water=3), [house, garden]) == (
        date(2026, 9, 1),
        date(2026, 10, 1),
    )


def test_billing_period_ends_at_the_invoice_issue_date_without_a_sale_date() -> None:
    chain = _chain(("2026-08-03", 6), ("2026-09-01", 7), ("2026-10-01", 5))
    invoice = _invoice(None, water=5, nw="2026-10-02")

    assert billing_period(invoice, [chain]) == (date(2026, 9, 1), date(2026, 10, 1))
    assert billing_period(_invoice(None, water=5), [chain]) is None


def test_dominant_month() -> None:
    assert dominant_month(date(2026, 9, 1), date(2026, 10, 1)) == date(2026, 9, 1)
    assert dominant_month(date(2026, 1, 6), date(2026, 3, 2)) == date(2026, 2, 1)
    # July and August tie: the later one wins.
    assert dominant_month(date(2025, 6, 30), date(2025, 8, 31)) == date(2025, 8, 1)


def test_invoice_month_falls_back_to_month_before_sale_date() -> None:
    assert invoice_month({"ns": "2026-10-01", "nw": "2026-10-01"}, []) == date(
        2026, 9, 1
    )
    assert invoice_month({"ns": "2026-01-08"}, []) == date(2025, 12, 1)
    assert invoice_month({"nw": "2026-03-03"}, []) == date(2026, 2, 1)
    assert invoice_month({}, []) is None


def test_monthly_changes_add_up_invoices_in_one_month_and_keep_corrections() -> None:
    correction = _invoice("2026-08-05", gross="-20,00")
    amounts, water = monthly_changes([JULY, correction, AUGUST], [READINGS])

    assert amounts == {date(2026, 7, 1): 80.0, date(2026, 8, 1): 150.0}
    assert water == {date(2026, 7, 1): 6.0, date(2026, 8, 1): 7.0}


def test_monthly_changes_skip_an_unreadable_amount_only_for_the_amount() -> None:
    broken = _invoice("2026-09-03", water=7, gross="n/a")

    amounts, water = monthly_changes([broken], [READINGS])

    assert amounts == {}
    assert water == {date(2026, 8, 1): 7.0}


def _month(year: int, month: int) -> datetime:
    return datetime(year, month, 1, tzinfo=UTC)


def test_cumulative_rows_start_from_last_stored_sum_before_window() -> None:
    stored = {_month(2026, 7): 300.0}
    changes = {_month(2026, 8): 80.0, _month(2026, 9): 150.0}

    assert cumulative_rows(changes, stored) == [
        (_month(2026, 8), 80.0, 380.0),
        (_month(2026, 9), 150.0, 530.0),
    ]


def test_cumulative_rows_start_from_zero_without_history() -> None:
    assert cumulative_rows({_month(2026, 8): 80.0}, {}) == [
        (_month(2026, 8), 80.0, 80.0)
    ]


def test_cumulative_rows_rewrite_stored_month_missing_from_invoices_with_zero() -> None:
    stored = {_month(2026, 8): 80.0, _month(2026, 9): 230.0, _month(2026, 10): 430.0}
    changes = {_month(2026, 8): 80.0, _month(2026, 10): 200.0}

    assert cumulative_rows(changes, stored) == [
        (_month(2026, 8), 80.0, 80.0),
        (_month(2026, 9), 0.0, 80.0),
        (_month(2026, 10), 200.0, 280.0),
    ]


def test_cumulative_rows_never_touch_rows_before_window() -> None:
    stored = {_month(2026, 6): 50.0, _month(2026, 7): 100.0}
    rows = cumulative_rows({_month(2026, 8): 10.0}, stored)

    assert [row[0] for row in rows] == [_month(2026, 8)]
    assert rows[0][2] == 110.0


def test_cumulative_rows_empty_without_changes() -> None:
    assert cumulative_rows({}, {_month(2026, 7): 100.0}) == []


def test_statistic_id() -> None:
    assert statistic_id(ENTRY, "invoiced_amount") == AMOUNT_ID
    assert statistic_id(ENTRY, "invoiced_water") == WATER_ID
    # The login must not leak into the recorder or dashboard YAML.
    assert "12345678" not in AMOUNT_ID
    assert "12345678" not in WATER_ID


def _data(*invoices: dict, readings=READINGS_TO_OCTOBER) -> dict:
    return {"invoices": list(invoices), "readouts": [{"odczyty": readings}]}


async def _rows(hass, statistic: str) -> list[dict]:
    await async_wait_recording_done(hass)
    found = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime.fromtimestamp(0, UTC),
        None,
        {statistic},
        "hour",
        None,
        {"sum", "state"},
    )
    return found.get(statistic, [])


def _sums(rows: list[dict]) -> list[float]:
    return [row["sum"] for row in rows]


async def _import(hass, data, failed=frozenset()) -> None:
    await async_import_invoice_statistics(hass, ENTRY, data, failed)
    await async_wait_recording_done(hass)


async def test_import_writes_two_statistics_with_cumulative_sums(
    recorder_mock, hass
) -> None:
    await hass.config.async_set_time_zone("Europe/Warsaw")

    await _import(hass, _data(JULY, AUGUST, SEPTEMBER))

    amount = await _rows(hass, AMOUNT_ID)
    water = await _rows(hass, WATER_ID)
    assert _sums(amount) == [100.0, 250.0, 450.0]
    assert [row["state"] for row in amount] == [100.0, 150.0, 200.0]
    assert _sums(water) == [6.0, 13.0, 18.0]


async def test_month_starts_at_local_midnight(recorder_mock, hass) -> None:
    await hass.config.async_set_time_zone("Europe/Warsaw")

    await _import(hass, _data(AUGUST))

    rows = await _rows(hass, AMOUNT_ID)
    assert rows[0]["start"] == datetime(2026, 8, 1, tzinfo=WARSAW).timestamp()


async def test_month_starts_on_the_hour_in_a_half_hour_zone(
    recorder_mock, hass
) -> None:
    await hass.config.async_set_time_zone("Asia/Kolkata")

    await _import(hass, _data(AUGUST))

    rows = await _rows(hass, AMOUNT_ID)
    assert rows
    for row in rows:
        start = datetime.fromtimestamp(row["start"], UTC)
        assert start.minute == 0
        local = dt_util.as_local(start)
        assert (local.month, local.day) == (8, 1)


async def test_import_twice_does_not_double(recorder_mock, hass) -> None:
    await hass.config.async_set_time_zone("Europe/Warsaw")
    data = _data(JULY, AUGUST, SEPTEMBER)

    await _import(hass, data)
    await _import(hass, data)

    assert _sums(await _rows(hass, AMOUNT_ID)) == [100.0, 250.0, 450.0]
    assert _sums(await _rows(hass, WATER_ID)) == [6.0, 13.0, 18.0]


async def test_oldest_invoice_dropping_off_keeps_history(recorder_mock, hass) -> None:
    await hass.config.async_set_time_zone("Europe/Warsaw")
    await _import(hass, _data(JULY, AUGUST, SEPTEMBER))

    await _import(hass, _data(AUGUST, SEPTEMBER))

    assert _sums(await _rows(hass, AMOUNT_ID)) == [100.0, 250.0, 450.0]


async def test_late_correction_rewrites_its_month(recorder_mock, hass) -> None:
    await hass.config.async_set_time_zone("Europe/Warsaw")
    await _import(hass, _data(JULY, AUGUST, SEPTEMBER))
    correction = _invoice("2026-08-05", gross="-20,00")

    await _import(hass, _data(JULY, correction, AUGUST, SEPTEMBER))

    amount = await _rows(hass, AMOUNT_ID)
    assert [row["state"] for row in amount] == [80.0, 150.0, 200.0]
    assert _sums(amount) == [80.0, 230.0, 430.0]


async def test_nothing_written_when_invoices_or_readouts_failed(
    recorder_mock, hass
) -> None:
    await hass.config.async_set_time_zone("Europe/Warsaw")

    await _import(hass, _data(AUGUST), failed=frozenset({"invoices"}))
    await _import(hass, _data(AUGUST), failed=frozenset({"readouts"}))
    await _import(hass, _data())

    assert await _rows(hass, AMOUNT_ID) == []
    assert await _rows(hass, WATER_ID) == []


async def test_nothing_written_without_a_recorder(hass, caplog) -> None:
    caplog.set_level(logging.DEBUG)

    await async_import_invoice_statistics(hass, ENTRY, _data(AUGUST), frozenset())

    assert "recorder" in caplog.text


async def test_statistics_error_does_not_fail_refresh(
    hass, enable_custom_integrations, portal, setup, monkeypatch, caplog
) -> None:
    async def _boom(hass, entry, data, failed) -> None:
        raise RuntimeError("recorder is gone")

    monkeypatch.setattr("custom_components.ibok.async_import_invoice_statistics", _boom)
    entry = await setup(options={"invoice_statistics": True})

    assert entry.runtime_data.data is not None
    assert entry.runtime_data.last_update_success
    assert "recorder is gone" in caplog.text


async def test_option_on_hands_each_refresh_to_the_import(
    hass, enable_custom_integrations, portal, setup, monkeypatch
) -> None:
    calls: list[tuple] = []

    async def _spy(hass, entry, data, failed) -> None:
        calls.append((entry, data, failed))

    monkeypatch.setattr("custom_components.ibok.async_import_invoice_statistics", _spy)

    entry = await setup(options={"invoice_statistics": True})

    assert len(calls) == 1
    assert calls[0][0] is entry
    assert calls[0][2] == frozenset()


async def test_option_off_writes_nothing(
    recorder_mock, hass, enable_custom_integrations, portal, setup
) -> None:
    await hass.config.async_set_time_zone("Europe/Warsaw")
    portal.readouts = [{"numer_fabryczny": "12345678", "odczyty": READINGS}]
    portal.invoices = [AUGUST]

    await setup(options={})

    assert await _rows(hass, AMOUNT_ID) == []
    assert await _rows(hass, WATER_ID) == []
