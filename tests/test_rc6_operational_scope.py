from types import SimpleNamespace

import da_dashboard_ux_hf6 as ux
import historical_candle_shadow_rc6 as candle_shadow


def test_trading_navigation_only_exposes_operational_family():
    labels = [item.label for item in ux.TRADING_NAV]
    assert labels == ["Resumen y motor", "Estrategias", "Acciones y CEDEAR"]
    assert ux.families_for_group("acciones-cedears") == ("ACCIONES", "CEDEARS")
    assert ux.families_for_group("bonos") == ()
    assert ux.families_for_group("futuros") == ()


def test_historical_shadow_rejects_non_operational_family_without_database():
    quote = SimpleNamespace(
        symbol="AL30", asset_class="BONOS", market="BYMA",
        currency="ARS", settlement="CI",
    )

    result = candle_shadow.collect(store=None, q=quote, at="2026-09-16T10:00:00-03:00")

    assert result["state"] == "OUT_OF_SCOPE"
    assert result["decision_effect"] == "OBSERVE_ONLY"
