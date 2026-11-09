"""A1031: offhost proof that legacy rootful V2 cannot cross a host boundary.

These fixtures execute only source validation and the local exit-79 gate.
They never invoke Docker, sudo, SSH, or the real deployment workflow.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import subprocess

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/porota-deploy-v2-promote.yml"


def guard():
    # RED-first when product lacks the source boundary module. Never skip it.
    assert importlib.util.find_spec("scripts.rc6_deploy_legacy_host_guard") is not None, (
        "A1031 legacy host guard is missing from the product-derived branch"
    )
    return importlib.import_module("scripts.rc6_deploy_legacy_host_guard")


def product():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def synthetic_held_legacy():
    """Inject only the previously reviewed *negative* A806 source boundary.

    This is NOT a proposed production workflow or a route to a successful deploy.
    The unmodified product workflow remains the input to other tests.
    """
    source = product()
    steps = source["jobs"]["promote"]["steps"]
    steps.insert(1, {
        "name": "Stop before host writes when exact staging cleanup is unverified",
        "id": "a806_staging_preadmission", "shell": "bash",
        "run": (
            "set -Eeuo pipefail\n"
            "echo 'STAGING_CLEANUP=DEFERRED_UNVERIFIED|no_independent_root_attestation|no_host_contact'\n"
            "echo '::error::RC6_A806_PREADMISSION_STAGING_UNVERIFIED: no root-issued stage ownership proof; forbidden to enter host promotion'\n"
            "echo 'RC6_DEPLOY_V2=NO_GO_PREMUTATION_STAGING_UNVERIFIED|remote_mutations=0'\n"
            "exit 79\n"
        ),
    })
    steps.insert(-2, {
        "name": "Mark deferred post-promotion cleanup as failed after validation",
        "id": "cleanup_deferred_gate", "shell": "bash",
        "if": "${{ always() && steps.promotion.outcome == 'success' && (steps.promotion.outputs.cleanup_status != 'GREEN' || steps.promotion.outputs.cleanup_parser_status != 'VERIFIED') }}",
        "run": "echo 'STAGING_CLEANUP=DEFERRED_UNVERIFIED'",
    })
    steps[-1] = {
        "name": "Runner cleanup", "shell": "bash", "if": "always()",
        "run": (
            "set -Eeuo pipefail\n"
            "# A806 holds before artifacts/SSH credentials are staged. Never\n"
            "# remove historical fixed /tmp names (or HOME paths) without an\n"
            "# independent, exact runner-artifact ownership proof. Those names\n"
            "# may belong to another attempt or tenant on a shared host.\n"
            "echo 'POROTA_DEPLOY_V2_RUNNER_CLEANUP=DEFERRED_UNVERIFIED|"
            "no_independent_runner_artifact_ownership|delete_attempts=0'\n"
        ),
    }
    return source


def test_existing_product_rootful_workflow_is_rejected_before_host():
    audit = guard()
    with pytest.raises(audit.LegacyHostBoundaryViolation, match="A806_GATE"):
        audit.classify_promotion_host_boundary(product())


def test_held_legacy_source_returns_only_blocked_receipt_and_gate_exits_79():
    audit = guard()
    source = synthetic_held_legacy()
    receipt = audit.classify_promotion_host_boundary(source)
    assert receipt["status"] == "BLOQUEADO"
    assert receipt["prehost_exit"] == 79
    # The legacy host still has at least one privileged Docker reference;
    # future reductions in that count must not invalidate a valid HOLD.
    assert receipt["rootful_docker_references"] > 0
    assert receipt["rootless_certified"] is False
    assert receipt["host_contact_authorized"] is False
    local = subprocess.run(
        ["bash", "-Eeuo", "pipefail", "-c", source["jobs"]["promote"]["steps"][1]["run"]],
        capture_output=True, text=True, timeout=5, check=False,
    )
    assert local.returncode == 79
    assert "remote_mutations=0" in local.stdout


@pytest.mark.parametrize("mutation,reason", [
    ("default_socket", "UNSAFE_PROMOTION_JOB"),
    ("new_unprotected_job", "UNREVIEWED_PROMOTION_JOB"),
    ("shell_startup", "UNTRUSTED_SHELL_ENV_BEFORE_A806"),
    ("forged_gate", "A806_GATE_NOT_EXECUTABLE_UNCONDITIONAL"),
    ("destructive_runner_cleanup", "UNREVIEWED_ALWAYS_TERMINAL"),
    ("rootful_string_removed", "NO_INDEPENDENT_ROOTLESS_ATTESTATION"),
])
def test_unsafe_default_fallback_or_unproven_cleanup_can_never_enable_host(mutation, reason):
    audit = guard()
    source = synthetic_held_legacy()
    job = source["jobs"]["promote"]
    if mutation == "default_socket":
        job["env"]["DOCKER_HOST"] = "unix:///var/run/docker.sock"
    elif mutation == "new_unprotected_job":
        source["jobs"]["bypass"] = {"runs-on": "ubuntu-24.04", "steps": [{"run": "sudo -n docker ps"}]}
    elif mutation == "shell_startup":
        job["env"]["BASH_ENV"] = "/tmp/unsafe-startup.sh"
    elif mutation == "forged_gate":
        job["steps"][1]["run"] = "echo GREEN\nexit 0\n"
    elif mutation == "destructive_runner_cleanup":
        job["steps"][-1]["run"] += "rm -rf /tmp/porota-deploy-v2-foreign\n"
    elif mutation == "rootful_string_removed":
        for step in job["steps"]:
            if "run" in step:
                step["run"] = step["run"].replace("sudo -n docker", "docker")
    with pytest.raises(audit.LegacyHostBoundaryViolation, match=reason):
        audit.classify_promotion_host_boundary(source)


def test_gate_remove_or_fail_suppression_is_rejected():
    audit = guard()
    source = synthetic_held_legacy()
    source["jobs"]["promote"]["steps"].pop(1)
    with pytest.raises(audit.LegacyHostBoundaryViolation, match="A806_GATE"):
        audit.classify_promotion_host_boundary(source)
    source = synthetic_held_legacy()
    source["jobs"]["promote"]["continue-on-error"] = True
    with pytest.raises(audit.LegacyHostBoundaryViolation, match="JOB_FAILURE_SUPPRESSION"):
        audit.classify_promotion_host_boundary(source)
