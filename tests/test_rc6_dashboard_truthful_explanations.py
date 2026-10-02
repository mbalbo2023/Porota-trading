


def test_bcra_is_not_an_active_dashboard_surface():
    from pathlib import Path
    source=Path("bg_paper_dashboard.py").read_text(encoding="utf-8")
    assert 'add("BCRA / INDEC"' not in source
    assert "Riesgo macro / BCRA — SHADOW" not in source
    assert "Riesgo macro BCRA — SHADOW" not in source
    assert "Indicadores BCRA e INDEC" not in source
    assert "import rc6_macro_risk_shadow" not in source
    assert 'add("INDEC / datos.gob.ar"' in source
    assert "Indicadores INDEC / datos.gob.ar" in source
    assert "upper(COALESCE(f.source,'')) <> 'BCRA'" in source
