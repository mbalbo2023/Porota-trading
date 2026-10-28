import rc6_macro_risk_shadow as shadow

def test_bcra_macro_shadow_is_retired_without_io(tmp_path):
    result=shadow.collect(tmp_path/"missing.db")
    assert shadow.BCRA_ACTIVE is False
    assert result["state"]=="RETIRED_OPERATOR_DECISION_2026-10-02"
    assert result["decision_effect"]=="NONE"
    assert result["indicators"]=={}
    assert not (tmp_path/"missing.db").exists()

def test_trading_dashboard_does_not_expose_retired_bcra_shadow_as_active_surface():
    source = open("bg_paper_dashboard.py", encoding="utf-8").read()

    assert "Riesgo macro BCRA — SHADOW" not in source
    assert "import rc6_macro_risk_shadow" not in source
