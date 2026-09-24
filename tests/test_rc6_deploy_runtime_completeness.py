from pathlib import Path

WORKFLOW = Path(".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml")


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
    assert 'RUNNING_DASHBOARD_IMAGE_ID="$(sudo -n docker inspect' in source
    assert 'test "$RUNNING_DASHBOARD_IMAGE_ID" = "$EXPECTED_DASHBOARD_IMAGE_ID"' in source
