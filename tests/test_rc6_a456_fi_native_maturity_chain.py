"""M1: synthetic reviewed LETRAS terminal; no entry, sale or source authority."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import importlib
import importlib.util
import json
import sqlite3
from types import SimpleNamespace

import pytest

from be_paper_engine import PaperBroker, Quote
from bm_exit_supervisor import PositionExitSupervisor
from bt_caucion_paper import pending_proceeds
from tests import test_rc6_fixed_income_native_redemption as fixture


NEXT = "2026-10-30T16:00:00+00:00"
LATER = "2026-10-31T16:00:00+00:00"


def terminal_api():
    assert importlib.util.find_spec("rc6_fixed_income_native_exit") is not None, "M1 terminal is absent"
    return importlib.import_module("rc6_fixed_income_native_exit")


@pytest.fixture
def lot(request):
    fx = fixture.NativeRedemptionReviewTest(
        "test_bond_full_redemption_review_is_idempotent_and_never_pays_or_closes")
    fx.setUp()
    request.addfinalizer(fx.doCleanups)
    fx._set_family("LETRAS")
    return fx


def paid(lot, *, currency="ARS"):
    economic = lot._economic_receipt()
    economic["event"]["payment_currency"] = currency
    lot._book(economic=economic)
    review = lot._review()
    review["expected_principal_by_currency"] = [[currency, "100"]]
    review["expected_cash_by_currency"] = [[currency, "100"]]
    review["issuer_schedule"]["principal_currency"] = currency
    review["issuer_schedule"]["obligations"][0]["payment_currency"] = currency
    approved = lot._apply(review)
    add_exit_plan(lot, approved)
    return approved


def mature(lot, review, *, at=NEXT, request_id="terminal-1"):
    return terminal_api().apply_reviewed_maturity(
        lot.store, paper_id="PAPER-AL30", request_id=request_id,
        approved_review_receipt_sha256=review["receipt_sha256"], applied_at=at)


def broker(lot):
    return PaperBroker(lot.store, initial_cash="1000", initial_cash_usd="100",
                       daily_loss_pct=None, clock_fn=lambda: NEXT)


def snapshot(lot):
    with lot.store.connect() as db:
        return list(db.iterdump())


@pytest.mark.parametrize("currency", ["ARS", "USD"])
def test_paid_letra_terminal_preserves_cash_without_sell_and_is_historical(lot, currency):
    terminal = terminal_api()
    review = paid(lot, currency=currency)
    paper = broker(lot)
    before = {name: paper._cash(as_of=NEXT, currency=name) for name in ("ARS", "USD")}
    earlier = paper._cash(as_of=fixture.BOOKED, currency="ARS")
    result = mature(lot, review)
    assert result["economic_cash_written"] == "0"
    assert result["sell_fills_created"] == 0
    assert result["family"] == "LETRAS"
    assert result["real_routes_used"] == [] and result["real_orders_sent"] == 0
    assert result["realized_pnl"] == ("-0.61" if currency == "ARS" else None)
    with lot.store.connect() as db:
        assert terminal.terminal_at(db, "PAPER-AL30", fixture.BOOKED) is None
        assert terminal.terminal_at(db, "PAPER-AL30", NEXT) == result
        assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "CLOSED"
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 1
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
        for name in ("ARS", "USD"):
            assert pending_proceeds(lot.store, NEXT, name, connection=db) == 0
    assert {name: paper._cash(as_of=NEXT, currency=name) for name in before} == before
    assert paper._cash(as_of=fixture.BOOKED, currency="ARS") == earlier
    assert lot.store.open_positions() == []
    assert lot.store.exit_positions() == ([], [])
    assert lot.store.recent_closed()[0]["close_reason"] == terminal.REASON
    assert paper.mark_equity({}, as_of=NEXT)["ARS"]["exposure"] == 0
    after = snapshot(lot)
    assert mature(lot, review, at=LATER) == result
    assert snapshot(lot) == after


@pytest.mark.parametrize("request_id,at", [("conflict", LATER), ("terminal-1", fixture.BOOKED)])
def test_terminal_replay_conflict_or_clock_rollback_cannot_mutate(lot, request_id, at):
    terminal_api()
    review = paid(lot)
    mature(lot, review)
    before = snapshot(lot)
    with pytest.raises(ValueError, match="FI_TERMINAL_(REPLAY_CONFLICT|CLOCK_ROLLBACK)"):
        mature(lot, review, request_id=request_id, at=at)
    assert snapshot(lot) == before


def test_concurrent_identical_terminal_is_singleton(lot):
    terminal_api()
    review = paid(lot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: mature(lot, review), range(2)))
    assert results[0] == results[1]
    with lot.store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_fi_native_maturities").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 1


def test_terminal_freezes_new_events_but_keeps_exact_economic_replay(lot):
    terminal_api()
    review = paid(lot)
    mature(lot, review)
    before = snapshot(lot)
    lot._book(at=LATER)  # original approved payment replay, not another credit
    assert snapshot(lot) == before
    fixing = lot._economic_receipt(event_id="late-fixing")
    fixing["entitlement"] = None
    fixing["event"].update(kind="FIXING", source_kind="OFFICIAL_FIXING",
        payment_currency=None, cash_amount=None, nominal_base=None, nominal_redeemed=None,
        fixing_code="SYNTHETIC_INDEX", fixing_value="1", fixing_unit="POINTS")
    with pytest.raises(ValueError, match="FI_LEDGER_TERMINAL_CLOSED"):
        lot._book(economic=fixing, at=LATER)
    assert snapshot(lot) == before


def test_terminal_failure_after_receipt_rolls_back_every_write(lot):
    terminal_api()
    review = paid(lot)
    with lot.store.connect() as db:
        db.execute("""CREATE TRIGGER reject_terminal_state BEFORE UPDATE OF status ON paper_positions
           BEGIN SELECT RAISE(ABORT,'SYNTHETIC terminal failure'); END""")
    before = snapshot(lot)
    with pytest.raises(sqlite3.IntegrityError, match="SYNTHETIC terminal failure"):
        mature(lot, review)
    assert snapshot(lot) == before


def test_missing_review_or_positive_residual_cannot_close(lot):
    api = terminal_api()
    lot._book(redeemed="30")
    before = snapshot(lot)
    with pytest.raises(ValueError, match="FI_TERMINAL_APPROVED_REVIEW_REQUIRED"):
        api.apply_reviewed_maturity(lot.store, paper_id="PAPER-AL30", request_id="terminal-1",
                                    approved_review_receipt_sha256="a" * 64, applied_at=NEXT)
    assert snapshot(lot) == before


@pytest.mark.parametrize("change", ["cash", "future-review", "real-orders"])
def test_invalid_persistent_state_cannot_turn_into_terminal(lot, change):
    terminal_api()
    review = paid(lot)
    with lot.store.connect() as db:
        if change == "cash":
            db.execute("UPDATE paper_fi_cash_entries SET amount='999'")
        elif change == "future-review":
            db.execute("UPDATE paper_fi_native_redemption_reviews SET reviewed_at=?", (LATER,))
        else:
            db.execute("UPDATE observer_state SET real_orders_sent=1")
    before = snapshot(lot)
    with pytest.raises(ValueError):
        mature(lot, review)
    assert snapshot(lot) == before


@pytest.mark.parametrize("family", ["BONOS", "OBLIGACIONES"])
def test_m1_never_grants_bond_or_on_terminal_authority(lot, family):
    terminal_api()
    lot._set_family(family)
    review = paid(lot)
    before = snapshot(lot)
    with pytest.raises(ValueError, match="FI_TERMINAL_LETRAS_ONLY"):
        mature(lot, review)
    assert snapshot(lot) == before


def add_exit_plan(lot, review):
    with lot.store.connect() as db:
        raw = db.execute("SELECT features_json FROM paper_positions").fetchone()[0]
        features = json.loads(raw)
        features["fixed_income_exit_plan"] = {
            "mode": "FI_NATIVE_MATURITY_EVENT_LEDGER_V1", "strategy_id": "FI_LETRA_PAPER_V1",
            "verified": True,
            "max_hold_minutes": None, "nominal_rebase_required": True,
            "exit_at": fixture.PAY, "quantity_vn": "100",
            "issuer_source_sha256": review["issuer_sha256"],
            "issuer_source_reference": review["issuer_source_reference"],
        }
        db.execute("UPDATE paper_positions SET features_json=?", (json.dumps(features),))


def test_supervisor_closes_only_already_paid_reviewed_plan_without_book(lot):
    terminal_api()
    review = paid(lot)
    add_exit_plan(lot, review)
    paper = broker(lot)
    sup = PositionExitSupervisor(paper, clock_fn=lambda: NEXT)
    root = lot.store.open_positions()[0]
    assert sup.supervise(root, None, NEXT).state == "CLOSED"
    assert lot.store.open_positions() == []
    assert not callable(getattr(paper, "fixed_income_native_exit_authorized", None))


def test_supervisor_does_not_infer_terminal_from_missing_plan(lot):
    terminal_api()
    paid(lot)
    with lot.store.connect() as db:
        features = json.loads(db.execute("SELECT features_json FROM paper_positions").fetchone()[0])
        features.pop("fixed_income_exit_plan")
        db.execute("UPDATE paper_positions SET features_json=?", (json.dumps(features),))
    paper = broker(lot)
    sup = PositionExitSupervisor(paper, clock_fn=lambda: NEXT)
    assert sup.supervise(lot.store.open_positions()[0], None, NEXT).state == "WATCH_FI_NATIVE_EXIT_UNVERIFIED"
    assert len(lot.store.open_positions()) == 1


@pytest.mark.parametrize("mutation", [
    "missing", "revoked", "unverified", "changed-maturity", "changed-issuer", "changed-nominal",
])
def test_direct_writer_requires_current_persisted_plan_under_lock(lot, mutation):
    terminal_api()
    review = paid(lot)
    with lot.store.connect() as db:
        features = json.loads(db.execute("SELECT features_json FROM paper_positions").fetchone()[0])
        plan = features["fixed_income_exit_plan"]
        if mutation == "missing":
            features.pop("fixed_income_exit_plan")
        elif mutation == "revoked":
            plan["revoked"] = True
        elif mutation == "unverified":
            plan["verified"] = False
        elif mutation == "changed-maturity":
            plan["exit_at"] = "2026-10-30T15:00:00+00:00"
        elif mutation == "changed-issuer":
            plan["issuer_source_sha256"] = "d" * 64
        else:
            plan["quantity_vn"] = "99"
        db.execute("UPDATE paper_positions SET features_json=?", (json.dumps(features),))
    before = snapshot(lot)
    with pytest.raises(ValueError, match="FI_TERMINAL_PERSISTED_PLAN_UNVERIFIED"):
        mature(lot, review)
    assert snapshot(lot) == before


def test_supervisor_plan_revoked_between_read_and_atomic_writer_cannot_close(lot, monkeypatch):
    api = terminal_api()
    paid(lot)
    original = api.apply_reviewed_maturity
    def revoke_then_apply(*args, **kwargs):
        with lot.store.connect() as db:
            features = json.loads(db.execute("SELECT features_json FROM paper_positions").fetchone()[0])
            features["fixed_income_exit_plan"]["revoked"] = True
            db.execute("UPDATE paper_positions SET features_json=?", (json.dumps(features),))
        return original(*args, **kwargs)
    monkeypatch.setattr(api, "apply_reviewed_maturity", revoke_then_apply)
    paper = broker(lot)
    verdict = PositionExitSupervisor(paper, clock_fn=lambda: NEXT).supervise(
        lot.store.open_positions()[0], None, NEXT)
    assert verdict.state == "WATCH_FI_NATIVE_LEDGER_BLOCKED"
    with lot.store.connect() as db:
        assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "OPEN"
        assert db.execute("SELECT count(*) FROM paper_fi_native_maturities").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_fi_cash_entries").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM paper_fills").fetchone()[0] == 1


def test_market_sale_guard_rejects_before_opening_database():
    assert importlib.util.find_spec("rc6_fixed_income_paper_market_sale") is not None, "A870 safe boundary absent"
    from rc6_fixed_income_paper_market_sale import apply_paper_market_sale
    with pytest.raises(ValueError, match="FI_NATIVE_MARKET_EXIT_NOT_ADMITTED"):
        apply_paper_market_sale(None, None, paper_id="PAPER-X", request_id="X", nominal="1",
            quote=None, book_bytes=b"", approved_book_sha256="a"*64,
            fee_bytes=b"", approved_fee_sha256="b"*64, applied_at=NEXT)


def test_forged_generic_closed_position_still_requires_real_sale_receipt(lot):
    with lot.store.connect() as db:
        db.execute("UPDATE paper_positions SET status='CLOSED',closed_at=?,close_reason='TEST'", (NEXT,))
    with pytest.raises(ValueError):
        pending_proceeds(lot.store, NEXT)


@pytest.fixture
def memory_schema():
    """Adversarial DDL tests never create a physical database."""
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE paper_positions(paper_id TEXT PRIMARY KEY)")
        db.commit()
        yield SimpleNamespace(connect=lambda: db), db


def schema_snapshot(db):
    return [tuple(row) for row in db.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]


@pytest.mark.parametrize("statement", [
    "INSERT OR REPLACE INTO paper_fi_native_maturities VALUES(?,?,?,?,?,?,?)",
    "REPLACE INTO paper_fi_native_maturities VALUES(?,?,?,?,?,?,?)",
    "UPDATE paper_fi_native_maturities SET request_id='changed' WHERE paper_id='PAPER-X'",
    "DELETE FROM paper_fi_native_maturities WHERE paper_id='PAPER-X'",
])
def test_schema_receipt_cannot_be_replaced_updated_or_deleted_without_recursive_triggers(
        memory_schema, statement):
    store, db = memory_schema
    terminal_api().init_schema(store)
    db.execute("PRAGMA recursive_triggers=0")
    original = ("PAPER-X", "original", "a"*64, "b"*64, "c"*64, NEXT, "{}")
    db.execute("INSERT INTO paper_fi_native_maturities VALUES(?,?,?,?,?,?,?)", original)
    db.commit()
    params = ("PAPER-X", "changed", "a"*64, "b"*64, "c"*64, LATER, "{}") if "VALUES" in statement else ()
    with pytest.raises(sqlite3.IntegrityError, match="FI_TERMINAL_RECEIPT_IMMUTABLE"):
        db.execute(statement, params)
    assert tuple(db.execute("SELECT * FROM paper_fi_native_maturities").fetchone()) == original
    assert db.execute("PRAGMA recursive_triggers").fetchone()[0] == 0


@pytest.mark.parametrize("trigger", [
    "paper_fi_native_maturity_no_update", "paper_fi_native_maturity_no_delete",
    "paper_fi_native_maturity_no_replace",
])
@pytest.mark.parametrize("wrong_table", [False, True])
def test_schema_rejects_named_trigger_with_wrong_body_or_table(memory_schema, trigger, wrong_table):
    store, db = memory_schema
    api = terminal_api()
    api.init_schema(store)
    db.execute(f"DROP TRIGGER IF EXISTS {trigger}")
    db.execute("CREATE TABLE unrelated(id TEXT)")
    target = "unrelated" if wrong_table else "paper_fi_native_maturities"
    db.execute(f"CREATE TRIGGER {trigger} BEFORE UPDATE ON {target} BEGIN SELECT 1; END")
    db.commit()
    before = schema_snapshot(db)
    with pytest.raises(ValueError, match="FI_TERMINAL_SCHEMA_INVALID"):
        api.init_schema(store)
    assert schema_snapshot(db) == before


@pytest.mark.parametrize("mutation", ["wrong-columns", "wrong-foreign-key"])
def test_schema_rejects_incompatible_preexisting_table(memory_schema, mutation):
    store, db = memory_schema
    if mutation == "wrong-columns":
        db.execute("CREATE TABLE paper_fi_native_maturities(paper_id TEXT PRIMARY KEY)")
    else:
        db.execute("""CREATE TABLE paper_fi_native_maturities(
          paper_id TEXT PRIMARY KEY, request_id TEXT NOT NULL,
          review_receipt_sha256 TEXT NOT NULL, issuer_sha256 TEXT NOT NULL,
          ledger_sha256 TEXT NOT NULL, closed_at TEXT NOT NULL, result_json TEXT NOT NULL)""")
    db.commit()
    before = schema_snapshot(db)
    with pytest.raises(ValueError, match="FI_TERMINAL_SCHEMA_INVALID"):
        terminal_api().init_schema(store)
    assert schema_snapshot(db) == before


@pytest.mark.parametrize("denied_trigger", [
    "paper_fi_native_maturity_no_update", "paper_fi_native_maturity_no_delete",
    "paper_fi_native_maturity_no_replace",
])
def test_schema_failed_trigger_creation_rolls_back_all_ddl(memory_schema, denied_trigger):
    store, db = memory_schema
    before = schema_snapshot(db)
    def authorize(action, arg1, arg2, database, source):
        return sqlite3.SQLITE_DENY if (
            action == sqlite3.SQLITE_CREATE_TRIGGER and arg1 == denied_trigger
        ) else sqlite3.SQLITE_OK
    db.set_authorizer(authorize)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            terminal_api().init_schema(store)
    finally:
        db.set_authorizer(None)
    assert schema_snapshot(db) == before
    assert db.in_transaction is False


def test_schema_valid_reinitialization_is_idempotent_and_keeps_receipt(memory_schema):
    store, db = memory_schema
    api = terminal_api()
    api.init_schema(store)
    original = ("PAPER-X", "original", "a"*64, "b"*64, "c"*64, NEXT, "{}")
    db.execute("INSERT INTO paper_fi_native_maturities VALUES(?,?,?,?,?,?,?)", original)
    db.commit()
    before = schema_snapshot(db)
    api.init_schema(store)
    assert schema_snapshot(db) == before
    assert tuple(db.execute("SELECT * FROM paper_fi_native_maturities").fetchone()) == original


def test_schema_trigger_collision_before_table_creation_leaves_no_partial_table(memory_schema):
    store, db = memory_schema
    db.execute("CREATE TABLE unrelated(id TEXT)")
    db.execute("""CREATE TRIGGER paper_fi_native_maturity_no_delete
        BEFORE DELETE ON unrelated BEGIN SELECT 1; END""")
    db.commit()
    before = schema_snapshot(db)
    with pytest.raises(ValueError, match="FI_TERMINAL_SCHEMA_INVALID"):
        terminal_api().init_schema(store)
    assert schema_snapshot(db) == before
    assert db.in_transaction is False


def test_schema_previous_two_trigger_layout_is_upgraded_without_mutating_receipt(memory_schema):
    store, db = memory_schema
    api = terminal_api()
    api.init_schema(store)
    db.execute("DROP TRIGGER paper_fi_native_maturity_no_replace")
    original = ("PAPER-X", "original", "a"*64, "b"*64, "c"*64, NEXT, "{}")
    db.execute("INSERT INTO paper_fi_native_maturities VALUES(?,?,?,?,?,?,?)", original)
    db.commit()
    with pytest.raises(ValueError, match="FI_TERMINAL_SCHEMA_INVALID"):
        api.terminal_row(db, "PAPER-X")
    api.init_schema(store)
    with pytest.raises(sqlite3.IntegrityError, match="FI_TERMINAL_RECEIPT_IMMUTABLE"):
        db.execute("REPLACE INTO paper_fi_native_maturities VALUES(?,?,?,?,?,?,?)", original)
    assert tuple(db.execute("SELECT * FROM paper_fi_native_maturities").fetchone()) == original


@pytest.mark.parametrize("trigger", [
    "paper_fi_native_maturity_no_update", "paper_fi_native_maturity_no_delete",
    "paper_fi_native_maturity_no_replace",
])
def test_schema_hot_reader_and_writer_reject_removed_trigger_without_repair(memory_schema, trigger):
    store, db = memory_schema
    api = terminal_api()
    api.init_schema(store)
    db.execute(f"DROP TRIGGER {trigger}")
    db.commit()
    before = schema_snapshot(db)
    with pytest.raises(ValueError, match="FI_TERMINAL_SCHEMA_INVALID"):
        api.terminal_row(db, "PAPER-X")
    with pytest.raises(ValueError, match="FI_TERMINAL_SCHEMA_INVALID"):
        api.apply_reviewed_maturity(store, paper_id="PAPER-X", request_id="terminal-1",
            approved_review_receipt_sha256="a"*64, applied_at=NEXT)
    assert schema_snapshot(db) == before
