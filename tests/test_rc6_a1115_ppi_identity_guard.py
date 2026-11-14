"""A1115-PI: native PPI response identity must precede PAPER quote and Book cache.

These tests use only in-process fixtures, an ephemeral SQLite budget and no wire.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import bf_production_paper_observer as observer
import bv_paper_runtime as runtime
from rc6_ppi_global_budget import GlobalPPIBudget
from tests.test_rc6_ppi_capacity_benchmark import Clock
from tests.test_rc6_ppi_global_budget import policy


IDENTITY = ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS")
NATIVE_FIELDS = (
    ("ticker", "OTHER"),
    ("type", "CEDEARS"),
    ("market", "A3"),
    ("currency", "USD"),
    ("settlement", "INMEDIATA"),
    ("settlement_used", "INMEDIATA"),
    ("settlementUsed", "INMEDIATA"),
)


def _book(at):
    return {"date": at, "bids": [{"price": 99, "quantity": 10}],
            "offers": [{"price": 101, "quantity": 10}]}


@pytest.mark.parametrize("endpoint", ("current", "book"))
@pytest.mark.parametrize("field,wrong", NATIVE_FIELDS)
def test_scanner_rejects_contradictory_native_identity_before_quote(
        monkeypatch, endpoint, field, wrong):
    """Removing native-vs-request checks would silently label OTHER as GGAL."""
    at = datetime(2026, 11, 10, 14, tzinfo=timezone.utc).isoformat()

    class Reader:
        def current(self, symbol, kind, settlement):
            value = {"price": 100, "date": at}
            if endpoint == "current":
                value[field] = wrong
            return value

        def book(self, symbol, kind, settlement):
            value = _book(at)
            if endpoint == "book":
                value[field] = wrong
            return value

    monkeypatch.setattr(observer.financial_catalog, "lookup", lambda *args, **kwargs: object())
    monkeypatch.setattr(observer.financial_catalog, "quote_terms", lambda metadata: {
        "market": "BYMA", "currency": "ARS", "metadata_source": "PPI_CATALOG:fixture",
        "opening_block_reason": ""})
    with pytest.raises(ValueError, match="PPI_RESPONSE_IDENTITY_MISMATCH"):
        observer._read_scanner_quote(
            Reader(), object(), "GGAL", "ACCIONES", "A-24HS", identity=IDENTITY)


def test_optional_native_fields_may_be_absent_or_authoritative_aliases(monkeypatch):
    """A true 3-key SDK response must not be rejected for omitting 5-key fields."""
    at = datetime(2026, 11, 10, 14, tzinfo=timezone.utc).isoformat()

    class Reader:
        def current(self, *args):
            return {"price": 100, "date": at, "ticker": "ggal", "instrumentType": "ACCIONES",
                    "market": "BCBA", "currency": "Pesos", "settlement": "T1"}

        def book(self, *args):
            return _book(at)

    monkeypatch.setattr(observer.financial_catalog, "lookup", lambda *args, **kwargs: object())
    monkeypatch.setattr(observer.financial_catalog, "quote_terms", lambda metadata: {
        "market": "BYMA", "currency": "ARS", "metadata_source": "PPI_CATALOG:fixture",
        "opening_block_reason": ""})
    q = observer._read_scanner_quote(
        Reader(), object(), "GGAL", "ACCIONES", "A-24HS", identity=IDENTITY)
    assert (q.symbol, q.asset_class, q.market, q.currency, q.settlement) == IDENTITY


@pytest.mark.parametrize("field,wrong", NATIVE_FIELDS)
def test_conflicting_book_cannot_seed_critical_cache(tmp_path, field, wrong):
    """A wrong Book must not be returned, saved or later served to a follower."""
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    calls = []

    def wrong_fetch():
        calls.append("wrong")
        return _book(clock.now().isoformat()) | {field: wrong}

    def good_fetch():
        calls.append("good")
        return _book(clock.now().isoformat())

    with pytest.raises(ValueError, match="PPI_RESPONSE_IDENTITY_MISMATCH"):
        budget.coalesced_book(IDENTITY, wrong_fetch, consumer="EXIT_READER",
                              priority="EXIT_CRITICAL")
    assert budget.coalesced_book(IDENTITY, good_fetch, consumer="EXIT_READER",
                                 priority="EXIT_CRITICAL")["date"] == clock.now().isoformat()
    assert budget.coalesced_book(IDENTITY, lambda: pytest.fail("unexpected second wire"),
                                 consumer="SCANNER", priority="OPENED_CRITICAL")["date"] == clock.now().isoformat()
    assert calls == ["wrong", "good"]


def test_native_aliases_pass_before_critical_book_cache(tmp_path):
    clock = Clock()
    budget = GlobalPPIBudget(tmp_path / "budget.sqlite", policy(clock), clock=clock.now)
    result = budget.coalesced_book(IDENTITY,
        lambda: _book(clock.now().isoformat()) | {
            "ticker": "ggal", "instrumentType": "ACCIONES", "market": "BCBA",
            "currency": "Pesos", "settlement": "T1"},
        consumer="EXIT_READER", priority="EXIT_CRITICAL")
    assert result["ticker"] == "ggal"
    cached = budget.coalesced_book(IDENTITY,
        lambda: pytest.fail("validated Book should be coalesced"),
        consumer="SCANNER", priority="OPENED_CRITICAL")
    assert cached["bids"][0]["price"] == "99"
    assert "ticker" not in cached  # cache keeps bounded depth, not provider identifiers


@pytest.mark.parametrize("native_book", (
    {"ticker": "DLR/OCT26", "type": "FUTUROS", "market": "BYMA", "currency": "USD"},
    {"ticker": "DLR/OCT26", "type": "FUTUROS", "market": "A3", "currency": "ARS",
     "settlementUsed": "A-24HS"},
))
def test_future_exit_reader_rejects_wrong_book_with_missing_catalog(
        monkeypatch, native_book):
    """The durable position identity must reach normalization before any quote write."""
    at = datetime(2026, 11, 10, 14, tzinfo=timezone.utc).isoformat()
    position = {"symbol": "DLR/OCT26", "asset_class": "FUTUROS", "market": "A3",
                "currency": "ARS", "settlement": "INMEDIATA", "lifecycle_id": "PAPER-FUT-1"}

    class Store:
        def __init__(self):
            self.saved = []
            self.events = []

        def exit_positions(self):
            return [], []

        def active_future_positions(self):
            return [position]

        def add_quote(self, quote):
            self.saved.append(quote)

        def event(self, *args):
            self.events.append(args)

    class Reader:
        def book(self, symbol, kind, settlement):
            return _book(at) | native_book

    monkeypatch.setattr("rc6_paper_family_lifecycle.future_position_contract",
        lambda row: SimpleNamespace(currency="ARS", market="A3", family="FUTUROS"))
    monkeypatch.setattr(runtime, "now_iso", lambda: at)
    monkeypatch.setattr(observer, "now_iso", lambda: at)
    monkeypatch.setattr(observer.financial_catalog, "lookup", lambda *args, **kwargs: None)
    store = Store()
    failures = runtime.collect_exit_books(Reader(), store, object(), at)
    assert failures == 1
    assert store.saved == []
    assert len(store.events) == 1 and store.events[0][0] == "EXIT_BOOK_ERROR"


def test_future_exit_reader_accepts_genuine_book_without_catalog(monkeypatch):
    """An existing position can still exit when catalogue metadata is unavailable."""
    at = datetime(2026, 11, 10, 14, tzinfo=timezone.utc).isoformat()
    position = {"symbol": "DLR/OCT26", "asset_class": "FUTUROS", "market": "A3",
                "currency": "ARS", "settlement": "INMEDIATA", "lifecycle_id": "PAPER-FUT-1"}

    class Store:
        def __init__(self):
            self.saved = []

        def exit_positions(self):
            return [], []

        def active_future_positions(self):
            return [position]

        def add_quote(self, quote):
            self.saved.append(quote)

    class Reader:
        def book(self, symbol, kind, settlement):
            return _book(at) | {"ticker": symbol, "type": kind,
                                "market": "A3", "currency": "ARS",
                                "settlement_used": "INMEDIATA"}

    monkeypatch.setattr("rc6_paper_family_lifecycle.future_position_contract",
        lambda row: SimpleNamespace(currency="ARS", market="A3", family="FUTUROS"))
    monkeypatch.setattr(observer, "now_iso", lambda: at)
    monkeypatch.setattr(observer.financial_catalog, "lookup", lambda *args, **kwargs: None)
    store = Store()
    assert runtime.collect_exit_books(Reader(), store, object(), at) == 0
    assert len(store.saved) == 1
    assert (store.saved[0].symbol, store.saved[0].asset_class, store.saved[0].market,
            store.saved[0].currency, store.saved[0].settlement) == (
                "DLR/OCT26", "FUTUROS", "A3", "ARS", "INMEDIATA")
