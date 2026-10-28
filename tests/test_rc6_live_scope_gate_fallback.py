from pathlib import Path


def test_live_and_motor_use_canonical_readiness_without_two_family_filter():
    source = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")

    assert source.count("FROM candidate_identity_v2 r") >= 3
    assert "UPPER(f.instrument_type) IN ('ACCIONES','CEDEARS')" not in source
    assert "UPPER(u.instrument_type) IN ('ACCIONES','CEDEARS')" not in source
    assert "r.can_simulate=1 AND upper(r.status)='AVAILABLE'" in source
    assert "SELECT g.* FROM trade_gate_evaluations g" in source
    assert "SELECT g.evaluated_at,g.symbol,g.final_result,g.reason" in source
