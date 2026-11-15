"""A652: scanner trade rejection cannot veto fresh-Book PAPER exit supervision.

These are synthetic offline fixtures; no provider, socket, host, or order route.
The actual scanner helper is loaded from its source AST to avoid importing its
network-capable runtime and accidentally initialising any provider client.
"""

import ast
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from be_paper_engine import PaperBroker, PaperStore, Quote
from bs_instrument_contracts import InstrumentContract
from rc6_paper_family_lifecycle import future_positions


D = Decimal
SCANNER = Path(__file__).resolve().parents[1] / "bf_production_paper_observer.py"


def scanner_helper():
    root = ast.parse(SCANNER.read_text(encoding="utf-8"))
    functions = [node for node in root.body if isinstance(node, ast.FunctionDef)
                 and node.name == "_scanner_trade_temporal_rejection"]
    assert len(functions) == 1
    isolated = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    scope = {}
    exec(compile(isolated, str(SCANNER), "exec"), scope)
    return scope[functions[0].name]


def spot_quote(at, *, price="100", last_kind="TRADE", trade_at=None, book_at=None):
    price = D(price)
    return Quote(
        "GGAL", "ACCIONES", "A-24HS", price,
        price - D("0.10"), price + D("0.10"), D("1000"), D("1000"), at,
        currency="ARS", market="BYMA", metadata_source="SYNTHETIC_TEST",
        book_at=book_at or at,
        trade_at=trade_at if trade_at is not None else (at if last_kind == "TRADE" else None),
        last_kind=last_kind)


def future_contract():
    return InstrumentContract(
        "DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
        D("1000"), D("1"), "CONTRACT_EVIDENCE_V2_BOUND",
        expires_at="2026-10-30T15:00:00-03:00",
        minimum_quantity=D("1"), paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=D("1"), underlying="DOLAR_A3500")


def future_quote(at, *, last_kind="TRADE", trade_at=None, book_at=None):
    return Quote(
        "DLR/OCT26", "FUTUROS", "INMEDIATA", D("1520"), D("1520"),
        D("1521"), D("10"), D("10"), at, contract=future_contract(),
        currency="ARS", market="A3", metadata_source="PPI_CATALOG:TEST",
        book_at=book_at or at,
        trade_at=trade_at if trade_at is not None else (at if last_kind == "TRADE" else None),
        last_kind=last_kind)


def test_scanner_run_uses_the_exit_preserving_guard_before_continue():
    root = ast.parse(SCANNER.read_text(encoding="utf-8"))
    run = next(n for n in root.body if isinstance(n, ast.FunctionDef) and n.name == "run")
    branches = [n for n in ast.walk(run) if isinstance(n, ast.If)
                and isinstance(n.test, ast.Call)
                and isinstance(n.test.func, ast.Name)
                and n.test.func.id == "_scanner_trade_temporal_rejection"]
    assert len(branches) == 1
    assert any(isinstance(n, ast.Continue) for n in branches[0].body)


def test_spot_exit_runs_with_fresh_book_even_when_last_trade_is_missing(tmp_path):
    at = "2026-08-28T11:00:00-03:00"
    store = PaperStore(str(tmp_path / "spot.sqlite"))
    broker = PaperBroker(store)
    assert broker._open(spot_quote(at), D("0.8"), {})[0]
    assert store.open_positions()

    later = "2026-08-28T11:01:00-03:00"
    only_book = spot_quote(later, price="90", last_kind="UNAVAILABLE")
    assert only_book.time_error(later, require_trade=True) == "LAST_IS_NOT_A_TRADE"
    assert only_book.time_error(later) == ""

    error = scanner_helper()(only_book, broker, store, checked_at=later)
    assert error == "LAST_IS_NOT_A_TRADE"
    assert store.open_positions() == []
    assert store.recent_closed()[0]["close_reason"] == "STOP_PAPER"
    # A duplicate scanner callback cannot sell twice or open a new position.
    assert scanner_helper()(only_book, broker, store, checked_at=later) == error
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM paper_fills WHERE side='BUY_SIMULATED'").fetchone()[0] == 1


def test_book_only_is_never_an_entry_signal_without_open_position(tmp_path, monkeypatch):
    at = "2026-08-28T11:01:00-03:00"
    store = PaperStore(str(tmp_path / "entry.sqlite"))
    broker = PaperBroker(store)
    def forbidden_signal(*args, **kwargs):
        raise AssertionError("Book-only rejection must not evaluate an entry")
    monkeypatch.setattr(broker, "decide", forbidden_signal)

    assert scanner_helper()(spot_quote(at, last_kind="UNAVAILABLE"), broker, store,
                            checked_at=at) == "LAST_IS_NOT_A_TRADE"
    assert store.open_positions() == []
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM paper_decisions WHERE action='HOLD'").fetchone()[0] == 1


def test_future_exit_supervises_fresh_book_without_a_trade(tmp_path):
    clock = ["2026-10-05T12:00:00-03:00"]
    store = PaperStore(str(tmp_path / "future.sqlite"))
    broker = PaperBroker(
        store, initial_cash="10000000", risk_pct="0.05", max_positions=10,
        participation="1", max_position_pct="1", max_total_exposure_pct="1",
        clock_fn=lambda: clock[0], session_policy=None, require_supervisor=False,
        ai_mode="OFF", economics_mode="SHADOW", quote_max_age_seconds=120,
        trade_max_age_seconds=900, daily_loss_pct="5", daily_soft_stop_pct="4")
    opened, reason, _ = broker._open_future(future_quote(clock[0]), D("0.8"), {"samples": 8})
    assert opened, reason
    assert len(future_positions(store, "ARS", active_only=True)) == 1

    clock[0] = "2026-10-05T14:50:00-03:00"
    only_book = future_quote(clock[0], last_kind="UNAVAILABLE")
    assert scanner_helper()(only_book, broker, store, checked_at=clock[0]) == "LAST_IS_NOT_A_TRADE"
    assert future_positions(store, "ARS", active_only=True) == []
    assert future_positions(store, "ARS")[0]["status"] == "CLOSED"


def test_future_book_only_without_position_cannot_evaluate_entry(tmp_path, monkeypatch):
    at = "2026-10-05T12:00:00-03:00"
    store = PaperStore(str(tmp_path / "future-no-entry.sqlite"))
    broker = PaperBroker(store)
    def forbidden_future_strategy(*args, **kwargs):
        raise AssertionError("Exit-only Book must not evaluate a FUTUROS entry")
    monkeypatch.setattr(broker, "_decide_future", forbidden_future_strategy)
    assert scanner_helper()(future_quote(at, last_kind="UNAVAILABLE"), broker, store,
                            checked_at=at) == "LAST_IS_NOT_A_TRADE"
    assert future_positions(store, "ARS") == []


@pytest.mark.parametrize("change", [
    {"book_at": "2026-08-28T10:55:00-03:00"},
    {"book_at": "2026-08-28T11:01:01-03:00"},
    {"book_at": None},
    {"observed_at": "2026-08-28T11:01:01-03:00"},
])
def test_bad_book_never_reaches_broker_even_if_trade_is_unavailable(change):
    at = "2026-08-28T11:01:00-03:00"
    q = replace(spot_quote(at, last_kind="UNAVAILABLE"), **change)

    class FakeBroker:
        quote_max_age_seconds = 120
        trade_max_age_seconds = 120
        recorded = []
        def on_quote(self, *args, **kwargs):
            raise AssertionError("Stale/future/absent book must not reach broker")
        def record_observation_rejection(self, quote, reason):
            self.recorded.append(reason)

    class FakeStore:
        events = []
        def event(self, kind, detail):
            self.events.append((kind, detail))

    broker, store = FakeBroker(), FakeStore()
    error = scanner_helper()(q, broker, store, checked_at=at)
    assert error
    assert broker.recorded == [error]
    assert store.events[-1][0] == "DATA_REJECTED"


def test_fresh_trade_leaves_normal_scanner_path_unchanged():
    at = "2026-08-28T11:01:00-03:00"
    class NeverBroker:
        quote_max_age_seconds = 120
        trade_max_age_seconds = 120
        def on_quote(self, *args, **kwargs):
            raise AssertionError("Normal trade should be processed by existing scanner path")
    class NeverStore:
        def event(self, *args, **kwargs):
            raise AssertionError("Fresh trade must not be rejected")
    assert scanner_helper()(spot_quote(at), NeverBroker(), NeverStore(), checked_at=at) == ""
