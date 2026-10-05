"""#462 FUTURES acceptance guards: durable PAPER lifecycle, risk and runtime.

Every database and provider response here is synthetic. These tests implement
the programming acceptance criteria; they perform no independent audit and
never connect to a broker, production database or runtime.
"""
import hashlib
import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import be_paper_engine as engine
import bv_paper_runtime as runtime
import rc6_paper_family_lifecycle as lifecycle
from be_paper_engine import PaperBroker, PaperStore, Quote
from bs_instrument_contracts import InstrumentContract
from bt_caucion_paper import CaucionOffer
from rc6_ppi_future_contract_policy import exit_due, standard_dlr_terms


D = Decimal
OPEN_AT = "2026-10-05T12:00:00-03:00"
MARK_AT = "2026-10-05T12:30:00-03:00"
VARIATION_AT = "2026-10-05T13:00:00-03:00"
CLOSE_AT = "2026-10-05T14:20:00-03:00"


@pytest.fixture(autouse=True)
def synthetic_ledger_policy(monkeypatch):
    # Multi-family reconciliation is separate from the existing W10 admission
    # tests. Its inputs do not claim a sector authority for synthetic positions.
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    monkeypatch.delenv("POROTA_RUNTIME_SCHEMA_READY", raising=False)


class TracedStore:
    def __init__(self, path):
        self.path = str(path)
        self.statements = []

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.set_trace_callback(self.statements.append)
        return connection


def dlr(symbol="DLR/OCT26", **changes):
    terms = standard_dlr_terms(symbol)
    contract = InstrumentContract(
        symbol, "FUTUROS", "ARS", "A3", "INMEDIATA",
        D("1000"), D("1"), "PPI_PRIMARY+A3_OFFICIAL:TEST:v1",
        expires_at=terms["expires_at"], minimum_quantity=D("1"),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=D("1"), underlying=terms["underlying"],
    )
    return replace(contract, **changes)


def quote(at=OPEN_AT, *, symbol="DLR/OCT26", bid="1499", ask="1500",
          contract=None, **changes):
    result = Quote(
        symbol, "FUTUROS", "INMEDIATA", D(bid), D(bid), D(ask),
        D("10"), D("10"), at, contract=contract or dlr(symbol),
        currency="ARS", market="A3", metadata_source="PPI_CATALOG:TEST",
        book_at=at, trade_at=at, last_kind="TRADE",
    )
    return replace(result, **changes)


def broker(tmp_path, clock, **changes):
    values = dict(
        initial_cash="10000000", initial_cash_by_currency={"USD": "1000"},
        risk_pct="0.005", max_positions=10, participation="1",
        max_position_pct="1", max_total_exposure_pct="1",
        clock_fn=lambda: clock[0], session_policy=None,
        require_supervisor=False, ai_mode="OFF", economics_mode="SHADOW",
        quote_max_age_seconds=120, trade_max_age_seconds=900,
        daily_loss_pct="5", daily_soft_stop_pct="4",
    )
    values.update(changes)
    return PaperBroker(PaperStore(str(tmp_path / "paper.sqlite")), **values)


def open_one(executor, *, contract=None, lifecycle_id="FUT-1",
             occurred_at=OPEN_AT, **changes):
    values = dict(lifecycle_id=lifecycle_id, event_id=lifecycle_id + ":OPEN",
                  entry_price="1500", quantity="1", entry_cost="100",
                  occurred_at=occurred_at)
    values.update(changes)
    return executor.open_future(contract or dlr(), **values)


def mark_one(executor, *, contract=None, lifecycle_id="FUT-1", **changes):
    values = dict(lifecycle_id=lifecycle_id, event_id=lifecycle_id + ":MARK",
                  mark_price="1510", book_at=MARK_AT, occurred_at=MARK_AT)
    values.update(changes)
    return executor.mark_future(contract or dlr(), **values)


def close_one(executor, *, contract=None, lifecycle_id="FUT-1", **changes):
    values = dict(lifecycle_id=lifecycle_id, event_id=lifecycle_id + ":CLOSE",
                  exit_price="1520", book_at=CLOSE_AT, occurred_at=CLOSE_AT,
                  exit_cost="100", reason="EOD")
    values.update(changes)
    return executor.close_future(contract or dlr(), **values)


def durable_rows(store):
    with store.connect() as connection:
        return {table: [tuple(row) for row in connection.execute(
            "SELECT * FROM " + table + " ORDER BY rowid")]
            for table in ("paper_family_lifecycle", "paper_family_lifecycle_events",
                          "paper_future_positions", "paper_future_marks")}


def schema_statements(statements):
    return [statement for statement in statements if statement.strip().upper().startswith(
        ("CREATE ", "ALTER ", "DROP ", "REINDEX "))
        or "INSERT OR REPLACE INTO PAPER_FAMILY_SCHEMA" in statement.upper()]


@pytest.mark.parametrize("reader", [
    lambda store: lifecycle.future_positions(store),
    lambda store: lifecycle.future_cash_effect(store, "ARS", OPEN_AT),
    lambda store: lifecycle.future_risk_snapshot(store, "ARS", OPEN_AT),
    lambda store: lifecycle.lifecycle_state(store, "not-opened"),
])
def test_public_family_readers_bootstrap_fresh_database_before_read(tmp_path, reader):
    store = TracedStore(tmp_path / "fresh.sqlite")
    reader(store)
    with store.connect() as connection:
        assert connection.execute("SELECT version FROM paper_family_schema").fetchone()[0] == 1
        for table in ("paper_future_positions", "paper_future_marks",
                      "paper_family_lifecycle_events"):
            assert connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
    assert schema_statements(store.statements)


def test_existing_database_double_init_preserves_other_state_and_open_lifecycle(tmp_path):
    store = TracedStore(tmp_path / "existing.sqlite")
    with store.connect() as connection:
        connection.execute("CREATE TABLE existing_runtime_state(value TEXT)")
        connection.execute("INSERT INTO existing_runtime_state VALUES('PAPER_ONLY')")
    executor = lifecycle.FamilyPaperExecutor(store)
    open_one(executor)
    before = durable_rows(store)
    lifecycle.init_schema(store)
    lifecycle.init_schema(store)
    assert durable_rows(store) == before
    with store.connect() as connection:
        assert connection.execute("SELECT value FROM existing_runtime_state").fetchone()[0] == "PAPER_ONLY"
    assert lifecycle.future_cash_effect(store, "ARS", OPEN_AT) == D("-1500100")


def test_hot_lifecycle_reads_and_writes_do_not_create_schema(tmp_path):
    store = TracedStore(tmp_path / "hot.sqlite")
    executor = lifecycle.FamilyPaperExecutor(store)
    store.statements.clear()
    lifecycle.ensure_initialized(store)
    lifecycle.FamilyPaperExecutor(store)
    open_one(executor)
    executor.active_future("DLR/OCT26")
    mark_one(executor)
    lifecycle.future_risk_snapshot(store, "ARS", MARK_AT)
    lifecycle.future_cash_effect(store, "ARS", MARK_AT)
    lifecycle.future_positions(store, "ARS", active_only=True)
    close_one(executor)
    assert schema_statements(store.statements) == []


def test_restarted_runtime_child_verifies_parent_schema_read_only(tmp_path, monkeypatch):
    path = tmp_path / "restart.sqlite"
    parent_store = TracedStore(path)
    open_one(lifecycle.FamilyPaperExecutor(parent_store))
    before = durable_rows(parent_store)
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "1")
    restarted = TracedStore(path)
    executor = lifecycle.FamilyPaperExecutor(restarted)
    assert executor.active_future("DLR/OCT26")["lifecycle_id"] == "FUT-1"
    assert lifecycle.future_cash_effect(restarted, "ARS", OPEN_AT) == D("-1500100")
    assert schema_statements(restarted.statements) == []
    assert durable_rows(restarted) == before


def test_runtime_child_without_parent_bootstrap_fails_closed_and_creates_no_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "1")
    store = TracedStore(tmp_path / "unmigrated.sqlite")
    with pytest.raises(RuntimeError, match="FUTURES_PARENT_SCHEMA_REQUIRED"):
        lifecycle.FamilyPaperExecutor(store)
    assert schema_statements(store.statements) == []


def test_paper_store_bootstrap_precedes_broker_daily_risk_and_child_restart(tmp_path, monkeypatch):
    clock = [OPEN_AT]
    b = broker(tmp_path, clock)
    assert b.daily_risk.evaluate(OPEN_AT)["ARS"]["state"] == "READY"
    opened, _, _ = b._open_future(quote(), D("0.8"), {})
    assert opened
    monkeypatch.setenv("POROTA_RUNTIME_SCHEMA_READY", "1")
    def forbid_schema(_store):
        raise AssertionError("runtime child attempted FUTURES DDL")
    monkeypatch.setattr(lifecycle, "init_schema", forbid_schema)
    child = broker(tmp_path, clock)
    assert len(lifecycle.future_positions(child.store, active_only=True)) == 1
    assert child.daily_risk.evaluate(OPEN_AT)["ARS"]["state"] == "READY"


def test_open_mark_variation_close_replay_survives_restart_and_cash_reconciles(tmp_path):
    path = tmp_path / "ledger.sqlite"
    store = TracedStore(path)
    executor = lifecycle.FamilyPaperExecutor(store)
    opened = open_one(executor)
    assert opened["idempotent"] is False
    assert open_one(executor)["idempotent"] is True
    assert lifecycle.future_cash_effect(store, "ARS", OPEN_AT) == D("-1500100")
    mark_one(executor)
    assert mark_one(executor)["idempotent"] is True
    varied = mark_one(executor, event_id="CUT-1", book_at=VARIATION_AT,
                      occurred_at=VARIATION_AT, settlement=True)
    assert D(varied["variation_realized"]) == D("10000")
    assert D(varied["unrealized_pnl"]) == 0
    before_restart = durable_rows(store)
    restarted_store = TracedStore(path)
    restarted = lifecycle.FamilyPaperExecutor(restarted_store)
    assert open_one(restarted)["idempotent"] is True
    assert mark_one(restarted, event_id="CUT-1", book_at=VARIATION_AT,
                    occurred_at=VARIATION_AT, settlement=True)["idempotent"] is True
    # A new request id for the same native book/cut cannot realize it twice.
    assert mark_one(restarted, event_id="CUT-RETRY", book_at=VARIATION_AT,
                    occurred_at=VARIATION_AT, settlement=True)["idempotent"] is True
    assert durable_rows(restarted_store) == before_restart
    assert lifecycle.future_cash_effect(restarted_store, "ARS", VARIATION_AT) == D("-1490100")
    risk = lifecycle.future_risk_snapshot(restarted_store, "ARS", VARIATION_AT)
    assert (risk["realized"], risk["unrealized"], risk["collateral"]) == (
        D("9900"), D("0"), D("1500000"))
    closed = close_one(restarted)
    assert closed["state"] == "CLOSED"
    assert D(closed["realized_pnl"]) == D("19800")
    final_rows = durable_rows(restarted_store)
    second_restart = lifecycle.FamilyPaperExecutor(TracedStore(path))
    assert close_one(second_restart)["idempotent"] is True
    assert durable_rows(second_restart.store) == final_rows
    assert lifecycle.future_cash_effect(second_restart.store, "ARS", CLOSE_AT) == D("19800")
    final_risk = lifecycle.future_risk_snapshot(second_restart.store, "ARS", CLOSE_AT)
    assert (final_risk["realized"], final_risk["unrealized"], final_risk["collateral"],
            final_risk["active_count"]) == (D("19800"), D("0"), D("0"), 0)
    events = final_rows["paper_family_lifecycle_events"]
    assert [row[4] for row in events].count("MARGIN_RESERVED") == 1
    assert [row[4] for row in events].count("DAILY_VARIATION") == 1
    assert [row[4] for row in events].count("CLOSE") == 1
    for result in (opened, varied, closed):
        assert result["paper_only"] is True and result["real_routes_used"] == []


@pytest.mark.parametrize("operation,changes,error", [
    ("open", {"entry_cost": "101"}, "FUTURES_OPEN_IDEMPOTENCY_MISMATCH"),
    ("open", {"entry_price": "1501"}, "FUTURES_LIFECYCLE_ID_COLLISION"),
    ("open", {"event_id": "DIFFERENT-OPEN"}, "FUTURES_OPEN_IDEMPOTENCY_MISMATCH"),
    ("mark", {"mark_price": "1511"}, "FUTURES_MARK_EVENT_COLLISION"),
    ("close", {"exit_cost": "101"}, "FUTURES_CLOSE_EVENT_COLLISION"),
    ("close", {"exit_price": "1521"}, "FUTURES_CLOSE_EVENT_COLLISION"),
])
def test_replayed_request_with_changed_financial_payload_is_rejected_atomically(tmp_path, operation, changes, error):
    executor = lifecycle.FamilyPaperExecutor(TracedStore(tmp_path / "collision.sqlite"))
    open_one(executor)
    if operation in {"mark", "close"}:
        mark_one(executor)
    if operation == "close":
        close_one(executor)
    before = durable_rows(executor.store)
    request = {"open": open_one, "mark": mark_one, "close": close_one}[operation]
    with pytest.raises(ValueError, match=error):
        request(executor, **changes)
    assert durable_rows(executor.store) == before


@pytest.mark.parametrize("operation", ["mark", "close"])
def test_idempotent_replay_cannot_replace_original_contract_provenance(tmp_path, operation):
    executor = lifecycle.FamilyPaperExecutor(TracedStore(tmp_path / "provenance.sqlite"))
    open_one(executor)
    mark_one(executor)
    if operation == "close":
        close_one(executor)
    before = durable_rows(executor.store)
    request = {"mark": mark_one, "close": close_one}[operation]
    with pytest.raises(ValueError, match="FUTURES_CONTRACT_POSITION_MISMATCH"):
        request(executor, contract=dlr(metadata_source="CHANGED_CURRENT_PROVIDER"))
    assert durable_rows(executor.store) == before


def test_historical_risk_cuts_use_original_open_and_events_without_future_information(tmp_path):
    executor = lifecycle.FamilyPaperExecutor(TracedStore(tmp_path / "asof.sqlite"))
    open_one(executor)
    mark_one(executor)
    mark_one(executor, event_id="CUT-1", book_at=VARIATION_AT,
             occurred_at=VARIATION_AT, settlement=True)
    close_one(executor)
    before_open = lifecycle.future_risk_snapshot(executor.store, "ARS", "2026-10-05T11:59:59-03:00")
    assert before_open["active_count"] == 0 and before_open["realized"] == 0
    opening = lifecycle.future_risk_snapshot(executor.store, "ARS", OPEN_AT)
    assert opening["active_count"] == 1 and opening["stale"] is False
    assert opening["unrealized"] == 0 and opening["realized"] == D("-100")
    marked = lifecycle.future_risk_snapshot(executor.store, "ARS", MARK_AT)
    assert marked["realized"] == D("-100") and marked["unrealized"] == D("10000")
    assert marked["collateral"] == D("1500000")
    varied = lifecycle.future_risk_snapshot(executor.store, "ARS", VARIATION_AT)
    assert varied["realized"] == D("9900") and varied["unrealized"] == 0
    assert varied["cash_effect"] == D("-1490100")


@pytest.mark.parametrize("book_at,occurred_at,error", [
    ("2026-10-05T12:30:01-03:00", MARK_AT, "FUTURES_BOOK_TIME_FUTURE"),
    ("2026-10-05T12:27:59-03:00", MARK_AT, "FUTURES_BOOK_STALE"),
    ("2026-10-05T12:20:00-03:00", "2026-10-05T12:20:00-03:00", "FUTURES_CLOCK_ROLLBACK"),
])
@pytest.mark.parametrize("operation", ["mark", "close"])
def test_future_stale_and_rollback_books_never_mutate_ledger(tmp_path, book_at, occurred_at, error, operation):
    executor = lifecycle.FamilyPaperExecutor(TracedStore(tmp_path / "clock.sqlite"))
    open_one(executor)
    mark_one(executor)
    before = durable_rows(executor.store)
    request = {"mark": mark_one, "close": close_one}[operation]
    with pytest.raises(ValueError, match=error):
        request(executor, event_id="REJECTED", book_at=book_at, occurred_at=occurred_at)
    assert durable_rows(executor.store) == before


@pytest.mark.parametrize("book_at", ["2026-10-05T12:00:01-03:00", "2026-10-05T11:57:59-03:00"])
def test_open_rejects_future_and_stale_native_book_before_reserving(tmp_path, book_at):
    executor = lifecycle.FamilyPaperExecutor(TracedStore(tmp_path / "open-time.sqlite"))
    with pytest.raises(ValueError, match="FUTURES_OPEN_BOOK_STALE_OR_FUTURE"):
        open_one(executor, book_at=book_at)
    assert lifecycle.future_positions(executor.store) == []
    assert lifecycle.future_cash_effect(executor.store, "ARS", OPEN_AT) == 0


def test_daily_risk_fresh_stale_missing_marks_and_opening_block(tmp_path):
    clock = [OPEN_AT]
    b = broker(tmp_path, clock)
    open_one(b.family_paper)
    fresh = b.daily_risk.evaluate(OPEN_AT)["ARS"]
    assert fresh["state"] == "READY" and D(fresh["daily_pnl"]) == D("-100")
    assert fresh["limit_pct"] == "5" and D(fresh["loss_budget"]) == D("500000")
    clock[0] = "2026-10-05T12:03:00-03:00"
    stale = b.daily_risk.evaluate(clock[0])["ARS"]
    assert stale["state"] == "STALE_MARKS" and stale["daily_pnl"] is None
    opened, reason, _ = b._open_future(quote(clock[0], symbol="DLR/NOV26"), D("0.8"), {})
    assert opened is False and reason == "DAILY_RISK_STALE_MARKS"
    mark_one(b.family_paper)
    clock[0] = MARK_AT
    assert b.daily_risk.evaluate(clock[0])["ARS"]["state"] == "READY"
    with b.store.connect() as connection:
        connection.execute("DELETE FROM paper_future_marks")
        row = connection.execute("SELECT metadata_json FROM paper_future_positions").fetchone()
        metadata = json.loads(row[0])
        metadata.pop("opening_book_at", None)
        connection.execute("UPDATE paper_future_positions SET last_book_at=NULL,metadata_json=?",
                           (json.dumps(metadata),))
    missing = b.daily_risk.evaluate(clock[0])["ARS"]
    assert missing["state"] == "STALE_MARKS" and missing["daily_pnl"] is None
    snapshot = lifecycle.future_risk_snapshot(b.store, "ARS", clock[0])
    assert snapshot["stale"] is True and snapshot["mark_timestamps"] == [None]


def test_multiple_exact_dlr_expiries_keep_currency_risk_and_cash_separate(tmp_path):
    clock = [OPEN_AT]
    b = broker(tmp_path, clock)
    open_one(b.family_paper)
    november = dlr("DLR/NOV26")
    open_one(b.family_paper, contract=november, lifecycle_id="FUT-NOV", quantity="2", entry_cost="200")
    mark_one(b.family_paper)
    mark_one(b.family_paper, contract=november, lifecycle_id="FUT-NOV", mark_price="1495")
    snapshot = lifecycle.future_risk_snapshot(b.store, "ARS", MARK_AT)
    assert snapshot["active_count"] == 2 and snapshot["stale"] is False
    assert snapshot["collateral"] == D("4500000")
    assert snapshot["realized"] == D("-300") and snapshot["unrealized"] == 0
    assert snapshot["exposure"] == D("4500000")
    assert b._cash(as_of=MARK_AT, currency="ARS") == D("5499700")
    assert b._cash(as_of=MARK_AT, currency="USD") == D("1000")
    rows = lifecycle.future_positions(b.store, active_only=True)
    assert {row["expires_at"] for row in rows} == {
        "2026-10-30T15:00:00-03:00", "2026-11-30T15:00:00-03:00"}
    risks = b.daily_risk.evaluate(MARK_AT)
    assert D(risks["ARS"]["daily_pnl"]) == D("-300")
    assert risks["USD"]["state"] == "READY" and D(risks["USD"]["daily_pnl"]) == 0
    usd = lifecycle.future_risk_snapshot(b.store, "USD", MARK_AT)
    assert usd["active_count"] == 0 and usd["cash_effect"] == 0


@pytest.mark.parametrize("limit,mark_price,state,admission", [
    ("0.5", "1449", "READY", "DAILY_RISK_SOFT_STOP"),
    ("1", "1399", "LATCHED", "DAILY_RISK_LATCHED"),
])
def test_future_pnl_respects_existing_soft_and_hard_daily_stop(tmp_path, limit, mark_price, state, admission):
    clock = [OPEN_AT]
    b = broker(tmp_path, clock, daily_loss_pct="1", daily_soft_stop_pct="0.5")
    open_one(b.family_paper)
    b.daily_risk.evaluate(OPEN_AT)
    mark_one(b.family_paper, mark_price=mark_price)
    current = b.daily_risk.evaluate(MARK_AT)["ARS"]
    assert current["state"] == state and current["limit_pct"] == "1"
    assert D(current["daily_pnl"]) <= -D("10000000") * D(limit) / 100
    assert b.daily_risk.admission_error("ARS", MARK_AT) == admission
    if state == "LATCHED":
        mark_one(b.family_paper, event_id="RECOVERY", mark_price="1520",
                 book_at="2026-10-05T12:31:00-03:00", occurred_at="2026-10-05T12:31:00-03:00")
        recovered = b.daily_risk.evaluate("2026-10-05T12:31:00-03:00")["ARS"]
        assert recovered["state"] == "LATCHED" and recovered["latched_at"] == current["latched_at"]


def test_future_daily_risk_and_execution_cash_fail_closed_after_clock_rollback(tmp_path):
    clock = [OPEN_AT]
    b = broker(tmp_path, clock)
    open_one(b.family_paper)
    mark_one(b.family_paper, event_id="CUT", settlement=True,
             book_at=VARIATION_AT, occurred_at=VARIATION_AT)
    b.daily_risk.evaluate(VARIATION_AT)
    rollback = b.daily_risk.evaluate(OPEN_AT)["ARS"]
    assert rollback["state"] == "CLOCK_ROLLBACK"
    with pytest.raises(ValueError, match="CASH_CLOCK_ROLLBACK"):
        b._cash(as_of=OPEN_AT, currency="ARS", for_execution=True)
    assert b._cash(as_of=OPEN_AT, currency="ARS") == D("8499900")


def test_snapshot_provenance_stays_immutable_across_marks_close_and_restart(tmp_path):
    path = tmp_path / "immutable.sqlite"
    executor = lifecycle.FamilyPaperExecutor(TracedStore(path))
    open_one(executor, detail={"provider_identity": "PPI_PRIMARY", "provider_contract_version": "v1"})
    original = json.loads(executor.active_future("DLR/OCT26")["metadata_json"])
    encoded = json.dumps(original["financial_contract"], sort_keys=True, separators=(",", ":"))
    assert original["contract_snapshot_sha256"] == hashlib.sha256(encoded.encode()).hexdigest()
    mark_one(executor)
    close_one(executor)
    restarted = TracedStore(path)
    row = lifecycle.future_positions(restarted)[0]
    final = json.loads(row["metadata_json"])
    assert final["financial_contract"] == original["financial_contract"]
    assert final["contract_snapshot_sha256"] == original["contract_snapshot_sha256"]
    assert final["provider_contract_version"] == "v1"
    restored = lifecycle.future_position_contract(row)
    assert restored == dlr()


@pytest.mark.parametrize("corruption", ["missing", "changed_provenance", "bad_digest"])
def test_corrupt_contract_snapshot_blocks_restore_risk_mark_and_close(tmp_path, corruption):
    executor = lifecycle.FamilyPaperExecutor(TracedStore(tmp_path / "corrupt.sqlite"))
    open_one(executor)
    row = executor.active_future("DLR/OCT26")
    metadata = json.loads(row["metadata_json"])
    if corruption == "missing":
        metadata.pop("financial_contract")
    elif corruption == "changed_provenance":
        metadata["financial_contract"]["metadata_source"] = "UNPROVED_PROVIDER"
    else:
        metadata["contract_snapshot_sha256"] = "0" * 64
    with executor.store.connect() as connection:
        connection.execute("UPDATE paper_future_positions SET metadata_json=?", (json.dumps(metadata),))
    before = durable_rows(executor.store)
    row = executor.active_future("DLR/OCT26")
    for request in (lambda: lifecycle.future_position_contract(row),
                    lambda: lifecycle.future_risk_snapshot(executor.store, "ARS", MARK_AT),
                    lambda: mark_one(executor), lambda: close_one(executor)):
        with pytest.raises(ValueError, match="FUTURES_"):
            request()
    assert durable_rows(executor.store) == before


@pytest.mark.parametrize("live_contract", [None, "changed"])
def test_restored_exit_after_restart_uses_open_contract_when_current_changes(tmp_path, live_contract):
    clock = [OPEN_AT]
    b = broker(tmp_path, clock)
    opened, _, lifecycle_id = b._open_future(quote(), D("0.8"), {})
    assert opened
    original = json.loads(b.family_paper.active_future("DLR/OCT26")["metadata_json"])
    b = broker(tmp_path, clock)
    clock[0] = "2026-10-05T14:50:00-03:00"
    current = None if live_contract is None else dlr(
        metadata_source="CHANGED_CURRENT_SOURCE", cash_multiplier=D("2000"),
        expires_at="2026-11-30T15:00:00-03:00")
    q = replace(quote(clock[0], bid="1510", ask="1511"), contract=current)
    b.on_quote(q, allow_new_openings=False)
    assert b.family_paper.active_future("DLR/OCT26") is None
    row = lifecycle.future_positions(b.store)[0]
    final = json.loads(row["metadata_json"])
    assert row["lifecycle_id"] == lifecycle_id and row["status"] == "CLOSED"
    assert final["financial_contract"] == original["financial_contract"]
    assert final["contract_snapshot_sha256"] == original["contract_snapshot_sha256"]
    assert final["terminal_state"] == "CLOSE"
    with b.store.connect() as connection:
        events = [dict(event) for event in connection.execute("SELECT * FROM paper_family_lifecycle_events")]
        assert connection.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
        assert connection.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM http_audit").fetchone()[0] == 0
    assert sum(event["to_state"] == "DAILY_VARIATION" for event in events) == 1
    assert sum(event["to_state"] == "CLOSE" for event in events) == 1
    for event in events:
        detail = json.loads(event["detail_json"])
        assert detail["mode"] == "PRODUCTION_PAPER" and detail["execution"] == "SIMULATION"
        assert detail["real_routes_used"] == []
    net = D(final["realized_pnl"])
    assert b._cash(as_of=clock[0], currency="ARS") == D("10000000") + net
    final_rows = durable_rows(b.store)
    b.on_quote(q, allow_new_openings=False)
    assert durable_rows(b.store) == final_rows


def test_expiry_is_not_due_during_opening_hours_of_expiry_day(tmp_path):
    contract = dlr()
    assert exit_due("2026-10-30T12:00:00-03:00", contract) is False
    assert exit_due("2026-10-30T14:49:00-03:00", contract) is False
    assert exit_due("2026-10-30T14:50:00-03:00", contract) is True
    executor = lifecycle.FamilyPaperExecutor(TracedStore(tmp_path / "expiry.sqlite"))
    open_one(executor, occurred_at="2026-10-30T12:00:00-03:00")
    before = durable_rows(executor.store)
    with pytest.raises(ValueError, match="FUTURES_EXPIRY_NOT_DUE"):
        close_one(executor, expiry=True, book_at="2026-10-30T14:59:59-03:00",
                  occurred_at="2026-10-30T14:59:59-03:00")
    assert durable_rows(executor.store) == before
    args = dict(expiry=True, reason="EXPIRY", book_at=contract.expires_at,
                occurred_at=contract.expires_at)
    closed = close_one(executor, **args)
    assert closed["state"] == "CLOSED" and closed["realized_pnl"] == "19800"
    assert lifecycle.lifecycle_state(executor.store, "FUT-1")["state"] == "EXPIRY"
    assert close_one(executor, **args)["idempotent"] is True
    assert lifecycle.future_cash_effect(executor.store, "ARS", contract.expires_at) == D("19800")


def test_live_future_signal_has_specialized_authority_and_never_calls_equity_decide(tmp_path, monkeypatch):
    clock = [OPEN_AT]
    b = broker(tmp_path, clock)
    def forbid_equity(_quote):
        raise AssertionError("equity decide() acquired FUTURES authority")
    monkeypatch.setattr(b, "decide", forbid_equity)
    start = datetime.fromisoformat(OPEN_AT)
    for minute in range(8):
        at = (start + timedelta(minutes=minute)).isoformat()
        q = quote(at, bid=str(1450 + minute * 10), ask=str(1451 + minute * 10))
        b.store.add_quote(q)
    clock[0] = q.observed_at
    b.on_quote(q)
    assert len(lifecycle.future_positions(b.store, active_only=True)) == 1
    with b.store.connect() as connection:
        decision = connection.execute("SELECT * FROM paper_decisions").fetchone()
        gates = connection.execute("SELECT * FROM trade_gate_evaluations").fetchone()
        assert decision["action"] == "BUY"
        features = json.loads(decision["features_json"])
        assert features["strategy"] == "futures-dlr-paper-v1"
        assert features["score_is_probability"] is False
        assert gates["final_result"] == "OPENED_SIMULATED"
        assert connection.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0] == 0
        assert connection.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_exit_book_collector_reads_active_futures_and_restores_exact_snapshot(tmp_path, monkeypatch):
    import bf_production_paper_observer as observer
    import bu_instrument_catalog as catalog
    clock = [OPEN_AT]
    b = broker(tmp_path, clock)
    open_one(b.family_paper)
    calls = []
    class Reader:
        def book(self, symbol, family, settlement):
            calls.append((symbol, family, settlement))
            return {"date": MARK_AT, "bid": "1510", "ask": "1511",
                    "bidsize": "5", "asksize": "5"}
        def current(self, *_args):
            raise AssertionError("exit required last-trade current instead of native book")
    class Policy:
        def execution_error(self, *_args):
            raise AssertionError("equity exit policy acquired FUTURES authority")
    monkeypatch.setattr(observer, "now_iso", lambda: MARK_AT)
    monkeypatch.setattr(catalog, "lookup", lambda *_args, **_kwargs: None)
    assert runtime.collect_exit_books(Reader(), b.store, Policy(), MARK_AT) == 0
    assert calls == [("DLR/OCT26", "FUTUROS", "INMEDIATA")]
    q = b.store.latest_quote({"symbol": "DLR/OCT26", "asset_class": "FUTUROS", "settlement": "INMEDIATA"})
    assert q.contract == dlr() and q.market == "A3" and q.currency == "ARS"
    assert q.book_at == MARK_AT and q.bid == D("1510")
    assert q.opening_block_reason == "FUTURES_EXIT_ONLY_DURABLE_CONTRACT"


def test_canonical_runtime_clock_supervises_open_future_without_scanner(tmp_path, monkeypatch):
    clock = [OPEN_AT]
    b = broker(tmp_path, clock)
    open_one(b.family_paper)
    clock[0] = "2026-10-05T14:50:00-03:00"
    b.store.add_quote(quote(clock[0], bid="1510", ask="1511", opening_block_reason="EXIT_ONLY"))
    events = []
    original = b.supervise_futures
    def supervise(at):
        events.append(("future_tick", at))
        original(at)
    monkeypatch.setattr(b, "supervise_futures", supervise)
    monkeypatch.setattr(runtime, "broker_from_environment", lambda *_args, **_kwargs: b)
    class Supervisor:
        def __init__(self, *_args, **_kwargs):
            pass
        def tick(self):
            events.append(("spot_tick", clock[0]))
        def heartbeat(self, _at, state, _detail):
            events.append(("heartbeat", state))
    class Stop:
        stopped = False
        def is_set(self):
            return self.stopped
        def wait(self, _interval):
            self.stopped = True
    class Children:
        def poll(self):
            events.append(("children", "poll"))
        def close(self):
            events.append(("children", "closed"))
    monkeypatch.setattr(runtime, "PositionExitSupervisor", Supervisor)
    runtime.run_clock(b.store, Children(), Stop(), clock_fn=lambda: clock[0], interval=0)
    assert ("future_tick", clock[0]) in events
    assert b.family_paper.active_future("DLR/OCT26") is None
    assert lifecycle.future_positions(b.store)[0]["close_reason"] == "EOD_PAPER"
    assert ("heartbeat", "DEGRADED") not in events and ("children", "closed") in events


def test_future_valuation_includes_unrealized_once_before_and_after_variation(tmp_path):
    clock = [OPEN_AT]
    b = broker(tmp_path, clock)
    open_one(b.family_paper)
    mark_one(b.family_paper)
    marked = b.mark_equity({}, as_of=MARK_AT)["ARS"]
    assert marked["cash"] == D("8499900") and marked["exposure"] == D("1500000")
    assert marked["realized_pnl"] == D("-100") and marked["unrealized_pnl"] == D("10000")
    assert marked["equity"] == D("10009900")
    mark_one(b.family_paper, event_id="CUT", settlement=True,
             book_at=VARIATION_AT, occurred_at=VARIATION_AT)
    varied = b.mark_equity({}, as_of=VARIATION_AT)["ARS"]
    assert varied["cash"] == D("8509900") and varied["exposure"] == D("1500000")
    assert varied["unrealized_pnl"] == 0 and varied["realized_pnl"] == D("9900")
    assert varied["equity"] == marked["equity"]
    close_one(b.family_paper)
    closed = b.mark_equity({}, as_of=CLOSE_AT)["ARS"]
    assert closed["cash"] == closed["equity"] == D("10019800")
    assert closed["exposure"] == closed["unrealized_pnl"] == 0
    assert closed["realized_pnl"] == D("19800")


def test_futures_spot_and_caucion_reconcile_without_counting_reserves_as_losses(tmp_path):
    clock = [OPEN_AT]
    b = broker(tmp_path, clock)
    equity_q = Quote("GGAL", "ACCIONES", "INMEDIATA", D("100"), D("99.9"), D("100.1"),
                     D("10"), D("10"), OPEN_AT, currency="ARS", market="BYMA",
                     metadata_source="TEST", book_at=OPEN_AT, trade_at=OPEN_AT, last_kind="TRADE")
    b.store.add_quote(equity_q)
    assert b._open(equity_q, D("0.8"), {})[0]
    spot = b.store.open_position("GGAL")
    offer = CaucionOffer(
        instrument_id="CAUCION-ARS-TEST", currency="ARS", annual_rate_fraction=D("0.50"),
        start_date="2026-10-05", maturity_at="2026-10-06T15:00:00-03:00",
        quoted_at=OPEN_AT, available_principal=D("100000"), minimum_principal=D("1000"),
        principal_step=D("100"), day_count_basis=365, fee_payment="MATURITY",
        metadata_source="TEST", quoted_total_fees=D("1"), fee_quote_principal=D("1000"))
    caucion = b.place_caucion(offer, "1000", "CAUCION-1", as_of=OPEN_AT)
    open_one(b.family_paper)
    same_time = "2026-10-05T12:01:00-03:00"
    b.family_paper.mark_future(dlr(), lifecycle_id="FUT-1", event_id="CUT", mark_price="1510",
                              book_at=same_time, occurred_at=same_time, settlement=True)
    equity_q = replace(equity_q, observed_at=same_time, book_at=same_time, trade_at=same_time)
    risk = b.daily_risk.evaluate(same_time, quotes={"GGAL": equity_q})["ARS"]
    spot_exit = (equity_q.bid * (1 - b.slippage)).quantize(D("0.0001"))
    spot_net = ((spot_exit - D(spot["entry_price"])) * D(spot["quantity"])
                - D(spot["entry_cost"]) - b._cost(spot_exit, D(spot["quantity"]), "ACCIONES"))
    assert risk["state"] == "READY"
    assert D(risk["daily_pnl"]) == spot_net + D("9900") - D(caucion["total_fees"])
    spot_commitment = D(spot["entry_price"]) * D(spot["quantity"]) + D(spot["entry_cost"])
    assert b._cash(as_of=same_time) == D("10000000") - spot_commitment - D("1000") - D("1490100")
    close_one(b.family_paper)
    final_q = replace(equity_q, observed_at=CLOSE_AT, book_at=CLOSE_AT, trade_at=CLOSE_AT)
    final = b.daily_risk.evaluate(CLOSE_AT, quotes={"GGAL": final_q})["ARS"]
    assert D(final["daily_pnl"]) == spot_net + D("19800") - D(caucion["total_fees"])
    assert b._cash(as_of=CLOSE_AT) == D("10000000") - spot_commitment - D("1000") + D("19800")
    assert b._cash(as_of=CLOSE_AT, currency="USD") == D("1000")
