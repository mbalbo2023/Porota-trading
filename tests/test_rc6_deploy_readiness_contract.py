from pathlib import Path

from scripts.porota_validate_deploy_artifact import is_runtime_relevant


PROMOTE = Path(".github/workflows/porota-deploy-v2-promote.yml").read_text(encoding="utf-8")
PREDEPLOY = Path(".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")


def test_deploy_requires_fresh_observer_heartbeat_and_exact_candidate_image():
    assert "heartbeat_at" in PROMOTE
    assert "fresh(last[6],120)" in PROMOTE
    assert "MARKET_DATA_REQUIRED" in PROMOTE
    assert "GO_CHECK=GREEN" in PROMOTE
    assert "OBSERVER_IMAGE_ID" in PROMOTE
    assert 'test "$OBSERVER_IMAGE_ID" = "$EXPECTED_IMAGE_ID"' in PROMOTE
    assert "OPERATIONAL_SCOPE=ALL_CONTRACT_FAMILIES" in PROMOTE


def test_deploy_bootstraps_compact_daily_views_without_reenabling_real_orders():
    assert "for job in validation_projection action4_audit; do" in PROMOTE
    assert 'timeout 60 python az_maintenance_job.py "$job"' in PROMOTE
    assert "REAL_ORDERS_SENT=0 REAL_ORDER_ROUTES=NOT_CALLED" in PROMOTE


def test_deploy_storage_cleanup_is_safe_and_fix_forward_only():
    assert "DISK_BEFORE=" in PROMOTE
    assert "DISK_AFTER=" in PROMOTE
    assert "sudo -n docker image prune -f" in PROMOTE
    assert "sudo -n docker builder prune -af" in PROMOTE
    assert "docker system prune" not in PROMOTE
    assert "docker volume prune" not in PROMOTE
    assert "docker container prune" not in PROMOTE
    assert "rollback" not in PROMOTE.lower()


def test_release_gate_runs_all_tests_in_the_exact_image():
    assert "Full automatic test discovery in exact image" in PREDEPLOY
    assert "-m pytest -q /app/tests" in PREDEPLOY
    assert "POROTA_FULL_TEST_DISCOVERY_RESULT" in PREDEPLOY
    assert "porota_classify_pytest_failures.py" in PREDEPLOY


def test_new_paper_database_initializes_spot_liquidity_ledger():
    engine = Path("be_paper_engine.py").read_text(encoding="utf-8")
    assert "spot_liquidity.init_schema(self)" in engine


def test_runtime_bundle_includes_cedear_calendar_and_all_root_runtime_python():
    assert is_runtime_relevant("am_us_equity_calendar_rc6.py")
    assert is_runtime_relevant("bf_production_paper_observer.py")
    assert "porota-deploy-bundle-v2.tgz" in PREDEPLOY
