from pathlib import Path


PROMOTE = Path(".github/workflows/porota-deploy-v2-promote.yml")
PREDEPLOY = Path(".github/workflows/porota-predeploy-v2.yml")


def test_deploy_requires_a_fresh_observer_heartbeat_and_candidate_source():
    body = PROMOTE.read_text(encoding="utf-8")
    assert "heartbeat_at" in body
    assert "fresh(last[6],120)" in body
    assert "MARKET_DATA_REQUIRED" in body
    assert "CANDIDATE_SHA" in body
    assert "POROTA_BUILD_ONCE_PROMOTION=GREEN" in body
    assert "POROTA_BUNDLE_INSTALL=GREEN" in body


def test_deploy_bootstraps_compact_daily_views_once_without_reenabling_real_orders():
    body = PROMOTE.read_text(encoding="utf-8")
    assert "for job in validation_projection action4_audit; do" in body
    assert 'timeout 60 python az_maintenance_job.py "$job"' in body
    assert "Cada tarea es diaria y acotada" in body
    assert "REAL_ORDERS_SENT=0 REAL_ORDER_ROUTES=NOT_CALLED" in body


def test_deploy_storage_audit_and_allowlisted_image_retention_are_safe():
    body = PROMOTE.read_text(encoding="utf-8")
    assert "DISK_BEFORE=" in body
    assert "DISK_AFTER=" in body
    assert "SPACE_RECOVERED=" in body
    assert "docker image prune -f" in body
    assert "docker builder prune -af" in body
    assert "PPI_WATCH_UNTOUCHED=GREEN" in body
    assert "docker system prune" not in body
    assert "docker volume prune" not in body
    assert "docker container prune" not in body
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "porota-trading-bot:17.0.0-rc6" in compose
    assert "rc4-test-ready1" not in compose


def test_release_gate_installs_test_dependencies_and_covers_governed_deploy_contracts():
    body = PREDEPLOY.read_text(encoding="utf-8")
    assert "pytest>=8.3" in body
    assert "tests/test_porota_*.py" in body
    assert "POROTA_VALIDATOR_TESTS=GREEN" in body
    assert "POROTA_ARTIFACT_INTEGRITY=GREEN" in body


def test_new_paper_database_initializes_spot_liquidity_ledger():
    engine = Path("be_paper_engine.py").read_text(encoding="utf-8")
    assert "spot_liquidity.init_schema(self)" in engine


def test_promotion_consumes_frozen_image_without_droplet_rebuild():
    body = PROMOTE.read_text(encoding="utf-8")
    assert "porota-predeploy-image.tar.gz" in body
    assert "docker load" in body
    assert "POROTA_BUILD_ONCE_PROMOTION=GREEN" in body
    assert "docker build" not in body
