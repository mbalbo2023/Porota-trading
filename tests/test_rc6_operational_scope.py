from types import SimpleNamespace

import da_dashboard_ux_hf6 as ux
import historical_candle_shadow_rc6 as candle_shadow


def test_trading_navigation_exposes_all_families_without_expanding_operation():
    labels = [item.label for item in ux.TRADING_NAV]
    assert labels == [
        "Resumen y motor", "Estrategias", "Acciones y CEDEAR", "Bonos",
        "ON", "Cauciones", "Letras", "ETF", "Índices", "Futuros", "Opciones",
    ]
    assert ux.families_for_group("acciones-cedears") == ("ACCIONES", "CEDEARS")
    assert ux.families_for_group("bonos") == ("BONOS",)
    assert ux.families_for_group("futuros") == ("FUTUROS",)
    assert ux.OPERATIONAL_FAMILIES == frozenset(("ACCIONES", "CEDEARS"))


def test_historical_shadow_rejects_non_operational_family_without_database():
    quote = SimpleNamespace(
        symbol="AL30", asset_class="BONOS", market="BYMA",
        currency="ARS", settlement="CI",
    )

    result = candle_shadow.collect(store=None, q=quote, at="2026-09-16T10:00:00-03:00")

    assert result["state"] == "OUT_OF_SCOPE"
    assert result["decision_effect"] == "OBSERVE_ONLY"


def test_sector_binding_remains_fail_closed_for_unmapped_equity(monkeypatch):
    import ck_policy_gate_hf6 as gate
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "BINDING")
    result=gate.evaluate(sectors={"groups":[]},candidate_sector=None,sector_applicable=True)
    assert result["execute_block"] is True
    assert result["verdict"]=="SECTOR_UNMAPPED_BINDING"


def test_sector_binding_is_not_applicable_to_non_equity_family(monkeypatch):
    import ck_policy_gate_hf6 as gate
    monkeypatch.setenv("PAPER_SECTOR_CONCENTRATION_POLICY", "BINDING")
    result=gate.evaluate(sectors={"groups":[]},candidate_sector=None,sector_applicable=False)
    assert result["execute_block"] is False
    assert result["gates"]["sector"]["reason"]=="SECTOR_POLICY_NOT_APPLICABLE_TO_FAMILY"
