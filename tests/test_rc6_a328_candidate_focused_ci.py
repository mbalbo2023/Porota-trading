"""Fail closed if a candidate sub-PR could start the full Predeploy gate."""

from pathlib import Path

import pytest
import yaml


WORKFLOW = Path(".github/workflows/porota-predeploy-v2.yml").read_text(encoding="utf-8")
PRODUCT = "deploy/rc6-pr69-isolated-20260915"
CANDIDATE = "candidate/rc6-a279-a280-20261101"
ARTIFACT_GUARD = "${{ !github.event.pull_request.draft && github.event.pull_request.base.ref == 'deploy/rc6-pr69-isolated-20260915' }}"
FOCUSED_GUARD = "${{ github.event.pull_request.base.ref == 'candidate/rc6-a279-a280-20261101' }}"
LEDGER_GUARD = "${{ github.event.pull_request.draft && github.event.pull_request.base.ref == 'deploy/rc6-pr69-isolated-20260915' }}"


def assert_candidate_ci_contract(source: str) -> None:
    workflow = yaml.safe_load(source)
    events = workflow.get("on", workflow.get(True))
    pull_request = events["pull_request"]
    assert PRODUCT in pull_request["branches"]
    assert CANDIDATE in pull_request["branches"]
    assert {"opened", "synchronize", "reopened", "ready_for_review"} <= set(
        pull_request["types"]
    )
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert jobs["artifact-gate"]["if"] == ARTIFACT_GUARD
    assert jobs["candidate-focused-offhost"]["if"] == FOCUSED_GUARD
    assert jobs["draft-ledger-permissions"]["if"] == LEDGER_GUARD

    focused = jobs["candidate-focused-offhost"]
    assert focused["runs-on"] == "ubuntu-24.04"
    assert 5 <= int(focused["timeout-minutes"]) <= 25
    steps = focused["steps"]
    checkout = steps[0]
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    scope = next(step for step in steps if step.get("name", "").startswith("Pin candidate sub-PR"))
    assert scope["env"]["RC6_BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert scope["env"]["RC6_HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    source_code = scope["run"]
    assert 'test "$(git rev-parse HEAD)" = "$RC6_HEAD_SHA"' in source_code
    assert 'git cat-file -e "$RC6_BASE_SHA^{commit}"' in source_code
    assert "git diff --name-only -z" in source_code
    assert "RC6_CANDIDATE_TEST_TARGET_MISSING" in source_code
    assert "name.startswith('tests/test_')" in source_code
    runner = next(step for step in steps if step.get("name", "").startswith("Run all changed test"))
    runner_code = runner["run"]
    assert 'test "$(id -u)" -ne 0' in runner_code
    assert "requirements.lock.txt" in runner_code
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest" in runner_code
    assert "rc6-candidate-focused.xml" in runner_code
    assert '("failures", "errors", "skipped")' in runner_code
    assert "RC6_CANDIDATE_CASE_RED" in runner_code
    assert "RC6_CANDIDATE_FOCUSED_TESTS=GREEN" in runner_code
    all_runs = "\n".join(step.get("run", "") for step in steps)
    assert not any(token in all_runs for token in ("docker ", "ssh ", "rsync ", "gh api", "secrets."))


def test_candidate_pr_focus_is_isolated_from_exact_product_predeploy():
    assert_candidate_ci_contract(WORKFLOW)


@pytest.mark.parametrize(
    ("base_ref", "is_draft", "ledger_runs"),
    [
        (PRODUCT, True, True),
        (PRODUCT, False, False),
        (CANDIDATE, True, False),
        (CANDIDATE, False, False),
    ],
)
def test_a309_ledger_job_does_not_follow_reverse_diff_on_candidate_pr(
    base_ref, is_draft, ledger_runs
):
    # A source HEAD inherited from c76 can lack the A309 WAL cases present
    # in candidate d11. Its two-tip diff must never admit the product ledger
    # job merely because those tests appear in the reverse diff.
    assert_candidate_ci_contract(WORKFLOW)
    assert (is_draft and base_ref == PRODUCT) is ledger_runs


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (ARTIFACT_GUARD, "${{ !github.event.pull_request.draft }}"),
        (FOCUSED_GUARD, "${{ github.event.pull_request.draft }}"),
        (LEDGER_GUARD, "${{ github.event.pull_request.draft }}"),
        ('("failures", "errors", "skipped")', '("failures", "errors")'),
        ("RC6_CANDIDATE_TEST_TARGET_MISSING", "RC6_CANDIDATE_TEST_TARGET_DISABLED"),
        ("persist-credentials: false", "persist-credentials: true"),
        ("git diff --name-only -z", "git diff --name-only"),
    ],
)
def test_candidate_ci_rejects_gate_evasion_or_evidence_loss(before, after):
    assert before in WORKFLOW
    with pytest.raises(AssertionError):
        assert_candidate_ci_contract(WORKFLOW.replace(before, after, 1))
