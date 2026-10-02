import zz_wave8_dashboard_live_rc6 as live


def test_intraday_pending_is_not_described_as_missing_financial_contract():
    html="<td>Contrato intradía aún no confirmado</td><span>PENDING_LIVE_CONFIRMATION</span>"
    out=live._truthful_operator_terms(html)
    assert "Contrato intradía aún no confirmado" not in out
    assert "segunda lectura estable del feed PPI" in out
    assert "PENDING_LIVE_CONFIRMATION" in out


def test_open_position_gate_is_explained_for_operator():
    out=live._truthful_operator_terms("OPEN_POSITION_NEEDS_SUPERVISION_OR_EXIT")
    assert "Posición PAPER abierta" in out
    assert "supervisión/estado de salida" in out


def test_zero_legacy_excursions_are_presented_as_unmeasured():
    for raw in ("MFE=0,0000; MAE=0,0000", "MFE=0.0000 · MAE=0.0000", "MFE=0 MAE=0"):
        out=live._truthful_operator_terms(raw)
        assert "MFE=NO_MEDIDO" in out
        assert "MAE=NO_MEDIDO" in out
        assert "trayectoria ejecutable persistida" in out


def test_nonzero_excursions_are_not_rewritten():
    raw="MFE=0,0123; MAE=-0,0080"
    assert live._truthful_operator_terms(raw) == raw


def test_stop_paper_gets_plain_language_cause_without_changing_code():
    out=live._truthful_operator_terms("causa STOP_PAPER")
    assert "STOP_PAPER" in out
    assert "bid fresco" in out
    assert "stop PAPER definido" in out
