"""Causal strategy scenarios cannot acquire PAPER entry authority."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from rc6_shadow_runtime.family_strategies import VERSION, evaluate_family_strategy
from rc6_shadow_runtime.families import family_reports
from test_rc6_shadow_family_runtime import fixture_db, instrument

AT = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)
KEY = ("TEST", "BONOS", "BYMA", "ARS", "A-24HS")


def field(value):
    return {"value": value, "review_required": False}


def book(ask=100, bid=99.95, unit="NOMINALS", price_unit="CASH_PER_100_NOMINAL"):
    return {"book_status": "OBSERVED_FRESH", "price_basis_status": "EXPLICIT_COMPATIBLE_UNITS",
            "price_unit": price_unit, "quantity_unit": unit, "ask": ask, "bid": bid,
            "ask_size": 10000, "bid_size": 10000, "review_required": False}


def observation(family):
    if family == "OPCIONES":
        return {"structurally_selected": True,
                "terms": {"option_right": field("CALL"), "underlying": field("GGAL"),
                          "expires_at": field((AT+timedelta(days=8)).isoformat())},
                "option_contract": {"status": "VERIFIED_EXACT_CONTRACT", "cash_multiplier": "100",
                                    "underlying": "GGAL", "option_right": "CALL",
                                    "expires_at": (AT+timedelta(days=8)).isoformat()},
                "underlying_quote": {"book_status": "OBSERVED_FRESH", "tradeable": True,
                                     "price": 100, "price_unit": "PER_SHARE"},
                "quote": book(2, 1.99, "CONTRACTS", "PREMIUM_PER_SHARE")}
    if family == "FCI":
        return {"terms": {k: field(v) for k, v in {
            "nav": 10, "nav_date": "2026-10-26", "subscription_status": "OPEN",
            "redemption_term": "T+1", "cutoff_time": "15:00 ART"}.items()},
                "cash": {"status": "OBSERVED_PAPER_CASH", "value": 10000}}
    return {"nominal_contract_status": "VERIFIED_NOMINAL_CONTRACT", "quote": book(),
            "terms": {"cash_multiplier": field("0.01"), "minimum_nominal": field(100),
                      "quantity_step_nominal": field(1), "flow_unit": field("CASH_PER_NOMINAL")},
            "maturity": field((AT+timedelta(days=30)).isoformat()),
            "analytics": {"duration": field(2), "curve": field("ARS_FIXED"),
                          "flows": field([{"payment_at": (AT+timedelta(days=30)).isoformat(),
                                           "amount": "1.1", "currency": "ARS"}])}}


def proposal(family):
    key = ("TEST", family, "BYMA", "ARS", "A-24HS")
    horizon = AT+timedelta(days=5 if family != "LETRAS" else 30)
    common = {"schema": VERSION, "model_id": "offline-hypothesis-1", "model_provenance": "TEST_ONLY",
              "model_frozen_at": (AT-timedelta(days=1)).isoformat(),
              "source_at": (AT-timedelta(seconds=2)).isoformat(), "received_at": AT.isoformat(),
              "horizon_at": horizon.isoformat()}
    if family == "FCI":
        scenario = {"committed_cash": "5000", "subscription_cash": "1000",
                    "scenario_redemption_cash": "1020", "all_subscription_redemption_fees": "2",
                    "nav_loss_allowance": "5", "opportunity_cost": "5",
                    "redeemable_at": (AT+timedelta(days=2)).isoformat(),
                    "liquidity_needed_at": (AT+timedelta(days=7)).isoformat()}
    elif family == "OPCIONES":
        scenario = {"contracts": "1", "side": "LONG", "contract_cash_multiplier": "100",
                    "scenario_exit_bid": "3", "cashflows_cash": "0",
                    "opportunity_cost": "1", "loss_allowance_cash": "5", "premium_risk_budget_cash": "300",
                    "price_unit": "PREMIUM_PER_SHARE", "cash_currency": "ARS",
                    "underlying_target_price": "105", "underlying_price_unit": "PER_SHARE"}
    else:
        scenario = {"nominals": "100", "scenario_exit_bid": "110" if family != "LETRAS" else "0",
                    "cashflows_cash": "0" if family != "LETRAS" else "110",
                    "opportunity_cost": "1", "loss_allowance_cash": "1",
                    "price_unit": "CASH_PER_100_NOMINAL", "cash_currency": "ARS"}
        if family in {"BONOS", "OBLIGACIONES"}:
            scenario.update(curve_reference="ARS_FIXED", max_duration_years="3", rate_shock_fraction="0.005")
        if family == "LETRAS":
            scenario["cashflow_unit"] = "CASH_FOR_EXACT_NOMINAL_QUANTITY"
        if family == "OBLIGACIONES":
            scenario.update(issuer="TEST_ISSUER", credit_review="CURRENT",
                            issuer_exposure_cash="100", issuer_limit_cash="10000")
    return dict(zip(("ticker", "instrument_type", "market", "currency", "settlement"), key)) | common | {"scenario": scenario}


@pytest.mark.parametrize("family", ["BONOS", "LETRAS", "OBLIGACIONES", "OPCIONES", "FCI"])
def test_positive_synthetic_hypothesis_is_never_entry_authority(family):
    key = ("TEST", family, "BYMA", "ARS", "A-24HS")
    result = evaluate_family_strategy(key, observation(family), proposal(family), as_of=AT)
    assert result["trigger"] == "POSITIVE_HYPOTHESIS", result
    assert result["status"] == result["oos"] == "NO_VERIFICADO"
    assert result["entry_authority"] is False and result["paper_auto_enabled"] is False
    assert result["real_order_routes"] == [] and result["scenario"]["positive_after_costs"]


@pytest.mark.parametrize("change,reason", [
    ({"source_at": (AT+timedelta(seconds=1)).isoformat()}, "SCENARIO_NOT_PREREGISTERED_OR_CAUSAL"),
    ({"model_frozen_at": AT.isoformat()}, "SCENARIO_NOT_PREREGISTERED_OR_CAUSAL"),
    ({"currency": "USD"}, "SCENARIO_IDENTITY_MISMATCH"),
])
def test_future_unfrozen_or_wrong_identity_research_fails_closed(change, reason):
    research = proposal("BONOS") | change
    result = evaluate_family_strategy(KEY, observation("BONOS"), research, as_of=AT)
    assert result["trigger"] == "HOLD" and result["reason_code"] == reason


def test_book_units_quantity_spread_credit_and_option_long_risk_fail_closed():
    fixed = observation("BONOS")
    fixed["quote"]["quantity_unit"] = None
    assert evaluate_family_strategy(KEY, fixed, proposal("BONOS"), as_of=AT)["reason_code"] == "EXECUTABLE_BOOK_OR_UNITS_NO_VERIFICADO"
    research = proposal("BONOS")
    research["scenario"]["nominals"] = "2000"
    research["scenario"]["loss_allowance_cash"] = "100"
    assert evaluate_family_strategy(KEY, observation("BONOS"), research, as_of=AT)["reason_code"] == "BOOK_PARTICIPATION_EXCEEDED"
    on = proposal("OBLIGACIONES")
    on["scenario"]["issuer_limit_cash"] = "100"
    key = ("TEST", "OBLIGACIONES", "BYMA", "ARS", "A-24HS")
    assert evaluate_family_strategy(key, observation("OBLIGACIONES"), on, as_of=AT)["reason_code"] == "ISSUER_LIMIT_EXCEEDED"
    option = proposal("OPCIONES")
    option["scenario"]["side"] = "SHORT"
    key = ("TEST", "OPCIONES", "BYMA", "ARS", "A-24HS")
    assert evaluate_family_strategy(key, observation("OPCIONES"), option, as_of=AT)["reason_code"] == "LONG_WHOLE_OPTION_CONTRACTS_ONLY"
    option["scenario"]["side"] = "LONG"
    option["scenario"]["premium_risk_budget_cash"] = "100"
    assert evaluate_family_strategy(key, observation("OPCIONES"), option, as_of=AT)["reason_code"] == "LONG_PREMIUM_RISK_BUDGET_EXCEEDED"


def test_letter_double_count_and_fund_cash_need_fail_closed():
    research = proposal("LETRAS")
    research["scenario"]["scenario_exit_bid"] = "100"
    key = ("TEST", "LETRAS", "BYMA", "ARS", "A-24HS")
    assert evaluate_family_strategy(key, observation("LETRAS"), research, as_of=AT)["reason_code"] == "LETTER_DOUBLE_COUNTED_EXIT_AND_REDEMPTION"
    fund = proposal("FCI")
    fund["scenario"]["committed_cash"] = "9500"
    key = ("TEST", "FCI", "BYMA", "ARS", "A-24HS")
    assert evaluate_family_strategy(key, observation("FCI"), fund, as_of=AT)["reason_code"] == "FUND_SURPLUS_CASH_INSUFFICIENT"


def test_family_report_dispatches_research_only_by_exact_identity(tmp_path):
    catalog = [instrument("TEST", "BONOS"), instrument("FUND", "FCI")]
    path = fixture_db(tmp_path, catalog)
    before = path.read_bytes()
    report = family_reports(path, as_of=AT, catalog=catalog,
        sources={"family_strategy_research": [proposal("BONOS")]})
    assert path.read_bytes() == before
    found = {r["identity"][1]: r["handler_result"]["strategy_hypothesis"] for r in report["instruments"]}
    assert found["BONOS"]["trigger"] == "HOLD"
    assert found["BONOS"]["reason_code"] == "FIXED_INCOME_NOMINAL_CONTRACT_NO_VERIFICADO"
    assert found["FCI"]["reason_code"] == "INDEPENDENT_SCENARIO_MISSING"
    assert all(not r["entry_authority"] for r in report["instruments"])
    report = family_reports(path, as_of=AT, catalog=catalog,
        sources={"family_strategy_research": [proposal("BONOS"), deepcopy(proposal("BONOS"))]})
    assert report["instruments"][0]["handler_result"]["strategy_hypothesis"]["reason_code"] == "DUPLICATE_EXACT_IDENTITY_RESEARCH"


def test_exact_clocked_bond_source_can_reach_non_binding_shadow_hypothesis(tmp_path):
    record = instrument("TEST", "BONOS")
    path = fixture_db(tmp_path, [record])
    source = {**record, "source": "FIXTURE_PROVIDER", "source_at": AT.isoformat(),
              "received_at": AT.isoformat(), "book_at": AT.isoformat(),
              "last": 100, "bid": 99.95, "ask": 100, "bid_size": 10000, "ask_size": 10000,
              "price_unit": "CASH_PER_100_NOMINAL", "quantity_unit": "NOMINALS",
              "quote_basis_nominal": 100, "quantity_step_nominal": 1, "minimum_nominal": 100,
              "cash_multiplier": ".01", "duration": 2, "curve": "ARS_FIXED",
              "maturity_at": (AT+timedelta(days=30)).isoformat()}
    prior = path.read_bytes()
    report = family_reports(path, as_of=AT, catalog=[record],
        sources={"family_evidence": [source], "family_strategy_research": [proposal("BONOS")]})
    hypothesis = report["instruments"][0]["handler_result"]["strategy_hypothesis"]
    assert hypothesis["trigger"] == "POSITIVE_HYPOTHESIS", hypothesis
    assert hypothesis["entry_authority"] is False and hypothesis["oos"] == "NO_VERIFICADO"
    assert path.read_bytes() == prior
