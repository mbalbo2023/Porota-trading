"""A228: fail-closed last-mile PAPER/0 admission, including filesystem aliases."""
import os
from decimal import Decimal

import pytest

from be_paper_engine import PaperStore
from bs_instrument_contracts import InstrumentContract
from rc6_paper_family_lifecycle import FamilyPaperExecutor, PaperFundTerms
from rc6_paper_runtime_safety import runtime_admission_context


def runtime_store(tmp_path, monkeypatch):
    path = tmp_path / "effective_paper.sqlite"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    return PaperStore(str(path))


def test_last_mile_runtime_context_accepts_only_verified_paper_zero(tmp_path, monkeypatch):
    store = runtime_store(tmp_path, monkeypatch)
    with store.connect() as db:
        assert runtime_admission_context(store, connection=db) == (
            True, "PAPER_RUNTIME_WRITE_LOCK_REQUIRED")
        db.execute("BEGIN IMMEDIATE")
        assert runtime_admission_context(store, connection=db) == (True, "")
        assert db.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0


@pytest.mark.parametrize("mode,real_sent", [
    ("REAL", 0), ("PRODUCTION_PAPER", 1), ("SHADOW", 0),
    ("PRODUCTION_PAPER", -1),
])
def test_last_mile_runtime_state_cannot_be_bypassed(tmp_path, monkeypatch,
                                                    mode, real_sent):
    store = runtime_store(tmp_path, monkeypatch)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE observer_state SET mode=?,real_orders_sent=? WHERE id=1",
                   (mode, real_sent))
        assert runtime_admission_context(store, connection=db) == (
            True, "PAPER_RUNTIME_STATE_NOT_SAFE")


def test_last_mile_rejects_runtime_dataset_provenance_mutation(tmp_path, monkeypatch):
    store = runtime_store(tmp_path, monkeypatch)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE paper_workspace SET origin='CLONED' WHERE id=1")
        assert runtime_admission_context(store, connection=db) == (
            True, "PAPER_RUNTIME_DATASET_UNTRUSTED")


def test_last_mile_runtime_hardlink_alias_is_conservative(tmp_path, monkeypatch):
    store = runtime_store(tmp_path, monkeypatch)
    alias = tmp_path / "same_inode_but_other_name.sqlite"
    os.link(store.path, alias)
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert runtime_admission_context(store, connection=db) == (
            True, "PAPER_RUNTIME_IDENTITY_UNAVAILABLE")


def test_an_independent_offline_paper_book_is_not_the_runtime_book(tmp_path, monkeypatch):
    runtime_store(tmp_path, monkeypatch)
    isolated = PaperStore(str(tmp_path / "fixture.sqlite"))
    with isolated.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        assert runtime_admission_context(isolated, connection=db) == (False, "")


def test_future_entry_aborts_atomically_on_nonzero_runtime_counter(tmp_path, monkeypatch):
    store = runtime_store(tmp_path, monkeypatch)
    contract = InstrumentContract(
        "DLR/OCT26", "FUTUROS", "ARS", "A3", "INMEDIATA",
        Decimal("1000"), Decimal("1"), "CONTRACT_EVIDENCE_V2_BOUND",
        expires_at="2026-10-30T15:00:00-03:00",
        minimum_quantity=Decimal("1"),
        paper_margin_policy="CONSERVATIVE_NOTIONAL_RATE",
        paper_margin_rate=Decimal("1"), underlying="DOLAR_A3500")
    executor = FamilyPaperExecutor(store)
    with store.connect() as db:
        db.execute("UPDATE observer_state SET real_orders_sent=1 WHERE id=1")
    with pytest.raises(ValueError, match="PAPER_RUNTIME_STATE_NOT_SAFE"):
        executor.open_future(contract, lifecycle_id="A228-FUTURE-1",
                             event_id="A228-FUTURE-OPEN-1", entry_price="1500",
                             quantity="1", occurred_at="2026-10-05T12:00:00-03:00",
                             book_at="2026-10-05T12:00:00-03:00",
                             cash_guard=lambda db: Decimal("100000000"),
                             admission_guard=lambda db: "")
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM paper_future_positions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM paper_family_lifecycle_events").fetchone()[0] == 0


@pytest.mark.parametrize('mode,real_orders', [
    ('REAL', 0), ('PRODUCTION_PAPER', 1),
])
def test_fund_entry_aborts_atomically_before_catalog_if_paper_zero_revoked(
        tmp_path, monkeypatch, mode, real_orders):
    store = runtime_store(tmp_path, monkeypatch)
    terms = PaperFundTerms(
        symbol='SIMULATED_CLASS_NOT_CATALOG_READY', family='FCI',
        currency='ARS', market='BYMA', settlement='A-24HS',
        metadata_source='SIMULATED:NOT_PPI',
        paper_subscription_policy='INTERNAL_RISK_BUDGET_BY_AMOUNT')
    with store.connect() as db:
        db.execute('UPDATE observer_state SET mode=?,real_orders_sent=? WHERE id=1',
                   (mode, real_orders))
    with pytest.raises(ValueError, match='PAPER_RUNTIME_STATE_NOT_SAFE'):
        FamilyPaperExecutor(store).subscribe_fund(
            terms, lifecycle_id='A228-FCI-1', event_id='A228-FCI-ENTRY-1',
            amount='1000', cash_guard=lambda _db: Decimal('100000'),
            admission_guard=lambda _db: '')
    with store.connect() as db:
        assert db.execute('SELECT COUNT(*) FROM paper_family_lifecycle').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM paper_family_lifecycle_events').fetchone()[0] == 0
