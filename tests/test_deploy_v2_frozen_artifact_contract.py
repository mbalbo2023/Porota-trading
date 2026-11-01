from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")
LEDGER_GUARD = "${{ github.event.pull_request.draft && github.event.pull_request.base.ref == 'deploy/rc6-pr69-isolated-20260915' }}"


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


def _assert_governed_pytest_isolated(workflow_text: str) -> None:
    """Keep the full release gate separate from the Draft-only WAL proof."""
    workflow = yaml.safe_load(workflow_text)
    jobs = workflow["jobs"]
    artifact = jobs["artifact-gate"]
    draft = jobs["draft-ledger-permissions"]
    assert artifact["if"] == _expected_artifact_guard(workflow)
    assert draft["if"] == LEDGER_GUARD

    command = "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest"
    artifact_scripts = "\n".join(step.get("run", "") for step in artifact["steps"])
    draft_scripts = "\n".join(step.get("run", "") for step in draft["steps"])
    # The established artifact gate still owns three governed pytest phases;
    # the fourth invocation is a separate offhost Draft-only focal regression.
    assert artifact_scripts.count(command) == 3
    assert draft_scripts.count(command) == 1
    assert "test \"$(id -u)\" -ne 0" in draft_scripts
    assert "--junitxml=/tmp/rc6-draft-ledger-permissions.xml" in draft_scripts
    assert "('failures', 'errors', 'skipped')" in draft_scripts
    assert "required <= set(names) and len(names) == len(set(names))" in draft_scripts
    assert "RC6_LEDGER_PERMISSION_REGRESSION=GREEN" in draft_scripts
    assert not any(token in draft_scripts for token in ("docker ", "ssh ", "rsync "))


def _expected_artifact_guard(workflow: dict) -> str:
    # PyYAML 1.1 reads the unquoted GitHub Actions `on` key as boolean True.
    events = workflow.get("on", workflow.get(True))
    branches = events["pull_request"]["branches"]
    product = "deploy/rc6-pr69-isolated-20260915"
    candidate = "candidate/rc6-a279-a280-20261101"
    assert product in branches
    if candidate in branches:
        # A candidate-targeted sub-PR may become Ready. It must NEVER run the
        # full artifact/deploy preflight, regardless of its Draft state.
        return "${{ !github.event.pull_request.draft && github.event.pull_request.base.ref == 'deploy/rc6-pr69-isolated-20260915' }}"
    return "${{ !github.event.pull_request.draft }}"


def test_governed_pytest_isolated_from_runner_entrypoints():
    _assert_governed_pytest_isolated(WORKFLOW)
    assert WORKFLOW.index("- name: Fast host-transition incident replay") < WORKFLOW.index(
        "- name: Governed automatic test discovery and execution"
    )


@pytest.mark.parametrize("regression", ["artifact_guard", "draft_guard", "junit_skipped", "required_names"])
def test_governed_pytest_rejects_draft_guard_or_junit_regression(regression):
    mutations = {
        "artifact_guard": (_expected_artifact_guard(yaml.safe_load(WORKFLOW)), "${{ github.event.pull_request.draft }}"),
        "draft_guard": (LEDGER_GUARD, "${{ github.event.pull_request.draft }}"),
        "junit_skipped": ("('failures', 'errors', 'skipped')", "('failures', 'errors')"),
        "required_names": ("required <= set(names) and len(names) == len(set(names))", "len(names) == len(set(names))"),
    }
    before, after = mutations[regression]
    assert before in WORKFLOW
    with pytest.raises(AssertionError):
        _assert_governed_pytest_isolated(WORKFLOW.replace(before, after, 1))


def test_candidate_pr_event_without_artifact_base_pin_is_rejected():
    workflow = yaml.safe_load(WORKFLOW)
    branches = workflow.get("on", workflow.get(True))["pull_request"]["branches"]
    if "candidate/rc6-a279-a280-20261101" in branches:
        unsafe = WORKFLOW.replace(_expected_artifact_guard(workflow),
                                  "${{ !github.event.pull_request.draft }}", 1)
    else:
        unsafe = WORKFLOW.replace("      - deploy/rc6-pr69-isolated-20260915",
                                  "      - deploy/rc6-pr69-isolated-20260915\n      - candidate/rc6-a279-a280-20261101", 1)
    with pytest.raises(AssertionError):
        _assert_governed_pytest_isolated(unsafe)


def test_candidate_pr_event_with_exact_product_base_pin_is_accepted():
    workflow = yaml.safe_load(WORKFLOW)
    branches = workflow.get("on", workflow.get(True))["pull_request"]["branches"]
    if "candidate/rc6-a279-a280-20261101" in branches:
        safe = WORKFLOW
    else:
        safe = WORKFLOW.replace("      - deploy/rc6-pr69-isolated-20260915",
                                "      - deploy/rc6-pr69-isolated-20260915\n      - candidate/rc6-a279-a280-20261101", 1)
        safe = safe.replace("${{ !github.event.pull_request.draft }}",
                            "${{ !github.event.pull_request.draft && github.event.pull_request.base.ref == 'deploy/rc6-pr69-isolated-20260915' }}", 1)
    _assert_governed_pytest_isolated(safe)
