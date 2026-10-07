from pathlib import Path
import shlex


WORKFLOW = Path(".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")


def test_frozen_artifact_is_exported_only_after_paper_safety():
    paper = WORKFLOW.index("- name: PAPER safety source gate")
    export = WORKFLOW.index("- name: Export frozen candidate artifacts")
    assert paper < export


def test_frozen_artifact_uses_exact_pr_head_and_cannot_be_skipped():
    assert "ref: ${{ env.CANDIDATE_SHA }}" in WORKFLOW
    assert "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha }}" in WORKFLOW
    assert 'docker save "$IMAGE" | gzip -1 > "${POROTA_PREDEPLOY_TMP}/porota-predeploy-image.tar.gz"' in WORKFLOW
    assert "porota-deploy-bundle-v2.tgz" in WORKFLOW

    export = WORKFLOW.index("- name: Export frozen candidate artifacts")
    contract = WORKFLOW.index("- name: Frozen candidate fail-closed contract")
    upload = WORKFLOW.index("- name: Upload predeploy evidence")
    export_step = WORKFLOW[export:contract]
    contract_step = WORKFLOW[contract:upload]
    assert "if:" not in export_step
    assert "if:" not in contract_step


def test_ready_for_review_retriggers_final_predeploy():
    assert "types: [opened, synchronize, reopened, ready_for_review]" in WORKFLOW


def test_governed_pytest_isolated_from_runner_entrypoints():
    commands = [line.strip() for line in WORKFLOW.splitlines()
                if "scripts/porota_predeploy_test_workspace.py supervise-pytest" in line]
    assert len(commands) == 2
    collection, execution = map(shlex.split, commands)
    for command in (collection, execution):
        assert command[0] == "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1"
        assert command[1] == "$BOOTSTRAP_PYTHON"
        assert command[2:4] == ["scripts/porota_predeploy_test_workspace.py", "supervise-pytest"]
        child = command[command.index("--") + 1:]
        assert child[:3] == ["python", "-m", "pytest"]
        assert child[child.index("-p") + 1] == "no:cacheprovider"
    assert collection[collection.index("--phase") + 1] == "collection"
    assert "--collect-only" in collection and "--collect-only" not in execution
    assert "--phase" not in execution  # The supervisor's default phase is execution.
    collection_fin = WORKFLOW.index("require-owned-fin --phase collection")
    execution_fin = WORKFLOW.index('require-owned-fin --scope "$POROTA_PREDEPLOY_OWNER"')
    assert WORKFLOW.index(commands[0]) < collection_fin < WORKFLOW.index(commands[1])
    assert WORKFLOW.index(commands[1]) < execution_fin < WORKFLOW.index("junit_bytes =")
