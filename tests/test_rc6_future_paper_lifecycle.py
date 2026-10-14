import sqlite3
from decimal import Decimal

from bs_instrument_contracts import InstrumentContract
from rc6_paper_family_lifecycle import (
    FamilyPaperExecutor, future_cash_effect, future_risk_snapshot,
)


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection


def dlr():
    return InstrumentContract(
        "DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
        Decimal("1000"), Decimal("1"), "CONTRACT_EVIDENCE_V2_BOUND",
        expires_at="2026-10-30T15:00:00-03:00",
        minimum_quantity=Decimal("1"),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=Decimal("1"),
        underlying="DOLAR_A3500",
    )


def test_future_lifecycle_reserve_mark_variation_close_and_cash(tmp_path):
    store = Store(tmp_path / "future.sqlite")
    executor = FamilyPaperExecutor(store)
    contract = dlr()

    opened = executor.open_future(
        contract, lifecycle_id="FUT-1", event_id="OPEN-1",
        entry_price="1500", quantity="1", entry_cost="100",
        occurred_at="2026-10-03T12:00:00-03:00")
    assert opened["state"] == "ACTIVE"
    assert opened["margin_reserved"] == "1500000"
    assert opened["real_routes_used"] == []
    assert future_cash_effect(store, "ARS", "2026-10-03T12:00:01-03:00") == Decimal("-1500100")

    mark = executor.mark_future(
        contract, lifecycle_id="FUT-1", event_id="MARK-1",
        mark_price="1510", book_at="2026-10-03T12:30:00-03:00",
        occurred_at="2026-10-03T12:30:01-03:00")
    assert Decimal(mark["unrealized_pnl"]) == Decimal("10000")
    risk = future_risk_snapshot(
        store, "ARS", "2026-10-03T12:30:30-03:00", max_mark_age_seconds=120)
    assert risk["realized"] == Decimal("-100")
    assert risk["unrealized"] == Decimal("10000")
    assert risk["collateral"] == Decimal("1500000")
    assert risk["stale"] is False
    assert risk["carry"] is False

    varied = executor.mark_future(
        contract, lifecycle_id="FUT-1", event_id="SETTLE-1",
        mark_price="1510", book_at="2026-10-03T13:00:00-03:00",
        occurred_at="2026-10-03T13:00:01-03:00", settlement=True,
        detail={"source": "EXPLICIT_TEST_SETTLEMENT"})
    assert Decimal(varied["variation_realized"]) == Decimal("10000")
    assert Decimal(varied["unrealized_pnl"]) == 0
    assert future_cash_effect(store, "ARS", "2026-10-03T13:00:02-03:00") == Decimal("-1490100")

    executor.mark_future(
        contract, lifecycle_id="FUT-1", event_id="MARK-2",
        mark_price="1520", book_at="2026-10-03T14:00:00-03:00",
        occurred_at="2026-10-03T14:00:01-03:00")
    closed = executor.close_future(
        contract, lifecycle_id="FUT-1", event_id="CLOSE-1",
        exit_price="1520", book_at="2026-10-03T14:20:00-03:00",
        occurred_at="2026-10-03T14:20:01-03:00",
        exit_cost="100", reason="EOD")
    assert closed["state"] == "CLOSED"
    assert Decimal(closed["realized_pnl"]) == Decimal("19800")
    assert future_cash_effect(store, "ARS", "2026-10-03T14:20:02-03:00") == Decimal("19800")

    repeated = executor.close_future(
        contract, lifecycle_id="FUT-1", event_id="CLOSE-1",
        exit_price="1520", book_at="2026-10-03T14:20:00-03:00",
        occurred_at="2026-10-03T14:20:01-03:00",
        exit_cost="100", reason="EOD")
    assert repeated["idempotent"] is True
    assert repeated["real_routes_used"] == []


def test_future_lifecycle_rejects_short_or_nonfull_notional_policy(tmp_path):
    store = Store(tmp_path / "future.sqlite")
    executor = FamilyPaperExecutor(store)
    contract = dlr()
    try:
        executor.open_future(
            contract, lifecycle_id="FUT-S", event_id="OPEN-S",
            entry_price="1500", quantity="1",
            occurred_at="2026-10-03T12:00:00-03:00", side="SHORT")
    except ValueError as exc:
        assert str(exc) == "FUTURES_PAPER_LONG_ONLY"
    else:
        raise AssertionError("short future must fail closed")

    partial = InstrumentContract(
        "DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
        Decimal("1000"), Decimal("1"), "TEST",
        expires_at="2026-10-30T15:00:00-03:00",
        minimum_quantity=Decimal("1"),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=Decimal("0.2"),
        underlying="DOLAR_A3500",
    )
    try:
        executor.open_future(
            partial, lifecycle_id="FUT-P", event_id="OPEN-P",
            entry_price="1500", quantity="1",
            occurred_at="2026-10-03T12:00:00-03:00")
    except ValueError as exc:
        assert str(exc) == "FUTURES_FULL_NOTIONAL_RESERVE_REQUIRED"
    else:
        raise AssertionError("partial notional reserve must fail closed")
