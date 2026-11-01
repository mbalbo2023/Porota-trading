"""A232: runtime PAPER admission never inherits 24x7 deploy/test permission.

These SQLite/PPI records are explicitly *synthetic fixtures*, NOT evidence of
authenticated external PPI availability, real account fees or production GO.
"""
from dataclasses import asdict, replace
from decimal import Decimal
import json

import pytest

from au_fee_schedule import CAUCION_PAPER_FEE_AUTHORITY
from be_paper_engine import PaperBroker, PaperStore
from bt_caucion_paper import CaucionOffer
from di_caucion_cash_sweep_runtime_hf6 import offers_from_store
from dj_caucion_live_ppi_rc6 import SOURCE as LIVE_PPI_SOURCE, init_schema as init_live_schema
from rc6_paper_family_lifecycle import PaperFundTerms
import bu_instrument_catalog as catalog
import rc6_paper_admission_gate as gate


def _runtime(tmp_path, monkeypatch):
    path = tmp_path / "effective_paper.sqlite"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    store = PaperStore(str(path))
    return store, PaperBroker(store, initial_cash="2000000")


def _catalog_fci(store, at):
    catalog.init_schema(store)
    terms = PaperFundTerms("SYNTHETIC_FCI", "FCI", "ARS", "FCI", "INMEDIATA",
                           "SYNTHETIC_PPI_LABEL_NOT_AUTHENTICATION",
                           paper_subscription_policy="INTERNAL_RISK_BUDGET_BY_AMOUNT")
    with store.connect() as db:
        catalog.persist(db, {
            "ticker": terms.symbol, "instrument_type": "FCI", "market": terms.market,
            "currency": "ARS", "settlement": terms.settlement,
            "settlement_source": "PPI_FIELD", "description": "A232 synthetic FCI fixture",
            "last_seen_at": at, "run_id": "A232_SYNTHETIC", "status": "AVAILABLE",
            "capability": "READY_PAPER_FCI_SUBSCRIPTION",
            "raw": {"_discovery_source": "PPI_PRIMARY",
                    "paper_family_contract_v1": json.loads(json.dumps(asdict(terms), default=str))},
        })
        db.execute("""CREATE TABLE IF NOT EXISTS candidate_identity_v2(
            ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
            can_simulate INTEGER,status TEXT,detail TEXT,checked_at TEXT,
            PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")
        db.execute("INSERT OR REPLACE INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
                   (terms.symbol, "FCI", terms.market, "ARS", terms.settlement,
                    1, "AVAILABLE", "A232_SYNTHETIC", at))
    return terms


def _fake_caucion(day, at, maturity):
    return CaucionOffer(
        instrument_id="CAUCION_SYNTHETIC_PPI_LABEL", currency="ARS",
        annual_rate_fraction=Decimal(".45"), start_date=day,
        maturity_at=maturity + "T15:00:00-03:00", quoted_at=at,
        available_principal=Decimal("100000"), minimum_principal=Decimal("1000"),
        principal_step=Decimal("100"), day_count_basis=365, fee_payment="MATURITY",
        metadata_source="CONTRACT_EVIDENCE_V2:FAKE_LABEL_NOT_PROOF",
        quoted_total_fees=Decimal("10"), fee_quote_principal=Decimal("10000"),
        paper_fill_policy="CONSERVATIVE_NOTIONAL_CAP")


@pytest.mark.parametrize("day,maturity", [
    ("2026-10-31", "2026-11-02"),  # Saturday
    ("2026-12-25", "2026-12-28"),  # Audited BYMA holiday
])
def test_saturday_and_holiday_go_do_not_create_fci_or_caucion_paper_entry(
        tmp_path, monkeypatch, day, maturity):
    store, broker = _runtime(tmp_path, monkeypatch)
    at = day + "T12:00:00-03:00"
    terms = _catalog_fci(store, at)
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))  # forged GO

    with pytest.raises(ValueError, match="FCI_RUNTIME_MARKET_CLOSED_OR_CALENDAR_UNKNOWN"):
        broker.family_paper.subscribe_fund(
            terms, lifecycle_id="FCI:" + day, event_id="FCI:SUB:" + day,
            amount="10000", occurred_at=at,
            cash_guard=lambda _db: Decimal("1000000"), admission_guard=lambda _db: "")
    with pytest.raises(ValueError, match="CAUCION_RUNTIME_MARKET_CLOSED_OR_CALENDAR_UNKNOWN"):
        broker.cauciones.place(
            _fake_caucion(day, at, maturity), Decimal("10000"), "CAUCION:" + day,
            at, available_cash=lambda _currency, _at, _db: Decimal("1000000"),
            admission=lambda _db, _currency, _at, _fees: "")

    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 0
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()) == (
            "PRODUCTION_PAPER", 0)


def test_fci_weekday_go_and_ready_catalog_still_need_authenticated_class_calendar(
        tmp_path, monkeypatch):
    store, broker = _runtime(tmp_path, monkeypatch)
    at = "2026-10-05T13:00:00-03:00"  # verified BYMA business day, no class cutoff
    terms = _catalog_fci(store, at)
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    with pytest.raises(ValueError, match="FCI_RUNTIME_SOURCE_CALENDAR_NOT_AUTHENTICATED"):
        broker.family_paper.subscribe_fund(
            terms, lifecycle_id="FCI:MONDAY", event_id="FCI:MONDAY:NEW",
            amount="10000", occurred_at=at,
            cash_guard=lambda _db: Decimal("1000000"), admission_guard=lambda _db: "")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 0
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()) == (
            "PRODUCTION_PAPER", 0)


@pytest.mark.parametrize("clock", ["09:00:00", "17:00:00"])
def test_fci_and_caucion_weekday_outside_conservative_session_stay_closed(
        tmp_path, monkeypatch, clock):
    store, broker = _runtime(tmp_path, monkeypatch)
    at = "2026-10-05T" + clock + "-03:00"
    terms = _catalog_fci(store, at)
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    with pytest.raises(ValueError, match="FCI_RUNTIME_OUTSIDE_CONSERVATIVE_MARKET_WINDOW"):
        broker.family_paper.subscribe_fund(
            terms, lifecycle_id="FCI:OUTSIDE", event_id="FCI:OUTSIDE:NEW",
            amount="10000", occurred_at=at,
            cash_guard=lambda _db: Decimal("1000000"), admission_guard=lambda _db: "")
    with pytest.raises(ValueError, match="CAUCION_RUNTIME_OUTSIDE_CONSERVATIVE_MARKET_WINDOW"):
        broker.cauciones.place(
            _fake_caucion("2026-10-05", at, "2026-10-08"), Decimal("10000"),
            "CAUCION:OUTSIDE", at,
            available_cash=lambda _currency, _at, _db: Decimal("1000000"),
            admission=lambda _db, _currency, _at, _fee: "")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 0


def _live_ppi_fixture(store, at):
    """Local source/book/history contract fixture; NO external PPI proof."""
    catalog.init_schema(store)
    init_live_schema(store)
    with store.connect() as db:
        catalog.persist(db, {
            "ticker": "PESOS3", "instrument_type": "CAUCIONES", "market": "BYMA",
            "currency": "ARS", "settlement": "INMEDIATA", "settlement_source": "PPI_FIELD",
            "description": "A232 synthetic local PPI BID fixture", "last_seen_at": at,
            "run_id": "A232_SYNTHETIC", "status": "AVAILABLE",
            "capability": "READY_PAPER_CAUCION_PLACING",
            "raw": {"_discovery_source": "PPI_PRIMARY", "paper_caucion_contract_v1": {
                "paper_fill_policy": "LIVE_PPI_BID_PARTICIPATION_CAP",
                "fee_authority": CAUCION_PAPER_FEE_AUTHORITY,
                "minimum_principal": "100000", "paper_principal_step": "1",
                "day_count_basis": 365, "fee_payment": "MATURITY"}},
        })
        db.execute("""CREATE TABLE candidate_identity_v2(
            ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
            can_simulate INTEGER,status TEXT,detail TEXT,checked_at TEXT,
            PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")
        db.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
                   ("PESOS3", "CAUCIONES", "BYMA", "ARS", "INMEDIATA",
                    1, "AVAILABLE", "A232_SYNTHETIC", at))
        book = ("PESOS3", "BYMA", "ARS", "INMEDIATA", 3, at,
                ".50", "1000000", "BOOK_READY", LIVE_PPI_SOURCE, at)
        db.execute("INSERT INTO paper_caucion_live_book VALUES(?,?,?,?,?,?,?,?,?,?,?)", book)
        db.execute("""INSERT INTO paper_caucion_live_book_history(
            ticker,market,currency,settlement,term_days,quoted_at,tna_fraction,
            available_principal,state,source,observed_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""", book)
    offers, errors = offers_from_store(store, now=at)
    assert not errors and len(offers) == 1
    return offers[0]


def test_runtime_caucion_monday_requires_reconstructed_live_ppi_book_and_is_idempotent(
        tmp_path, monkeypatch):
    store, broker = _runtime(tmp_path, monkeypatch)
    at = "2026-10-05T16:40:00-03:00"
    offer = _live_ppi_fixture(store, at)
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))

    first = broker.place_caucion(offer, "100000", "A232:MONDAY", as_of=at)
    second = broker.place_caucion(offer, "100000", "A232:MONDAY", as_of=at)
    assert first["paper_id"] == second["paper_id"]
    assert first["status"] == "OPEN" and Decimal(first["total_fees"]) > 0
    assert broker._cash(at, "ARS") == Decimal("1900000")  # only one principal debit
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 1
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()) == (
            "PRODUCTION_PAPER", 0)

    # A forged source label, altered economics, or missing PPI history receipt
    # cannot open another position even while GO stays permissive.
    fake = replace(offer, metadata_source="PPI_PRIMARY_LIVE_BOOK+PAPER_POLICY:v1;FAKE")
    with pytest.raises(ValueError, match="CAUCION_RUNTIME_PPI_LIVE_BOOK_OR_TERMS_UNVERIFIED"):
        broker.place_caucion(fake, "100000", "A232:FAKE_METADATA", as_of=at)
    with store.connect() as db:
        db.execute("DELETE FROM paper_caucion_live_book_history")
    with pytest.raises(ValueError, match="CAUCION_RUNTIME_PPI_IDENTITY_OR_RECEIPT_UNVERIFIED"):
        broker.place_caucion(offer, "100000", "A232:MISSING_HISTORY", as_of=at)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 1


def test_runtime_caucion_fake_v2_metadata_on_monday_is_not_source_authority(
        tmp_path, monkeypatch):
    store, broker = _runtime(tmp_path, monkeypatch)
    at = "2026-10-05T12:00:00-03:00"
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    with pytest.raises(ValueError, match="CAUCION_RUNTIME_PPI_LIVE_CONTRACT_REQUIRED"):
        broker.cauciones.place(
            _fake_caucion("2026-10-05", at, "2026-10-08"), Decimal("10000"),
            "A232:FAKE_V2", at,
            available_cash=lambda _currency, _at, _db: Decimal("1000000"),
            admission=lambda _db, _currency, _at, _fee: "")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state WHERE id=1").fetchone()[0] == 0


@pytest.mark.parametrize("damage", [
    "REVOKE_CANDIDATE", "FAKE_BOOK_SOURCE", "STALE_BOOK", "CHANGE_FEE_AUTHORITY",
    "FUTURE_RECEIPT", "STALE_RECEIPT",
])
def test_runtime_caucion_runtime_quote_contract_or_candidate_drift_is_atomic_hold(
        tmp_path, monkeypatch, damage):
    store, broker = _runtime(tmp_path, monkeypatch)
    at = "2026-10-05T16:40:00-03:00"
    offer = _live_ppi_fixture(store, at)
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    with store.connect() as db:
        if damage == "REVOKE_CANDIDATE":
            db.execute("UPDATE candidate_identity_v2 SET can_simulate=0 WHERE ticker='PESOS3'")
        elif damage == "FAKE_BOOK_SOURCE":
            db.execute("UPDATE paper_caucion_live_book SET source='IOL' WHERE ticker='PESOS3'")
        elif damage == "STALE_BOOK":
            db.execute("UPDATE paper_caucion_live_book SET observed_at=? WHERE ticker='PESOS3'",
                       ("2026-10-05T16:30:00-03:00",))
        elif damage in {"FUTURE_RECEIPT", "STALE_RECEIPT"}:
            time = ("2026-10-05T16:41:00-03:00" if damage == "FUTURE_RECEIPT"
                    else "2026-10-05T16:37:00-03:00")
            db.execute("UPDATE paper_caucion_live_book SET observed_at=? WHERE ticker='PESOS3'",
                       (time,))
            db.execute("UPDATE paper_caucion_live_book_history SET observed_at=? WHERE ticker='PESOS3'",
                       (time,))
        else:
            raw = json.loads(db.execute("SELECT metadata_json FROM financial_instrument_catalog "
                                        "WHERE ticker='PESOS3'").fetchone()[0])
            raw["paper_caucion_contract_v1"]["fee_authority"] = "FAKE_TARIFF"
            db.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='PESOS3'",
                       (json.dumps(raw),))
    with pytest.raises(ValueError, match="CAUCION_RUNTIME_PPI_"):
        broker.place_caucion(offer, "100000", "A232:DRIFT:" + damage, as_of=at)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 0
        assert tuple(db.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()) == (
            "PRODUCTION_PAPER", 0)
