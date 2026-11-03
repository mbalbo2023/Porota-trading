from decimal import Decimal

import ec_forward_lab_metrics_hf2 as metrics


def test_expectancy_can_be_positive_with_win_rate_below_half():
    value=metrics.expectancy(p_win="0.40",avg_win="3",avg_loss="1")
    assert value==Decimal("0.60")


def test_executable_excursions_are_returns_not_price_units():
    result=metrics.executable_excursions_long(
        entry_ask="100",
        executable_exit_prices=["98","101","105","99"],
    )
    assert result["mfe_exec_return"]==Decimal("0.05")
    assert result["mae_exec_return"]==Decimal("-0.02")


def test_net_return_normalizes_pnl_by_entry_notional():
    assert metrics.net_return(net_pnl="250",entry_notional="10000")==Decimal("0.025")


def test_realized_expectancy_uses_payoff_and_win_probability():
    result=metrics.realized_expectancy(["0.03","-0.01","0.03","-0.01","-0.01"])
    assert result["p_win"]==Decimal("0.4")
    assert result["avg_win"]==Decimal("0.03")
    assert result["avg_loss"]==Decimal("0.01")
    assert result["expectancy"]==Decimal("0.006")


def test_concentration_uses_share_of_positive_pnl():
    result=metrics.pnl_concentration([
        ("AAA",100),("BBB",50),("CCC",25),("AAA",25),("DDD",-80)
    ])
    assert result["positive_pnl_total"]==Decimal("200")
    assert result["share_top_1_positive_pnl"]==Decimal("0.625")
    assert result["share_top_2_positive_pnl"]==Decimal("0.875")
    assert result["top_positive_symbols"]==["AAA","BBB"]
