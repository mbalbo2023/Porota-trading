"""R162: skipped Predeploy may not masquerade as release evidence.

OFFHOST only. This tests evidence binding, not owner GO or runtime safety.
"""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pytest
import yaml

from scripts import rc6_ci_release_required_guard as guard


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/porota-predeploy-v2.yml"
SHA = "a" * 40
PIN = "f831e2929d1a622f409211010fadaece6b15e953"


def evidence(**changes):
    valid = {
        "RC6_RELEASE_REPO": guard.REPO,
        "RC6_RELEASE_BASE_REF": guard.PRODUCT,
        "RC6_RELEASE_PR_DRAFT": "false",
        "RC6_RELEASE_HEAD_SHA": SHA,
        "RC6_RELEASE_CHECKOUT_SHA": SHA,
        "RC6_RELEASE_GATE_RESULT": "success",
        "RC6_RELEASE_ARTIFACT_SHA": SHA,
        "RC6_RELEASE_ARTIFACT_NAME": f"porota-predeploy-v2-{SHA}",
        "RC6_RELEASE_ARTIFACT_ID": "13272023016",
        "RC6_RELEASE_ARTIFACT_DIGEST": "b" * 64,
    }
    valid.update(changes)
    return valid


def test_exact_ci_evidence_is_non_authoritative():
    receipt = guard.verify(evidence())
    assert receipt["candidate_sha"] == SHA
    assert receipt["artifact_id"] == 13272023016
    assert receipt["artifact_digest"] == "b" * 64
    assert receipt["status"] == "REQUIRED_EXACT_ARTIFACT_CI_EVIDENCE_PRESENT"
    for field in ("artifact_contents_verified_by_this_guard", "owner_go_verified",
                  "runtime_verified", "automatic_go", "auto_merge", "auto_deploy",
                  "auto_trade"):
        assert receipt[field] is False, field


@pytest.mark.parametrize("result", [
    "skipped", "failure", "cancelled", "neutral", "timed_out",
    "action_required", "", "SUCCESS",
])
def test_skipped_failure_cancelled_or_missing_is_red(result):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        guard.verify(evidence(RC6_RELEASE_GATE_RESULT=result))


@pytest.mark.parametrize("draft", ["true", "", "TRUE", "False", "1"])
def test_draft_never_uses_prior_successful_artifact(draft):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        guard.verify(evidence(RC6_RELEASE_PR_DRAFT=draft))


@pytest.mark.parametrize("variant", [
    {"RC6_RELEASE_REPO": "fork/Porota-trading"},
    {"RC6_RELEASE_BASE_REF": "main"},
    {"RC6_RELEASE_HEAD_SHA": "c" * 40},
    {"RC6_RELEASE_HEAD_SHA": "A" * 40},
    {"RC6_RELEASE_HEAD_SHA": "a" * 39},
    {"RC6_RELEASE_CHECKOUT_SHA": "c" * 40},
    {"RC6_RELEASE_ARTIFACT_SHA": "c" * 40},
    {"RC6_RELEASE_ARTIFACT_NAME": "porota-predeploy-v2-" + "c" * 40},
    {"RC6_RELEASE_ARTIFACT_ID": ""},
    {"RC6_RELEASE_ARTIFACT_ID": "0"},
    {"RC6_RELEASE_ARTIFACT_ID": "01"},
    {"RC6_RELEASE_ARTIFACT_ID": "-1"},
    {"RC6_RELEASE_ARTIFACT_ID": "1" * 18},
    {"RC6_RELEASE_ARTIFACT_DIGEST": ""},
    {"RC6_RELEASE_ARTIFACT_DIGEST": "b" * 63},
    {"RC6_RELEASE_ARTIFACT_DIGEST": "z" * 64},
    {"RC6_RELEASE_ARTIFACT_DIGEST": "B" * 64},
])
def test_exact_identity_and_same_run_outputs_are_required(variant):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        guard.verify(evidence(**variant))


@pytest.mark.parametrize("variant", [
    None, [], {**evidence(), "raw_log": "SECRET"},
    {key: value for key, value in evidence().items()
     if key != "RC6_RELEASE_ARTIFACT_DIGEST"},
    evidence(RC6_RELEASE_ARTIFACT_ID=True),
])
def test_untrusted_values_and_fields_do_not_escape_guard(variant):
    with pytest.raises(guard.RequiredReleaseEvidenceRed, match="RC6_REQUIRED_RELEASE_EVIDENCE_RED"):
        guard.verify(variant)


def test_cli_red_is_sanitized_and_no_go():
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, evidence(), clear=True), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert guard.main() == 0
    assert "automatic_go=NO" in stdout.getvalue()
    assert stderr.getvalue() == ""

    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, evidence(RC6_RELEASE_ARTIFACT_ID="SECRET-CANNOT-ECHO"),
                    clear=True), contextlib.redirect_stdout(stdout), \
            contextlib.redirect_stderr(stderr):
        assert guard.main() == 2
    assert "RC6_REQUIRED_RELEASE_EVIDENCE=RED" in stderr.getvalue()
    assert "SECRET-CANNOT-ECHO" not in stderr.getvalue()
    assert stdout.getvalue() == ""


def test_untrusted_candidate_script_cannot_be_release_authority():
    with tempfile.TemporaryDirectory() as directory:
        poisoned = Path(directory) / "pr-head/scripts/rc6_ci_release_required_guard.py"
        poisoned.parent.mkdir(parents=True)
        poisoned.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
        env = {**os.environ, **evidence(RC6_RELEASE_GATE_RESULT="skipped")}
        forged = subprocess.run([sys.executable, "-I", str(poisoned)],
                                env=env, capture_output=True, text=True, check=False)
        assert forged.returncode == 0
        trusted = subprocess.run([sys.executable, "-I", str(Path(guard.__file__).resolve())],
                                 env=env, capture_output=True, text=True, check=False)
        assert trusted.returncode == 2
        assert "automatic_go=NO" in trusted.stderr


def test_required_job_fails_closed_even_when_artifact_gate_is_skipped():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    artifact = workflow["jobs"]["artifact-gate"]
    assert artifact["outputs"] == {
        "candidate_sha": "${{ steps.rc6_artifact_identity.outputs.candidate_sha }}",
        "artifact_name": "${{ steps.rc6_artifact_identity.outputs.artifact_name }}",
        "artifact_id": "${{ steps.rc6_predeploy_upload.outputs.artifact-id }}",
        "artifact_digest": "${{ steps.rc6_predeploy_upload.outputs.artifact-digest }}",
    }
    steps = artifact["steps"]
    native = next(s for s in steps if s["name"] == "Exact image native sizing and generated catalog proof")
    cleanup = next(s for s in steps if s["name"] == "Local cleanup")
    # This is a semantic gate: no unconditional trailing exit is needed as
    # long as a created image or stage remains HOLD79 and the release guard
    # rejects skipped/failed artifact evidence. Never mistake the genuine
    # both-skipped NO_RESOURCE branch for an artifact certificate.
    native_run = native["run"]
    assert native_run.index("exit 79") < native_run.index("# RC6_A980_NATIVE_OWNER_BEGIN")
    assert "root_same_uid_path_swap_unresolved" in native_run
    cleanup_run = cleanup["run"]
    assert cleanup["if"] == "always()"
    assert cleanup["env"]["RC6_BUILD_OUTCOME"] == "${{ steps.rc6_build_once.outcome }}"
    assert cleanup["env"]["RC6_EXTRACT_OUTCOME"] == "${{ steps.rc6_extract_application.outcome }}"
    assert "NO_RESOURCE|build_not_started" in cleanup_run
    assert "NO_RESOURCE|extract_not_started" in cleanup_run
    assert "RETAINED|HOLD79|tag_namespace_mutable_no_delete" in cleanup_run
    assert "RETAINED|HOLD79|pathname_swap_unresolved_no_delete" in cleanup_run
    assert 'if [[ "$rc6_image_state" != GREEN || "$rc6_stage_state" != GREEN ]]; then' in cleanup_run
    assert "POROTA_PREDEPLOY_LOCAL_CLEANUP=HOLD79|ownership_not_proven" in cleanup_run
    assert "exit 79" in cleanup_run
    assert 'docker image rm' not in cleanup_run and 'rm -rf -- "$RC6_EXTRACT_STAGE"' not in cleanup_run
    required_evidence = next(i for i, step in enumerate(steps)
                             if step["name"] == "Verify required published native evidence")
    identity = next(i for i, step in enumerate(steps)
                    if step.get("id") == "rc6_artifact_identity")
    upload = next(i for i, step in enumerate(steps)
                  if step.get("id") == "rc6_predeploy_upload")
    assert required_evidence < identity < upload
    assert "git rev-parse HEAD" in steps[identity]["run"]
    assert "GITHUB_OUTPUT" in steps[identity]["run"]
    assert steps[upload]["if"] == "${{ always() && steps.rc6_artifact_gate.outcome == 'success' }}"
    assert steps[upload]["uses"] == "actions/upload-artifact@v4"

    required = workflow["jobs"]["release-evidence-required"]
    assert required["needs"] == "artifact-gate"
    assert required["if"] == (
        "${{ always() && github.event.pull_request.base.ref == "
        "'deploy/rc6-pr69-isolated-20260915' }}"
    )
    # R254 consumes exact R251 D/E evidence via the reviewed R252 main guard.
    # Only read-only REST privileges are necessary; never grant write scope.
    assert required["permissions"] == {
        "actions": "read", "contents": "read", "pull-requests": "read",
    }
    assert "continue-on-error" not in required
    assert required["timeout-minutes"] == "30"
    head, trusted, check = required["steps"]
    assert head["with"]["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert head["with"]["path"] == ".rc6-release-head-inspect"
    assert head["with"]["persist-credentials"] == "false"
    assert trusted["with"]["ref"] == PIN
    assert trusted["with"]["path"] == ".rc6-release-guard-trusted"
    assert trusted["with"]["persist-credentials"] == "false"
    assert "git -C .rc6-release-guard-trusted rev-parse HEAD" in check["run"]
    assert "python3 -I .rc6-release-guard-trusted/scripts/rc6_ci_release_required_guard.py" in check["run"]
    assert "--trusted-release-mode PR_CANDIDATE" in check["run"]
    assert "python -m scripts.rc6_ci_release_required_guard" not in check["run"]
    assert check["env"]["RC6_BRIDGE_REPOSITORY"] == "${{ github.repository }}"
    assert check["env"]["RC6_BRIDGE_MODE"] == "PR_CANDIDATE"
    assert check["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert check["env"]["RC6_RELEASE_PR_DRAFT"] == "${{ github.event.pull_request.draft }}"
    for selector in ("RC6_BRIDGE_E_RUN_ID", "RC6_BRIDGE_E_RUN_ATTEMPT",
                     "RC6_BRIDGE_E_ARTIFACT_ID", "RC6_BRIDGE_PUBLISHER_MAIN_SHA"):
        assert check["env"][selector] == "", selector
    assert "RC6_RELEASE_GATE_RESULT" not in check["env"]
    assert "RC6_RELEASE_ARTIFACT_ID" not in check["env"]
    assert "RC6_RELEASE_ARTIFACT_DIGEST" not in check["env"]
