"""Independent finance review of native core callers on the integrated tree.

Synthetic books and temporary ledgers only. The integrator owns core code;
these tests retain adverse inputs and positive controls for its fixes.
"""
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal as D
import hashlib
import json
from zoneinfo import ZoneInfo

import pytest

from be_paper_engine import PaperBroker, PaperStore, Quote
from bs_instrument_contracts import InstrumentContract
from rc6_performance.costs import ledger_leg_cost
from rc6_ppi_future_contract_policy import standard_dlr_terms
from rc6_paper_family_lifecycle import future_position_contract, future_risk_snapshot


OPEN = "2026-10-05T12:00:00-03:00"
CLOSE = "2026-10-05T12:01:00-03:00"


@pytest.fixture(autouse=True)
def offline_policy(monkeypatch):
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")
    monkeypatch.delenv("POROTA_RUNTIME_SCHEMA_READY", raising=False)
    monkeypatch.delenv("FEE_ACCIONES_COMISION", raising=False)
    monkeypatch.delenv("PAPER_T1_FULL_DATE_RELEASE", raising=False)


def contract():
    terms = standard_dlr_terms("DLR/OCT26")
    return InstrumentContract("DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
        D("1000"), D("1"), "PPI_PRIMARY+A3_OFFICIAL:SYNTHETIC_REVIEW",
        expires_at=terms["expires_at"], underlying=terms["underlying"],
        minimum_quantity=D("1"), paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=D("1"))


def quote(at=OPEN, *, bid="1499.5", ask="1500", future=True):
    return Quote("DLR/OCT26" if future else "GGAL", "FUTUROS" if future else "ACCIONES",
        "INMEDIATA" if future else "A-24HS", D(bid), D(bid), D(ask), D("1000"),
        D("1000"), at, contract=contract() if future else None, currency="ARS",
        market="A3" if future else "BYMA", metadata_source="PPI_CATALOG:SYNTHETIC_REVIEW",
        book_at=at, trade_at=at, last_kind="TRADE")


def broker(path, clock, *, economics_mode="SHADOW"):
    return PaperBroker(PaperStore(str(path)), initial_cash="100000000",
        risk_pct="0.005", max_positions=10, participation="1",
        max_position_pct="1", max_total_exposure_pct="1", slippage_bps="0",
        clock_fn=lambda: clock[0], session_policy=None, require_supervisor=False,
        ai_mode="OFF", economics_mode=economics_mode, stop_loss_pct=".02",
        target_gain_pct=".10", daily_loss_pct="5", daily_soft_stop_pct="4")


def open_future(engine):
    opened, reason, _ = engine._open_future(quote(), D(".8"), {})
    assert opened, reason
    return engine.store.active_future_positions()[0]


@pytest.mark.parametrize("bid,ask", [("1480.123", "1480.5"), ("1480", "1479")])
def test_u09_direct_future_close_rejects_off_grid_or_crossed_book_with_valid_control(tmp_path, bid, ask):
    clock = [OPEN]
    engine = broker(tmp_path / "future.sqlite", clock)
    position = open_future(engine)
    clock[0] = CLOSE
    assert not engine._close_future(quote(CLOSE, bid=bid, ask=ask), position, "STOP_PAPER")
    assert len(engine.store.active_future_positions()) == 1
    assert engine._close_future(quote(CLOSE, bid="1480", ask="1480.5"), position, "STOP_PAPER")
    assert not engine.store.active_future_positions()
    with engine.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events "
            "WHERE to_state='CLOSE'").fetchone()[0] == 1


@pytest.mark.parametrize("source_future", [True, False])
def test_u10_quote_clock_distinguishes_zoneinfo_fold_instants(source_future):
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    second = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    q = quote(future=False)
    if source_future:
        q = replace(q, observed_at=first, book_at=second, trade_at=first)
        assert q.time_error(first, require_trade=True) == "BOOK_TIME_FUTURE"
    else:
        q = replace(q, observed_at=first, book_at=first, trade_at=first)
        assert q.time_error(second, require_trade=True) == "RECEIPT_STALE_OR_FUTURE"
    fresh = replace(q, observed_at=second, book_at=second, trade_at=second)
    assert fresh.time_error(second, require_trade=True) == ""


@pytest.mark.parametrize("economics_mode", ["SHADOW", "BINDING"])
def test_u06_fee_policy_change_before_commit_cannot_mix_fill_diagnostics_and_risk(tmp_path, monkeypatch,
                                                                             economics_mode):
    clock = [OPEN]
    engine = broker(tmp_path / "spot.sqlite", clock, economics_mode=economics_mode)
    canonical = engine._economic_admission
    calls = 0

    def change_policy_at_locked_admission(q, features):
        nonlocal calls
        calls += 1
        if calls == 2:
            monkeypatch.setenv("FEE_ACCIONES_COMISION", ".010")
        return canonical(q, features)

    monkeypatch.setattr(engine, "_economic_admission", change_policy_at_locked_admission)
    features = {}
    opened, reason, _ = engine.admit_paper_candidate(
        quote(bid="107.5", ask="107.55", future=False), D(".8"), features)
    assert calls >= 2
    with engine.store.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
    if not opened:
        assert reason and count == 0
        return
    assert count == 1
    position = engine.store.open_positions()[0]
    entry, qty = D(position["entry_price"]), D(position["quantity"])
    expected_cost = ledger_leg_cost(entry, qty, "ACCIONES")
    assert D(position["entry_cost"]) == expected_cost
    stop_fill = (D(position["stop_price"]) * (1 - engine.slippage)).quantize(D(".0001"))
    expected_risk = ((entry - stop_fill) * qty + expected_cost
                     + ledger_leg_cost(stop_fill, qty, "ACCIONES"))
    assert D(features["candidate_stop_risk"]) == expected_risk
    assert features["economics"]["cost_contract"]["components"]["commission"] == "0.01"


@pytest.mark.parametrize("currency,market", [("USD_MEP", "A3"), ("ARS", "OTHER")])
def test_u08_future_supervisor_after_restart_uses_exact_book_amid_newer_wrong_identity(tmp_path,
                                                                                    currency, market):
    clock = [OPEN]
    path = tmp_path / "future.sqlite"
    engine = broker(path, clock)
    open_future(engine)
    clock[0] = CLOSE
    exact = quote(CLOSE, bid="1469", ask="1469.5")
    engine.store.add_quote(exact)
    newer = (datetime.fromisoformat(CLOSE) + timedelta(microseconds=1)).isoformat()
    engine.store.add_quote(replace(quote(newer), currency=currency, market=market, contract=None))
    restarted = broker(path, clock)
    restarted.supervise_futures(CLOSE)
    assert not restarted.store.active_future_positions()
    with restarted.store.connect() as connection:
        terminal = connection.execute("SELECT detail_json FROM paper_family_lifecycle_events "
            "WHERE to_state='CLOSE'").fetchall()
    assert len(terminal) == 1 and "1469" in terminal[0][0]


def test_u08_future_partial_depth_does_not_create_terminal_fill_and_recovers_on_later_full_book(tmp_path):
    clock = [OPEN]
    engine = broker(tmp_path / "future.sqlite", clock)
    position = open_future(engine)
    clock[0] = CLOSE
    incomplete = replace(quote(CLOSE, bid="1469", ask="1469.5"), bid_size=D("0.5"))
    assert not engine._close_future(incomplete, position, "STOP_PAPER")
    assert len(engine.store.active_future_positions()) == 1
    with engine.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events "
            "WHERE to_state='CLOSE'").fetchone()[0] == 0
    later = (datetime.fromisoformat(CLOSE) + timedelta(seconds=1)).isoformat()
    clock[0] = later
    assert engine._close_future(quote(later, bid="1469", ask="1469.5"), position, "STOP_PAPER")
    assert not engine.store.active_future_positions()


def test_u06_positive_binding_fill_costs_each_leg_once_and_retains_atomic_decision_clock(tmp_path, monkeypatch):
    clock = [OPEN]
    engine = broker(tmp_path / "spot.sqlite", clock, economics_mode="BINDING")
    features = {}
    opened, reason, paper_id = engine.admit_paper_candidate(
        quote(bid="107.5", ask="107.55", future=False), D(".8"), features)
    assert opened, reason
    position = engine.store.open_positions()[0]
    entry, qty = D(position["entry_price"]), D(position["quantity"])
    entry_cost = ledger_leg_cost(entry, qty, "ACCIONES")
    assert D(position["entry_cost"]) == entry_cost
    with engine.store.connect() as connection:
        snapshot = connection.execute("SELECT payload_sha256,payload_json "
            "FROM decision_evidence_snapshots WHERE decision_key=?", ("PAPER_FILL:" + paper_id,)).fetchone()
    assert snapshot[0] == hashlib.sha256(snapshot[1].encode()).hexdigest()
    evidence = json.loads(snapshot[1])
    assert evidence["capture_phase"] == "ATOMIC_PAPER_ADMISSION"
    assert evidence["admission_at"] == evidence["entry_fill_recorded_at"] == evidence["captured_at"] == OPEN
    assert evidence["entry_fill_committed_at"] is None
    assert evidence["runtime"].get("entry_fill_committed_at") is None
    assert evidence["inputs_used"]["economics"]["passed"] is True
    assert (evidence["inputs_used"]["economics"]["cost_contract"]["policy_sha256"]
            == features["economics"]["cost_contract"]["policy_sha256"])
    assert engine._cash(as_of=OPEN, currency="ARS") == engine.initial_cash - entry * qty - entry_cost
    clock[0] = CLOSE
    assert engine._close(position, quote(CLOSE, bid="108", ask="108.05", future=False), "TEST_CONTROL")
    with engine.store.connect() as connection:
        closed = connection.execute("SELECT gross_pnl,net_pnl,exit_cost FROM paper_positions "
            "WHERE paper_id=?", (paper_id,)).fetchone()
        receipt = connection.execute("SELECT available_at,basis FROM paper_sale_receivables "
            "WHERE paper_id=?", (paper_id,)).fetchone()
        fills = connection.execute("SELECT costs FROM paper_fills WHERE paper_id=? ORDER BY id", (paper_id,)).fetchall()
    exit_cost = ledger_leg_cost("108", qty, "ACCIONES")
    gross, net = (D("108") - entry) * qty, (D("108") - entry) * qty - entry_cost - exit_cost
    assert tuple(map(D, closed)) == (gross, net, exit_cost)
    assert [D(fill[0]) for fill in fills] == [entry_cost, exit_cost]
    assert engine._cash(as_of=CLOSE, currency="ARS") == engine.initial_cash - entry * qty - entry_cost
    assert tuple(receipt) == (None, "PENDING_CONFIRMATION")
    from cf_sale_settlement import conservative_unconfirmed_availability
    release = conservative_unconfirmed_availability("A-24HS", CLOSE)
    assert release is not None
    assert engine._cash(as_of=release, currency="ARS") == engine.initial_cash - entry * qty - entry_cost
    monkeypatch.setenv("PAPER_T1_FULL_DATE_RELEASE", "true")
    assert (engine._cash(as_of=release - timedelta(microseconds=1), currency="ARS")
            == engine.initial_cash - entry * qty - entry_cost)
    assert engine._cash(as_of=release, currency="ARS") == engine.initial_cash + net
    assert engine._cash(as_of=release, currency="USD_MEP") == 0


@pytest.mark.parametrize("future", [False, True])
def test_aud19_native_decision_key_cannot_silently_cover_two_financial_commits(tmp_path, future):
    clock = [OPEN]
    engine = broker(tmp_path / "paper.sqlite", clock, economics_mode="SHADOW" if future else "BINDING")
    key = "SYNTHETIC:NATIVE-ONCE"
    receipt_key = "PAPER_ADMISSION:"+key
    entry = quote() if future else quote(bid="107.5", ask="107.55", future=False)
    first = engine.admit_paper_candidate(entry, D(".8"), {"native_decision_key": key})
    assert first[0], first[1]
    position = (engine.store.active_future_positions() if future else engine.store.open_positions())[0]
    clock[0] = CLOSE
    close = quote(CLOSE) if future else quote(CLOSE, bid="108", ask="108.05", future=False)
    assert (engine._close_future(close, position, "CONTROL") if future
            else engine._close(position, close, "CONTROL"))
    with engine.store.connect() as connection:
        original = tuple(connection.execute("SELECT payload_sha256,payload_json "
            "FROM decision_evidence_snapshots WHERE decision_key=?", (receipt_key,)).fetchone())
        evidence = json.loads(original[1])
        assert evidence["capture_phase"] == "ATOMIC_PAPER_ADMISSION"
        assert evidence["native_decision_key"] == key
        assert evidence["entry_fill_committed_at"] is None
    later = (datetime.fromisoformat(CLOSE) + timedelta(minutes=1)).isoformat()
    clock[0] = later
    next_entry = (quote(later, bid="1500.5", ask="1501") if future
                  else quote(later, bid="109", ask="109.05", future=False))
    try:
        duplicate = engine.admit_paper_candidate(next_entry, D(".8"), {"native_decision_key": key})
        assert duplicate[0] is False and duplicate[1]
    except ValueError as error:
        assert str(error)
    assert not (engine.store.active_future_positions() if future else engine.store.open_positions())
    table = "paper_future_positions" if future else "paper_positions"
    with engine.store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 1
        assert tuple(connection.execute("SELECT payload_sha256,payload_json "
            "FROM decision_evidence_snapshots WHERE decision_key=?", (receipt_key,)).fetchone()) == original
    fresh_key = key + ":NEXT"
    control = engine.admit_paper_candidate(next_entry, D(".8"), {"native_decision_key": fresh_key})
    assert control[0], control[1]
    with engine.store.connect() as connection:
        current = json.loads(connection.execute("SELECT payload_json FROM decision_evidence_snapshots "
            "WHERE decision_key=?", ("PAPER_ADMISSION:"+fresh_key,)).fetchone()[0])
    assert current["native_decision_key"] == fresh_key
    assert current["capture_phase"] == "ATOMIC_PAPER_ADMISSION"
    assert current["decision"]["paper_id"] == control[2] != first[2]


def test_r19_rehashed_wrong_multiplier_cannot_create_or_restore_future_cash_authority(tmp_path):
    clock = [OPEN]
    engine = broker(tmp_path / "future-contract.sqlite", clock)

    def financial_rows():
        with engine.store.connect() as connection:
            return {table: [tuple(row) for row in connection.execute(
                "SELECT * FROM " + table + " ORDER BY rowid")]
                for table in ("paper_family_lifecycle", "paper_family_lifecycle_events",
                              "paper_future_positions", "paper_future_marks", "paper_fills",
                              "decision_evidence_snapshots")}

    initial = financial_rows()
    initial_cash = engine._cash(as_of=OPEN, currency="ARS")
    absent = engine._open_future(replace(quote(), contract=None), D(".8"), {})
    assert absent == (False, "FUTURES_EXACT_PAPER_CONTRACT_REQUIRED", None)
    wrong = replace(contract(), cash_multiplier=D("2000"))
    rejected = engine._open_future(replace(quote(), contract=wrong), D(".8"), {})
    assert rejected == (False, "FUTURES_EXACT_STANDARD_DLR_REQUIRED", None)
    with pytest.raises(ValueError, match="FUTURES_EXACT_STANDARD_DLR_REQUIRED"):
        engine.family_paper.open_future(wrong, lifecycle_id="REJECTED-FUTURE",
            event_id="REJECTED-FUTURE:OPEN", entry_price="1500", quantity="1",
            entry_cost="100", occurred_at=OPEN)
    assert financial_rows() == initial
    assert engine._cash(as_of=OPEN, currency="ARS") == initial_cash

    position = open_future(engine)
    committed_cash = engine._cash(as_of=OPEN, currency="ARS")
    original_metadata = position["metadata_json"]
    metadata = json.loads(original_metadata)
    metadata["financial_contract"]["cash_multiplier"] = "2000"
    metadata["contract_snapshot_sha256"] = hashlib.sha256(json.dumps(
        metadata["financial_contract"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    # The raw snapshot hash and matching position dimension are coherent.
    # Only the exact DLR contract guard may reject this changed multiplier.
    with engine.store.connect() as connection:
        connection.execute("UPDATE paper_future_positions SET cash_multiplier=?,metadata_json=? "
            "WHERE lifecycle_id=?", ("2000", json.dumps(metadata), position["lifecycle_id"]))
        corrupted = dict(connection.execute("SELECT * FROM paper_future_positions "
            "WHERE lifecycle_id=?", (position["lifecycle_id"],)).fetchone())
    before_rejected_restore = financial_rows()
    for request in (lambda: future_position_contract(corrupted),
                    lambda: future_risk_snapshot(engine.store, "ARS", OPEN)):
        with pytest.raises(ValueError, match="FUTURES_EXACT_STANDARD_DLR_REQUIRED"):
            request()
    clock[0] = CLOSE
    assert engine._close_future(quote(CLOSE), corrupted, "CONTROL") is False
    assert financial_rows() == before_rejected_restore

    # Restoring the original synthetic authority proves the blocked requests
    # did not debit/credit cash or create any additional mark or terminal row.
    with engine.store.connect() as connection:
        connection.execute("UPDATE paper_future_positions SET cash_multiplier=?,metadata_json=? "
            "WHERE lifecycle_id=?", (position["cash_multiplier"], original_metadata,
                                     position["lifecycle_id"]))
    assert future_position_contract(engine.store.active_future_positions()[0]) == contract()
    assert engine._cash(as_of=OPEN, currency="ARS") == committed_cash < initial_cash
