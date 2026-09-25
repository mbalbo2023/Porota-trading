from pathlib import Path


PRODUCTION_BRANCH = "deploy/rc6-pr69-isolated-20260915"
EXPECTED_PUSH_WORKFLOW = ".github/workflows/porota-deploy-v2-promote.yml"


def _trigger_block(text: str) -> str:
    # Workflows in this repository place permissions/jobs after the trigger.
    # Restrict the check to the header so branch names in scripts/comments
    # cannot masquerade as a production trigger.
    cut = len(text)
    for marker in ("\npermissions:", "\njobs:"):
        pos = text.find(marker)
        if pos >= 0:
            cut = min(cut, pos)
    return text[:cut]


def test_exactly_one_workflow_can_push_deploy_current_production_branch():
    matches = []
    for path in sorted(Path(".github/workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        trigger = _trigger_block(text)
        if PRODUCTION_BRANCH in trigger and "\n  push:" in trigger:
            matches.append(path.as_posix())
    assert matches == [EXPECTED_PUSH_WORKFLOW]


def test_legacy_rebuild_workflow_is_retired_from_actions():
    assert not Path(
        ".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml"
    ).exists()
