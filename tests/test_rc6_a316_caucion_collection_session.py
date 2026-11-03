"""COM19031 caución collection vs cash sweep; no PPI network/real routes."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import bf_production_paper_observer as observer
import bu_instrument_catalog as catalog
import dj_caucion_live_ppi_rc6 as live
from bd_ppi_readonly_guard import caucion_discovery_days
from be_paper_engine import PaperStore

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def at(clock):
    return datetime.fromisoformat(clock).astimezone(TZ)


@pytest.mark.parametrize("clock,expected", [
    ("2026-11-02T10:29:59-03:00", False),
    ("2026-11-02T10:30:00-03:00", True),
    ("2026-11-02T12:00:00-03:00", True),
    ("2026-11-02T16:25:00-03:00", True),
    ("2026-11-02T16:55:00-03:00", True),
    ("2026-11-02T16:59:59-03:00", True),
    ("2026-11-02T17:00:00-03:00", False),
    ("2026-11-02T18:00:00-03:00", False),
    ("2026-11-06T12:00:00-03:00", False),
    ("2026-11-07T12:00:00-03:00", False),
])
def test_bounded_ppi_collection_covers_verified_regular_caucion_clock(clock, expected):
    assert live.collection_window(at(clock)) is expected


@pytest.mark.parametrize("clock,expected", [
    ("2026-11-02T10:30:00-03:00", False),
    ("2026-11-02T12:00:00-03:00", False),
    ("2026-11-02T16:24:59-03:00", False),
    ("2026-11-02T16:25:00-03:00", True),
    ("2026-11-02T16:55:00-03:00", True),
    ("2026-11-02T16:55:01-03:00", False),
    ("2026-11-02T17:00:00-03:00", False),
    ("2026-11-07T16:40:00-03:00", False),
])
def test_eod_sweep_window_remains_separate(clock, expected):
    assert live.cash_sweep_window(at(clock)) is expected


def _store(tmp_path, quote_at):
    store = PaperStore(str(tmp_path / "caucion_book.db"))
    observer._support_schema(store)
    record = catalog.normalize_record({
        "ticker":"PESOS1", "description":"PESOS1", "currency":"Pesos",
        "type":"CAUCIONES", "market":"BYMA", "nominalInPrice":1,
        "_provider_instrument_type":"CAUCIONES", "_discovery_source":"PPI_PRIMARY",
    }, "INMEDIATA", quote_at.isoformat(), "test")
    with store.connect() as conn:
        catalog.persist(conn, record)
    return store


@pytest.mark.parametrize("clock", [
    "2026-11-02T10:30:00-03:00", "2026-11-02T12:00:00-03:00",
    "2026-11-02T16:50:00-03:00",
])
def test_exact_ppi_book_persists_current_and_history_intraday(tmp_path, clock):
    observed = at(clock)
    store = _store(tmp_path, observed)
    calls = []
    class Reader:
        def book(self, ticker, family, settlement):
            calls.append((ticker,family,settlement))
            return {"date": observed.isoformat(), "bids":[
                {"price":"19.1", "quantity":"100000"}], "offers":[]}
    result = live.refresh(Reader(), store, now=observed, clock_fn=lambda:observed)
    assert result["state"] == "OK" and result["ready"] == 1
    assert result["real_routes"] == []
    assert calls == [("PESOS1", "CAUCIONES", "INMEDIATA")]
    with store.connect() as conn:
        current = conn.execute("SELECT state,source FROM paper_caucion_live_book").fetchone()
        receipt = conn.execute("SELECT state,source FROM paper_caucion_live_book_history").fetchone()
    assert tuple(current) == ("BOOK_READY",live.SOURCE)
    assert tuple(receipt) == tuple(current)


@pytest.mark.parametrize("clock", [
    "2026-11-02T10:29:59-03:00", "2026-11-02T17:00:00-03:00",
    "2026-11-07T12:00:00-03:00",
])
def test_no_read_or_receipt_offhours(tmp_path, clock):
    store = _store(tmp_path, at(clock))
    class Reader:
        def book(self, *_args):
            raise AssertionError("OFFHOURS_PPI_CALL")
    assert live.refresh(Reader(), store, now=at(clock)) == {
        "state":"OUTSIDE_COLLECTION_WINDOW", "records":0, "ready":0,
        "real_routes":[]}
    with store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='paper_caucion_live_book'").fetchone()


def test_source_budget_and_tenor3_need_explicit_ppi_discovery():
    assert caucion_discovery_days(raw="") == (1,2,7,30,120)
    assert 3 not in caucion_discovery_days(raw="")
    # Five identities * 390 minutes / default 60s cadence, conditional only.
    assert len(caucion_discovery_days(raw="")) * 390 == 1950
