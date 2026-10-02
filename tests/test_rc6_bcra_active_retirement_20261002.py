from pathlib import Path
import inspect
import as_greeks_engine as greeks
import bi_operational_services as ops
import rc6_macro_risk_shadow as macro_shadow

def test_active_financial_refresh_has_no_bcra_network_loop():
    source=inspect.getsource(ops.refresh_financial)
    assert "OFFICIAL_BCRA" not in source
    assert "BCRA_VARIABLES" not in source
    assert ops.BCRA_VARIABLES == {}
    assert "INDEC/datos.gob.ar" in source

def test_bcra_shadow_is_inert_and_greeks_do_not_read_macro_cache():
    result=macro_shadow.collect()
    assert result["decision_effect"]=="NONE"
    assert result["indicators"]=={}
    assert greeks._tasa_desde_macro() is None

def test_paper_engine_has_only_retirement_marker():
    source=Path("be_paper_engine.py").read_text(encoding="utf-8")
    start=source.index("# BCRA macro context retired")
    end=source.index("# Noticias GDELT:",start)
    block=source[start:end]
    assert "rc6_macro_risk_shadow" not in block
    assert "RETIRED_OPERATOR_DECISION_2026-10-02" in block
