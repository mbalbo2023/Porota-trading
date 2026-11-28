"""PAPER regression for the 27 November window using a synthetic signed book."""
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
import json
from threading import Event

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

import bu_instrument_catalog as catalog
import di_caucion_cash_sweep_runtime_hf6 as sweep
import rc6_caucion_repo_book as repo_book
import rc6_paper_admission_gate as gate
from be_paper_engine import PaperBroker, PaperStore
from bf_production_paper_observer import _support_schema
from cg_paper_workspace import artifact_root
from rc6_ppi_shared_feed import _canonical_bytes
from scripts import rc6_operator_paper_rearm as operator


AT = datetime.fromisoformat("2026-11-27T16:40:00-03:00")
MATURITY = datetime.fromisoformat("2026-11-30T17:00:00-03:00")
IDENTITY = ["PESOS3", "CAUCIONES", "BYMA", "ARS", "INMEDIATA"]
RELEASE = ["b" * 40, "c" * 40]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    path = tmp_path / "paper.db"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    monkeypatch.setenv("PAPER_INITIAL_CAPITAL_ARS", "2000000")
    monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "ACTIVE_PAPER")
    monkeypatch.setenv("POROTA_RC6_MARKET_PROVIDER", "IOL_REST")
    monkeypatch.setenv("POROTA_BUILD_SHA", RELEASE[0])
    monkeypatch.setenv("POROTA_CANDIDATE_TREE_SHA", RELEASE[1])
    monkeypatch.setattr(gate, "admission_status", lambda **_: (True, ""))
    store = PaperStore(str(path))
    _support_schema(store)
    raw = {"ticker": IDENTITY[0], "type": "CAUCIONES", "market": "BYMA",
           "currency": "Pesos", "_discovery_source": "PPI_PRIMARY"}
    record = catalog.normalize_record(raw, "INMEDIATA", AT.isoformat(), "SYNTHETIC")
    with store.connect() as db:
        catalog.persist(db, record)
        catalog.sync_candidate_universe(db, AT.isoformat())
        assert tuple(db.execute("SELECT can_simulate,status,detail FROM candidate_identity_v2").fetchone()) == (
            0, "AVAILABLE", "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED;CAPABILITY:NEEDS_CAUCION_TERMS")
    store.state(mode="PRODUCTION_PAPER", real_orders_sent=0)
    return store


@pytest.fixture
def publisher(runtime, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes_raw()

    def operator_pin(directory, *, receipt_name):
        assert directory == operator.OPERATOR_DIR_RUNTIME and receipt_name == repo_book.AUTHORITY_NAME
        return {"schema": repo_book.AUTHORITY_SCHEMA, "release": RELEASE,
                "provider": "BYMA_STRUCTURED_API", "signer_sha256": sha256(public).hexdigest()}

    monkeypatch.setattr(operator, "_root_owned_receipt", operator_pin)

    def publish(*, updated=None, duplicate=False, signing_key=key, mutate_signature=False):
        book = {"identity": IDENTITY, "provider_instrument_id": "SYNTHETIC-REPO-3D",
                "provider_payload_sha256": "1" * 64, "side": "COLOCADORA", "term_days": 3,
                "freshness_basis": "PROVIDER_BOOK_TIMESTAMP",
                "provider_book_at": (AT - timedelta(seconds=5)).isoformat(),
                "received_at": (AT - timedelta(seconds=3)).isoformat(),
                "maturity_at": MATURITY.isoformat(), "annual_rate_fraction": "0.20",
                "available_principal": "2000000", "minimum_principal": "100000",
                "principal_step": "1", "day_count_basis": 365, "fee_payment": "UPFRONT",
                "quoted_total_fees": "24", "fee_quote_principal": "100000"}
        book.update(updated or {})
        claims = {"schema": repo_book.SCHEMA, "release": RELEASE,
                  "provider": "BYMA_STRUCTURED_API", "books": [book]}
        if duplicate:
            claims["books"].append({**book, "annual_rate_fraction": "0.23"})
        signature = signing_key.sign(_canonical_bytes(claims))
        if mutate_signature:
            book["available_principal"] = "10000000"
        root = artifact_root(runtime.path)
        root.mkdir(parents=True, exist_ok=True)
        (root / repo_book.RECEIPT_NAME).write_text(json.dumps({
            "claims": claims, "signature_hex": signature.hex(),
            "public_key_hex": signing_key.public_key().public_bytes_raw().hex(),
        }))

    return publish


def _read_tick(store, at=AT):
    class OneTick(Event):
        def wait(self, _timeout=None):
            with store.connect() as db:
                self.state = dict(db.execute("SELECT * FROM paper_caucion_cash_sweep_state").fetchone())
            self.set()
            return True

    stop = OneTick()
    sweep.run_worker(store, stop, clock_fn=lambda: at)
    return stop.state


def _legacy_reserve(store, amount):
    with store.connect() as db:
        db.execute("""INSERT INTO paper_family_lifecycle VALUES(
            'legacy-unposted','FCI','SYNTHETIC_FCI','ARS','SUBSCRIBE_REQUESTED',?,?,?)""",
            (AT.isoformat(), str(-amount), json.dumps({"due_at": MATURITY.isoformat()})))


def test_book_arrival_after_hold_places_once_and_settles_cash_once(runtime, publisher):
    assert _read_tick(runtime)["code"] == repo_book.CLOCK_UNVERIFIED
    assert _read_tick(runtime, AT + timedelta(seconds=1))["code"] == repo_book.CLOCK_UNVERIFIED
    _legacy_reserve(runtime, 1_800_000)
    publisher()
    placed = _read_tick(runtime, AT + timedelta(seconds=2))
    replay = _read_tick(runtime, AT + timedelta(seconds=3))
    assert placed["state"] == replay["state"] == "PLACED_SIMULATED", placed
    assert placed["paper_id"] == replay["paper_id"] and placed["paper_id"]
    assert placed["real_orders_sent"] == replay["real_orders_sent"] == 0
    assert placed["routes_json"] == replay["routes_json"] == "[]"
    with runtime.connect() as db:
        attempts = db.execute("SELECT status,result_json FROM paper_caucion_cash_sweep_attempts ORDER BY rowid").fetchall()
        assert [row[0] for row in attempts] == ["HOLD", "PLACED_SIMULATED"]
        result = json.loads(attempts[-1]["result_json"])
        assert Decimal(result["plan"]["required_reserve"]) == 1_800_000
        assert Decimal(result["plan"]["cash_debit"]) == 100_024
        assert db.execute("SELECT COUNT(*) FROM paper_caucion_allocations").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
        assert db.execute("SELECT settlement_source FROM financial_instrument_catalog").fetchone()[0] == "REQUEST_CANDIDATE"
    broker = PaperBroker(runtime, initial_cash="2000000")
    assert broker._cash(AT + timedelta(seconds=3), "ARS") == 1_899_976
    assert broker.settle_cauciones(MATURITY - timedelta(seconds=1)) == []
    assert broker.settle_cauciones(MATURITY) == [placed["paper_id"]]
    assert broker.settle_cauciones(MATURITY) == []
    assert broker._cash(MATURITY, "ARS") == Decimal("2000140.38")
    with runtime.connect() as db:
        assert tuple(db.execute("SELECT status,interest_days FROM paper_cauciones").fetchone()) == ("MATURED", 3)
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


@pytest.mark.parametrize(("updated", "reason"), [
    ({"freshness_basis": "HTTP_CAPTURE"}, repo_book.CLOCK_UNVERIFIED),
    ({"provider_book_at": "2026-11-27T16:39:55"}, repo_book.CLOCK_UNVERIFIED),
    ({"side": "TOMADORA"}, "CAUCION_REPO_BOOK_IDENTITY_OR_SIDE_INVALID"),
    ({"identity": ["PESOS3", "CAUCIONES", "BYMA", "USD", "INMEDIATA"]},
     "CAUCION_REPO_BOOK_IDENTITY_OR_SIDE_INVALID"),
    ({"term_days": 1}, "CAUCION_TERM_IDENTITY_MISMATCH"),
    ({"maturity_at": "2026-11-28T17:00:00-03:00"}, "CAUCION_REPO_BOOK_MATURITY_UNVERIFIED"),
    ({"maturity_at": "2026-11-30"}, "CAUCION_REPO_BOOK_MATURITY_UNVERIFIED"),
    ({"maturity_at": "2026-11-30T18:00:00-03:00"}, "NO_VERIFIED_POSITIVE_NET_CAUCION_FITS_LIQUIDITY"),
    ({"provider_book_at": "2026-11-27T16:37:00-03:00"}, "CAUCION_REPO_BOOK_STALE_OR_FUTURE"),
    ({"received_at": "2026-11-27T16:41:00-03:00"}, "CAUCION_REPO_BOOK_STALE_OR_FUTURE"),
    ({"quoted_total_fees": None}, "CAUCION_REPO_BOOK_EXACT_FEE_REQUIRED"),
])
def test_invalid_book_holds(runtime, publisher, updated, reason):
    publisher(updated=updated)
    state = _read_tick(runtime)
    assert (state["state"], state["code"], state["real_orders_sent"]) == ("HOLD", reason, 0)
    with runtime.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 0


@pytest.mark.parametrize("case", [
    "wrong_signer", "tampered", "duplicate", "no_go", "late_no_go", "off", "cash_reserved", "release_changed",
])
def test_untrusted_book_or_admission_cannot_place(runtime, publisher, monkeypatch, case):
    options, reason = {
        "wrong_signer": ({"signing_key": Ed25519PrivateKey.generate()}, "CAUCION_REPO_BOOK_SIGNER_UNVERIFIED"),
        "tampered": ({"mutate_signature": True}, "CAUCION_REPO_BOOK_SIGNATURE_INVALID"),
        "duplicate": ({"duplicate": True}, "CAUCION_REPO_BOOK_DUPLICATE_IDENTITY"),
        "no_go": ({}, "NO_GO_PAPER_SCALPING"),
        "late_no_go": ({}, "OPERATOR_NO_GO_PENDING_EXPLICIT_REARM"),
        "off": ({}, "CAUCION_REPO_BOOK_SWEEP_DISABLED"),
        "cash_reserved": ({}, "NO_FREE_SETTLED_CASH_AFTER_RESERVE"),
        "release_changed": ({}, "CAUCION_REPO_BOOK_AUTHORITY_INVALID"),
    }[case]
    publisher(**options)
    if case == "no_go":
        monkeypatch.setattr(gate, "admission_status", lambda **_: (False, reason))
    if case == "late_no_go":
        decisions = iter(((True, ""), (False, reason)))
        monkeypatch.setattr(gate, "admission_status", lambda **_: next(decisions))
    if case == "off":
        monkeypatch.setenv("PAPER_CAUCION_SWEEP_MODE", "OFF")
    if case == "cash_reserved":
        _legacy_reserve(runtime, 2_000_000)
    if case == "release_changed":
        monkeypatch.setenv("POROTA_CANDIDATE_TREE_SHA", "a" * 40)
    state = _read_tick(runtime)
    assert (state["state"], state["code"], state["real_orders_sent"]) == ("HOLD", reason, 0)
    with runtime.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["book", "identity", "reserve"])
def test_allocation_rechecks_evidence_and_cash_under_ledger_lock(runtime, publisher, change):
    publisher()
    offers, errors = repo_book.iol_offers_from_store(runtime, at=AT)
    assert len(offers) == 1 and not errors
    schedule = sweep.paper_schedule(AT)
    snapshot = sweep.obligation_snapshot_from_ledger(
        runtime, observed_at=AT, liquidity_deadline=schedule["liquidity_deadline"])
    reason = "CAUCION_REPO_BOOK_OR_TERMS_UNVERIFIED"
    if change == "book":
        publisher(updated={"annual_rate_fraction": "0.30"})
    elif change == "identity":
        with runtime.connect() as db:
            db.execute("UPDATE candidate_identity_v2 SET detail='RETRY_IDENTITY_AMBIGUOUS'")
    else:
        _legacy_reserve(runtime, 2_000_000)
        reason = "CAUCION_REPO_BOOK_CASH_RESERVE_CHANGED"
    broker = PaperBroker(runtime, initial_cash="2000000")
    with pytest.raises(ValueError, match=reason):
        sweep.run_paper_sweep(
            broker, offers, obligation_snapshot=snapshot, currency="ARS", as_of=AT,
            request_id=sweep.daily_sweep_request_id(AT), **schedule)
    with runtime.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_cauciones").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM paper_caucion_allocations").fetchone()[0] == 0
