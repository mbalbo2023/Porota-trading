from pathlib import Path

WORKFLOW = Path(".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml")
DOCKERFILE = Path("Dockerfile")


def test_runtime_dependency_is_packaged_installed_rolled_back_and_verified():
    source = WORKFLOW.read_text(encoding="utf-8")
    module = "rc6_source_consolidation.py"
    assert module in source
    assert source.count(module) >= 6
    assert 'test -f "$STAGE/rc6_source_consolidation.py"' in source
    assert 'test -f "$REPO/rc6_source_consolidation.py"' in source
    assert "RC6_DEPLOY_IMPORT=GREEN|rc6_source_consolidation" in source
    assert "RUNNING_SOURCE_SHA=GREEN|observer|$f" in source


def test_postclose_reconciliation_is_only_required_when_it_can_run():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert 'STATUS=SKIPPED_MARKET_NOT_CLOSED' in source
    assert 'RC6_FULL_CONTRACT_RECONCILIATION=SKIPPED_MARKET_OPEN' in source
    assert 'grep -Fq "STATUS=OK" <<< "$CONTRACT_OUTPUT"' in source
    assert 'grep -Fq "QUICK_CHECK=ok" <<< "$CONTRACT_OUTPUT"' in source


def test_dashboard_bootstrap_dependencies_and_storage_cleanup_are_deploy_guards():
    source = WORKFLOW.read_text(encoding="utf-8")
    for module in ("_version.py", "cg_paper_workspace.py"):
        assert source.count(module) >= 4
    assert "RC6_HOST_RUNTIME_IMAGE=GREEN" in source
    assert "RC6_HOST_RUNTIME_BOOTSTRAP=GREEN" in source
    assert "RC6_POSTDEPLOY_STORAGE_CLEANUP=START" in source
    assert "RC6_POSTDEPLOY_STORAGE_CLEANUP=GREEN" in source


def test_dashboard_is_recreated_directly_from_verified_candidate():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "RC6_VERIFY_DASHBOARD_SOURCE_SHA=GREEN" in source
    assert "RC6_VERIFY_DASHBOARD_IMAGE=GREEN" in source
    assert '"$STAGE"; then' in source
    assert "RC6_IMAGE_SOURCE_SHA=GREEN|dashboard|$f" in source
    assert "requirements.txt docker-compose.yml Dockerfile" in source
    assert ".dockerignore requirements.txt docker-compose.yml Dockerfile" in source
    assert "RC6_IMAGE_SOURCE_SHA_MISMATCH=dashboard|$f" in source
    assert 'RUNNING_DASHBOARD_IMAGE_ID="$(sudo -n docker inspect' in source
    assert 'test "$RUNNING_DASHBOARD_IMAGE_ID" = "$EXPECTED_DASHBOARD_IMAGE_ID"' in source


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
    source = WORKFLOW.read_text(encoding="utf-8")
    verify = source[source.index("- name: Verify deployed candidate and Paper safety"):]
    assert 'sudo -n docker image inspect "$VERIFY_IMAGE" >/dev/null' in verify
    assert 'docker build --no-cache --pull=false -t "$CANDIDATE_IMAGE" "$REPO"' not in verify


def test_late_verification_uses_retained_stable_image_tag():
    source = WORKFLOW.read_text(encoding="utf-8")
    verify = source[source.index("- name: Verify deployed candidate and Paper safety"):]
    assert 'TARGET_IMAGE="porota-trading-bot:17.0.0-rc6"' in verify
    assert 'VERIFY_IMAGE="$TARGET_IMAGE"' in verify
    assert 'docker image inspect "$VERIFY_IMAGE"' in verify
    assert 'docker image inspect "$CANDIDATE_IMAGE"' not in verify


def test_contract_runner_uses_host_timeout_and_python_entrypoint():
    source = WORKFLOW.read_text(encoding="utf-8")
    verify = source[source.index("- name: Verify deployed candidate and Paper safety"):]
    assert 'sudo -n timeout 1200 sudo -n docker run' in verify
    assert '--entrypoint python "$VERIFY_IMAGE" ck_contract_evidence_runner_hf6.py' in verify
    assert '--entrypoint timeout "$VERIFY_IMAGE" 1200 python ck_contract_evidence_runner_hf6.py' not in verify


def test_sector_map_and_multisource_helpers_are_immutable_deploy_inputs():
    source = WORKFLOW.read_text(encoding="utf-8")
    for artifact in ("POROTA_SECTOR_MAP_V1.csv", "rc6_multisource_discovery.py", "rc6_iol_family_reference.py"):
        assert artifact in source
    assert 'test -f "$STAGE/POROTA_SECTOR_MAP_V1.csv"' in source
    assert 'test -f "$STAGE/rc6_multisource_discovery.py"' in source
    assert 'test -f "$STAGE/rc6_iol_family_reference.py"' in source
    assert "SECTOR_MAP_RUNTIME=GREEN" in source
    assert "SECTOR_MAP_EMPTY" in source

def test_deploy_scope_rejects_watchlist_as_universe_authority():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "DISCOVERY=DYNAMIC_MULTISOURCE" in source
    assert 'assert observer.DISCOVERY_SEEDS == {}' in source
    assert 'assert "WATCHLIST_PATH.read_text" not in source' in source

def test_live_safety_checks_do_not_use_heavy_quick_check():
    source = WORKFLOW.read_text(encoding="utf-8")
    verify = source[source.index("- name: Verify deployed candidate and Paper safety"):]
    assert "PRAGMA quick_check" not in verify
    assert "POST_STATE=" in verify
    assert "PRODUCTION_PAPER|0" in verify


def test_sector_map_runtime_verifier_keeps_stdin_attached():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "SECTOR_MAP_CHECK=\"$(sudo -n docker exec -i porota_production_observer python - <<'PY'" in source


def test_history_cutoff_repair_is_decoupled_from_code_deploy():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "HISTORY_CUTOFF_REPAIR_EXECUTION=DECOUPLED_FROM_DEPLOY" in source
    assert "deploy_nonblocking=true" in source
    assert "RC6_HISTORY_ALLOW_PPI_GAP_REPAIR=APPROVED" not in source
    assert "timeout 3600 python /app/scripts/rc6_history_cutoff_repair_once.py" not in source
