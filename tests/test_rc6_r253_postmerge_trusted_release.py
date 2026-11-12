"""R253 OFFHOST regression: product promotion consumes only an R252 receipt.

The R252 verifier and R251 release producer are independently owned. These
tests exercise the candidate promotion adapter with synthetic evidence only;
they neither fetch GitHub artifacts nor authorize any host operation.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/porota-deploy-v2-promote.yml"
PRODUCT_REF = "refs/heads/deploy/rc6-pr69-isolated-20260915"
PRODUCT_SHA = "a" * 40
CANDIDATE_SHA = "b" * 40
TREE_SHA = "c" * 40
PUBLISHER_SHA = "d" * 40
ARTIFACT_DIGEST = "sha256:" + "e" * 64
TRUSTED_MAIN_SHA = "f831e2929d1a622f409211010fadaece6b15e953"


def _jobs():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return workflow, workflow["jobs"]["release-evidence-required"], workflow["jobs"]["promote"]


def _run(script: str, *, env: dict[str, str], cwd: Path | None = None):
    return subprocess.run(
        ["bash", "-c", script], env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
        cwd=cwd or ROOT, capture_output=True, text=True, timeout=10, check=False,
    )


def test_r253_release_adapter_only_trusted_immutable_source_and_no_host_go():
    workflow, gate, promote = _jobs()
    assert list(workflow["on"]) == ["push"]
    assert workflow["concurrency"] == {
        "group": "rc6-unified-paper-deploy", "cancel-in-progress": "false",
    }
    assert list(workflow["jobs"]) == ["release-evidence-required", "promote"]
    assert gate["permissions"] == {
        "contents": "read", "actions": "read", "pull-requests": "read",
    }
    assert re.fullmatch("[0-9a-f]{40}", gate["env"]["RC6_R252_TRUSTED_MAIN_SHA"])
    assert gate["env"]["RC6_R252_TRUSTED_MAIN_SHA"] == TRUSTED_MAIN_SHA
    assert [step["name"] for step in gate["steps"]] == [
        "Reject unpinned postmerge verifier or wrong product event",
        "Checkout immutable R252 trusted-main verifier",
        "Authenticate exact R251 D/E receipts through R252",
        "Hold host control pending independent physical authority",
    ]
    checkout = gate["steps"][1]
    assert checkout["uses"] == "actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683"
    assert checkout["with"] == {
        "repository": "mbalbo2023/Porota-trading",
        "ref": "${{ env.RC6_R252_TRUSTED_MAIN_SHA }}",
        "path": ".rc6-release-guard-trusted",
        "persist-credentials": "false",
    }
    audit = gate["steps"][2]
    assert audit["id"] == "trusted_release"
    assert audit["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert audit["env"]["RC6_BRIDGE_MODE"] == "PRODUCT_POSTMERGE"
    assert audit["env"]["RC6_BRIDGE_PRODUCT_SHA"] == "${{ github.sha }}"
    assert audit["env"]["RC6_BRIDGE_PRODUCT_REF"] == "${{ github.ref }}"
    # R259 discovers exactly one E via the authenticated Actions REST listing,
    # and R252 derives the candidate/tree from the two-parent product merge.
    for optional in ("RC6_BRIDGE_CANDIDATE_SHA", "RC6_BRIDGE_CANDIDATE_TREE_SHA",
                     "RC6_BRIDGE_PUBLISHER_MAIN_SHA", "RC6_BRIDGE_E_RUN_ID",
                     "RC6_BRIDGE_E_RUN_ATTEMPT", "RC6_BRIDGE_E_ARTIFACT_ID"):
        assert audit["env"][optional] == "", optional
    assert "RECEIPT_SELECTOR_MISSING" not in audit["run"]
    assert "--trusted-release-mode PRODUCT_POSTMERGE" in audit["run"]
    assert ".rc6-release-guard-trusted/scripts/rc6_ci_release_required_guard.py" in audit["run"]
    assert "RC6_BRIDGE_CANDIDATE_SHA" in audit["env"]
    assert "RC6_BRIDGE_CANDIDATE_TREE_SHA" in audit["env"]
    assert "RC6_BRIDGE_PUBLISHER_MAIN_SHA" in audit["env"]
    assert "HOST_CONTROL_HOLD79" in gate["steps"][3]["run"]
    assert gate["steps"][3]["run"].rstrip().endswith("exit 79")
    assert "host_control_verified" not in gate.get("outputs", {})
    assert "needs.release-evidence-required.outputs.host_control_verified == 'true'" in promote["if"]
    assert promote["needs"] == "release-evidence-required"


def test_r253_all_empty_selectors_are_required_no_repo_variables_or_host_bypass():
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "${{ vars.RC6_R251_" not in source
    assert "RC6_BRIDGE_E_RUN_ID" in source
    assert "TRUSTED_RELEASE_BYTES_BOUND" in source
    # Main's independent R252 guard authenticates E/D and the product merge.
    # The product workflow itself must still force physical host HOLD79.
    _, gate, promote = _jobs()
    assert gate["steps"][3]["run"].rstrip().endswith("exit 79")
    assert "host_control_verified" not in gate["outputs"]
    assert "needs.release-evidence-required.outputs.host_control_verified == 'true'" in promote["if"]


def test_r253_r252_large_release_zip_audit_has_bounded_runner_time():
    """A GitHub REST D artifact up to 2 GiB must not hit a 5-minute cutoff.

    A 30-minute bound covers the trusted main R252 ZIP fetch+verification
    without making the source guard a non-terminating worker.
    """
    _, gate, _ = _jobs()
    assert 20 <= int(gate["timeout-minutes"]) <= 45
    assert gate["timeout-minutes"] == "30"


@pytest.mark.parametrize("event,ref,sha,reason", [
    ("push", PRODUCT_REF, PRODUCT_SHA, "TRUSTED_MAIN_SOURCE_UNPINNED"),
    ("pull_request", PRODUCT_REF, PRODUCT_SHA, "SOURCE_EVENT_OR_REF_MISMATCH"),
    ("push", "refs/heads/main", PRODUCT_SHA, "SOURCE_EVENT_OR_REF_MISMATCH"),
    ("push", PRODUCT_REF, "not-a-sha", "PRODUCT_SHA_INVALID"),
])
def test_r253_actual_bash_precheckout_guard79(event, ref, sha, reason):
    _, gate, _ = _jobs()
    result = _run(gate["steps"][0]["run"], env={
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": ref,
        "GITHUB_SHA": sha,
        "RC6_EXPECTED_PRODUCT_REF": PRODUCT_REF,
        "RC6_R252_TRUSTED_MAIN_SHA": "0" * 40,
    })
    assert result.returncode == 79, result
    assert reason in result.stdout + result.stderr
    assert "automatic_go=NO" in result.stdout + result.stderr


def test_r253_valid_main_pin_reaches_receipt_checkout_only_without_host_contact():
    _, gate, _ = _jobs()
    result = _run(gate["steps"][0]["run"], env={
        "GITHUB_EVENT_NAME": "push", "GITHUB_REF": PRODUCT_REF,
        "GITHUB_SHA": PRODUCT_SHA,
        "RC6_EXPECTED_PRODUCT_REF": PRODUCT_REF,
        "RC6_R252_TRUSTED_MAIN_SHA": TRUSTED_MAIN_SHA,
    })
    assert result.returncode == 0, result
    assert "SOURCE_PIN=GREEN" in result.stdout
    assert "host_go=NO" in result.stdout


def _valid_r252_receipt():
    return {
        "status": "TRUSTED_RELEASE_BYTES_BOUND",
        "mode": "PRODUCT_POSTMERGE",
        "candidate_sha": CANDIDATE_SHA,
        "candidate_tree_sha": TREE_SHA,
        "product_sha": PRODUCT_SHA,
        "release_artifact_id": 111,
        "release_artifact_digest": ARTIFACT_DIGEST,
        "release_run_id": 222,
        "release_run_attempt": 2,
        "trusted_main_audit_run_id": 333,
        "trusted_main_audit_run_attempt": 1,
        "artifact_contents_verified_by_this_guard": True,
        "owner_go_verified": False,
        "runtime_verified": False,
        "automatic_go": False,
    }


@pytest.mark.parametrize("field,value", [
    (None, None),
    ("release_artifact_id", "111"),
    ("release_artifact_digest", "0" * 64),
    ("release_run_attempt", 0),
    ("trusted_main_audit_run_id", 0),
    ("candidate_tree_sha", "f" * 39),
    ("product_sha", "f" * 40),
    ("artifact_contents_verified_by_this_guard", False),
    ("automatic_go", True),
    ("owner_go_verified", True),
    ("runtime_verified", True),
])
def test_r253_integration_parser_fails_closed_on_wrong_r252_receipt(field, value):
    _, gate, _ = _jobs()
    receipt = _valid_r252_receipt()
    if field is None:
        receipt["extra_authority"] = "GO"
    else:
        receipt[field] = value
    result = _execute_mock_r252(gate["steps"][2]["run"], receipt)
    assert result.returncode == 79, result


def test_r253_adapter_accepts_typed_r252_receipt_without_host_authority():
    _, gate, _ = _jobs()
    result = _execute_mock_r252(gate["steps"][2]["run"], _valid_r252_receipt())
    assert result.returncode == 0, result
    assert "release_bytes_verified=true" in result.output
    assert f"release_product_sha={PRODUCT_SHA}" in result.output
    assert "release_artifact_id=111" in result.output
    assert "release_artifact_digest=sha256:" in result.output
    assert "host_control_verified" not in result.output
    hold = _run(gate["steps"][3]["run"], env={})
    assert hold.returncode == 79
    assert "HOST_CONTROL_HOLD79" in hold.stdout + hold.stderr


def _execute_mock_r252(script, receipt):
    """Only fake the not-yet-merged trusted script: no real REST or host calls."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        source = path / ".rc6-release-guard-trusted/scripts"
        source.mkdir(parents=True)
        checker = source / "rc6_ci_release_required_guard.py"
        checker.write_text(
            "import json\nprint(json.dumps(" + repr(receipt) + "))\n",
            encoding="utf-8",
        )
        gitroot = path / ".rc6-release-guard-trusted"
        subprocess.run(["git", "init", "-q", str(gitroot)], check=True, timeout=5)
        subprocess.run(["git", "-C", str(gitroot), "add", "scripts"], check=True, timeout=5)
        subprocess.run([
            "git", "-C", str(gitroot), "-c", "user.name=R253-test",
            "-c", "user.email=r253@example.invalid", "commit", "-qm", "test-only",
        ], check=True, timeout=5)
        pinned = subprocess.run(
            ["git", "-C", str(gitroot), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        output = path / "outputs.txt"
        output.touch()
        runner = path / "runner-temp"
        runner.mkdir()
        env = {
            "GITHUB_REPOSITORY": "mbalbo2023/Porota-trading",
            "GITHUB_SHA": PRODUCT_SHA,
            "GITHUB_REF": PRODUCT_REF,
            "GITHUB_OUTPUT": str(output),
            "RUNNER_TEMP": str(runner),
            "RC6_R252_TRUSTED_MAIN_SHA": pinned,
            "GITHUB_TOKEN": "synthetic-offhost-only",
            "RC6_BRIDGE_MODE": "PRODUCT_POSTMERGE",
            "RC6_BRIDGE_REPOSITORY": "mbalbo2023/Porota-trading",
            "RC6_BRIDGE_PRODUCT_SHA": PRODUCT_SHA,
            "RC6_BRIDGE_PRODUCT_REF": PRODUCT_REF,
            "RC6_BRIDGE_CANDIDATE_SHA": "",
            "RC6_BRIDGE_CANDIDATE_TREE_SHA": "",
            "RC6_BRIDGE_PUBLISHER_MAIN_SHA": "",
            "RC6_BRIDGE_E_RUN_ID": "",
            "RC6_BRIDGE_E_RUN_ATTEMPT": "",
            "RC6_BRIDGE_E_ARTIFACT_ID": "",
        }
        result = _run(script, cwd=path, env=env)
        result.output = output.read_text(encoding="utf-8")
        return result


def test_r253_deployment_fetch_is_exact_and_never_chooses_latest_by_date():
    _, _, promote = _jobs()
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "max(candidates,key=lambda x:x[0])" not in source
    assert "created_at" not in source
    assert "/actions/artifacts?name=" not in source
    assert "${{ vars.RC6_R251_" not in source
    assert "release_artifact_id" in promote["if"]
    assert "release_artifact_digest" in promote["if"]
    assert "release_run_attempt" in promote["if"]
    assert "PREDEPLOY_ARTIFACT_ID" in source  # legacy consumer of exact new D ID
    assert "PREDEPLOY_RUN_ID" in source
    assert "sha256sum" in source  # raw ZIP digest verified before extraction


@pytest.mark.parametrize("mutated", [
    None,
    "RC6_RELEASE_CANDIDATE_SHA", "RC6_RELEASE_CANDIDATE_TREE",
    "RC6_RELEASE_ARTIFACT_ID", "RC6_RELEASE_ARTIFACT_DIGEST",
    "RC6_RELEASE_RUN_ID", "RC6_RELEASE_RUN_ATTEMPT",
    "RC6_RELEASE_AUDIT_RUN_ID", "RC6_RELEASE_AUDIT_RUN_ATTEMPT",
])
def test_r253_exact_d_artifact_selector_never_falls_back_to_latest(mutated):
    _, _, promote = _jobs()
    selector = next(step for step in promote["steps"]
                    if step["name"] == "Use only exact R252-verified R251 D release artifact identity")
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "github-env"
        output.touch()
        values = {
            "GITHUB_ENV": str(output),
            "CANDIDATE_SHA": CANDIDATE_SHA,
            "CANDIDATE_TREE": TREE_SHA,
            "RC6_RELEASE_CANDIDATE_SHA": CANDIDATE_SHA,
            "RC6_RELEASE_CANDIDATE_TREE": TREE_SHA,
            "RC6_RELEASE_ARTIFACT_ID": "111",
            "RC6_RELEASE_ARTIFACT_DIGEST": ARTIFACT_DIGEST,
            "RC6_RELEASE_RUN_ID": "222",
            "RC6_RELEASE_RUN_ATTEMPT": "2",
            "RC6_RELEASE_AUDIT_RUN_ID": "333",
            "RC6_RELEASE_AUDIT_RUN_ATTEMPT": "1",
        }
        if mutated is not None:
            values[mutated] = "untrusted"
        result = _run(selector["run"], env=values)
        if mutated is not None:
            assert result.returncode == 79, result
            assert "MISSING_TRUSTED_RECEIPT" in result.stdout + result.stderr
            assert output.read_text() == ""
        else:
            assert result.returncode == 0, result
            assert output.read_text() == (
                "PREDEPLOY_ARTIFACT_ID=111\nPREDEPLOY_RUN_ID=222\n"
                "RC6_RELEASE_ARTIFACT_SHA256=" + "e" * 64 + "\n"
            )
