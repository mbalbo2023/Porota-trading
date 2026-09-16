from pathlib import Path


WORKFLOW = Path(".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml")


def test_deploy_requires_a_fresh_observer_heartbeat_and_candidate_source():
    body = WORKFLOW.read_text(encoding="utf-8")
    assert "heartbeat_at" in body
    assert "fresh(last[6], 120)" in body
    assert "MARKET_DATA_REQUIRED" in body
    assert "OBSERVER_CANDIDATE_SHA=GREEN" in body
    assert "RUNNING_OBSERVER_SHA" in body


def test_deploy_bootstraps_compact_daily_views_once_without_reenabling_real_orders():
    body = WORKFLOW.read_text(encoding="utf-8")
    assert "for job in validation_projection action4_audit; do" in body
    assert 'timeout 30 python az_maintenance_job.py "$job"' in body
    assert "REAL_ORDERS_SENT=0 REAL_ORDER_ROUTES=NOT_CALLED" in body


def test_deploy_storage_audit_is_explicitly_read_only():
    body = WORKFLOW.read_text(encoding="utf-8")
    assert "STORAGE_AUDIT=READ_ONLY" in body
    assert "STORAGE_CLEANUP=NOT_EXECUTED" in body
    assert "docker system prune" not in body


def test_release_gate_installs_test_dependencies_and_covers_deterministic_paper_exits():
    body = WORKFLOW.read_text(encoding="utf-8")

    assert '"pytest>=8.3.0,<9" requests' in body
    assert "tests/test_rc6_take_profit_regression.py" in body
    assert "max_hold_sin_datos_persiste_y_se_ejecuta_despues_de_reiniciar" in body
    assert "1655_sigue_dentro_de_rueda_y_un_stop_puede_cerrar" in body


def test_new_paper_database_initializes_spot_liquidity_ledger():
    engine = Path("be_paper_engine.py").read_text(encoding="utf-8")

    assert "spot_liquidity.init_schema(self)" in engine
