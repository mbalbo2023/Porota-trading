import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREDEPLOY = ROOT / ".github/workflows/porota-predeploy-v2.yml"
WORKFLOW = ROOT / ".github/workflows/porota-deploy-v2-promote.yml"
POLICY = ROOT / "ops/policy/porota-policy.yaml"
DOCKERFILE = Path("Dockerfile")


def _predeploy() -> str:
    return PREDEPLOY.read_text(encoding="utf-8")


def _deploy() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_runtime_dependency_is_packaged_installed_rolled_back_and_verified():
    from scripts.porota_build_deploy_bundle_v2 import select_bundle_paths

    source = _deploy()
    module = "rc6_source_consolidation.py"
    assert select_bundle_paths([module]) == [module]
    assert 'assert data.get("status")=="GREEN" and data.get("files")' in source
    assert 'assert src.is_file() and sha(src)==row["sha256"]' in source
    assert 'shutil.copy2(src,dst)' in source
    assert 'assert sha(dst)==row["sha256"]' in source
    assert "rollback" not in source.lower()


def test_local_v2_reconciliation_is_required_even_when_remote_refresh_is_closed_only():
    source = _deploy()
    assert 'REMOTE_PPI_V1_REFRESH=SKIPPED_MARKET_OPEN' in source
    assert 'DATABASE_READONLY_PROBE=1' in source
    assert 'grep -Fq "STATUS=OK" <<< "$CONTRACT_OUTPUT"' in source
    assert 'grep -Fq "QUICK_CHECK=ok" <<< "$CONTRACT_OUTPUT"' in source
    assert "LATEST_EVIDENCE_V2_RUN=('CONTRACT_EVIDENCE_V2_MASS_PPI_CATALOG', 'OK'" in source


def test_dashboard_bootstrap_dependencies_and_storage_cleanup_are_deploy_guards():
    from scripts.porota_build_deploy_bundle_v2 import select_bundle_paths

    source = _deploy()
    assert select_bundle_paths(["_version.py", "cg_paper_workspace.py"]) == [
        "_version.py", "cg_paper_workspace.py"
    ]
    assert "SAFE_PREFLIGHT_RECLAIM=" in source
    assert "CURRENT_STATE_V2=GREEN" in source
    assert "SPACE_RECOVERED=" in source
    assert "POROTA_DEPLOY_V2_RUNNER_CLEANUP=GREEN" in source


def test_dashboard_is_recreated_directly_from_verified_candidate():
    source = _deploy()
    loaded = source.index('gunzip -c "$REMOTE_DIR/porota-predeploy-image.tar.gz" | sudo -n docker load')
    tagged = source.index('sudo -n docker tag "$FROZEN_IMAGE" "$STABLE_IMAGE"')
    recreated = source.index('porota_mode_manager.py" simulation')
    assert loaded < tagged < recreated
    assert not re.search(r"(?m)^\s*(?:sudo -n )?docker build(?:\s|$)", source)
    assert 'DASHBOARD_IMAGE_ID="$(sudo -n docker inspect' in source
    assert 'test "$DASHBOARD_IMAGE_ID" = "$RUNTIME_IMAGE_ID"' in source


def test_all_verified_dashboard_modules_are_explicit_image_inputs():
    source = DOCKERFILE.read_text(encoding="utf-8")
    modules = (
        "bg_paper_dashboard.py", "zz_wave8_dashboard_live_rc6.py", "da_dashboard_ux_hf6.py",
        "rc6_annual_instrument_analysis.py", "rc6_on_validation.py", "bd_ppi_readonly_guard.py",
        "c_ppi_client.py", "cr_pending_settlement_diagnostics_hf6.py", "eq_dashboard_table_layout_rc6.py",
        "rc6_dashboard_responsive_ux.py", "rc6_family_readiness.py", "o_dashboard.py",
    )
    for module in modules:
        assert f"COPY {module} /app/{module}" in source


def test_contract_reconciliation_reuses_the_immutable_candidate_image():
    source = _deploy()
    promote = source[source.index("- name: Promote exact frozen candidate and verify"):]
    assert 'gunzip -c "$REMOTE_DIR/porota-predeploy-image.tar.gz" | sudo -n docker load' in promote
    assert '--entrypoint python "$STABLE_IMAGE" ck_contract_evidence_runner_hf6.py' in promote
    assert not re.search(r"(?m)^\s*(?:sudo -n )?docker build(?:\s|$)", promote)


def test_contract_reconciliation_cannot_race_observer_startup_catalog_refresh():
    source = _deploy()
    stopped = source.index("OBSERVER_PAUSED_FOR_CONTRACT_RECONCILIATION=GREEN")
    contract = source.index('CONTRACT_OUTPUT="$(sudo -n timeout')
    restarted = source.index("OBSERVER_RESTARTED_AFTER_CONTRACT_RECONCILIATION=GREEN")
    checked = source.index('test "$CONTRACT_RC" -eq 0')
    assert stopped < contract < restarted < checked


def test_late_verification_uses_retained_stable_image_tag():
    source = _deploy()
    assert 'STABLE_IMAGE="porota-trading-bot:17.0.0-rc6"' in source
    assert 'sudo -n docker tag "$FROZEN_IMAGE" "$STABLE_IMAGE"' in source
    assert '--pull never' in source
    assert 'docker image rm "$STABLE_IMAGE"' not in source


def test_contract_runner_uses_host_timeout_and_python_entrypoint():
    source = _deploy()
    assert 'sudo -n timeout --signal=TERM --kill-after=60 3300 sudo -n docker run' in source
    assert 'timeout-minutes: 90' in source
    assert '--entrypoint python "$STABLE_IMAGE" ck_contract_evidence_runner_hf6.py' in source
    assert '--entrypoint timeout' not in source


def test_sector_map_and_multisource_helpers_are_immutable_deploy_inputs():
    from scripts.porota_build_deploy_bundle_v2 import select_bundle_paths

    artifacts = ["POROTA_SECTOR_MAP_V1.csv", "rc6_multisource_discovery.py", "rc6_iol_family_reference.py"]
    assert select_bundle_paths(artifacts) == sorted(artifacts)
    assert "POROTA_SECTOR_MAP_V1.csv" in _predeploy()
    source = _deploy()
    assert 'for row in data["files"]' in source
    assert 'assert src.is_file() and sha(src)==row["sha256"]' in source


def test_iol_fail_safe_contract_is_packaged_and_migrated_before_runtime_audit():
    from scripts.porota_build_deploy_bundle_v2 import select_bundle_paths

    migrator = "scripts/rc6_iol_reference_contract_migrate.py"
    assert select_bundle_paths([migrator]) == [migrator]
    source = _deploy()
    migrated = source.index("IOL_REFERENCE_CONTRACT_MIGRATION=GREEN")
    audited = source.index("rc6_zero_known_error_runtime_audit.py")
    assert migrated < audited
    assert "ZERO_FILL=false|REAL_ROUTES=[]" in source

def test_deploy_scope_rejects_watchlist_as_universe_authority():
    observer = (ROOT / "bf_production_paper_observer.py").read_text(encoding="utf-8")
    assert "DISCOVERY_SEEDS = {}" in observer
    assert "WATCHLIST_PATH.read_text" not in observer
    assert "from rc6_multisource_discovery import" in observer

def test_live_safety_checks_do_not_use_heavy_quick_check():
    source = _deploy()
    fast = source[source.index('POST_STATE=""'):source.index('CONTRACT_OUTPUT=')]
    assert "PRAGMA quick_check" not in fast
    assert "PRAGMA query_only=ON" in fast
    assert "POST_STATE_HOST_READONLY_ATTEMPT" in fast
    assert 'test "$POST_STATE" = "STATE_FAST|PRODUCTION_PAPER|0"' in fast
    assert "POST_SQLITE_QUICK_CHECK=DEFERRED_TO_CONTRACT_RUNNER" in source


def test_sector_map_runtime_verifier_keeps_stdin_attached():
    source = _deploy()
    assert '--entrypoint python -i "$STABLE_IMAGE" - <<\'PY\'' in source
    assert "OPERATIONAL_SCOPE=ALL_CONTRACT_FAMILIES" in source


def test_history_cutoff_repair_is_decoupled_from_code_deploy():
    source = _deploy()
    policy = POLICY.read_text(encoding="utf-8")
    assert "rc6_history_cutoff_repair_once.py" not in source
    assert "RC6_HISTORY_ALLOW_PPI_GAP_REPAIR" not in source
    assert "strategy: FIX_FORWARD_ONLY" in policy
    assert "rollback_allowed: false" in policy
    assert not (ROOT / ".github/workflows/deploy.yml").exists()
    assert not (ROOT / ".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml").exists()


def test_runtime_audit_outer_budget_covers_measured_route_cycle():
    source = _deploy()
    assert source.count("docker exec porota_production_dashboard timeout 300") == 2


def test_deploy_v2_fails_closed_without_two_parent_promotion_merge():
    source = _deploy()
    assert 'if [ "${#PARTS[@]}" -ne 3 ]; then' in source
    assert "POROTA_DEPLOY_V2_PROMOTION_COMMIT_INVALID=RED" in source
    assert "requires a two-parent merge commit" in source
    assert "exit 31" in source
    assert 'CANDIDATE_SHA="${PARTS[2]}"' in source

def test_zero_known_error_output_path_is_writable_by_dashboard_runtime_before_audit():
    source = _deploy()
    prepared = source.index('AUDIT_HOST_DIR="$REPO/data/deploy"')
    audited = source.index('AUDIT_OUTPUT="$(sudo -n docker exec porota_production_dashboard timeout 300')
    assert prepared < audited
    assert 'AUDIT_UID="$(sudo -n docker exec porota_production_dashboard id -u)"' in source
    assert 'AUDIT_GID="$(sudo -n docker exec porota_production_dashboard id -g)"' in source
    assert 'install -d -o "$AUDIT_UID" -g "$AUDIT_GID" -m 0750 "$AUDIT_HOST_DIR"' in source
    assert "-name 'zero-known-error-*.json'" in source
    assert 'test "$AUDIT_HOST_STAT" = "$AUDIT_UID:$AUDIT_GID|750"' in source
    assert 'docker exec porota_production_dashboard test -w /app/data/deploy' in source
    assert "RC6_ZERO_KNOWN_ERROR_OUTPUT_PATH_WRITABLE=GREEN" in source

def test_deploy_waits_boundedly_for_scalping_recovery_before_immediate_audit():
    source = _deploy()
    baseline = source.index("RC6_POST_RECONCILIATION_RESTART_BASELINE=")
    stabilized = source.index("RC6_SCALPING_STABILIZATION=GREEN")
    audited = source.index('AUDIT_OUTPUT="$(sudo -n docker exec porota_production_dashboard timeout 300')
    assert baseline < stabilized < audited
    assert "SCALPING_STABILIZATION_SECONDS=420" in source
    assert "SCALPING_STABILIZATION_POLL_SECONDS=15" in source
    assert 'SCALPING_STABILIZATION_STARTED_AT="$(date -u' in source
    assert 'healthy_state=state in ("RUNNING","WAITING_MARKET")' in source
    assert "post_gate_heartbeat=dt >= started" in source
    assert "healthy=(healthy_state and failed==0 and 0 <= age <= 300" in source
    assert "and post_gate_heartbeat" in source
    assert 'test "$OBSERVER_RESTART_NOW" = "$OBSERVER_RESTART_BASE"' in source
    assert 'test "$DASHBOARD_RESTART_NOW" = "$DASHBOARD_RESTART_BASE"' in source
    assert 'test "$SCALPING_STABILIZED" = true' in source
    stabilization = source[source.index("SCALPING_STABILIZATION_SECONDS=420"):stabilized]
    assert 'state=="DEGRADED"' not in stabilization
    assert "docker restart" not in stabilization
    assert "docker stop" not in stabilization


def test_deploy_stability_fails_on_any_runtime_container_restart():
    source = _deploy()
    assert "OBSERVER_RESTART_BASE=" in source
    assert "DASHBOARD_RESTART_BASE=" in source
    assert "RC6_POST_RECONCILIATION_RESTART_BASELINE=" in source
    assert 'test "$OBSERVER_RESTART_BASE" = 0' in source
    assert 'test "$DASHBOARD_RESTART_BASE" = 0' in source
    assert "RC6_STABILITY_RESTART_BASELINE=" in source
    assert 'test "$OBSERVER_RESTART_NOW" = "$OBSERVER_RESTART_BASE"' in source
    assert 'test "$DASHBOARD_RESTART_NOW" = "$DASHBOARD_RESTART_BASE"' in source
    assert source.index("OBSERVER_RESTART_BASE=") < source.index("BOOTSTRAP_")
    assert source.index("OBSERVER_RESTART_BASE=") < source.index("for cycle in 1 2 3; do")
    assert source.index('test "$OBSERVER_RESTART_NOW" = "$OBSERVER_RESTART_BASE"') < source.index('echo "RC6_ZERO_KNOWN_ERROR_STABILITY_$cycle=GREEN"')



def test_deploy_has_lightweight_exit139_soak_after_full_audits():
    source = _deploy()
    assert "for cycle in 1 2 3; do" in source
    assert "for soak in 1 2 3 4 5; do" in source
    assert "RC6_EXIT139_SOAK=GREEN" in source
    assert 'test "$OBSERVER_RESTART_SOAK" = "$OBSERVER_RESTART_BASE"' in source
    assert 'test "$DASHBOARD_RESTART_SOAK" = "$DASHBOARD_RESTART_BASE"' in source


def test_preopen_failure_preserves_diagnostics_before_fail_closed_exit():
    source = _deploy()
    start = source.index("for phase in T_MINUS_45 T_MINUS_10; do")
    end = source.index("# Observe several worker/dashboard refresh cycles", start)
    block = source[start:end]
    assert "set +e" in block
    assert "PREOPEN_RC=$?" in block
    assert "set -e" in block
    assert 'echo "$PREOPEN_OUTPUT"' in block
    assert 'RC6_PREOPEN_${phase}=RED|RC=$PREOPEN_RC' in block
    assert 'exit "$PREOPEN_RC"' in block
    assert block.index('echo "$PREOPEN_OUTPUT"') < block.index('exit "$PREOPEN_RC"')
    # The strict result parser replaces GREEN-only grep without weakening
    # process diagnostics or calendar/readiness validation (see its 13 tests).
    assert 'printf \'%s\\n\' "$PREOPEN_OUTPUT" |' in block
    assert '"$REPO/scripts/rc6_deploy_preopen_gate.py"' in block
    assert '--phase "$phase" --return-code "$PREOPEN_RC"' in block
    assert 'grep -Fq \'"status": "GREEN"\'' not in block
    assert 'RC6_PREOPEN_${phase}=RESULT_ACCEPTED' in block


def test_deploy_reclaims_own_transient_artifacts_before_preopen_disk_gate():
    source = _deploy()
    immediate = source.index('RC6_ZERO_KNOWN_ERROR_IMMEDIATE=GREEN')
    reclaim = source.index('RC6_PREOPEN_SAFE_RECLAIM=GREEN')
    preopen = source.index('for phase in T_MINUS_45 T_MINUS_10; do')
    assert immediate < reclaim < preopen
    block = source[immediate:preopen]
    assert 'rm -f "$REMOTE_DIR/porota-predeploy-image.tar.gz" "$REMOTE_DIR/porota-deploy-bundle-v2.tgz"' in block
    assert 'rm -rf "$STAGE"' in block
    assert "rc6-disk-housekeeping-v1.json" in block
    assert "post_cleanup_min_free_bytes" in block
    assert 'test "$DISK_PREOPEN_AFTER_SAFE_RECLAIM" -ge "$POST_CLEANUP_MIN_FREE"' in block


def test_deploy_refreshes_byma_authority_before_dry_preopen():
    source = _deploy()
    refresh = source.index('RC6_BYMA_MORNING_REFRESH=GREEN')
    preopen = source.index('for phase in T_MINUS_45 T_MINUS_10; do')
    assert refresh < preopen
    block = source[source.index('BYMA_MORNING_OUTPUT='):preopen]
    assert 'rc6_byma_morning_pipeline.py' in block
    assert 'BYMA_MORNING_RC=$?' in block
    assert 'RC6_BYMA_MORNING_REFRESH=RED|RC=$BYMA_MORNING_RC' in block
    assert 'exit "$BYMA_MORNING_RC"' in block

