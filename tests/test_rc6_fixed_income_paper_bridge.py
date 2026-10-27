"""Synthetic closed PAPER lot plus independent A123 receipt events."""
from dataclasses import asdict, replace
from decimal import Decimal
import json
import sqlite3

import pytest

from be_paper_engine import PaperStore
from bs_instrument_contracts import contract_from_metadata
from rc6_fixed_income_event_reducer import EconomicEvent, PositionSeed
from rc6_fixed_income_paper_bridge import (
    ENTRY_AUTHORITY, REAL_ROUTES_USED, reconcile_closed_paper_store,
)
from rc6_performance.costs import paper_cost_contract


OPENED = "2026-10-27T14:00:00+00:00"
CLOSED = "2026-10-27T14:30:00+00:00"
AS_OF = "2026-10-27T15:00:00+00:00"


def position(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = PaperStore(str(tmp_path / "fixed-paper.db"))
    contract = contract_from_metadata("AL30", "BONOS", {
        "market": "BYMA", "currency": "ARS", "settlement": "A-24HS",
        "cash_multiplier": "0.01", "quantity_step": "1", "minimum_quantity": "1",
        "metadata_source": "SYNTHETIC_PPI_EXACT_CONTRACT",
    })
    cost_policy = paper_cost_contract("BONOS", "ARS", OPENED)
    features = {"financial_contract": asdict(contract), "contract_cash_multiplier": "0.01",
                "economics": {"modeled_tariff": "FULL_PER_LEG_CONSERVATIVE_NO_INTRADAY_REBATE",
                              "cost_contract": cost_policy.diagnostic()}}
    with store.connect() as db:
        db.execute("""INSERT INTO paper_positions
          (paper_id,source,strategy_version,symbol,asset_class,settlement,status,
           quantity,entry_price,entry_cost,stop_price,target_price,opened_at,
           closed_at,exit_price,exit_cost,gross_pnl,net_pnl,close_reason,
           features_json,currency,market,currency_source)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          ("PAPER-AL30", "PRODUCTION_PAPER", "SYNTHETIC_NO_STRATEGY_AUTHORITY",
           "AL30", "BONOS", "A-24HS", "CLOSED", "100", "100", "0.61", "90", "110",
           OPENED, CLOSED, "105", "0.64", "5", "3.75", "SYNTHETIC_EXIT",
           json.dumps(features, default=str), "ARS", "BYMA", "SYNTHETIC"))
        db.execute("""INSERT INTO paper_fills
          (paper_id,source,side,filled_at,quantity,price,costs,slippage)
          VALUES(?,?,?,?,?,?,?,?)""",
          ("PAPER-AL30", "PRODUCTION_PAPER", "BUY_SIMULATED", OPENED,
           "100", "100", "0.61", "0"))
        db.execute("""INSERT INTO paper_fills
          (paper_id,source,side,filled_at,quantity,price,costs,slippage)
          VALUES(?,?,?,?,?,?,?,?)""",
          ("PAPER-AL30", "PRODUCTION_PAPER", "SELL_SIMULATED", CLOSED,
           "100", "105", "0.64", "0"))
    seed = PositionSeed("PAPER-AL30", "AL30", "BONOS", "BYMA", "A-24HS",
                        "ARS", "100", "VN", OPENED, "SYNTHETIC_ADMISSION",
                        "b" * 64)
    return store, seed


def coupon(**changes):
    values = dict(event_id="coupon-1", obligation_id="coupon-date-1",
                  paper_id="PAPER-AL30", kind="COUPON",
                  effective_at="2026-10-27T14:20:00+00:00",
                  source_at="2026-10-27T14:25:00+00:00",
                  recorded_at="2026-10-27T14:26:00+00:00",
                  source_kind="SETTLEMENT_PROOF", source_reference="SYNTHETIC_COUPON_RECEIPT",
                  source_sha256="c" * 64, payment_currency="USD", cash_amount="2",
                  nominal_base="100")
    values.update(changes)
    return EconomicEvent(**values)


def test_closed_paper_bond_keeps_ars_sale_and_usd_coupon_separate(tmp_path):
    store, seed = position(tmp_path)
    result = reconcile_closed_paper_store(store.path, seed, [coupon()], as_of=AS_OF)
    assert result.entry_cash == Decimal("100.61")
    assert result.exit_cash == Decimal("104.36")
    assert result.trade_net_pnl == Decimal("3.75")
    assert result.receipt_cash_by_currency == (("USD", Decimal("2")),)
    assert result.net_by_currency == (("ARS", Decimal("3.75")), ("USD", Decimal("2")))
    assert result.cost_scope == "PAPER_ESTIMATE_NOT_ACCOUNT_FEE"
    assert result.status == "CLOSED_PAPER_INPUT_RECONCILIATION_ONLY"
    assert result.entry_authority is False and ENTRY_AUTHORITY is False and REAL_ROUTES_USED == ()
    with store.connect() as db:
        assert db.execute("SELECT real_orders_sent FROM observer_state").fetchone()[0] == 0


def test_exact_receipt_replay_does_not_double_coupon(tmp_path):
    store, seed = position(tmp_path)
    result = reconcile_closed_paper_store(store.path, seed, [coupon(), coupon()], as_of=AS_OF)
    assert result.receipt_cash_by_currency == (("USD", Decimal("2")),)


@pytest.mark.parametrize("change,code", [
    ({"nominal_unit": "SHARES"}, "FIXED_INCOME_POSITION_VN_OR_TIME_MISMATCH"),
    ({"trading_currency": "USD"}, "FIXED_INCOME_POSITION_IDENTITY_MISMATCH"),
    ({"symbol": "GD30"}, "FIXED_INCOME_POSITION_IDENTITY_MISMATCH"),
])
def test_position_identity_must_match_lot_exactly(tmp_path, change, code):
    store, seed = position(tmp_path)
    with pytest.raises(ValueError, match=code):
        reconcile_closed_paper_store(store.path, replace(seed, **change), [coupon()], as_of=AS_OF)


def test_unknown_price_basis_and_pnl_mismatch_fail_closed(tmp_path):
    store, seed = position(tmp_path / "corrupt-pnl")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE paper_positions SET features_json='{}' WHERE paper_id='PAPER-AL30'")
    with pytest.raises(ValueError, match="FIXED_INCOME_PRICE_BASIS_UNVERIFIED"):
        reconcile_closed_paper_store(store.path, seed, [coupon()], as_of=AS_OF)
    store, seed = position(tmp_path)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE paper_positions SET net_pnl='99' WHERE paper_id='PAPER-AL30'")
    with pytest.raises(ValueError, match="Importes del cierre simple no concilian"):
        reconcile_closed_paper_store(store.path, seed, [coupon()], as_of=AS_OF)


def test_cost_model_binding_requires_versioned_public_paper_assumptions(tmp_path):
    store, seed = position(tmp_path)
    with sqlite3.connect(store.path) as db:
        raw = db.execute("SELECT features_json FROM paper_positions WHERE paper_id='PAPER-AL30'").fetchone()[0]
        changed = json.loads(raw)
        changed["economics"]["cost_contract"]["account_terms"] = "INFERRED_EFFECTIVE"
        db.execute("UPDATE paper_positions SET features_json=? WHERE paper_id='PAPER-AL30'",
                   (json.dumps(changed),))
    with pytest.raises(ValueError, match="FIXED_INCOME_PAPER_COST_POLICY_UNVERIFIED"):
        reconcile_closed_paper_store(store.path, seed, [coupon()], as_of=AS_OF)
    store, seed = position(tmp_path / "wrong-fee")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE paper_positions SET entry_cost='1' WHERE paper_id='PAPER-AL30'")
        db.execute("UPDATE paper_fills SET costs='1' WHERE side='BUY_SIMULATED'")
        db.execute("UPDATE paper_positions SET net_pnl='3.36' WHERE paper_id='PAPER-AL30'")
    with pytest.raises(ValueError, match="FIXED_INCOME_PAPER_FEE_LEDGER_MISMATCH"):
        reconcile_closed_paper_store(store.path, seed, [coupon()], as_of=AS_OF)


def test_partial_sale_or_future_receipt_cannot_be_called_closed_earnings(tmp_path):
    store, seed = position(tmp_path / "future-receipt")
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE paper_fills SET quantity='50' WHERE side='SELL_SIMULATED'")
    with pytest.raises(ValueError, match="Cierre simple incompatible"):
        reconcile_closed_paper_store(store.path, seed, [coupon()], as_of=AS_OF)
    store, seed = position(tmp_path)
    future = coupon(effective_at="2026-10-27T14:40:00+00:00",
                    source_at="2026-10-27T14:45:00+00:00",
                    recorded_at="2026-10-27T14:46:00+00:00")
    with pytest.raises(ValueError, match="FIXED_INCOME_POST_EXIT_CASH_NOT_ENTITLED"):
        reconcile_closed_paper_store(store.path, seed, [future], as_of=AS_OF)


def test_amortized_vn_needs_native_position_rebase_before_sale(tmp_path):
    store, seed = position(tmp_path)
    amortization = coupon(event_id="amort-1", obligation_id="amort-date-1",
                          kind="AMORTIZATION", nominal_redeemed="10", cash_amount="10")
    with pytest.raises(ValueError, match="FIXED_INCOME_AMORTIZATION_REQUIRES_NATIVE_VN_REBASE"):
        reconcile_closed_paper_store(store.path, seed, [amortization], as_of=AS_OF)


def test_receipt_unknown_at_cut_and_missing_settlement_proof_fail_closed(tmp_path):
    store, seed = position(tmp_path)
    with pytest.raises(ValueError, match="FIXED_INCOME_EVENT_CLOCK_INVALID"):
        reconcile_closed_paper_store(store.path, seed,
            [coupon(recorded_at="2026-10-27T14:45:00+00:00")],
            as_of="2026-10-27T14:40:00+00:00")
    with pytest.raises(ValueError, match="FIXED_INCOME_SETTLEMENT_PROOF_REQUIRED"):
        reconcile_closed_paper_store(store.path, seed, [coupon(source_kind="IOL_SCHEDULE")], as_of=AS_OF)
