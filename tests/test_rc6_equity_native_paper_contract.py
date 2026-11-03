"""Native equity PAPER regression with exact identities and synthetic books.

The catalog identities originate in the operator's 2026-08-27 PPI diagnostic
fixture. All quotes, candidate readiness, and preopen GO below are synthetic:
these tests prove code paths, not current market availability, a live preopen
receipt, account-specific charges, or out-of-sample strategy edge.
"""

import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from be_paper_engine import PaperBroker, PaperStore, Quote
from bq_exit_policy import PaperSessionPolicy
import bu_instrument_catalog as catalog
import rc6_paper_admission_gate as preopen


AT = datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)
PRICES = ("100", "100.2", "100.4", "100.6", "100.8", "101", "101.4", "102")


@pytest.fixture(autouse=True)
def synthetic_policy(monkeypatch):
    # A test receipt cannot certify a productive preopen. The real admission
    # guard still checks the catalog/candidate under the PAPER write path.
    monkeypatch.setattr(preopen, "admission_status", lambda: (True, ""))
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "OBSERVATION_ONLY")


def _setup(tmp_path, *, symbol, family):
    raw = next(item for item in json.loads(
        (ROOT / "tests/fixtures/ppi_catalog_20260827.json").read_text()
    )["records"] if item["ticker"] == symbol)
    assert raw["type"] == family and raw["currency"] == "Pesos"
    store = PaperStore(str(tmp_path / "equity-native.db"))
    catalog.init_schema(store)
    observed_at = AT.isoformat()
    record = catalog.normalize_record(raw, "A-24HS", observed_at, "SYNTHETIC_TEST_PROJECTION")
    assert record["capability"] == "READY_PAPER_SPOT"
    with store.connect() as connection:
        catalog.persist(connection, record)
        connection.execute("""CREATE TABLE candidate_identity_v2(
            ticker TEXT,instrument_type TEXT,market TEXT,currency TEXT,
            settlement TEXT,can_simulate INTEGER,status TEXT,detail TEXT,
            checked_at TEXT)""")
        connection.execute("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
                           (symbol, family, "BYMA", "ARS", "A-24HS", 1,
                            "AVAILABLE", "SYNTHETIC_TEST_ONLY", observed_at))
    verified = catalog.lookup(store, symbol, family, "A-24HS", market="BYMA", currency="ARS")
    terms = catalog.quote_terms(verified, now=AT)
    assert terms["opening_block_reason"] == ""
    broker = PaperBroker(store, initial_cash="1000000",
                         session_policy=PaperSessionPolicy(), target_gain_pct="0.05",
                         admission_guard=preopen.paper_admission_guard)
    return store, broker, terms


def _quote(symbol, family, terms, price, minute, *, at=None):
    stamp = (at or AT + timedelta(minutes=minute)).isoformat()
    price = Decimal(price)
    return Quote(symbol, family, "A-24HS", price, price - Decimal(".10"),
                 price + Decimal(".10"), Decimal("100000"), Decimal("100000"),
                 stamp, book_at=stamp, trade_at=stamp, last_kind="TRADE", **terms)


def _signal(store, broker, symbol, family, terms):
    for minute, price in enumerate(PRICES):
        quote = _quote(symbol, family, terms, price, minute)
        store.add_quote(quote)
        broker.on_quote(quote)


@pytest.mark.parametrize("symbol,family", [("ALUA", "ACCIONES"), ("AAPL", "CEDEARS")])
def test_exact_identity_native_signal_admission_and_complete_paper_sale(tmp_path, symbol, family):
    store, broker, terms = _setup(tmp_path, symbol=symbol, family=family)
    _signal(store, broker, symbol, family, terms)

    opened = store.open_positions()
    assert len(opened) == 1
    position = opened[0]
    assert (position["symbol"], position["asset_class"], position["market"],
            position["currency"], position["settlement"]) == (
                symbol, family, "BYMA", "ARS", "A-24HS")
    assert position["paper_id"].startswith("PAPER-")
    with store.connect() as connection:
        approved = connection.execute("""SELECT COUNT(*) FROM trade_gate_evaluations
            WHERE symbol=? AND final_result='OPENED_SIMULATED'""", (symbol,)).fetchone()[0]
        detail = json.loads(connection.execute("""SELECT detail_json FROM trade_gate_evaluations
            WHERE symbol=? AND final_result='OPENED_SIMULATED'""", (symbol,)).fetchone()[0])
    assert approved == 1
    signal = detail["entry_signal_inputs"]
    assert signal["price_sample_status"] == "NATIVE_EXACT_VECTOR"
    assert signal["temporal_contract"]["temporal_ready"] is True
    assert [item["price"] for item in signal["price_samples"]] == list(PRICES)
    assert all((item["symbol"], item["asset_class"], item["currency"],
                item["market"], item["settlement"]) ==
               (symbol, family, "ARS", "BYMA", "A-24HS")
               for item in signal["price_samples"])
    costs = detail["economics"]["cost_contract"]
    assert costs["authority"] == "EXPLICIT_PAPER_ASSUMPTIONS"
    assert costs["account_authority"] == costs["account_terms"] == "NO_VERIFICADO"

    closing = _quote(symbol, family, terms,
                     str(Decimal(position["target_price"]) + 1), 9)
    store.add_quote(closing)
    broker.on_quote(closing)
    assert store.open_positions() == []
    closed = store.recent_closed(1)[0]
    assert closed["paper_id"] == position["paper_id"]
    assert closed["close_reason"] == "TAKE_PROFIT_PAPER"
    quantity = Decimal(closed["quantity"])
    gross = (Decimal(closed["exit_price"]) - Decimal(closed["entry_price"])) * quantity
    assert Decimal(closed["gross_pnl"]) == gross
    assert Decimal(closed["net_pnl"]) == (gross - Decimal(closed["entry_cost"])
                                           - Decimal(closed["exit_cost"])) > 0
    with store.connect() as connection:
        fills = connection.execute("SELECT side,source FROM paper_fills ORDER BY id").fetchall()
        learning = connection.execute("SELECT outcome,label_timestamp FROM paper_learning_samples").fetchone()
    assert [(row["side"], row["source"]) for row in fills] == [
        ("BUY_SIMULATED", "PRODUCTION_PAPER"), ("SELL_SIMULATED", "PRODUCTION_PAPER")]
    assert learning["outcome"] == "WIN" and learning["label_timestamp"]


@pytest.mark.parametrize("symbol,family", [("ALUA", "ACCIONES"), ("AAPL", "CEDEARS")])
def test_native_gate_rejects_unready_exact_candidate_after_positive_signal(tmp_path, symbol, family):
    store, broker, terms = _setup(tmp_path, symbol=symbol, family=family)
    with store.connect() as connection:
        connection.execute("UPDATE candidate_identity_v2 SET can_simulate=0,status='PAUSED_EXPLICIT'")
    _signal(store, broker, symbol, family, terms)
    assert store.open_positions() == []
    with store.connect() as connection:
        rows = connection.execute("""SELECT d.action,g.final_result,g.reason
            FROM trade_gate_evaluations g JOIN paper_decisions d USING(decision_key)
            WHERE g.symbol=? ORDER BY g.id""", (symbol,)).fetchall()
    assert rows and rows[-1]["action"] == "BUY"
    assert rows[-1]["final_result"] == "BLOCKED"
    assert rows[-1]["reason"] == "PAPER_EXACT_CONTRACT_NOT_READY"


@pytest.mark.parametrize("symbol,family", [("ALUA", "ACCIONES"), ("AAPL", "CEDEARS")])
def test_native_ledger_rejects_contradictory_contract_after_positive_signal(tmp_path, symbol, family):
    store, broker, terms = _setup(tmp_path, symbol=symbol, family=family)
    terms = dict(terms, contract=replace(terms["contract"], symbol="OTHER"))
    _signal(store, broker, symbol, family, terms)
    assert store.open_positions() == []
    with store.connect() as connection:
        decision = connection.execute("""SELECT action FROM paper_decisions
            WHERE symbol=? ORDER BY id DESC LIMIT 1""", (symbol,)).fetchone()
        gate = connection.execute("""SELECT final_result,reason FROM trade_gate_evaluations
            WHERE symbol=? ORDER BY id DESC LIMIT 1""", (symbol,)).fetchone()
        fills = connection.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
    assert decision["action"] == "BUY"
    assert gate["final_result"] == "BLOCKED"
    assert gate["reason"] == "Contrato incompatible con esta cotización y libro de caja"
    assert fills == 0


@pytest.mark.parametrize("symbol,family", [("ALUA", "ACCIONES"), ("AAPL", "CEDEARS")])
def test_monetary_contradiction_and_closed_session_hold_before_native_open(tmp_path, symbol, family):
    store, broker, terms = _setup(tmp_path, symbol=symbol, family=family)
    first = _quote(symbol, family, terms, PRICES[0], 0)
    contradictory = replace(first, currency="USD_MEP")
    store.add_quote(contradictory)
    broker.on_quote(contradictory)
    assert store.open_positions() == []
    assert broker.decide(contradictory)[0] == "HOLD"
    assert "contradice" in broker.decide(contradictory)[2]

    weekend = _quote(symbol, family, terms, PRICES[0], 0,
                     at=datetime(2026, 8, 29, 14, 0, tzinfo=timezone.utc))
    store.add_quote(weekend)
    broker.on_quote(weekend)
    assert store.open_positions() == []
    assert broker.decide(weekend)[0:3:2] == ("HOLD", "MARKET_CLOSED_OR_CALENDAR_UNKNOWN")
