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
    binding = Path("scripts/porota_predeploy_binding.py").read_text(encoding="utf-8")
    assert 'CANDIDATE_TREE="$(git rev-parse "$CANDIDATE_SHA^{tree}")"' in PROMOTE
    assert 'test "$CANDIDATE_TREE" = "$DEPLOY_TREE"' in PROMOTE
    assert "porota_predeploy_binding.py locate" in PROMOTE
    assert 'execution.get("conclusion") != ("success" if require_completed else None)' in binding
    assert 'require_completed=True' in binding
    assert 'execution.get("workflow_id") != approval["workflow_id"]' in binding
    assert 'execution.get("run_attempt") != approval["run_attempt"]' in binding
    assert 'execution.get("head_sha") != approval["candidate_sha"]' in binding
    assert "parse_merge_approval" in binding


def test_deploy_v2_preserves_paper_and_ppi_watch_invariants():
    assert 'test "$POST_STATE" = "STATE_FAST|PRODUCTION_PAPER|0"' in PROMOTE
    assert "POST_SQLITE_QUICK_CHECK=DEFERRED_TO_CONTRACT_RUNNER" in PROMOTE
    assert "PPI_WATCH_UNTOUCHED=GREEN" in PROMOTE
    assert "REAL_ORDERS_SENT=0 REAL_ORDER_ROUTES=NOT_CALLED" in PROMOTE
    assert '"real_order_routes":"NOT_CALLED"' in PROMOTE
    assert "rollback" not in PROMOTE.lower()


def test_deploy_v2_materializes_exact_runtime_provenance():
    assert "CANDIDATE_TREE='$CANDIDATE_TREE'" in PROMOTE
    assert 'install -m 0644 "$REMOTE_DIR/porota-frozen-candidate.json"' in PROMOTE
    assert '"$REPO/data/deploy/porota-frozen-candidate.json"' in PROMOTE
    assert 'install -m 0644 "$REMOTE_DIR/porota-deploy-bundle-v2-manifest.json"' in PROMOTE
    assert '"$REPO/data/deploy/porota-deploy-bundle-v2-manifest.json"' in PROMOTE
    assert 'assert frozen["candidate_sha"] == candidate' in PROMOTE
    assert 'assert frozen["candidate_tree_sha"] == tree' in PROMOTE
    assert 'assert frozen["image_tar_sha256"] == image_tar_sha' in PROMOTE
    assert "POROTA_RUNTIME_PROVENANCE_MATERIALIZED=GREEN" in PROMOTE


def test_deploy_v2_uses_loaded_runtime_identity_after_docker_normalization():
    assert 'RUNTIME_IMAGE_ID="$LOADED_IMAGE_ID"' in PROMOTE
    assert 'test "$OBSERVER_IMAGE_ID" = "$RUNTIME_IMAGE_ID"' in PROMOTE
    assert 'test "$DASHBOARD_IMAGE_ID" = "$RUNTIME_IMAGE_ID"' in PROMOTE
    assert 'test "$IMAGE_TAR_SHA" = "$EXPECTED_TAR_SHA"' in PROMOTE
    assert "POROTA_RUNTIME_IMAGE_ID_VERIFY=observer=" in PROMOTE


def test_deploy_v2_retries_post_state_with_diagnostics():
    assert "POST_CONTAINER_OOM_KILLED" in PROMOTE
    assert "for POST_STATE_ATTEMPT in 1 2 3" in PROMOTE
    assert 'timeout 10 python3 - "$REPO/data/paper_v17/observer_v17.db"' in PROMOTE
    assert "POST_STATE_HOST_READONLY_ATTEMPT=" in PROMOTE
    assert "timeout 45 docker exec" not in PROMOTE
    assert 'test "$POST_STATE_RC" -eq 0' in PROMOTE


def test_deploy_v2_recovers_dashboard_after_contract_memory_peak():
    assert "DASHBOARD_POST_RECONCILIATION_STATUS=" in PROMOTE
    assert "DASHBOARD_OOM_KILLED" in PROMOTE
    assert "DASHBOARD_POST_RECONCILIATION_RESTART=PERFORMED" in PROMOTE
    assert "for DASHBOARD_ATTEMPT in $(seq 1 18)" in PROMOTE
    assert "docker logs --tail 200 porota_production_dashboard" in PROMOTE
    assert 'test "$DASHBOARD_IMAGE_ID" = "$RUNTIME_IMAGE_ID"' in PROMOTE


def test_deploy_v2_reads_runtime_state_host_readonly():
    assert 'POST_STATE_HOST_READONLY_ATTEMPT=' in PROMOTE
    assert 'python3 - "$REPO/data/paper_v17/observer_v17.db"' in PROMOTE
    assert 'RUNTIME_CHECK="$(sudo -n timeout 120 python3 - "$REPO/data/paper_v17/observer_v17.db"' in PROMOTE
    assert 'OPERATIONAL_SCOPE=ALL_CONTRACT_FAMILIES' in PROMOTE


def test_deploy_v2_separates_fast_state_guard_from_contract_quick_check():
    assert "PRAGMA busy_timeout=1000" in PROMOTE
    assert 'timeout 10 python3 - "$REPO/data/paper_v17/observer_v17.db"' in PROMOTE
    assert 'grep -Fq "QUICK_CHECK=ok" <<< "$CONTRACT_OUTPUT"' in PROMOTE


def test_deploy_v2_preflight_state_guard_is_bounded_and_never_scans_full_db():
    preflight = PROMOTE.split('PRE_STATE="NO_RUNNING_OBSERVER"', 1)[1].split(
        'python3 - "$REMOTE_DIR"', 1)[0]
    assert 'timeout 10 python3 - "$REPO/data/paper_v17/observer_v17.db"' in preflight
    assert "PRE_STATE_HOST_READONLY=" in preflight
    assert "PRAGMA quick_check" not in preflight
    assert "docker exec -i porota_production_observer python" not in preflight
