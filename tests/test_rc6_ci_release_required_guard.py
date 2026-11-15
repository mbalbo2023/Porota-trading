"""R162: skipped Predeploy may not masquerade as release evidence.

OFFHOST only. This tests evidence binding, not owner GO or runtime safety.
"""

import contextlib
import io
import os
from pathlib import Path
import re
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


def test_skipped_release_evidence_remains_rejected_without_workflow_text():
    # The old B/C/D/E workflow topology is retired. Keep the actual evidence
    # validator assertion; protocol source text no longer attests a release.
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        guard.verify(evidence(RC6_RELEASE_GATE_RESULT="skipped"))
