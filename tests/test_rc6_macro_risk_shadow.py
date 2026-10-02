import rc6_macro_risk_shadow as shadow

def test_bcra_macro_shadow_is_retired_without_io(tmp_path):
    result=shadow.collect(tmp_path/"missing.db")
    assert shadow.BCRA_ACTIVE is False
    assert result["state"]=="RETIRED_OPERATOR_DECISION_2026-10-02"
    assert result["decision_effect"]=="NONE"
    assert result["indicators"]=={}
    assert not (tmp_path/"missing.db").exists()
