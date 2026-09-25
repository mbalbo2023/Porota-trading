from types import SimpleNamespace

import da_dashboard_ux_hf6 as ux
import historical_candle_shadow_rc6 as candle_shadow


def test_trading_navigation_exposes_current_multifamily_paper_shadow_scope():
    labels = [item.label for item in ux.TRADING_NAV]
    assert labels == [
        "Resumen y motor", "Estrategias", "Acciones y CEDEAR", "Bonos",
        "ON", "Cauciones", "Letras", "ETF", "Índices", "Futuros", "Opciones",
    ]
    assert ux.families_for_group("acciones-cedears") == ("ACCIONES", "CEDEARS")
    assert ux.families_for_group("bonos") == ("BONOS",)
    assert ux.families_for_group("futuros") == ("FUTUROS",)
    assert {
        "ACCIONES", "CEDEARS", "BONOS", "LETRAS", "ON", "CAUCIONES",
        "OPCIONES", "FUTUROS", "FCI", "ETF",
    }.issubset(ux.OPERATIONAL_FAMILIES)


def test_historical_shadow_does_not_reject_non_equity_family_by_scope():
    quote = SimpleNamespace(
        symbol="AL30", asset_class="BONOS", market="BYMA",
        currency="ARS", settlement="CI",
    )
    result = candle_shadow.collect(store=None, q=quote, at="2026-09-16T10:00:00-03:00")
    assert result["state"] != "OUT_OF_SCOPE"
    assert result["decision_effect"] == "OBSERVE_ONLY"
    assert result["state"] == "INSUFFICIENT_DATA"


def test_observer_family_scope_is_the_canonical_contract_family_set():
    import bf_production_paper_observer as observer
    from bs_instrument_contracts import FAMILIES
    assert observer.OPERATIONAL_FAMILIES == FAMILIES
    assert {
        "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS",
        "OBLIGACIONES", "OPCIONES", "FUTUROS", "CAUCIONES", "FCI",
    } == set(FAMILIES)
