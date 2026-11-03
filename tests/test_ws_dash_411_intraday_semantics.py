from pathlib import Path

SOURCE = Path("bg_paper_dashboard.py").read_text(encoding="utf-8")


def test_scalping_contract_states_are_explicitly_per_identity():
    assert '"PENDING_LIVE_CONFIRMATION":"Pendiente de confirmación intradía para esta identidad"' in SOURCE
    assert '"PPI_INTRADAY_UNSUPPORTED":"PPI no ofrece intradía para esta identidad"' in SOURCE
    assert '"CONFIRMED_INTERVAL_VOLUME":"Contrato intradiario confirmado para esta identidad"' in SOURCE
    assert "contract_state_labels.get(r['state']" in SOURCE


def test_healthy_worker_does_not_publish_identity_gap_as_global_failure():
    assert 'state in {"RUNNING","WAITING_MARKET"}' in SOURCE
    assert '"PENDING_LIVE_CONFIRMATION","PPI_INTRADAY_UNSUPPORTED"' in SOURCE
    assert "Las limitaciones de contrato intradiario se detallan por identidad." in SOURCE
    assert "<b>Estado global del worker:</b>" in SOURCE
    assert "worker.get('detail','Esperando estado del proceso.')" not in SOURCE


def test_intraday_unsupported_has_human_blocker_label_without_readiness_mutation():
    assert '"PPI_INTRADAY_UNSUPPORTED":"PPI no ofrece intradía para esta identidad"' in SOURCE
    segment=SOURCE[SOURCE.index("def scalping_page():"):SOURCE.index("def _is_today")]
    assert "UPDATE candidate_identity_v2" not in segment
    assert "DELETE FROM candidate_identity_v2" not in segment
