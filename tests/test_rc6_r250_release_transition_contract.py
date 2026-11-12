"""R250: reproduce the R144/R164 release handoff HOLD without changing gates.

This is OFFHOST/TEST-ONLY evidence. A metadata-only predeploy receipt and an
R206 PROBE receipt are not trusted RELEASE bytes or product runtime authority.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest
import yaml

from scripts import rc6_ci_release_required_guard as release_guard


ROOT = Path(__file__).resolve().parents[1]
PREDEPLOY = ROOT / ".github/workflows/porota-predeploy-v2.yml"
PROMOTE = ROOT / ".github/workflows/porota-deploy-v2-promote.yml"
PRODUCT_REF = "refs/heads/deploy/rc6-pr69-isolated-20260915"
TEST_SHA = "a" * 40


def _workflow(path: Path) -> dict:
    # BaseLoader preserves the literal GitHub Actions `on` key.
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_r250_preupload_probe_cannot_replace_same_run_full_release_receipt():
    """Reproduce the missing U/M→P/N handoff, not an unsafe success."""
    pre = _workflow(PREDEPLOY)
    gate = pre["jobs"]["artifact-gate"]
    required = pre["jobs"]["release-evidence-required"]
    expr = gate["if"]
    assert "github.event.pull_request.number != 1087" in expr
    assert "toJSON(github.event.pull_request.draft) == 'false'" in expr
    assert required["needs"] == "artifact-gate"

    # Even valid-looking public six-member B/P proof cannot satisfy an exact
    # SAME-RUN frozen image/bundle artifact from artifact-gate U/M.
    original = {
        "RC6_RELEASE_REPO": release_guard.REPO,
        "RC6_RELEASE_BASE_REF": release_guard.PRODUCT,
        "RC6_RELEASE_PR_DRAFT": "false",
        "RC6_RELEASE_HEAD_SHA": TEST_SHA,
        "RC6_RELEASE_CHECKOUT_SHA": TEST_SHA,
        "RC6_RELEASE_GATE_RESULT": "skipped",
        "RC6_RELEASE_ARTIFACT_SHA": TEST_SHA,
        "RC6_RELEASE_ARTIFACT_NAME": f"rc6-r163-offhost-991-2-{TEST_SHA}",
        "RC6_RELEASE_ARTIFACT_ID": "881",
        "RC6_RELEASE_ARTIFACT_DIGEST": "b" * 64,
    }
    with pytest.raises(release_guard.RequiredReleaseEvidenceRed):
        release_guard.verify(original)

    # Adding REST attempt/publisher fields to the *old* exact contract also
    # fails closed. A future trusted RELEASE auditor needs a reviewed schema.
    original.update(RC6_RELEASE_GATE_RESULT="success",
                    RC6_RELEASE_ARTIFACT_NAME=f"porota-predeploy-v2-{TEST_SHA}",
                    RC6_TRUSTED_PUBLISHER_RUN_ID="992",
                    RC6_TRUSTED_PUBLISHER_RUN_ATTEMPT="2")
    with pytest.raises(release_guard.RequiredReleaseEvidenceRed):
        release_guard.verify(original)


@pytest.mark.parametrize("event,ref,marker", [
    ("push", PRODUCT_REF, "TRUSTED_RELEASE_AUDIT_UNAVAILABLE"),
    ("pull_request", PRODUCT_REF, "SOURCE_EVENT_OR_REF_MISMATCH"),
    ("push", "refs/heads/main", "SOURCE_EVENT_OR_REF_MISMATCH"),
])
def test_r250_product_transition_is_a_real_executable_hold(event, ref, marker):
    """A healthy SHA and source ref cannot fabricate postmerge byte evidence."""
    product = _workflow(PROMOTE)
    jobs = product["jobs"]
    assert list(jobs) == ["release-evidence-required", "promote"]
    gate = jobs["release-evidence-required"]
    promote = jobs["promote"]
    assert "outputs" not in gate
    assert promote["needs"] == "release-evidence-required"
    assert "needs.release-evidence-required.outputs.release_bytes_verified == 'true'" in promote["if"]
    script = gate["steps"][0]["run"]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": ref,
        "GITHUB_SHA": TEST_SHA,
        "RC6_EXPECTED_PRODUCT_REF": PRODUCT_REF,
    }
    proc = subprocess.run(["bash", "-c", script], env=env,
                          capture_output=True, text=True, check=False, timeout=5)
    assert proc.returncode == 79, (proc.returncode, proc.stdout, proc.stderr)
    assert marker in proc.stdout + proc.stderr
    assert "automatic_go=NO" in proc.stdout + proc.stderr
