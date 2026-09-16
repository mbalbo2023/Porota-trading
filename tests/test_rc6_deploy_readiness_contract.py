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
