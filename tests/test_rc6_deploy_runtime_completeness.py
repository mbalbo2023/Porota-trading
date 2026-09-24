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
    assert 'sudo -n docker image inspect "$CANDIDATE_IMAGE" >/dev/null' in verify
    assert 'docker build --no-cache --pull=false -t "$CANDIDATE_IMAGE" "$REPO"' not in verify
