from decimal import Decimal

import au_fee_schedule
import fh_scalping_economics_rc6 as economics


def test_equity_scalp_models_one_full_plus_one_discounted_leg():
    spread = Decimal("0.003")
    result = economics.modeled_roundtrip_cost("ACCIONES", spread)
    full = Decimal(str(au_fee_schedule.costo_por_tramo("ACCIONES")))
    discounted = Decimal(str(au_fee_schedule.costo_por_tramo_bonificado("ACCIONES")))
    expected = full + discounted + spread + Decimal("0.0004")
    assert result["fee_model"] == "PPI_INTRADAY_BONUS"
    assert result["modeled_roundtrip_fraction"] == expected
    assert result["modeled_roundtrip_fraction"] < full * 2 + spread + Decimal("0.0004")


def test_cedear_scalp_gets_same_local_intraday_rule():
    result = economics.modeled_roundtrip_cost("CEDEARS", Decimal("0.002"))
    assert result["fee_model"] == "PPI_INTRADAY_BONUS"
    assert result["discounted_leg_fee_fraction"] < result["full_leg_fee_fraction"]


def test_options_keep_two_full_legs_because_bonus_not_modeled():
    spread = Decimal("0.004")
    result = economics.modeled_roundtrip_cost("OPCIONES", spread)
    full = Decimal(str(au_fee_schedule.costo_por_tramo("OPCIONES")))
    assert result["fee_model"] == "FULL_BOTH_LEGS"
    assert result["modeled_roundtrip_fraction"] == full * 2 + spread + Decimal("0.0004")


def test_cost_helper_has_no_execution_capability():
    economics.assert_no_execution_capability()
