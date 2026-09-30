from pathlib import Path


WORKFLOW = Path(".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")


def test_frozen_artifact_is_exported_only_after_paper_safety():
    paper = WORKFLOW.index("- name: PAPER safety source gate")
    export = WORKFLOW.index("- name: Export frozen candidate artifacts")
    assert paper < export


def test_frozen_artifact_uses_exact_pr_head_and_cannot_be_skipped():
    assert "ref: ${{ env.CANDIDATE_SHA }}" in WORKFLOW
    assert "CANDIDATE_SHA: ${{ github.event.pull_request.head.sha }}" in WORKFLOW
    assert "docker save \"$IMAGE\" | gzip -1 > /tmp/porota-predeploy-image.tar.gz" in WORKFLOW
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
    assert WORKFLOW.count(
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest"
    ) == 2
