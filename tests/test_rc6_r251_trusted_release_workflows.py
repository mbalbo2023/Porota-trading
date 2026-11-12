"""R251 offhost trusted-release producer and completed-run auditor contract.

These tests never invoke GitHub Actions, send broker orders or build Docker.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / ".github/workflows/rc6-r251-trusted-release-build.yml"
POST = ROOT / ".github/workflows/rc6-r251-trusted-release-postcompletion.yml"


def _workflow(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def test_manual_build_is_isolated_and_never_transfers_to_droplet():
    workflow = _workflow(BUILD)
    assert list(workflow["on"]) == ["workflow_dispatch"]
    assert "workflow_run" not in workflow["on"]
    assert workflow["permissions"] == {"contents": "read", "actions": "read", "issues": "read", "pull-requests": "read"}
    assert "rc6-unified-paper-deploy" not in workflow["concurrency"]["group"]
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    full = BUILD.read_text()
    assert "rc6_r163_artifact_byte_verifier.py" in full
    assert "porota_build_deploy_bundle_v2.py" in full
    assert "7ac6e368bd42137817e3271839c35f579bdbbbbe" in full
    assert "0fe454dcdddc445d719288ed0673f3bfa279526f" in full
    assert "ea165f8d65b6e75b540449e92b4886f43607fa02" in full
    assert "persist-credentials: false" in full
    assert "--owner-comment-id" in full
    assert "--candidate-tree-sha" in full
    assert "--r206-publisher-run-id" in full
    assert "R144=HOLD" in full
    assert "release_go=NO" in full
    for forbidden in ("ssh ", "scp ", "sudo ", "docker push", "docker system prune", "rsync ", "rm -rf"):
        assert forbidden not in full


def test_postcompletion_audits_only_exact_build_workflow_and_never_self_attests():
    workflow = _workflow(POST)
    assert list(workflow["on"]) == ["workflow_run"]
    assert workflow["on"]["workflow_run"]["types"] == ["completed"]
    assert workflow["on"]["workflow_run"]["workflows"] == [
        "RC6 R251 Trusted Release Build (OFFHOST ONLY)",
    ]
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    full = POST.read_text()
    assert "github.event.workflow_run.event == 'workflow_dispatch'" in full
    assert "github.event.workflow_run.conclusion == 'success'" in full
    assert "--publisher-run-id" in full
    assert "--publisher-run-attempt" in full
    assert "--expected-publisher-sha" in full
    assert "release_go=NO" in full
    assert "R144=HOLD" in full
    assert "persist-credentials: false" in full
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in full
    for forbidden in ("ssh ", "scp ", "sudo ", "docker build", "docker save", "docker system prune"):
        assert forbidden not in full


def test_no_plain_candidate_script_is_executed_outside_docker():
    full = BUILD.read_text()
    assert "python3 .rc6-untrusted-source/" not in full
    assert "bash .rc6-untrusted-source/" not in full
    assert "python3 -I scripts/rc6_r163_artifact_byte_verifier.py" in full
    assert "ref: ${{ github.sha }}" in full


@pytest.mark.parametrize("path", [BUILD, POST])
def test_every_shell_step_is_bash_syntax_valid_without_running_the_workflow(path):
    workflow = _workflow(path)
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            if "run" not in step:
                continue
            result = subprocess.run(["bash", "-n"], text=True,
                                    input=step["run"], capture_output=True,
                                    check=False)
            assert result.returncode == 0, (path.name, step["name"], result.stderr)


def test_r251_full_release_legacy_host_payload_compatibility_and_separate_e_receipt():
    full = BUILD.read_text()
    assert "porota-host-manifest-v2.json" in full
    assert "porota-host-policy-v2.json" in full
    assert "porota-frozen-candidate.json" in full
    assert "porota-predeploy-image.tar.gz" in full
    assert "porota-image-build-receipt.json" in full
    assert "porota-deploy-bundle-v2-manifest.json" in full
    assert "porota-deploy-bundle-v2.tgz" in full
    assert "--staging-root" in full
    assert "compression-level: 0" in full
    post = POST.read_text()
    assert "rc6-r251-release-postcompletion-v1.json" in post
    assert "RC6_CANDIDATE_SHA" in post
    assert "--receipt-out" in post


def test_one_docker_build_pins_embedded_identity_and_validates_saved_frozen_tar():
    full = BUILD.read_text()
    assert full.count("docker build ") == 1
    assert "--build-arg \"POROTA_BUILD_SHA=$RC6_SOURCE_SHA\"" in full
    assert "--build-arg \"POROTA_CANDIDATE_TREE_SHA=$RC6_SOURCE_TREE\"" in full
    assert "verify_source_identity(sys.argv[1], sys.argv[2])" in full
    assert "POROTA_RUNTIME_IMPORT_SMOKE=GREEN" in full
    assert "3a745c5746083b1d0fab8cabe4c4d772e09682ea" in full
    assert ".rc6-pinned-product/tests/ci_frozen_candidate_contract.py validate" in full
    assert "--frozen \"$RC6_RELEASE_STAGE/porota-frozen-candidate.json\"" in full
    assert "--image \"$RC6_RELEASE_STAGE/porota-predeploy-image.tar.gz\"" in full
    assert "--manifest \"$RC6_RELEASE_STAGE/porota-deploy-bundle-v2-manifest.json\"" in full


def test_same_release_image_runs_required_native_and_paper_stages_before_save_and_seal():
    """B's separate PROBE suites cannot stand in for D's exact RELEASE image."""
    workflow = _workflow(BUILD)
    steps = workflow["jobs"]["trusted-release"]["steps"]
    names = [step["name"] for step in steps]
    build_idx = names.index("Build one ephemeral offhost RELEASE image")
    native_idx = names.index("Execute native PAPER suite on exact RELEASE image ID")
    export_idx = names.index("Save verified RELEASE image ID and bind native receipt")
    seal_idx = names.index("Seal seven-member owned artifact manifest")
    assert build_idx < native_idx < export_idx < seal_idx
    build, native, export = (steps[index]["run"]
                             for index in (build_idx, native_idx, export_idx))
    assert "docker build " in build
    assert "RC6_IMAGE_ID=" in build
    assert 'docker create ' in native
    assert '"$RC6_IMAGE_ID" sleep ' in native
    assert '"$RC6_IMAGE_ID"' in build
    assert 'docker build ' not in native + export
    for required in (
        "--network none", "--read-only", "--cap-drop ALL",
        "--security-opt no-new-privileges", "--user 1000:1000",
        "--mount type=volume,target=/app/data", '--volumes-from "$source_cid:ro"',
        "tests/test_rc6_shadow_native_bound.py", "tests/test_rc6_shadow_streaming_cost.py",
        "tests/test_rc6_shadow_shared_audit.py", "tests/test_rc6_shadow_owned_state.py",
        "tests/test_rc6_shadow_report_lifetime.py", "tests/test_rc6_shadow_cold_audit.py",
        "tests/test_rc6_owned_funnel_details.py", "tests/test_rc6_physical_cadence.py",
        "rc6_postdeploy_stress.py --full-catalog-check",
        "rc6_postdeploy_stress.py --output /app/data/session",
        "rc6_postdeploy_stress.py --cadence-stage fresh",
        "rc6_postdeploy_stress.py --cadence-stage legacy",
        "rc6_r163_native_volume_proof.py pack", "--native-tar",
        '--private-junit-root "$private_junit_root"',
        'docker cp "$source_cid:/app/data/$relative"',
        "--source-cid", "--collector-cid", "--image-id",
        "native-proof", "docker rm -v", "owned_cid",
    ):
        assert required in native
    assert 'docker save "$RC6_IMAGE"' in export
    assert 'test "$(docker image inspect -f' in export
    assert '"native_proof":' in export
    assert 'RC6_NATIVE_PROOF_FILE' in export
    assert '"porota-image-build-receipt.json"' in export
    assert '"$RC6_RELEASE_STAGE"' not in native  # no private report in upload folder
    assert native.index('docker cp "$source_cid:/app/data/$relative"') < native.index(
        'docker stop --time 10 "$source_cid"')  # mounted volume still live
    upload = next(step for step in steps if step["name"] == "Upload one trusted RELEASE artifact")
    assert upload["with"]["path"] == "${{ env.RC6_RELEASE_STAGE }}"
