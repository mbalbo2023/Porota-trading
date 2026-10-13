"""#462 final acceptance: shared PAPER FUTURES/spot admission and risk.

Only synthetic quotes and fresh local SQLite databases are used. The real
broker, sector authority, durable ledgers and admission transactions remain
active. This is programming acceptance evidence, not an independent audit.
"""
import itertools
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sys
from threading import Barrier, Lock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from decimal import Decimal

import ck_policy_gate_hf6 as policy
from be_paper_engine import PaperBroker, PaperStore, Quote
from bs_instrument_contracts import InstrumentContract
from dh_paper_dynamic_risk_gate_hf6 import portfolio_capacity, position_stop_risk
from rc6_paper_family_lifecycle import future_positions
from rc6_ppi_future_contract_policy import standard_dlr_terms


D = Decimal
START = "2026-10-05T12:00:00-03:00"
NEXT = "2026-10-05T12:00:01-03:00"
CLOSE_LOSS = "2026-10-05T12:00:02-03:00"
CLOSE_WIN = "2026-10-05T12:00:03-03:00"


@pytest.fixture(autouse=True)
def default_native_policy(monkeypatch):
    # Restore production's default policy, rather than bypass its authority.
    for name in (
        "PAPER_SECTOR_CONCENTRATION_POLICY", "PAPER_MAX_POSITIONS_PER_SECTOR",
        "PAPER_EXPECTANCY_POLICY", "PAPER_MARKET_REGIME_POLICY",
        "PAPER_EMERGENCY_MAX_OPEN_POSITIONS", "POROTA_SECTOR_MAP_PATH",
        "POROTA_RUNTIME_SCHEMA_READY",
    ):
        monkeypatch.delenv(name, raising=False)
    assert policy.active_policies()["sector_concentration"] == "BINDING"
    assert policy.active_policies()["sector_limit"] == 2


class TracedPaperStore(PaperStore):
    """Observe real SQLite connections without changing transaction behavior."""

    def __init__(self, path):
        self.statements = []
        self._connections = itertools.count()
        self._trace_lock = Lock()
        super().__init__(str(path))

    def connect(self):
        connection = super().connect()
        identity = next(self._connections)

        def capture(statement):
            with self._trace_lock:
                self.statements.append((identity, statement))

        connection.set_trace_callback(capture)
        return connection


def make_broker(tmp_path, clock, *, filename="paper.sqlite", store=None):
    # The existing risk, stop and exposure percentages are unchanged. A larger
    # synthetic capital lets one native integer DLR contract fit those guards.
    store = store or TracedPaperStore(tmp_path / filename)
    return PaperBroker(
        store, initial_cash="100000000", risk_pct="0.002",
        max_position_pct="0.25", max_total_exposure_pct="0.60",
        daily_loss_pct="2.5", daily_soft_stop_pct="1.5",
        participation="0.10", stop_loss_pct="0.02",
        clock_fn=lambda: clock[0], session_policy=None,
        require_supervisor=False, ai_mode="OFF", economics_mode="SHADOW",
        quote_max_age_seconds=120, trade_max_age_seconds=900,
    )


def future_quote(at=START, *, symbol="DLR/OCT26", bid="1499", ask="1500",
                 depth="10"):
    terms = standard_dlr_terms(symbol)
    contract = InstrumentContract(
        symbol, "FUTUROS", "ARS", "A3", "INMEDIATA", D("1000"), D("1"),
        "PPI_PRIMARY+A3_OFFICIAL:TEST:v1", expires_at=terms["expires_at"],
        minimum_quantity=D("1"), paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=D("1"), underlying=terms["underlying"],
    )
    return Quote(
        symbol, "FUTUROS", "INMEDIATA", D(bid), D(bid), D(ask),
        D(depth), D(depth), at, contract=contract, currency="ARS", market="A3",
        metadata_source="PPI_CATALOG:TEST", book_at=at, trade_at=at,
        last_kind="TRADE",
    )


def spot_quote(at=START, *, symbol="GGAL", bid="999", ask="1000", depth="10"):
    return Quote(
        symbol, "ACCIONES", "INMEDIATA", D(bid), D(bid), D(ask),
        D(depth), D(depth), at, currency="ARS", market="BYMA",
        metadata_source="PPI_CATALOG:TEST", book_at=at, trade_at=at,
        last_kind="TRADE",
    )


def admit(broker, quote):
    broker.store.add_quote(quote)
    detail = {}
    method = broker._open_future if quote.asset_class == "FUTUROS" else broker._open
    return method(quote, D("0.8"), detail), detail


def open_ok(broker, quote):
    result, detail = admit(broker, quote)
    assert result[0], result
    return result[2], detail


def mark_future(broker, clock, price):
    clock[0] = NEXT
    row = broker.family_paper.active_future("DLR/OCT26")
    quote = future_quote(NEXT, bid=str(price), ask=str(D(price) + 1))
    broker.store.add_quote(quote)
    broker.family_paper.mark_future(
        quote.contract, lifecycle_id=row["lifecycle_id"],
        event_id=row["lifecycle_id"] + ":NATIVE_MARK:" + NEXT,
        mark_price=price, book_at=quote.book_at, occurred_at=clock[0],
        max_mark_age_seconds=broker.quote_max_age_seconds,
        detail={"source": "PPI_BOOK_PAPER_TEST"},
    )
    assert broker.daily_risk.evaluate(clock[0])["ARS"]["state"] == "READY"


def total_exposure(broker):
    spot = sum((D(row["entry_price"]) * D(row["quantity"])
                * broker._position_multiplier(row)
                for row in broker.store.open_positions() if row["currency"] == "ARS"), D(0))
    return spot + broker._future_exposure("ARS")


def ledger_state(broker, at):
    """Rejection may record a reason, but may not alter any economic ledger."""
    with broker.store.connect() as connection:
        return (
            broker._cash(as_of=at, connection=connection),
            tuple(tuple(row) for row in connection.execute(
                "SELECT * FROM paper_positions ORDER BY paper_id")),
            tuple(tuple(row) for row in connection.execute(
                "SELECT * FROM paper_fills ORDER BY id")),
            tuple(tuple(row) for row in connection.execute(
                "SELECT * FROM paper_future_positions ORDER BY lifecycle_id")),
            tuple(tuple(row) for row in connection.execute(
                "SELECT * FROM paper_family_lifecycle_events ORDER BY rowid")),
        )


def assert_shared_reads_under_admission_lock(store, insert_table):
    connections = {}
    for identity, sql in store.statements:
        connections.setdefault(identity, []).append(sql.upper())
    admitted = [sql for sql in connections.values()
                if any("INSERT INTO " + insert_table.upper() in item for item in sql)]
    assert admitted, "No real durable admission was recorded"
    for statements in admitted:
        insert = next(index for index, item in enumerate(statements)
                      if "INSERT INTO " + insert_table.upper() in item)
        begin = max(index for index, item in enumerate(statements[:insert])
                    if item.strip() == "BEGIN IMMEDIATE")
        guarded = statements[begin + 1:insert]
        assert any("SELECT" in item and "FROM PAPER_POSITIONS" in item for item in guarded)
        assert any("SELECT" in item and "FROM PAPER_FUTURE_POSITIONS" in item for item in guarded)


def test_default_binding_underlying_cap_admits_two_dlr_months_then_blocks_third(tmp_path):
    clock = [START]
    broker = make_broker(tmp_path, clock)
    for symbol in ("DLR/OCT26", "DLR/NOV26"):
        _, detail = open_ok(broker, future_quote(symbol=symbol))
        assert detail["quantity"] == "1"
    rows = future_positions(broker.store, "ARS", active_only=True)
    assert {row["symbol"] for row in rows} == {"DLR/OCT26", "DLR/NOV26"}
    assert {json.loads(row["metadata_json"])["financial_contract"]["underlying"]
            for row in rows} == {"DOLAR_A3500"}
    assert broker._cash(as_of=clock[0]) > D("1500000")
    assert portfolio_capacity(broker, "ARS", clock[0]).remaining_before_candidate > D("200000")
    before = ledger_state(broker, clock[0])
    result, _ = admit(broker, future_quote(symbol="DLR/DIC26"))
    assert result == (False, "FUTURES_UNDERLYING_CONCENTRATION_CAP", None)
    assert ledger_state(broker, clock[0]) == before
    assert_shared_reads_under_admission_lock(broker.store, "paper_future_positions")


@pytest.mark.parametrize("configured_cap", [1, 3])
def test_future_underlying_concentration_honors_existing_configured_sector_cap(
        tmp_path, monkeypatch, configured_cap):
    monkeypatch.setenv("PAPER_MAX_POSITIONS_PER_SECTOR", str(configured_cap))
    assert policy.active_policies()["sector_concentration"] == "BINDING"
    assert policy.active_policies()["sector_limit"] == configured_cap
    clock = [START]
    broker = make_broker(tmp_path, clock)
    for index, symbol in enumerate(("DLR/OCT26", "DLR/NOV26", "DLR/DIC26")):
        before = ledger_state(broker, clock[0])
        result, _ = admit(broker, future_quote(symbol=symbol))
        if index < configured_cap:
            assert result[0], result
        else:
            assert result == (False, "FUTURES_UNDERLYING_CONCENTRATION_CAP", None)
            assert ledger_state(broker, clock[0]) == before
    assert len(broker.store.active_future_positions()) == configured_cap


def test_spot_sizing_consumes_marked_future_exposure_at_unchanged_total_cap(tmp_path):
    clock = [START]
    broker = make_broker(tmp_path, clock)
    open_ok(broker, future_quote())
    # Admission sees the freshly marked active portfolio before its next exit
    # supervision pass. Marking never changes an instrument's contract units.
    mark_future(broker, clock, "59000")
    quote = spot_quote(NEXT, ask="100000", bid="99999", depth="1000")
    _, detail = open_ok(broker, quote)
    assert D(detail["qty_by_total_cap"]) < min(
        D(detail["qty_by_risk"]), D(detail["qty_by_cash"]),
        D(detail["qty_by_liquidity"]), D(detail["qty_by_position_cap"]))
    row = broker.store.open_position("GGAL")
    assert D(row["quantity"]) == D(detail["qty_by_total_cap"]) == D("9")
    assert total_exposure(broker) <= broker.initial_cash * D("0.60")
    assert broker._cash(as_of=clock[0]) > D("90000000")
    assert_shared_reads_under_admission_lock(broker.store, "paper_positions")
    before = ledger_state(broker, clock[0])
    result, _ = admit(broker, spot_quote(NEXT, symbol="YPFD", ask="100000", bid="99999", depth="1000"))
    assert result[0] is False
    assert "exposicion_total" in result[1]
    assert ledger_state(broker, clock[0]) == before


def test_future_sizing_consumes_spot_and_future_exposure_at_unchanged_total_cap(tmp_path):
    clock = [START]
    broker = make_broker(tmp_path, clock)
    _, spot_detail = open_ok(
        broker, spot_quote(ask="500000", bid="499999", depth="60"))
    assert spot_detail["qty_by_liquidity"] == "6"
    open_ok(broker, future_quote())
    mark_future(broker, clock, "56000")
    assert broker._future_exposure("ARS") + D("1500300") < broker.initial_cash * D("0.60")
    assert total_exposure(broker) + D("1500300") > broker.initial_cash * D("0.60")
    assert portfolio_capacity(broker, "ARS", clock[0]).remaining_before_candidate > D("200000")
    assert broker._cash(as_of=clock[0]) > D("90000000")
    before = ledger_state(broker, clock[0])
    result, _ = admit(broker, future_quote(NEXT, symbol="DLR/NOV26"))
    assert result == (False, "FUTURES_CAPITAL_LIQUIDITY_OR_RISK_INSUFFICIENT", None)
    assert ledger_state(broker, clock[0]) == before


@pytest.mark.parametrize("candidate_family", ["FUTUROS", "ACCIONES"])
def test_emergency_position_cap_counts_both_ledgers_for_each_executor(
        tmp_path, monkeypatch, candidate_family):
    monkeypatch.setenv("PAPER_EMERGENCY_MAX_OPEN_POSITIONS", "2")
    clock = [START]
    broker = make_broker(tmp_path, clock)
    open_ok(broker, future_quote())
    open_ok(broker, spot_quote())
    assert len(broker.store.open_positions()) == len(broker.store.active_future_positions()) == 1
    assert portfolio_capacity(broker, "ARS", clock[0]).remaining_before_candidate > D("200000")
    before = ledger_state(broker, clock[0])
    candidate = (future_quote(symbol="DLR/NOV26") if candidate_family == "FUTUROS"
                 else spot_quote(symbol="YPFD"))
    result, _ = admit(broker, candidate)
    assert result == (False, "EMERGENCY_POSITION_CAP", None)
    assert ledger_state(broker, clock[0]) == before


def race_admissions(brokers, quotes):
    # Quotes are persisted before the race; threads only perform genuine local
    # broker admission. SQLite owns serialization and the guards stay intact.
    for broker, quote in zip(brokers, quotes):
        broker.store.add_quote(quote)
    barrier = Barrier(len(brokers))

    def worker(broker, quote):
        detail = {}
        method = broker._open_future if quote.asset_class == "FUTUROS" else broker._open
        barrier.wait(timeout=10)
        return method(quote, D("0.8"), detail), detail

    with ThreadPoolExecutor(max_workers=len(brokers)) as pool:
        tasks = [pool.submit(worker, broker, quote)
                 for broker, quote in zip(brokers, quotes)]
        return [task.result(timeout=30) for task in tasks]


def test_concurrent_spot_future_admission_cannot_overrun_shared_emergency_cap(
        tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_EMERGENCY_MAX_OPEN_POSITIONS", "1")
    clock = [START]
    first = make_broker(tmp_path, clock)
    second = make_broker(tmp_path, clock, store=first.store)
    first.daily_risk.evaluate(clock[0])
    first.store.statements.clear()
    results = race_admissions((first, second), (future_quote(), spot_quote()))
    assert sum(result[0] for result, _ in results) == 1, results
    assert [result[1] for result, _ in results if not result[0]] == ["EMERGENCY_POSITION_CAP"]
    spot = first.store.open_positions()
    futures = first.store.active_future_positions()
    assert len(spot) + len(futures) == 1
    assert_shared_reads_under_admission_lock(
        first.store, "paper_positions" if spot else "paper_future_positions")
    assert total_exposure(first) < first.initial_cash * D("0.60")


@pytest.mark.parametrize("candidate_family", ["FUTUROS", "ACCIONES"])
def test_concurrent_soft_stop_sizes_each_executor_from_combined_open_risk(
        tmp_path, candidate_family):
    clock = [START]
    broker = make_broker(tmp_path, clock)
    open_ok(broker, future_quote())
    future_only = portfolio_capacity(broker, "ARS", clock[0])
    open_ok(broker, spot_quote())
    mixed = portfolio_capacity(broker, "ARS", clock[0])
    spot_risk = position_stop_risk(broker, broker.store.open_position("GGAL"))
    assert mixed.open_stop_risk == future_only.open_stop_risk + spot_risk
    # A fresh unrealized futures loss remains below the existing soft stop,
    # while reserving more than a fresh trade's risk budget in the shared gate.
    mark_future(broker, clock, "140")
    before = portfolio_capacity(broker, "ARS", clock[0])
    assert before.realized_loss_consumed == 0
    assert D(0) < before.remaining_before_candidate < broker.initial_cash * D("0.002")
    candidate = (future_quote(NEXT, symbol="DLR/NOV26", depth="10000")
                 if candidate_family == "FUTUROS" else
                 spot_quote(NEXT, symbol="YPFD", depth="100000"))
    _, detail = open_ok(broker, candidate)
    assert D(detail["risk_budget"]) == before.remaining_before_candidate
    if candidate_family == "ACCIONES":
        locked = detail["concurrent_risk_locked"]
        assert D(locked["open_stop_risk"]) == before.open_stop_risk
        assert D(detail["candidate_stop_risk"]) <= before.remaining_before_candidate
    after = portfolio_capacity(broker, "ARS", clock[0])
    assert before.open_stop_risk < after.open_stop_risk <= after.soft_stop_budget
    assert after.remaining_before_candidate >= 0
    assert_shared_reads_under_admission_lock(
        broker.store, "paper_future_positions" if candidate_family == "FUTUROS" else "paper_positions")


def test_concurrent_spot_future_admission_cannot_spend_same_soft_stop_capacity_twice(tmp_path):
    clock = [START]
    broker = make_broker(tmp_path, clock)
    open_ok(broker, future_quote())
    open_ok(broker, spot_quote())
    mark_future(broker, clock, "140")
    before = portfolio_capacity(broker, "ARS", clock[0])
    assert D("100000") < before.remaining_before_candidate < D("125000")
    other = make_broker(tmp_path, clock, store=broker.store)
    candidates = (
        future_quote(NEXT, symbol="DLR/NOV26", bid="3499", ask="3500"),
        spot_quote(NEXT, symbol="YPFD", bid="2999999", ask="3000000"),
    )
    broker.store.statements.clear()
    results = race_admissions((broker, other), candidates)
    assert sum(result[0] for result, _ in results) == 1, results
    assert len(broker.store.open_positions()) + len(broker.store.active_future_positions()) == 3
    after = portfolio_capacity(broker, "ARS", clock[0])
    assert before.open_stop_risk < after.open_stop_risk <= after.soft_stop_budget
    assert after.remaining_before_candidate >= 0
    winner = next(quote for quote, (result, _) in zip(candidates, results) if result[0])
    assert_shared_reads_under_admission_lock(
        broker.store, "paper_future_positions" if winner.asset_class == "FUTUROS" else "paper_positions")


def test_closed_losing_future_consumes_budget_even_after_profitable_future_close(tmp_path):
    clock = [START]
    broker = make_broker(tmp_path, clock)
    open_ok(broker, future_quote())
    open_ok(broker, future_quote(symbol="DLR/NOV26"))
    clock[0] = CLOSE_LOSS
    loss = broker.family_paper.active_future("DLR/OCT26")
    assert broker._close_future(
        future_quote(CLOSE_LOSS, bid="100", ask="101", depth="100"), loss, "TEST_LOSS")
    before_win = portfolio_capacity(broker, "ARS", clock[0])
    clock[0] = CLOSE_WIN
    winner = broker.family_paper.active_future("DLR/NOV26")
    assert broker._close_future(
        future_quote(CLOSE_WIN, symbol="DLR/NOV26", bid="5000", ask="5001", depth="100"),
        winner, "TEST_WIN")
    closed = future_positions(broker.store, "ARS")
    pnls = [D(json.loads(row["metadata_json"])["realized_pnl"]) for row in closed]
    assert sum(pnls) > 0
    consumed_loss = -next(pnl for pnl in pnls if pnl < 0)
    after_win = portfolio_capacity(broker, "ARS", clock[0])
    assert before_win.realized_loss_consumed == after_win.realized_loss_consumed == consumed_loss
    assert after_win.open_stop_risk == 0
    assert after_win.remaining_before_candidate == after_win.soft_stop_budget - consumed_loss
    assert after_win.remaining_before_candidate < broker.initial_cash * D("0.002")
    assert broker._cash(as_of=clock[0]) == broker.initial_cash + sum(pnls)
    assert broker.daily_risk.evaluate(clock[0])["ARS"]["state"] == "READY"
    _, detail = open_ok(broker, future_quote(CLOSE_WIN, symbol="DLR/DIC26", depth="10000"))
    assert D(detail["risk_budget"]) == after_win.remaining_before_candidate
    assert detail["quantity"] == "1"
    assert portfolio_capacity(broker, "ARS", clock[0]).realized_loss_consumed == consumed_loss
