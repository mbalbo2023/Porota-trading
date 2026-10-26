import sqlite3

import pytest

import rc6_paper_family_lifecycle as lifecycle
from bs_instrument_contracts import contract_from_metadata


class Store:
    def __init__(self, path):
        self.path = str(path)

    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection


def event(store, family, state, number, amount="0"):
    return lifecycle.apply_paper_event(
        store, lifecycle_id=f"{family}-1", event_id=f"{family}-{number}",
        family=family, instrument="TEST", currency="ARS", to_state=state,
        amount=amount, occurred_at=f"2026-09-28T18:{number:02d}:00+00:00")


def test_fci_paper_lifecycle_is_idempotent(tmp_path):
    store = Store(tmp_path / "fci.db")
    states = ["SUBSCRIBE", "PENDING", "NAV_APPLIED", "SETTLED",
              "REDEEM_REQUESTED", "REDEEMED"]
    for number, state in enumerate(states):
        result = event(store, "FCI", state, number, "10" if state == "NAV_APPLIED" else "0")
        assert result["paper_only"] is True and result["real_routes_used"] == []
    duplicate = event(store, "FCI", "NAV_APPLIED", 2, "10")
    assert duplicate == {"idempotent": True, "state": "NAV_APPLIED",
                         "real_routes_used": [], "paper_only": True}
    assert lifecycle.lifecycle_state(store, "FCI-1")["ledger_total"] == "10"


def test_futures_paper_margin_and_variation_cycle(tmp_path):
    store = Store(tmp_path / "futures.db")
    for number, state, amount in [
        (0, "OPEN", "0"), (1, "MARGIN_RESERVED", "-50000"),
        (2, "DAILY_VARIATION", "1500"), (3, "MARGIN_OK", "0"),
        (4, "DAILY_VARIATION", "-2500"), (5, "MARGIN_DEFICIT", "0"),
        (6, "CLOSE", "51000"),
    ]:
        event(store, "FUTUROS", state, number, amount)
    assert lifecycle.lifecycle_state(store, "FUTUROS-1")["state"] == "CLOSE"


def test_invalid_transition_and_identity_fail_closed(tmp_path):
    store = Store(tmp_path / "blocked.db")
    with pytest.raises(ValueError, match="INVALID_TRANSITION"):
        event(store, "FCI", "SETTLED", 0)
    event(store, "FUTUROS", "OPEN", 1)
    with pytest.raises(ValueError, match="IDENTITY_MISMATCH"):
        lifecycle.apply_paper_event(
            store, lifecycle_id="FUTUROS-1", event_id="different-identity",
            family="FUTUROS", instrument="OTHER", currency="ARS",
            to_state="MARGIN_RESERVED")


def test_module_has_no_real_route_surface():
    assert lifecycle.PAPER_ONLY is True
    assert lifecycle.REAL_ROUTES_USED == ()
    assert not any(name.startswith(("place_", "send_", "confirm_", "accept_"))
                   for name in vars(lifecycle))


def test_connected_fund_executor_enforces_ars_thousand_minimum(tmp_path):
    store = Store(tmp_path / "connected-fci.db")
    terms = lifecycle.fund_terms_from_metadata("ADCAP.AP.A", {
        "currency": "ARS", "market": "FCI", "settlement": "INMEDIATA",
        "subscription_min": "1000", "subscription_step": "1",
        "metadata_source": "PPI_OFFICIAL_DOCUMENTATION",
    })
    executor = lifecycle.FamilyPaperExecutor(store)
    with pytest.raises(ValueError, match="BELOW_MINIMUM"):
        executor.subscribe_fund(terms, lifecycle_id="fci-1", event_id="sub-1", amount="1")
    result = executor.subscribe_fund(
        terms, lifecycle_id="fci-1", event_id="sub-1000", amount="1000",
        occurred_at="2026-09-28T18:00:00+00:00")
    assert result["state"] == "SUBSCRIBE_REQUESTED" and result["real_routes_used"] == []


def test_fci_subscription_reserves_shared_paper_cash_until_recorded_redemption(tmp_path):
    from decimal import Decimal
    from be_paper_engine import PaperBroker, PaperStore

    store = PaperStore(str(tmp_path / "shared-cash.db"))
    broker = PaperBroker(store, initial_cash="10000", initial_cash_usd="5000")
    executor = broker.family_paper
    terms = lifecycle.PaperFundTerms("FUND1", "FCI", "ARS", "FCI", "INMEDIATA",
        "EXACT_PAPER_FUND_TERMS", paper_subscription_policy="INTERNAL_RISK_BUDGET_BY_AMOUNT")
    start = "2026-10-26T12:00:00+00:00"
    next_minute = "2026-10-26T12:01:00+00:00"
    end = "2026-10-26T12:04:00+00:00"
    intent = dict(lifecycle_id="fund-1", event_id="subscription-1", amount="1000",
                  occurred_at=start)
    assert broker._cash(start, "ARS") == Decimal("10000")
    assert executor.subscribe_fund(terms, **intent)["state"] == "SUBSCRIBE_REQUESTED"
    assert executor.subscribe_fund(terms, **intent)["idempotent"] is True
    assert lifecycle.fund_cash_effect(store, "ARS", start, exclusive=True) == 0
    assert broker._cash(start, "ARS") == Decimal("9000")
    assert broker._cash(start, "USD") == Decimal("5000")
    for number, state, amount in ((1, "PENDING", "0"), (2, "NAV_APPLIED", "100"),
                                  (3, "SETTLED", "0"), (4, "REDEEM_REQUESTED", "0"),
                                  (5, "REDEEMED", "1050")):
        lifecycle.apply_paper_event(store, lifecycle_id="fund-1", event_id=f"fund-{number}",
            family="FCI", instrument="FUND1", currency="ARS", to_state=state,
            amount=amount, occurred_at=f"2026-10-26T12:0{number}:00+00:00")
    assert broker._cash(next_minute, "ARS") == Decimal("9000")
    assert broker._cash(end, "ARS") == Decimal("9000")
    assert broker._cash("2026-10-26T12:05:00+00:00", "ARS") == Decimal("10050")
    with pytest.raises(ValueError, match="CASH_CLOCK_ROLLBACK"):
        broker._cash(start, "ARS", for_execution=True)
    with store.connect() as db:
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_fci_cash_projection_rejects_invalid_event_direction(tmp_path):
    from be_paper_engine import PaperBroker, PaperStore

    store = PaperStore(str(tmp_path / "invalid-fund-cash.db"))
    broker = PaperBroker(store)
    lifecycle.apply_paper_event(store, lifecycle_id="fund-bad", event_id="bad-1",
        family="FCI", instrument="FUND1", currency="ARS", to_state="SUBSCRIBE_REQUESTED",
        amount="1000", occurred_at="2026-10-26T12:00:00+00:00")
    with pytest.raises(ValueError, match="FCI_CASH_EVENT_DIRECTION_INVALID"):
        broker._cash("2026-10-26T12:00:00+00:00", "ARS")


def test_runtime_fci_subscription_requires_global_and_atomic_cash_gates(tmp_path, monkeypatch):
    from be_paper_engine import PaperBroker, PaperStore
    from dataclasses import asdict
    import json
    import rc6_paper_admission_gate as gate
    import bu_instrument_catalog as catalog

    path = tmp_path / "runtime-fund.db"
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(path))
    store = PaperStore(str(path))
    broker = PaperBroker(store, initial_cash="10000")
    catalog.init_schema(store)
    terms = lifecycle.PaperFundTerms("FUND1", "FCI", "ARS", "FCI", "INMEDIATA",
        "EXACT_PAPER_FUND_TERMS", paper_subscription_policy="INTERNAL_RISK_BUDGET_BY_AMOUNT")
    with store.connect() as db:
        catalog.persist(db, {"ticker": "FUND1", "instrument_type": "FCI",
            "market": "FCI", "currency": "ARS", "settlement": "INMEDIATA",
            "settlement_source": "PPI_FIELD", "description": "Fixture FCI",
            "last_seen_at": "2026-10-26T12:00:00+00:00", "run_id": "fixture",
            "status": "AVAILABLE", "capability": "READY_PAPER_FCI_SUBSCRIPTION",
            "raw": {"_discovery_source": "PPI_PRIMARY",
                    "paper_family_contract_v1": json.loads(json.dumps(asdict(terms), default=str))}})
        db.execute("""CREATE TABLE IF NOT EXISTS candidate_identity_v2(
            ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,settlement TEXT,
            can_simulate INTEGER,status TEXT,detail TEXT,checked_at TEXT,
            PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")
        db.execute("INSERT OR REPLACE INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
            ("FUND1", "FCI", "FCI", "ARS", "INMEDIATA", 1, "AVAILABLE", "fixture",
             "2026-10-26T12:00:00+00:00"))
    at = "2026-10-26T12:00:00+00:00"
    request = dict(lifecycle_id="fund-1", event_id="subscription-1", amount="1000",
                   occurred_at=at)
    with pytest.raises(ValueError, match="FCI_PAPER_ADMISSION_GUARDS_REQUIRED"):
        broker.family_paper.subscribe_fund(terms, **request)
    callbacks = dict(cash_guard=lambda c: broker._cash(at, "ARS", connection=c,
                                                       for_execution=True),
                     admission_guard=lambda _c: "")
    monkeypatch.setattr(gate, "admission_status", lambda: (False, "NO_GO_PAPER_SCALPING"))
    with pytest.raises(ValueError, match="NO_GO_PAPER_SCALPING"):
        broker.family_paper.subscribe_fund(terms, **request, **callbacks)
    monkeypatch.setattr(gate, "admission_status", lambda: (True, ""))
    with pytest.raises(ValueError, match="FCI_PAPER_INSUFFICIENT_CASH"):
        broker.family_paper.subscribe_fund(terms, **{**request, "amount": "11000"}, **callbacks)
    with store.connect() as db:
        db.execute("UPDATE candidate_identity_v2 SET can_simulate=0 WHERE ticker='FUND1'")
    with pytest.raises(ValueError, match="FCI_EXACT_CATALOG_NOT_READY"):
        broker.family_paper.subscribe_fund(terms, **request, **callbacks)
    with store.connect() as db:
        db.execute("UPDATE candidate_identity_v2 SET can_simulate=1 WHERE ticker='FUND1'")
        original = db.execute("SELECT metadata_json FROM financial_instrument_catalog WHERE ticker='FUND1'").fetchone()[0]
        changed = json.loads(original)
        changed["paper_family_contract_v1"]["paper_amount_unit"] = "1"
        db.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='FUND1'",
                   (json.dumps(changed),))
    with pytest.raises(ValueError, match="FCI_EXACT_TERMS_MISMATCH"):
        broker.family_paper.subscribe_fund(terms, **request, **callbacks)
    with store.connect() as db:
        db.execute("UPDATE financial_instrument_catalog SET metadata_json=? WHERE ticker='FUND1'",
                   (original,))
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 0
    result = broker.family_paper.subscribe_fund(terms, **request, **callbacks)
    assert result["state"] == "SUBSCRIBE_REQUESTED"
    monkeypatch.setattr(gate, "admission_status", lambda: (False, "NO_GO_PAPER_SCALPING"))
    assert broker.family_paper.subscribe_fund(terms, **request)["idempotent"] is True
    assert broker._cash(at, "ARS") == 9000
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events").fetchone()[0] == 1
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_connected_futures_executor_requires_real_future_contract(tmp_path):
    store = Store(tmp_path / "connected-future.db")
    contract = contract_from_metadata("DLR/DIC26", "FUTUROS", {
        "currency": "ARS", "market": "ROFEX", "settlement": "INMEDIATA",
        "cash_multiplier": "1000", "quantity_step": "1", "minimum_quantity": "1",
        "expires_at": "2026-12-31T15:00:00-03:00",
        "initial_margin": "100000", "maintenance_margin": "80000",
        "underlying": "USDARS", "metadata_source": "fixture",
    })
    executor = lifecycle.FamilyPaperExecutor(store)
    opened = executor.future_event(
        contract, lifecycle_id="future-1", event_id="open-1", to_state="OPEN")
    reserved = executor.future_event(
        contract, lifecycle_id="future-1", event_id="margin-1",
        to_state="MARGIN_RESERVED", amount="-100000")
    assert opened["paper_only"] is True and reserved["ledger_total"] == "-100000"
