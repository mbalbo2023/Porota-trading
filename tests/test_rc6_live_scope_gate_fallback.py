from pathlib import Path


def test_live_and_motor_gate_fallbacks_keep_only_operational_families():
    source = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")

    assert source.count("UPPER(f.instrument_type) IN ('ACCIONES','CEDEARS')") >= 3
    assert source.count("UPPER(u.instrument_type) IN ('ACCIONES','CEDEARS')") >= 3
    assert "SELECT g.* FROM trade_gate_evaluations g" in source
    assert "SELECT g.evaluated_at,g.symbol,g.final_result,g.reason" in source
