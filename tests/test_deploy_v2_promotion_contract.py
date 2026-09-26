from pathlib import Path


PROMOTE=Path(".github/workflows/porota-deploy-v2-promote.yml").read_text(encoding="utf-8")
LEGACY_PATH=Path(".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml")


def test_deploy_v2_promotes_frozen_artifact_without_droplet_build():
    assert "porota-predeploy-image.tar.gz" in PROMOTE
    assert "docker load" in PROMOTE
    import re
    assert not re.search(r"\bdocker\s+build(?:\s|$)", PROMOTE)
    assert "POROTA_BUILD_ONCE_PROMOTION=GREEN" in PROMOTE


def test_deploy_v2_is_the_only_rc6_production_push_path():
    assert "push:" in PROMOTE
    assert "deploy/rc6-pr69-isolated-20260915" in PROMOTE
    assert not LEGACY_PATH.exists()


def test_deploy_v2_requires_merge_tree_identity_and_successful_predeploy():
    assert 'CANDIDATE_TREE="$(git rev-parse "$CANDIDATE_SHA^{tree}")"' in PROMOTE
    assert 'test "$CANDIDATE_TREE" = "$DEPLOY_TREE"' in PROMOTE
    assert 'run.get("name")!="Porota Predeploy V2"' in PROMOTE
    assert 'run.get("conclusion")!="success"' in PROMOTE


def test_deploy_v2_preserves_paper_and_ppi_watch_invariants():
    assert 'test "$POST_STATE" = "ok|PRODUCTION_PAPER|0"' in PROMOTE
    assert "PPI_WATCH_UNTOUCHED=GREEN" in PROMOTE
    assert "REAL_ORDERS_SENT=0 REAL_ORDER_ROUTES=NOT_CALLED" in PROMOTE
    assert "rollback" not in PROMOTE.lower()


def test_deploy_v2_uses_loaded_runtime_identity_after_docker_normalization():
    assert 'RUNTIME_IMAGE_ID="$LOADED_IMAGE_ID"' in PROMOTE
    assert 'test "$OBSERVER_IMAGE_ID" = "$RUNTIME_IMAGE_ID"' in PROMOTE
    assert 'test "$DASHBOARD_IMAGE_ID" = "$RUNTIME_IMAGE_ID"' in PROMOTE
    assert 'test "$IMAGE_TAR_SHA" = "$EXPECTED_TAR_SHA"' in PROMOTE
    assert "POROTA_RUNTIME_IMAGE_ID_VERIFY=observer=" in PROMOTE


def test_deploy_v2_retries_post_state_with_diagnostics():
    assert "POST_CONTAINER_OOM_KILLED" in PROMOTE
    assert "POST_STATE_ATTEMPT=" in PROMOTE
    assert "timeout 45 docker exec" in PROMOTE
    assert 'test "$POST_STATE_RC" -eq 0' in PROMOTE


def test_deploy_v2_reads_runtime_state_host_readonly():
    assert 'POST_STATE_HOST_READONLY_ATTEMPT=' in PROMOTE
    assert 'python3 - "$REPO/data/paper_v17/observer_v17.db"' in PROMOTE
    assert 'RUNTIME_CHECK="$(sudo -n timeout 120 python3 - "$REPO/data/paper_v17/observer_v17.db"' in PROMOTE
    assert 'OPERATIONAL_SCOPE=ALL_CONTRACT_FAMILIES' in PROMOTE
