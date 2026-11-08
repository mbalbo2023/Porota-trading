"""A1004: main-only WS12 entry must not self-certify candidate QA."""

from pathlib import Path
import importlib.util
import json
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts/rc6_ci_ws12_main_entry.py"
WORKFLOW = ROOT / ".github/workflows/rc6-ws-tests-12-current-contract.yml"


def entry_module():
    spec = importlib.util.spec_from_file_location("rc6_ws12_main_entry", ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_context():
    return {
        "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_PROTECTED": "true",
        "GITHUB_WORKFLOW_REF": (
            "mbalbo2023/Porota-trading/.github/workflows/"
            "rc6-ws-tests-12-current-contract.yml@refs/heads/main"
        ),
        "GITHUB_WORKFLOW_SHA": "a" * 40,
        "GITHUB_SHA": "a" * 40,
        "RC6_CANDIDATE_SHA": "b" * 40,
        "RC6_CANDIDATE_TREE_SHA": "c" * 40,
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_RUN_ID": "123456789",
        "GITHUB_RUN_ATTEMPT": "1",
    }


def test_main_dispatch_is_accepted_only_as_hold(capsys):
    result = entry_module().main(valid_context())
    assert result == 79
    assert capsys.readouterr() == (
        "RC6_WS12_CONTROL=MAIN_CONTEXT_VERIFIED_HOLD_NO_OCI|"
        "qa_certified=false|automatic_go=false\n", ""
    )


@pytest.mark.parametrize("change", [
    {"GITHUB_REPOSITORY": "other/repo"},
    {"GITHUB_EVENT_NAME": "pull_request"},
    {"GITHUB_REF": "refs/heads/candidate"},
    {"GITHUB_REF_PROTECTED": "false"},
    {"GITHUB_REF_PROTECTED": ""},
    {"GITHUB_REF_PROTECTED": "TRUE"},
    {"GITHUB_WORKFLOW_REF": "mbalbo2023/Porota-trading/.github/workflows/rc6-ws-tests-12-current-contract.yml@refs/heads/feature"},
    {"GITHUB_WORKFLOW_SHA": "d" * 40},
    {"RUNNER_ENVIRONMENT": "self-hosted"},
    {"GITHUB_RUN_ID": "0"},
    {"GITHUB_RUN_ATTEMPT": "0"},
    {"RC6_CANDIDATE_SHA": "main"},
    {"RC6_CANDIDATE_TREE_SHA": ""},
    {"RC6_CANDIDATE_SHA": "a" * 40},
])
def test_forged_or_incomplete_context_rejected_without_leaking_values(change, capsys):
    context = valid_context()
    context.update(change)
    context["GITHUB_TOKEN"] = "SYNTHETIC_SECRET"
    result = entry_module().main(context)
    assert result == 79
    out = capsys.readouterr()
    assert out == (
        "RC6_WS12_CONTROL=UNTRUSTED_CONTEXT|qa_certified=false|"
        "automatic_go=false\n", ""
    )
    assert "SYNTHETIC_SECRET" not in out[0]


def test_workflow_uses_single_protected_main_entry_before_any_candidate_execution():
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = doc.get("on", doc.get(True))
    assert set(triggers) == {"workflow_dispatch"}
    assert doc["permissions"] == {"contents": "read"}
    assert len(doc["jobs"]) == 1
    job = next(iter(doc["jobs"].values()))
    assert job["runs-on"] == "ubuntu-24.04"
    # `runner` context is unavailable in jobs.<job_id>.env. GitHub must be
    # able to parse this workflow before any job can enforce HOLD=79.
    assert "runner.environment" not in json.dumps(job.get("env", {}))
    assert not any(key.startswith(("GITHUB_", "RUNNER_")) for key in job.get("env", {}))
    steps = job["steps"]
    assert steps[0]["name"] == "Reject untrusted workflow provenance before checkout"
    assert "GITHUB_WORKFLOW_SHA" in steps[0]["run"]
    assert "RUNNER_ENVIRONMENT" in steps[0]["run"]
    assert "exit 79" in steps[0]["run"]
    assert steps[1]["uses"] == "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
    assert steps[1]["with"] == {
        "ref": "${{ github.workflow_sha }}", "persist-credentials": False,
    }
    assert steps[2]["run"].strip().startswith("set -Eeuo pipefail")
    assert "python3 -I -S scripts/rc6_ci_ws12_main_entry.py" in steps[2]["run"]
    assert not any("docker" in json.dumps(step).lower() for step in steps)
    assert not any("actions/upload-artifact" in json.dumps(step).lower() for step in steps)


@pytest.mark.parametrize("protected", ["false", "", "TRUE", None])
def test_workflow_precheckout_rejects_unprotected_ref(protected):
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    step = next(iter(doc["jobs"].values()))["steps"][0]
    good_env = valid_context()
    verified = subprocess.run(["bash", "-e"], input=step["run"], env=good_env,
                              text=True, capture_output=True, check=False)
    assert verified.returncode == 0, verified.stdout
    bad_env = dict(good_env)
    if protected is None:
        bad_env.pop("GITHUB_REF_PROTECTED")
    else:
        bad_env["GITHUB_REF_PROTECTED"] = protected
    denied = subprocess.run(["bash", "-e"], input=step["run"], env=bad_env,
                            text=True, capture_output=True, check=False)
    assert denied.returncode == 79
    assert denied.stdout.strip() == (
        "RC6_WS12_CONTROL=UNTRUSTED_CONTEXT|qa_certified=false|automatic_go=false"
    )
