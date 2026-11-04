"""Offline FI exit regression: generic spot SELL must never own a VN lifecycle."""
from dataclasses import asdict
from decimal import Decimal
import json

import pytest

from be_paper_engine import PaperBroker, PaperStore, Quote
from bq_exit_policy import PaperSessionPolicy
from bs_instrument_contracts import contract_from_metadata
from rc6_performance.costs import paper_cost_contract
from test_rc6_fixed_income_event_ledger import BOOKED, apply, lot, receipt


OPENED = "2026-10-27T14:00:00+00:00"
EXIT = "2026-10-27T14:30:00+00:00"


def _quote(symbol, family, at):
    contract = contract_from_metadata(symbol, family, {
        "market": "BYMA", "currency": "ARS", "settlement": "A-24HS",
        "cash_multiplier": "0.01", "quantity_step": "1", "minimum_quantity": "1",
        "metadata_source": "SYNTHETIC_PPI_EXACT_CONTRACT",
    })
    return Quote(symbol, family, "A-24HS", Decimal("105"), Decimal("105"),
                 Decimal("105.1"), Decimal("1000"), Decimal("1000"), at,
                 contract=contract, currency="ARS", market="BYMA",
                 metadata_source="SYNTHETIC_PPI_EXACT_CONTRACT",
                 book_at=at, trade_at=at, last_kind="TRADE")


def _broker(store, at):
    return PaperBroker(store, initial_cash="1000", clock_fn=lambda: at,
                       session_policy=PaperSessionPolicy(),
                       require_supervisor=False, daily_loss_pct="2.5")


def _open_without_native_lot(tmp_path, symbol, family):
    store = PaperStore(str(tmp_path / "fi-paper.db"))
    q = _quote(symbol, family, EXIT)
    policy = paper_cost_contract(family, "ARS", OPENED)
    features = {
        "financial_contract": asdict(q.contract),
        "contract_cash_multiplier": "0.01",
        "contract_quantity_step": "1",
        "economics": {"cost_contract": policy.diagnostic()},
    }
    entry_fee = policy.leg_cost(Decimal("100"), decision_at=OPENED)
    with store.connect() as db:
        db.execute("""INSERT INTO paper_positions
            (paper_id,source,strategy_version,symbol,asset_class,settlement,status,
             quantity,entry_price,entry_cost,stop_price,target_price,opened_at,
             features_json,currency,market,currency_source)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("PAPER-FI", "PRODUCTION_PAPER", "SYNTHETIC_NO_ENTRY_AUTHORITY",
             symbol, family, "A-24HS", "OPEN", "100", "100", str(entry_fee),
             "90", "110", OPENED, json.dumps(features, default=str),
             "ARS", "BYMA", "SYNTHETIC"))
        db.execute("""INSERT INTO paper_fills
            (paper_id,source,side,filled_at,quantity,price,costs,slippage)
            VALUES(?,?,?,?,?,?,?,?)""",
            ("PAPER-FI", "PRODUCTION_PAPER", "BUY_SIMULATED",
             OPENED, "100", "100", str(entry_fee), "0"))
        assert db.execute("SELECT count(*) FROM paper_fi_lots").fetchone()[0] == 0
    return store, q


@pytest.mark.parametrize("symbol,family", [
    ("AL30", "BONOS"), ("D30N6", "LETRAS"), ("YMCJO", "OBLIGACIONES"),
])
def test_fi_without_native_lot_never_falls_through_generic_sell(
        tmp_path, symbol, family):
    store, quote = _open_without_native_lot(tmp_path, symbol, family)
    broker = _broker(store, EXIT)
    position = store.open_positions()[0]
    cash_before = broker._cash(EXIT, "ARS")
    assert broker._close(position, quote, "SYNTHETIC_FI_EXIT_PROBE", as_of=EXIT) is False
    with store.connect() as db:
        assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "OPEN"
        assert [r[0] for r in db.execute("SELECT side FROM paper_fills")] == [
            "BUY_SIMULATED"]
        assert db.execute("SELECT count(*) FROM paper_fi_lots").fetchone()[0] == 0
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
    assert broker._cash(EXIT, "ARS") == cash_before


@pytest.mark.parametrize("kind", ["COUPON", "AMORTIZATION"])
def test_fi_with_native_event_lot_still_never_uses_generic_sell(tmp_path, kind):
    store, settled = lot(tmp_path)
    event = (receipt(settled) if kind == "COUPON" else
             receipt(settled, kind="AMORTIZATION", event_id="full",
                     redeemed="100", cash="100", unit="1", currency="ARS"))
    apply(store, event)
    quote = _quote("AL30", "BONOS", BOOKED)
    broker = _broker(store, BOOKED)
    position = store.open_positions()[0]
    cash_before = broker._cash(BOOKED, "ARS")
    assert broker._close(position, quote, "SYNTHETIC_FI_EXIT_PROBE",
                         as_of=BOOKED) is False
    with store.connect() as db:
        assert db.execute("SELECT status FROM paper_positions").fetchone()[0] == "OPEN"
        assert [r[0] for r in db.execute("SELECT side FROM paper_fills")] == [
            "BUY_SIMULATED"]
        assert db.execute("SELECT count(*) FROM paper_fi_lots").fetchone()[0] == 1
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0
    assert broker._cash(BOOKED, "ARS") == cash_before
