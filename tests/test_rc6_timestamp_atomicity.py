"""Invalid source clocks reject evidence without aborting or partial candles."""
from dataclasses import replace
from datetime import timedelta
import sqlite3

import pytest

from bl_candle_engine import SampleMaterializer
from bs_instrument_contracts import register_exact_time_sql, utc_microseconds
from tests.test_rc6_shadow_runtime_wiring import make_store, quote, OPEN


OVERFLOW_CLOCKS = (
    "0001-01-01T00:00:00+14:00",
    "9999-12-31T23:59:59-12:00",
)


@pytest.mark.parametrize("raw", OVERFLOW_CLOCKS)
def test_unrepresentable_utc_is_a_typed_validation_error(raw):
    with pytest.raises(ValueError, match="UTC"):
        utc_microseconds(raw)


@pytest.mark.parametrize("raw", OVERFLOW_CLOCKS)
@pytest.mark.parametrize("field", ("book_at", "trade_at", "observed_at", "as_of"))
def test_quote_rejects_unrepresentable_source_or_decision_clock(raw, field):
    asset = dict(ticker="S0", instrument_type="ACCIONES", settlement="A-24HS")
    original = quote(asset, OPEN)
    sample = original if field == "as_of" else replace(original, **{field: raw})
    assert sample.time_error(raw if field == "as_of" else OPEN,
                             require_trade=True) == "INVALID_OR_NAIVE_TIMESTAMP"


@pytest.mark.parametrize("raw", (
    "0001-01-01T00:00:00-03:00", "0001-01-01T00:00:00+00:00",
    *OVERFLOW_CLOCKS,
))
def test_rejected_clock_leaves_no_partial_candle_and_cursor_recovers(tmp_path, raw):
    store, assets = make_store(tmp_path, count=1)
    worker = SampleMaterializer(store)
    store.add_quote(replace(quote(assets[0], OPEN), trade_at=raw))
    rejected = worker.tick(OPEN)
    assert (rejected["accepted"], rejected["rejected"], rejected["cursor"]) == (0, 1, 1)
    with store.connect() as connection:
        for table in ("candle_series", "candle_samples", "candle_dirty", "candle_versions"):
            assert connection.execute("SELECT count(*) FROM " + table).fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM candle_rejections").fetchone()[0] == 1
    store.add_quote(quote(assets[0], OPEN))
    recovered = worker.tick(OPEN + timedelta(minutes=6))
    assert (recovered["accepted"], recovered["rejected"], recovered["versions"]) == (1, 0, 2)
    assert worker.tick(OPEN + timedelta(minutes=7))["versions"] == 0
    with store.connect() as connection:
        assert connection.execute("SELECT count(*) FROM candle_samples").fetchone()[0] == 2


@pytest.mark.parametrize("raw", (*OVERFLOW_CLOCKS, "not-a-clock"))
def test_financial_sql_clock_keeps_invalid_ledgers_fail_closed(raw):
    # A NULL would hide an invalid ledger row from cash/risk predicates.
    with sqlite3.connect(":memory:") as connection:
        register_exact_time_sql(connection)
        with pytest.raises(sqlite3.Error):
            connection.execute("SELECT rc6_instant_us(?)", (raw,)).fetchone()


def test_exact_microsecond_offsets_remain_equal():
    assert utc_microseconds("2026-10-05T10:30:00.123456-03:00") == utc_microseconds(
        "2026-10-05T13:30:00.123456+00:00")
    assert utc_microseconds("2026-10-05T13:30:00.123457Z") - utc_microseconds(
        "2026-10-05T13:30:00.123456Z") == 1
