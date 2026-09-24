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
