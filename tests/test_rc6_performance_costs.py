import sys
from pathlib import Path
from decimal import Decimal as D

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_performance.costs import FeeModel, expected_round_trip_cost, realized_round_trip_cost, paper_fee_model
from rc6_performance.scanner import ScanBudget, plan_active_universe
from rc6_performance.metrics import audit_master, historical_sessions


def fees():
    return FeeModel(D(".006"), D(".0005"), D(".21"), True, True, "TEST_PAPER")


def position():
    return dict(paper_id="PAPER-test", symbol="GGAL", asset_class="ACCIONES", settlement="A-24HS",
                currency="ARS", market="BYMA", quantity="3", entry_price="100", entry_cost="2",
                exit_cost="1", gross_pnl="6", net_pnl="3", strategy_version="test",
                opened_at="2026-09-09T14:00:00Z", closed_at="2026-09-09T15:00:00Z",
                close_reason="EOD_PAPER", decision_score=".7")


def fills():
    return [dict(fill_id=1, paper_id="PAPER-test", side="BUY_SIMULATED", quantity="3", price="100", costs="2"),
            dict(fill_id=2, paper_id="PAPER-test", side="SELL_SIMULATED", quantity="1", price="102", costs=".33"),
            dict(fill_id=3, paper_id="PAPER-test", side="SELL_SIMULATED", quantity="2", price="102", costs=".67")]


def test_intraday_discount_bonifies_only_smaller_leg_and_keeps_rights_vat():
    result = expected_round_trip_cost(100, 102, 10, 1, fees(), intraday_eligible=True)
    assert result["commission"] == D("6.12")
    assert result["rights"] == D("1.010")
    assert result["vat"] == D("1.49730")
    assert result["total_expected_friction"] == D("8.62730")
    assert result["account_terms"] == "NO_VERIFICADO"


def test_execution_prices_do_not_pay_spread_twice():
    with pytest.raises(ValueError, match="DOUBLE_COUNT"):
        expected_round_trip_cost(100, 99, 1, 1, fees(), spread=".01")
    a = expected_round_trip_cost(100, 99, 1, 1, fees())
    assert a["spread"] == 0
    b = expected_round_trip_cost(100, 99, 1, 1, fees(), prices_include_friction=False, spread=".01")
    assert b["total_expected_friction"] == a["total_expected_friction"] + 1


def test_stop_is_price_trigger_not_net_loss_guarantee():
    entry, stop_fill = D(1), D(".98") * D(".9998")
    cost = expected_round_trip_cost(entry, stop_fill, 1, 1, fees(), intraday_eligible=True)
    loss = entry - stop_fill + cost["explicit_fees"]
    assert loss == D(".02865378142")
    assert loss > D(".02")


def test_partial_fills_reconcile_as_one_trade():
    result = realized_round_trip_cost(position(), fills())
    assert (result["gross"], result["costs"], result["net"]) == (6, 3, 3)
    assert result["fills"] == 3


@pytest.mark.parametrize("mutation,error", [("duplicate", "DUPLICATE"), ("currency", "MISMATCH"),
                                             ("quantity", "QUANTITY"), ("net", "MONEY")])
def test_bad_ledger_evidence_fails_closed(mutation, error):
    p, fs = position(), fills()
    if mutation == "duplicate": fs.append(fs[0])
    if mutation == "currency": fs[1]["currency"] = "USD_MEP"
    if mutation == "quantity": fs[-1]["quantity"] = "1"
    if mutation == "net": p["net_pnl"] = "5"
    with pytest.raises(ValueError, match=error): realized_round_trip_cost(p, fs)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", True, None])
def test_invalid_rates_do_not_become_zero(value):
    with pytest.raises(ValueError): FeeModel(value, D(".0005"), D(".21"), True, True, "test")


def test_no_generic_equity_fee_for_other_families():
    with pytest.raises(ValueError, match="FAMILY"): paper_fee_model("FUTUROS")


def test_scanner_historical_rotation_is_infeasible_and_catalog_is_preserved():
    budget = ScanBudget(7603, 7603, 9, 140, 6, 5400)
    report = budget.report()
    assert report["status"] == "INFEASIBLE"
    assert report["revisit_seconds"] == 118300
    assert report["estimated_samples_per_window"] == 0
    assert report["ideal_active_upper_bound"] == 57
    assert report["conservative_active_limit"] == 54
    with pytest.raises(ValueError, match="INFEASIBLE"): budget.require_feasible()
    plan = plan_active_universe(range(7603), budget)
    assert len(plan["active"]) == 54 and plan["status"] == "FEASIBLE"
    assert plan["catalog_effect"] == "NONE"


def test_zero_slots_and_degraded_scanner_never_claim_observability():
    assert ScanBudget(100, 50, 0, 10, 6, 90).report()["status"] == "INFEASIBLE"
    assert ScanBudget(100, 54, 9, 140, 6, 5400, .5).report()["status"] == "INFEASIBLE"


def master():
    p = position()
    p["fills"] = fills()
    return {"read_only": True, "network_calls_performed": False, "broker_calls_performed": False,
            "safety": {"mode": "PRODUCTION_PAPER", "real_orders_sent": 0},
            "generated_at": "2026-10-03T01:23:15Z", "trades": [p]}


def test_audit_keeps_zero_days_and_currencies_separate():
    source = master()
    p = position() | {"paper_id": "PAPER-usd", "currency": "USD_MEP"}
    p["fills"] = [f | {"fill_id": f["fill_id"]+10, "paper_id": "PAPER-usd"} for f in fills()]
    source["trades"].append(p)
    result = audit_master(source, sessions=historical_sessions())
    assert result["n_sessions"] == 20 and result["closed_count"] == 2 and result["fills_count"] == 6
    assert result["by_currency"]["ARS"]["net"] == 3
    assert result["by_currency"]["USD_MEP"]["net"] == 3
    assert len([x for x in result["cohorts"] if x["dimension"] == "day"]) == 40
    assert "net" not in result


def test_incomplete_extraction_does_not_turn_unobserved_days_into_zeros():
    source = master() | {"generated_at": "2026-09-23T23:00:00Z"}
    with pytest.raises(ValueError, match="CUTOFF"): audit_master(source, sessions=historical_sessions())


def test_non_equity_cash_flow_requires_contract_multiplier():
    p = position() | {"asset_class": "FUTUROS"}
    with pytest.raises(ValueError, match="MULTIPLIER"):
        realized_round_trip_cost(p, fills())


def test_extracted_replays_use_same_ids_canonical_costs_and_expose_missing_coverage(tmp_path):
    import json
    from rc6_performance.counterfactuals import extracted_replays
    source = master()
    source["trades"][0]["close_reason"] = "STOP_PAPER"
    source["trades"][0]["post_exit_recovery"] = {"max_bid_120m": "100", "observations_used": 2}
    guard = {k: source[k] for k in ("read_only", "network_calls_performed", "broker_calls_performed", "safety")}
    target = guard | {"targets": {".01": {"changed": [{"paper_id": "PAPER-test", "delta_net_pnl": "-4"}]}}, "net_targets": {}}
    grid = guard | {"details": {"missing": [], "complete": [{"paper_id": "PAPER-test", "status": "REPLAYED", "net_pnl": "-1"}]}}
    for name, payload in (("targets", target), ("grilla", grid)):
        (tmp_path/f"fuente_replay_{name}.json").write_text(json.dumps(payload))
    result = extracted_replays(source, tmp_path, sessions=historical_sessions())
    assert result["strict_target_replays_ars"][0]["candidate_net_ars"] == -1
    assert next(x for x in result["grid_replays_ars"] if x["profile"] == "missing")["missing_positions"] == 1
    assert result["post_stop_120m"][0]["modeled_net_at_best_bid"] < 0
    assert not result["economic_edge_validated"] and not result["real_order_routes"]
    target["targets"][".01"]["changed"] *= 2
    (tmp_path/"fuente_replay_targets.json").write_text(json.dumps(target))
    with pytest.raises(ValueError, match="DUPLICATE"):
        extracted_replays(source, tmp_path, sessions=historical_sessions())
