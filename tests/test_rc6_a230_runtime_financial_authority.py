"""A230: adversarial PAPER-cash replay with NO real route and no deploy clock gate."""
from decimal import Decimal
import os

import pytest

import rc6_paper_admission_gate as gate
from be_paper_engine import PaperStore
from bs_instrument_contracts import InstrumentContract
from rc6_paper_family_lifecycle import (
    FamilyPaperExecutor, FUTURE_ADMIN_CANCEL_AUTHORITY_A207,
    _FUTURE_LOCKED_BROKER_COMMIT, cancel_open_future_admin,
    apply_paper_event, fund_cash_effect, future_cash_effect,
)
from rc6_paper_runtime_safety import runtime_admission_context


def _runtime(tmp_path, monkeypatch):
    path = tmp_path / "runtime.sqlite"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    store = PaperStore(str(path))
    return store, FamilyPaperExecutor(store)


def _dlr(symbol="DLR/NOV26", expires="2026-11-30T15:00:00-03:00"):
    return InstrumentContract(
        symbol, "FUTUROS", "ARS", "A3", "INMEDIATA", Decimal("1000"),
        Decimal("1"), "CONTRACT_EVIDENCE_V2_BOUND", expires_at=expires,
        minimum_quantity=Decimal("1"),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=Decimal("1"), underlying="DOLAR_A3500")


def _events(store):
    with store.connect() as db:
        return db.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0]


def test_generic_future_event_cannot_mint_runtime_cash(tmp_path, monkeypatch):
    store, executor = _runtime(tmp_path, monkeypatch)
    contract = _dlr()
    request = dict(lifecycle_id="ORPHAN", event_id="ORPHAN:OPEN",
                   to_state="OPEN", amount="1000000",
                   occurred_at="2026-11-02T11:00:00-03:00")
    with pytest.raises(ValueError, match="FUTURES_DIRECT_RUNTIME_FINANCIAL_EVENT_FORBIDDEN"):
        executor.future_event(contract, **request)
    with pytest.raises(ValueError, match="FUTURES_DIRECT_RUNTIME_FINANCIAL_EVENT_FORBIDDEN"):
        apply_paper_event(store, family="FUTUROS", instrument=contract.symbol,
                          currency="ARS", detail={}, **request)
    assert _events(store) == 0
    assert future_cash_effect(store, "ARS", "2026-11-02T12:00:00-03:00") == 0


def test_generic_fci_redemption_cannot_mint_runtime_cash(tmp_path, monkeypatch):
    store, _ = _runtime(tmp_path, monkeypatch)
    # Historical PENDING/REDEEM_REQUESTED state is a deliberately synthetic
    # fixture; it does not certify PPI NAV, fund units, or clearing evidence.
    with store.connect() as db:
        db.execute("""INSERT INTO paper_family_lifecycle
            (lifecycle_id,family,instrument,currency,state,updated_at,ledger_total,metadata_json)
            VALUES('UNVERIFIED_FCI','FCI','FONDO','ARS','REDEEM_REQUESTED',
                   '2026-11-02T10:00:00-03:00','-1000','{}')""")
    with pytest.raises(ValueError, match="FCI_RUNTIME_NAV_OR_REDEMPTION_WRITER_UNVERIFIED"):
        apply_paper_event(store, lifecycle_id="UNVERIFIED_FCI", event_id="FCI:FAKE_REDEEM",
                          family="FCI", instrument="FONDO", currency="ARS",
                          to_state="REDEEMED", amount="1000000",
                          occurred_at="2026-11-02T11:00:00-03:00")
    assert _events(store) == 0
    assert fund_cash_effect(store, "ARS", "2026-11-02T12:00:00-03:00") == 0


def test_direct_runtime_future_requires_cash_and_broker_commit_evidence(tmp_path, monkeypatch):
    store, executor = _runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    request = dict(lifecycle_id="DIRECT_FUTURE", event_id="DIRECT:OPEN",
                   entry_price="1500", quantity="1", entry_cost="100",
                   occurred_at="2026-11-02T11:00:00-03:00",
                   book_at="2026-11-02T11:00:00-03:00",
                   admission_guard=lambda _: "")
    with pytest.raises(ValueError, match="FUTURES_RUNTIME_CASH_GUARD_REQUIRED"):
        executor.open_future(_dlr(), **request)
    with pytest.raises(ValueError, match="FUTURES_RUNTIME_BROKER_EVIDENCE_REQUIRED"):
        executor.open_future(_dlr(), cash_guard=lambda _: Decimal("100000000"),
                             commit_evidence=lambda _connection, _result: None, **request)
    # Even test-only access to the module-private handoff cannot replace the
    # broker's transaction-bound quote/financial proof with a no-op callback.
    with pytest.raises(ValueError, match="FUTURES_RUNTIME_ADMISSION_SNAPSHOT_REQUIRED"):
        executor.open_future(_dlr(), cash_guard=lambda _: Decimal("100000000"),
                             commit_evidence=lambda _connection, _result: None,
                             _runtime_broker_authority=_FUTURE_LOCKED_BROKER_COMMIT,
                             **request)
    assert _events(store) == 0
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_future_positions").fetchone()[0] == 0


def test_valid_broker_runtime_future_commits_quote_snapshot_and_stays_paper_zero(
        tmp_path, monkeypatch):
    from tests.test_rc6_future_paper_engine import broker as make_broker, quote
    path = tmp_path / "paper.sqlite"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    clock = ["2026-10-05T12:00:00-03:00"]
    paper = make_broker(tmp_path, clock)
    paper.admission_guard = lambda _q, _store: (True, "")
    opened, reason, lifecycle_id = paper._open_future(quote(clock[0]), Decimal("0.8"),
                                                      {"samples": 8})
    assert opened is True, reason
    assert reason == "FUTURES_PAPER_OPENED_SIMULATED"
    with paper.store.connect() as db:
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()) == (
            "PRODUCTION_PAPER", 0)
        assert db.execute("""SELECT COUNT(*) FROM decision_evidence_snapshots
            WHERE decision_key=?""", ("PAPER_FUTURE_FILL:" + lifecycle_id,)).fetchone()[0] == 1
    assert future_cash_effect(paper.store, "ARS", clock[0]) < 0


def test_valid_runtime_future_mark_settlement_and_exit_are_quote_bound(
        tmp_path, monkeypatch):
    from tests.test_rc6_future_paper_engine import broker as make_broker, quote
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "paper.sqlite"))
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    clock = ["2026-10-05T12:00:00-03:00"]
    paper = make_broker(tmp_path, clock)
    paper.admission_guard = lambda _q, _store: (True, "")
    opened, reason, lifecycle_id = paper._open_future(quote(clock[0]), Decimal("0.8"),
                                                      {"samples": 8})
    assert opened, reason
    clock[0] = "2026-10-05T13:00:00-03:00"
    paper._on_future_quote(quote(clock[0], bid="1510", ask="1511"))
    clock[0] = "2026-10-05T14:50:00-03:00"
    paper._on_future_quote(quote(clock[0], bid="1520", ask="1521"))
    with paper.store.connect() as db:
        assert db.execute("""SELECT status FROM paper_future_positions
            WHERE lifecycle_id=?""", (lifecycle_id,)).fetchone()[0] == "CLOSED"
        receipts = db.execute("""SELECT decision_key FROM decision_evidence_snapshots
            WHERE decision_key LIKE 'PAPER_FUTURE_%' ORDER BY decision_key""").fetchall()
        assert any("PAPER_FUTURE_MARK:" in row[0] for row in receipts)
        assert any("PAPER_FUTURE_CLOSE:" in row[0] for row in receipts)
    assert future_cash_effect(paper.store, "ARS", clock[0]) > Decimal("-1500000")


def _open_offline_fixture_in_runtime_book(tmp_path, monkeypatch):
    """Adversarial *fixture*, never an operator-authorized runtime opening."""
    store, executor = _runtime(tmp_path, monkeypatch)
    canonical = store.path
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(tmp_path / "independent_fixture.sqlite"))
    executor.open_future(
        _dlr(), lifecycle_id="EXISTING", event_id="EXISTING:OPEN",
        entry_price="1500", quantity="1", entry_cost="2",
        occurred_at="2026-10-05T11:00:00-03:00", book_at="2026-10-05T11:00:00-03:00")
    monkeypatch.setenv("PAPER_V17_DB_PATH", canonical)
    return store, executor


def test_runtime_future_mark_and_exit_need_atomic_quote_proof_not_just_private_token(
        tmp_path, monkeypatch):
    store, executor = _open_offline_fixture_in_runtime_book(tmp_path, monkeypatch)
    instant = "2026-11-02T11:00:00-03:00"
    with pytest.raises(ValueError, match="FUTURES_RUNTIME_QUOTE_PROOF_REQUIRED"):
        executor.mark_future(
            _dlr(), lifecycle_id="EXISTING", event_id="EXISTING:MARK:FAKE",
            mark_price="5000", book_at=instant, occurred_at=instant, settlement=True,
            detail={"source": "UNVERIFIED_LOCAL_QUOTE"},
            _runtime_broker_authority=_FUTURE_LOCKED_BROKER_COMMIT,
            commit_evidence=lambda _db, _result: None)
    with pytest.raises(ValueError, match="FUTURES_RUNTIME_QUOTE_PROOF_REQUIRED"):
        executor.close_future(
            _dlr(), lifecycle_id="EXISTING", event_id="EXISTING:CLOSE:FAKE",
            exit_price="5000", book_at=instant, occurred_at=instant,
            _runtime_broker_authority=_FUTURE_LOCKED_BROKER_COMMIT,
            commit_evidence=lambda _db, _result: None)
    assert _events(store) == 2
    assert future_cash_effect(store, "ARS", instant) == Decimal("-1500002")
    with store.connect() as db:
        assert db.execute("SELECT status FROM paper_future_positions").fetchone()[0] == "ACTIVE"
        assert db.execute("SELECT COUNT(*) FROM paper_future_marks").fetchone()[0] == 0


@pytest.mark.parametrize("fake_state", [
    "DAILY_VARIATION", "MARGIN_OK", "MARGIN_DEFICIT", "CLOSE", "EXPIRY", "ADMIN_CANCEL",
])
def test_runtime_cash_projection_rejects_preexisting_forged_positive_event_with_position(
        tmp_path, monkeypatch, fake_state):
    store, _ = _open_offline_fixture_in_runtime_book(tmp_path, monkeypatch)
    with store.connect() as db:
        db.execute("""INSERT INTO paper_family_lifecycle_events
            (event_id,lifecycle_id,family,from_state,to_state,amount,occurred_at,detail_json)
            VALUES(?, 'EXISTING','FUTUROS','MARGIN_RESERVED',?,'1000000',
                   '2026-11-02T11:00:00-03:00','{}')""", ("FAKE:" + fake_state, fake_state))
    with pytest.raises(ValueError, match="FUTURES_RUNTIME_"):
        future_cash_effect(store, "ARS", "2026-11-02T12:00:00-03:00")


def test_mathematically_matching_fake_settlement_cannot_credit_without_quote_proof(
        tmp_path, monkeypatch):
    store, _ = _open_offline_fixture_in_runtime_book(tmp_path, monkeypatch)
    instant = "2026-11-02T11:00:00-03:00"
    with store.connect() as db:
        db.execute("""INSERT INTO paper_future_marks
            (event_id,lifecycle_id,mark_price,book_at,observed_at,is_settlement,
             unrealized_pnl,detail_json)
            VALUES('FAKE:MARK','EXISTING','5000',?,?,1,'0','{}')""", (instant, instant))
        db.execute("""INSERT INTO paper_family_lifecycle_events
            (event_id,lifecycle_id,family,from_state,to_state,amount,occurred_at,detail_json)
            VALUES('FAKE:MARK','EXISTING','FUTUROS','MARGIN_RESERVED',
                   'DAILY_VARIATION','3500000',?,?)""", (
                       instant, '{"book_at":"' + instant + '","settlement_price":"5000",'
                       '"variation_pnl":"3500000"}'))
        assert db.execute("SELECT COUNT(*) FROM decision_evidence_snapshots").fetchone()[0] == 0
    with pytest.raises(ValueError, match="FUTURES_RUNTIME_QUOTE_PROOF_REQUIRED"):
        future_cash_effect(store, "ARS", "2026-11-02T12:00:00-03:00")


def test_valid_a207_admin_reversal_cannot_be_retimed_before_original_cash_reserve(
        tmp_path, monkeypatch):
    store, _ = _open_offline_fixture_in_runtime_book(tmp_path, monkeypatch)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        result = cancel_open_future_admin(
            db, "EXISTING", "2026-10-05T11:01:00-03:00",
            FUTURE_ADMIN_CANCEL_AUTHORITY_A207)
    assert future_cash_effect(store, "ARS", "2026-10-05T11:02:00-03:00") == 0
    with store.connect() as db:
        db.execute("UPDATE paper_family_lifecycle_events SET occurred_at=? WHERE event_id=?",
                   ("2026-10-04T11:00:00-03:00", result["cancel_event_id"]))
    with pytest.raises(ValueError, match="FUTURES_RUNTIME_FINANCIAL_EVENT_BEFORE_OPEN"):
        future_cash_effect(store, "ARS", "2026-10-04T12:00:00-03:00")


@pytest.mark.parametrize("stamp,contract,blocked", [
    ("2026-10-03T12:00:00-03:00", _dlr("DLR/OCT26", "2026-10-30T15:00:00-03:00"),
     "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"),
    ("2026-12-25T11:00:00-03:00", _dlr("DLR/DIC26", "2026-12-30T15:00:00-03:00"),
     "MARKET_CLOSED_OR_CALENDAR_UNKNOWN"),
    ("2026-10-02T12:00:00-03:00", _dlr("DLR/OCT26", "2026-10-30T15:00:00-03:00"),
     "FUTURES_EXECUTION_GRID_NOT_KNOWN_AT_PIT"),
])
def test_runtime_future_never_uses_closed_calendar_or_future_price_knowledge(
        tmp_path, monkeypatch, stamp, contract, blocked):
    store, executor = _runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    with pytest.raises(ValueError, match=blocked):
        executor.open_future(
            contract, lifecycle_id="OFFHOURS", event_id="OFFHOURS:OPEN",
            entry_price="1500", quantity="1", occurred_at=stamp, book_at=stamp,
            cash_guard=lambda _: Decimal("100000000"), admission_guard=lambda _: "",
            commit_evidence=lambda _connection, _result: None,
            _runtime_broker_authority=_FUTURE_LOCKED_BROKER_COMMIT)
    assert _events(store) == 0


def test_runtime_cash_read_rejects_preexisting_positive_orphan_future(tmp_path, monkeypatch):
    store, _ = _runtime(tmp_path, monkeypatch)
    with store.connect() as db:
        db.execute("""INSERT INTO paper_family_lifecycle
            (lifecycle_id,family,instrument,currency,state,updated_at,ledger_total,metadata_json)
            VALUES('ORPHAN','FUTUROS','DLR/NOV26','ARS','OPEN',
                   '2026-11-02T11:00:00-03:00','1000000','{}')""")
        db.execute("""INSERT INTO paper_family_lifecycle_events
            (event_id,lifecycle_id,family,from_state,to_state,amount,occurred_at,detail_json)
            VALUES('ORPHAN:OPEN','ORPHAN','FUTUROS',NULL,'OPEN','1000000',
                   '2026-11-02T11:00:00-03:00','{}')""")
    with pytest.raises(ValueError, match="FUTURES_RUNTIME_ORPHAN_CASH_EVENT"):
        future_cash_effect(store, "ARS", "2026-11-02T12:00:00-03:00")


def test_runtime_cash_read_rejects_preexisting_unverified_fci_redemption(tmp_path, monkeypatch):
    store, _ = _runtime(tmp_path, monkeypatch)
    with store.connect() as db:
        db.execute("""INSERT INTO paper_family_lifecycle
            (lifecycle_id,family,instrument,currency,state,updated_at,ledger_total,metadata_json)
            VALUES('FCI1','FCI','FONDO','ARS','REDEEMED',
                   '2026-11-02T11:00:00-03:00','1000000','{}')""")
        db.execute("""INSERT INTO paper_family_lifecycle_events
            (event_id,lifecycle_id,family,from_state,to_state,amount,occurred_at,detail_json)
            VALUES('FCI1:REDEEM','FCI1','FCI','REDEEM_REQUESTED','REDEEMED',
                   '1000000','2026-11-02T11:00:00-03:00','{}')""")
    with pytest.raises(ValueError, match="FCI_RUNTIME_REDEMPTION_SOURCE_UNVERIFIED"):
        fund_cash_effect(store, "ARS", "2026-11-02T12:00:00-03:00")


def test_offline_alias_cannot_hide_hardlink_from_runtime_safety(tmp_path, monkeypatch):
    _runtime(tmp_path, monkeypatch)
    offline = PaperStore(str(tmp_path / "isolated.sqlite"))
    os.link(offline.path, tmp_path / "isolated-link.sqlite")
    with offline.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert runtime_admission_context(offline, connection=db) == (
            True, "PAPER_RUNTIME_IDENTITY_UNAVAILABLE")
