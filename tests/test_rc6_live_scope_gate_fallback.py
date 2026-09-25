from pathlib import Path


def test_live_and_motor_gate_fallbacks_keep_current_multifamily_scope_fail_closed():
    source = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")

    # The current RC6 PAPER/SHADOW universe is multifamily: live/dashboard
    # fallbacks must not silently reintroduce the old ACCIONES/CEDEARS scope.
    assert "UPPER(f.instrument_type) IN ('ACCIONES','CEDEARS')" not in source
    assert "UPPER(u.instrument_type) IN ('ACCIONES','CEDEARS')" not in source

    # Fail-closed conditions remain: only AVAILABLE catalog identities and
    # simulatable fallback candidates can appear in gate views.
    assert source.count("f.status='AVAILABLE'") >= 2
    assert source.count("u.can_simulate=1") >= 2
    assert "SELECT g.* FROM trade_gate_evaluations g" in source
    assert "SELECT g.evaluated_at,g.symbol,g.final_result,g.reason" in source
