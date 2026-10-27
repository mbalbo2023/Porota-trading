"""Synthetic scenarios only: no real order, fill, settlement or OOS claim."""
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from be_paper_engine import Quote
from bs_instrument_contracts import InstrumentContract
from rc6_fixed_income_event_reducer import EconomicEvent, PositionSeed
from rc6_fixed_income_strategy_shadow import (
    ContractProof, ProjectedFlow, RiskLimits, evaluate_shadow, replay_shadow_exit,
)


AT = datetime(2026, 10, 27, 14, tzinfo=timezone.utc)
EXIT = datetime(2026, 10, 29, 14, tzinfo=timezone.utc)
PUB = datetime(2026, 10, 26, 14, tzinfo=timezone.utc)
SHA = "a" * 64


def setup(family, symbol, *, currency="ARS", quote_at=AT, bid="79.9", ask="80.1"):
    contract = InstrumentContract(
        symbol=symbol, family=family, currency=currency, market="BYMA",
        settlement="A-24HS", cash_multiplier=Decimal("0.01"),
        quantity_step=Decimal("1"), minimum_quantity=Decimal("100"),
        metadata_source="SYNTHETIC_EXACT_NOMINAL_FIXTURE")
    stamp = quote_at.isoformat()
    quote = Quote(symbol, family, "A-24HS", Decimal("80"), Decimal(bid),
                  Decimal(ask), Decimal("2000"), Decimal("2000"), stamp,
                  contract=contract, currency=currency, market="BYMA",
                  metadata_source="SYNTHETIC_BOOK_FIXTURE", book_at=stamp,
                  trade_at=stamp, last_kind="TRADE")
    proof = ContractProof(contract, "fixture:independent-contract", SHA,
                          PUB.isoformat(), PUB.isoformat())
    limits = RiskLimits(cash_available="100000", max_allocation="1000",
                        max_loss_budget="200", stress_bid_drop="0.10",
                        max_spread_bps="50", max_holding_days=5,
                        participation="0.10", slippage_fraction="0.001")
    return quote, proof, limits


def flow(kind="COUPON", *, amount="0.05", currency="ARS", due=None):
    return ProjectedFlow("OBLIGATION-1", kind, (due or (EXIT if kind == "AMORTIZATION"
                                                      else EXIT - timedelta(days=1))).isoformat(),
                         amount, currency, "fixture:published-terms", SHA,
                         PUB.isoformat(), PUB.isoformat())


def assess(quote, proof, limits, flows, *, mode="MARKET_EXIT", at=AT,
           source="SYNTHETIC_TEST"):
    return evaluate_shadow(quote, proof, flows, limits, quantity_vn=100,
                           decision_at=at.isoformat(), planned_exit_at=EXIT.isoformat(),
                           exit_mode=mode, book_reference="fixture:synthetic-book",
                           book_sha256=SHA, book_source_kind=source)


@pytest.mark.parametrize("family,symbol", [
    ("BONOS", "SYNTHETIC_BOND_ARS"), ("OBLIGACIONES", "SYNTHETIC_ON_ARS")])
def test_positive_carry_is_only_static_bid_shadow_then_receipt_replay(family, symbol):
    quote, proof, limits = setup(family, symbol)
    plan = assess(quote, proof, limits, [flow(due=EXIT - timedelta(days=1))])
    assert plan.status == "SHADOW_HYPOTHESIS" and plan.signal == "POSITIVE_SCENARIO"
    assert plan.scenario_net > 0 and plan.entry_cash > 0 and plan.stress_loss <= 200
    assert plan.projected_flows == (("ARS", Decimal("5.00")),)
    assert plan.fee_policy_sha256 and plan.entry_authority is False
    assert plan.oos_status == "OOS_NO_VERIFICADO"

    seed = PositionSeed("SHADOW-ONLY", symbol, family, "BYMA", "A-24HS",
                        "ARS", "100", "VN", AT.isoformat(),
                        "fixture:unbooked-scenario", SHA)
    coupon_time = EXIT - timedelta(days=1)
    receipt = EconomicEvent("receipt-1", "OBLIGATION-1", "SHADOW-ONLY", "COUPON",
                            coupon_time.isoformat(), coupon_time.isoformat(),
                            "SETTLEMENT_PROOF", "fixture:synthetic-proof", SHA,
                            coupon_time.isoformat(), payment_currency="ARS",
                            cash_amount="5", nominal_base="100")
    later = replace(quote, last=Decimal("81"), bid=Decimal("80.9"),
                    ask=Decimal("81.1"), observed_at=EXIT.isoformat(),
                    book_at=EXIT.isoformat(), trade_at=EXIT.isoformat())
    replay = replay_shadow_exit(plan, seed, [receipt], as_of=EXIT.isoformat(),
                                exit_book=later,
                                exit_book_reference="fixture:synthetic-exit-book",
                                exit_book_sha256=SHA,
                                exit_book_source_kind="SYNTHETIC_TEST")
    assert replay.receipt_cash_by_currency == (("ARS", Decimal("5")),)
    assert replay.scenario_net_by_currency[0][0] == "ARS"
    assert replay.status == "INDICATIVE_BOOK_REPLAY_NOT_LEDGER_REALIZATION"
    assert replay.observed_entry_source == replay.observed_exit_source == "SYNTHETIC_TEST"
    assert replay.entry_authority is False


def test_fixed_ars_letter_redemption_scenario_requires_actual_settlement_proof():
    quote, proof, limits = setup("LETRAS", "SYNTHETIC_FIXED_ARS_LETTER")
    plan = assess(quote, proof, limits,
                  [flow("AMORTIZATION", amount="1", due=EXIT)],
                  mode="MATURITY_REDEMPTION")
    assert plan.signal == "POSITIVE_SCENARIO" and plan.scenario_net > 0
    assert plan.exit_cash_scenario == 0  # No fictional market sale at maturity.
    seed = PositionSeed("SHADOW-LETTER", quote.symbol, "LETRAS", "BYMA", "A-24HS",
                        "ARS", "100", "VN", AT.isoformat(), "fixture:unbooked", SHA)
    receipt = EconomicEvent("redeem-1", "OBLIGATION-1", seed.paper_id,
                            "AMORTIZATION", EXIT.isoformat(), EXIT.isoformat(),
                            "SETTLEMENT_PROOF", "fixture:synthetic-proof", SHA,
                            EXIT.isoformat(), payment_currency="ARS",
                            cash_amount="100", nominal_base="100", nominal_redeemed="100")
    with pytest.raises(ValueError, match="FI_SHADOW_EXIT_NOT_OBSERVED"):
        replay_shadow_exit(plan, seed, [receipt], as_of=AT.isoformat())
    replay = replay_shadow_exit(plan, seed, [receipt], as_of=EXIT.isoformat())
    assert replay.receipt_cash_by_currency == (("ARS", Decimal("100")),)
    assert replay.scenario_net_by_currency[0][1] == Decimal("100") - plan.entry_cash


@pytest.mark.parametrize("family,symbol", [
    ("BONOS", "SYNTHETIC_BOND"), ("LETRAS", "SYNTHETIC_LETTER"),
    ("OBLIGACIONES", "SYNTHETIC_ON")])
def test_cross_currency_payment_never_becomes_ars_pnl(family, symbol):
    quote, proof, limits = setup(family, symbol)
    kind, mode = ("AMORTIZATION", "MATURITY_REDEMPTION") if family == "LETRAS" else (
        "COUPON", "MARKET_EXIT")
    plan = assess(quote, proof, limits, [flow(kind, amount="1", currency="USD")], mode=mode)
    assert plan.status == "BLOCKED" and plan.signal == "HOLD"
    assert plan.reason == "FI_SHADOW_CROSS_CURRENCY_NO_VERIFIED_FX"
    assert plan.projected_flows == (("USD", Decimal("100")),)
    assert plan.scenario_net is None and plan.entry_authority is False


def test_future_terms_and_stale_book_and_wrong_nominal_contract_reject():
    quote, proof, limits = setup("BONOS", "SYNTHETIC_BOND")
    with pytest.raises(ValueError, match="FI_SHADOW_FUTURE_OR_REVISED_SOURCE"):
        assess(quote, replace(proof, known_at=(AT + timedelta(seconds=1)).isoformat()),
               limits, [flow()])
    with pytest.raises(ValueError, match="FI_SHADOW_FUTURE_OR_REVISED_SOURCE"):
        assess(quote, proof, limits,
               [replace(flow(), known_at=(AT + timedelta(seconds=1)).isoformat())])
    stale = replace(quote, observed_at=(AT - timedelta(minutes=3)).isoformat(),
                    book_at=(AT - timedelta(minutes=3)).isoformat())
    assert assess(stale, proof, limits, [flow()]).reason == "FI_SHADOW_BOOK_OR_TRADE_CLOCK_INVALID"
    with pytest.raises(ValueError, match="FI_SHADOW_EXACT_NOMINAL_CONTRACT_REQUIRED"):
        assess(replace(quote, contract=replace(proof.contract, symbol="OTHER")),
               proof, limits, [flow()])


def test_spread_depth_cash_stress_and_amortization_block_without_buy():
    quote, proof, limits = setup("BONOS", "SYNTHETIC_BOND")
    assert assess(replace(quote, bid=Decimal("70")), proof, limits,
                  [flow()]).reason == "FI_SHADOW_SPREAD_LIMIT"
    assert assess(replace(quote, ask_size=Decimal("40")), proof, limits,
                  [flow()]).reason == "FI_SHADOW_DEPTH_INSUFFICIENT"
    assert assess(quote, proof, replace(limits, max_allocation="1"),
                  [flow()]).reason == "FI_SHADOW_CASH_OR_ALLOCATION_LIMIT"
    assert assess(quote, proof, replace(limits, max_loss_budget="1"),
                  [flow()]).reason == "FI_SHADOW_STRESS_RISK_BUDGET"
    assert assess(quote, proof, limits,
                  [flow("AMORTIZATION", amount="1")]).reason == (
                      "FI_SHADOW_AMORTIZATION_REQUIRES_MATURITY_LIFECYCLE")


def test_specific_indexed_letter_without_known_fixing_cannot_use_fixed_face():
    quote, proof, limits = setup("LETRAS", "D30N6")
    # A floating A3500 future fixing is unknown at the decision cutoff.
    # The model has no cash_per_vn to invent from primary auction terms.
    with pytest.raises(ValueError, match="FI_SHADOW_FLOW_CASH_PER_VN_INVALID"):
        assess(quote, proof, limits,
               [replace(flow("AMORTIZATION", amount="1"), cash_per_vn=None)],
               mode="MATURITY_REDEMPTION")
    assert assess(quote, proof, limits, [], mode="MATURITY_REDEMPTION").reason == (
        "FI_SHADOW_CASHFLOW_SCHEDULE_REQUIRED")


def test_complementary_book_can_only_support_shadow_hypothesis_not_ppi_fill():
    quote, proof, limits = setup("BONOS", "SYNTHETIC_BOND")
    quote = replace(quote, metadata_source="IOL_MCP_FIXTURE")
    plan = assess(quote, proof, limits, [flow()], source="IOL_MCP")
    assert plan.signal == "POSITIVE_SCENARIO" and plan.observed_book_source == "IOL_MCP"
    assert plan.entry_authority is False and plan.real_routes_used == ()
    no_native_book = replace(quote, book_at=None)
    with pytest.raises(ValueError, match="FI_SHADOW_SOURCE_AT_INVALID"):
        assess(no_native_book, proof, limits, [flow()], source="IOL_MCP")
    with pytest.raises(ValueError, match="FI_SHADOW_BOOK_SOURCE_KIND_UNVERIFIED"):
        assess(quote, proof, limits, [flow()], source="PPI_FROM_IOL")


def test_no_exit_day_coupon_entitlement_or_unbound_usd_letter_rights():
    bond, proof, limits = setup("BONOS", "SYNTHETIC_BOND")
    same_day = assess(bond, proof, limits, [flow(due=EXIT)])
    assert same_day.reason == "FI_SHADOW_EXIT_DAY_ENTITLEMENT_UNVERIFIED"
    usd_letter, usd_proof, usd_limits = setup("LETRAS", "SYNTHETIC_USD_LETTER",
                                               currency="USD")
    result = assess(usd_letter, usd_proof, usd_limits,
                    [flow("AMORTIZATION", amount="1", currency="USD")],
                    mode="MATURITY_REDEMPTION")
    assert result.status == "BLOCKED" and result.reason == (
        "FI_SHADOW_COST_COMPONENT_UNKNOWN:rights")
    assert result.entry_authority is False


def test_receipt_replay_rejects_missing_or_changed_obligation_and_wrong_exit_source():
    bond, proof, limits = setup("BONOS", "SYNTHETIC_BOND")
    plan = assess(bond, proof, limits, [flow()])
    seed = PositionSeed("SHADOW-ONLY", bond.symbol, "BONOS", "BYMA", "A-24HS",
                        "ARS", "100", "VN", AT.isoformat(), "fixture:unbooked", SHA)
    exit_quote = replace(bond, observed_at=EXIT.isoformat(), book_at=EXIT.isoformat(),
                         trade_at=EXIT.isoformat())
    args = dict(as_of=EXIT.isoformat(), exit_book=exit_quote,
                exit_book_reference="fixture:later-book", exit_book_sha256=SHA,
                exit_book_source_kind="BYMA_PUBLIC")
    with pytest.raises(ValueError, match="FI_SHADOW_RECEIPT_OBLIGATIONS_MISMATCH"):
        replay_shadow_exit(plan, seed, [], **args)
    day_before = EXIT - timedelta(days=1)
    wrong = EconomicEvent("wrong", "OTHER", seed.paper_id, "COUPON",
                          day_before.isoformat(), day_before.isoformat(),
                          "SETTLEMENT_PROOF", "fixture:synthetic-proof", SHA,
                          day_before.isoformat(), payment_currency="ARS",
                          cash_amount="5", nominal_base="100")
    with pytest.raises(ValueError, match="FI_SHADOW_RECEIPT_OBLIGATIONS_MISMATCH"):
        replay_shadow_exit(plan, seed, [wrong], **args)
    correct = replace(wrong, obligation_id="OBLIGATION-1")
    with pytest.raises(ValueError, match="FI_SHADOW_EXIT_BOOK_SOURCE_UNVERIFIED"):
        replay_shadow_exit(plan, seed, [correct], **{**args,
            "exit_book_source_kind": "PPI_FROM_IOL"})
