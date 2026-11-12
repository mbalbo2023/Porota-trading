"""OFFHOST tests for a proposed, not-yet-trusted artifact byte gate."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import warnings
import zipfile

import pytest

from scripts import rc6_r163_artifact_byte_verifier as byte_gate
from scripts.rc6_r163_artifact_byte_verifier import (
    ArtifactByteEvidenceRed,
    verify_artifact_bytes,
)


SHA = "a" * 40
TREE = "b" * 40
RUN_ID = 991
RUN_ATTEMPT = 1
ARTIFACT_ID = 881
NAME = f"porota-predeploy-v2-{SHA}"
OFFHOST_PATH = ".github/workflows/rc6-r163-offhost-guard79-proof.yml"
OFFHOST_NAME = f"rc6-r163-offhost-{RUN_ID}-{RUN_ATTEMPT}-{SHA}"
MANIFEST_NAME = "porota-artifact-byte-manifest-v1.json"
PAYLOAD = {
    "porota-deploy-bundle-v2.tgz": b"bundle bytes for exact candidate",
    "porota-frozen-candidate.json": b'{"candidate_sha":"' + SHA.encode() + b'"}',
}

RELEASE_PATH = ".github/workflows/rc6-r251-trusted-release-build.yml"
PUBLISHER_MAIN_SHA = "c" * 40
RELEASE_NAME = f"rc6-r251-release-{RUN_ID}-{RUN_ATTEMPT}-{SHA}"


def _evidence(tmp_path: Path) -> dict:
    archive = tmp_path / "downloaded-artifact.zip"
    rows = [
        {
            "path": name,
            "type": "file",
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        for name, data in sorted(PAYLOAD.items())
    ]
    manifest = {
        "schema_version": 1,
        "candidate_sha": SHA,
        "candidate_tree_sha": TREE,
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "artifact_name": NAME,
        "file_count": len(rows),
        "total_size_bytes": sum(len(data) for data in PAYLOAD.values()),
        "files": rows,
    }
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, json.dumps(manifest, sort_keys=True))
        for name, data in PAYLOAD.items():
            zf.writestr(name, data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    artifact = {
        "id": ARTIFACT_ID,
        "name": NAME,
        "size_in_bytes": archive.stat().st_size,
        "digest": f"sha256:{digest}",
        "expired": False,
        "workflow_run": {"id": RUN_ID, "head_sha": SHA},
    }
    run = {
        "id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "head_sha": SHA,
        "status": "completed",
        "conclusion": "success",
        "event": "pull_request",
        "path": ".github/workflows/porota-predeploy-v2.yml",
        "repository": {"full_name": "mbalbo2023/Porota-trading"},
    }
    return {
        "archive": archive,
        "artifact_receipt": artifact,
        "run_receipt": run,
        "expected_candidate_sha": SHA,
        "expected_tree_sha": TREE,
        "expected_run_id": RUN_ID,
        "expected_run_attempt": RUN_ATTEMPT,
        "expected_artifact_id": ARTIFACT_ID,
        "expected_digest": digest,
    }


def _reject(evidence: dict, reason: str) -> None:
    with pytest.raises(ArtifactByteEvidenceRed, match=f"^{reason}$"):
        verify_artifact_bytes(**evidence)


def _reseal(evidence: dict) -> None:
    """Simulate a fresh GitHub digest for a *different* uploaded ZIP."""
    digest = hashlib.sha256(evidence["archive"].read_bytes()).hexdigest()
    evidence["expected_digest"] = digest
    evidence["artifact_receipt"]["digest"] = f"sha256:{digest}"
    evidence["artifact_receipt"]["size_in_bytes"] = evidence["archive"].stat().st_size


def _rewrite(evidence: dict, *, manifest_change=None, replacement=None, extra=None):
    archive = evidence["archive"]
    with zipfile.ZipFile(archive) as source:
        rows = [(info.filename, source.read(info)) for info in source.infolist()]
    updated = []
    for name, data in rows:
        if name == MANIFEST_NAME and manifest_change is not None:
            manifest = json.loads(data)
            manifest_change(manifest)
            data = json.dumps(manifest).encode()
        elif replacement is not None and name == replacement[0]:
            data = replacement[1]
        updated.append((name, data))
    if extra is not None:
        updated.append(extra)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as dest:
        for name, data in updated:
            dest.writestr(name, data)
    _reseal(evidence)


def _offhost_evidence(tmp_path: Path) -> dict:
    evidence = _evidence(tmp_path)
    evidence["run_receipt"]["path"] = OFFHOST_PATH
    evidence["expected_workflow_path"] = OFFHOST_PATH
    evidence["artifact_receipt"]["name"] = OFFHOST_NAME
    _rewrite(evidence, manifest_change=lambda manifest: manifest.__setitem__(
        "artifact_name", OFFHOST_NAME))
    return evidence


def _release_evidence(tmp_path: Path) -> dict:
    """The release publisher runs on trusted main, *not* candidate HEAD."""
    evidence = _evidence(tmp_path)
    stage = Path(tempfile.mkdtemp(prefix="trusted-stage-", dir=tmp_path))
    for filename, content in RELEASE_FILES.items():
        (stage / filename).write_bytes(content)
    byte_gate.seal_release_directory(
        stage, candidate_sha=SHA, candidate_tree_sha=TREE,
        publisher_main_sha=PUBLISHER_MAIN_SHA, run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
    )
    with zipfile.ZipFile(evidence["archive"], "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(stage.iterdir()):
            zf.write(file, file.name)
    evidence["run_receipt"].update({
        "event": "workflow_dispatch", "path": RELEASE_PATH,
        "head_branch": "main", "head_sha": PUBLISHER_MAIN_SHA,
        "name": "RC6 R251 Trusted Release Build (OFFHOST ONLY)",
    })
    evidence["artifact_receipt"]["workflow_run"]["head_sha"] = PUBLISHER_MAIN_SHA
    evidence["artifact_receipt"]["name"] = RELEASE_NAME
    evidence["expected_workflow_path"] = RELEASE_PATH
    evidence["expected_publisher_sha"] = PUBLISHER_MAIN_SHA
    _reseal(evidence)
    return evidence


def test_trusted_release_dual_sha_identity_accepts_only_completed_main(tmp_path):
    evidence = _release_evidence(tmp_path)
    receipt = verify_artifact_bytes(**evidence)
    assert receipt["publisher_main_sha"] == PUBLISHER_MAIN_SHA
    assert receipt["candidate_sha"] == SHA
    assert receipt["file_count"] == 7
    assert receipt["artifact_contents_verified_by_this_guard"] is True
    assert receipt["automatic_go"] is False


@pytest.mark.parametrize("which,key,value,reason", [
    ("run_receipt", "head_sha", SHA, "RUN_SHA_MISMATCH"),
    ("run_receipt", "event", "pull_request", "RUN_WORKFLOW_MISMATCH"),
    ("run_receipt", "head_branch", "candidate", "RUN_WORKFLOW_MISMATCH"),
    ("run_receipt", "run_attempt", 2, "RUN_ATTEMPT_MISMATCH"),
    ("artifact_receipt", "name", NAME, "ARTIFACT_NAME_MISMATCH"),
])
def test_release_refuses_forged_trusted_publisher_receipts(tmp_path, which, key, value, reason):
    evidence = _release_evidence(tmp_path)
    evidence[which][key] = value
    _reject(evidence, reason)


def test_release_refuses_candidate_sha_as_trusted_main_and_bad_ref(tmp_path):
    evidence = _release_evidence(tmp_path)
    evidence["expected_publisher_sha"] = SHA
    _reject(evidence, "ARTIFACT_SHA_MISMATCH")
    evidence = _release_evidence(tmp_path)
    evidence["run_receipt"]["path"] = RELEASE_PATH + "@refs/heads/sneaky"
    _reject(evidence, "RUN_WORKFLOW_MISMATCH")


def test_release_refuses_missing_or_changed_manifest_main_identity(tmp_path):
    evidence = _release_evidence(tmp_path)
    _rewrite(evidence, manifest_change=lambda manifest: manifest.pop("publisher_main_sha"))
    _reject(evidence, "MANIFEST_SCHEMA_INVALID")
    evidence = _release_evidence(tmp_path)
    _rewrite(evidence, manifest_change=lambda manifest: manifest.update({
        "publisher_main_sha": "d" * 40,
    }))
    _reject(evidence, "MANIFEST_PUBLISHER_SHA_MISMATCH")


def test_untrusted_workflow_must_not_accept_main_identity_override(tmp_path):
    evidence = _evidence(tmp_path)
    evidence["expected_publisher_sha"] = PUBLISHER_MAIN_SHA
    _reject(evidence, "EXPECTED_PUBLISHER_INVALID")


def _reseal_release_member(evidence: dict, name: str, changed: bytes) -> None:
    def edit(manifest):
        for row in manifest["files"]:
            if row["path"] == name:
                manifest["total_size_bytes"] += len(changed) - row["size_bytes"]
                row["size_bytes"] = len(changed)
                row["sha256"] = hashlib.sha256(changed).hexdigest()
                return
        raise AssertionError("required release member not found")
    _rewrite(evidence, manifest_change=edit, replacement=(name, changed))


def test_release_rejects_resealed_fake_frozen_paper_authority(tmp_path):
    evidence = _release_evidence(tmp_path)
    name = "porota-frozen-candidate.json"
    original = json.loads(RELEASE_FILES[name])
    original["real_orders_sent_required"] = 1
    _reseal_release_member(evidence, name, json.dumps(original).encode())
    _reject(evidence, "RELEASE_INNER_CLAIM_RED")


def test_release_rejects_resealed_image_tar_not_matching_frozen_digest(tmp_path):
    evidence = _release_evidence(tmp_path)
    name = "porota-predeploy-image.tar.gz"
    _reseal_release_member(evidence, name, b"bad swapped image byte")
    _reject(evidence, "RELEASE_INNER_CLAIM_RED")


def test_release_rejects_resealed_forged_builder_bundle_digest(tmp_path):
    evidence = _release_evidence(tmp_path)
    name = "porota-deploy-bundle-v2-manifest.json"
    changed = json.loads(RELEASE_FILES[name])
    changed["bundle_sha256"] = "0" * 64
    _reseal_release_member(evidence, name, json.dumps(changed).encode())
    _reject(evidence, "RELEASE_INNER_CLAIM_RED")


RELEASE_FILES = {
    "porota-deploy-bundle-v2.tgz": b"fixed source bundle",
    "porota-predeploy-image.tar.gz": b"fixed saved image",
    "porota-host-manifest-v2.json": json.dumps({
        "schema_version": 2, "status": "GREEN", "source_git_sha": SHA,
    }).encode(),
    "porota-host-policy-v2.json": b'{"status":"GREEN"}',
}
_IMAGE_SHA = hashlib.sha256(RELEASE_FILES["porota-predeploy-image.tar.gz"]).hexdigest()
_BUNDLE_SHA = hashlib.sha256(RELEASE_FILES["porota-deploy-bundle-v2.tgz"]).hexdigest()
_IMAGE_ID = "sha256:" + "e" * 64
_NATIVE_XML_PATHS = (
    "native-bound.xml", "session/dashboard_auth_52.xml",
    "session/publication_io_identity.xml", "session/scalping_capacity.xml",
    "session/scheduler_monitor_backup.xml", "session/signals_risk_exit_ledger.xml",
    "session/sources_readiness_network.xml", "session/sql_shadow_wal.xml",
    "session/treasury_caucion.xml",
)
_NATIVE_GENERATIONS = {
    "catalog/full-catalog.json": 2, "session/stress.json": 2,
    "cadence/cadence-fresh.json": 12, "cadence/cadence-legacy.json": 12,
}
_NATIVE_PROOF = {
    "schema": "RC6_R251_RELEASE_NATIVE_V1", "schema_version": 1,
    "status": "SAME_IMAGE_TESTS_PASS", "candidate_sha": SHA,
    "candidate_tree_sha": TREE, "trusted_main_sha": PUBLISHER_MAIN_SHA,
    "image_id": _IMAGE_ID, "run_id": RUN_ID, "run_attempt": RUN_ATTEMPT,
    "source_cid": "1" * 64, "collector_cid": "2" * 64,
    "native_tar_sha256": "3" * 64, "native_tar_size_bytes": 20480,
    "native_file_count": 13,
    "native_case_counts": {name: 1520 if name == "native-bound.xml" else 1
                           for name in _NATIVE_XML_PATHS},
    "native_generations": _NATIVE_GENERATIONS,
    "stage_exit_codes": {name: 0 for name in (
        "cases", "catalog", "session", "cadence-fresh", "cadence-legacy")},
    "private_raw_uploaded": False, "paper_only": True,
    "release_go": False, "host_runtime_verified": False,
    "real_orders_physical_verified": False,
}
RELEASE_FILES["porota-deploy-bundle-v2-manifest.json"] = json.dumps({
    "schema_version": 1, "status": "GREEN", "file_count": 1,
    "files": [{"path": "sample.py", "sha256": "f" * 64, "bytes": 10}],
    "bundle_sha256": _BUNDLE_SHA,
}).encode()
RELEASE_FILES["porota-frozen-candidate.json"] = json.dumps({
    "schema_version": 1, "candidate_sha": SHA, "candidate_tree_sha": TREE,
    "image_id": _IMAGE_ID, "image_tar_sha256": _IMAGE_SHA,
    "image_config_sha256": "d" * 64, "image_size_bytes": 123456,
    "paper_mode_required": "PRODUCTION_PAPER",
    "real_orders_sent_required": 0, "real_order_capability_required": "BLOCKED",
    "build_once": True,
}).encode()
RELEASE_FILES["porota-image-build-receipt.json"] = json.dumps({
    "schema_version": 1, "candidate_sha": SHA, "candidate_tree_sha": TREE,
    "trusted_main_sha": PUBLISHER_MAIN_SHA,
    "builder_product_sha": "7ac6e368bd42137817e3271839c35f579bdbbbbe",
    "builder_blob_sha": "0fe454dcdddc445d719288ed0673f3bfa279526f",
    "image_id": _IMAGE_ID, "image_size_bytes": 123456,
    "image_tar_sha256": _IMAGE_SHA, "bundle_sha256": _BUNDLE_SHA,
    "native_proof": _NATIVE_PROOF,
    "paper_only": True, "release_go": False, "host_runtime_verified": False,
}).encode()


@pytest.mark.parametrize("mutation", [
    lambda receipt: receipt.pop("native_proof"),
    lambda receipt: receipt["native_proof"].update(image_id="sha256:" + "4" * 64),
    lambda receipt: receipt["native_proof"].update(candidate_sha=TREE),
    lambda receipt: receipt["native_proof"].update(candidate_tree_sha=SHA),
    lambda receipt: receipt["native_proof"].update(trusted_main_sha=SHA),
    lambda receipt: receipt["native_proof"].update(run_id=RUN_ID + 1),
    lambda receipt: receipt["native_proof"].update(run_attempt=RUN_ATTEMPT + 1),
    lambda receipt: receipt["native_proof"].update(status="PROBE_ONLY"),
    lambda receipt: receipt["native_proof"]["stage_exit_codes"].update(cases=5),
    lambda receipt: receipt["native_proof"]["native_case_counts"].update(
        {"native-bound.xml": 0}),
    lambda receipt: receipt["native_proof"]["native_generations"].update(
        {"cadence/cadence-legacy.json": 0}),
    lambda receipt: receipt["native_proof"].update(private_raw_uploaded=True),
    lambda receipt: receipt["native_proof"].update(release_go=True),
    lambda receipt: receipt["native_proof"].update(source_cid="2" * 64),
    lambda receipt: receipt["native_proof"].update(native_tar_sha256="bad"),
    lambda receipt: receipt["native_proof"].update(native_file_count=12),
    lambda receipt: receipt["native_proof"].update(extra="FORGED"),
])
def test_e_real_zip_verifier_rejects_resealed_missing_mismatch_or_skipped_native(
    tmp_path, mutation,
):
    evidence = _release_evidence(tmp_path)
    receipt = json.loads(RELEASE_FILES["porota-image-build-receipt.json"])
    mutation(receipt)
    _reseal_release_member(evidence, "porota-image-build-receipt.json",
                           json.dumps(receipt).encode())
    _reject(evidence, "RELEASE_INNER_CLAIM_RED")


def _synthetic_r206_public_tar(tmp_path):
    from scripts import rc6_r163_trusted_postrun_byte_audit as r206

    raw_root = tmp_path / "private-raw-junit"
    raw_root.mkdir(mode=0o700)
    (raw_root / "session").mkdir(mode=0o700)
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name in sorted(r206.TAR_FILES):
            if name.endswith(".xml"):
                count = 9 if name == "native-bound.xml" else 1
                required_modules = (
                    "test_rc6_shadow_native_bound", "test_rc6_shadow_streaming_cost",
                    "test_rc6_shadow_shared_audit", "test_rc6_shadow_owned_state",
                    "test_rc6_shadow_report_lifetime", "test_rc6_shadow_cold_audit",
                    "test_rc6_owned_funnel_details", "test_rc6_physical_cadence",
                )
                raw_xml = (
                    '<testsuites><testsuite tests="' + str(count) +
                    '" failures="0" errors="0" skipped="0">' +
                    ''.join(f'<testcase classname="tests.{required_modules[index % 8]}" '
                            f'name="case-{index}"/>' for index in range(count)) +
                    '</testsuite></testsuites>\n').encode()
                (raw_root / name).write_bytes(raw_xml)
                (raw_root / name).chmod(0o600)
                content = (
                    '<testsuite schema="RC6_R206_PUBLIC_JUNIT_V1" '
                    'status="PRIVATE_SOURCE_UNATTESTED" path="' + name +
                    '" name="R206_PUBLIC_SYNTHETIC_ONLY" tests="' + str(count) +
                    '" failures="0" errors="0" skipped="0" '
                    'private_raw_size_bytes="' + str(len(raw_xml)) +
                    '" private_raw_sha256_unattested="' +
                    hashlib.sha256(raw_xml).hexdigest() + '" release_go="false">' +
                    ''.join(f'<testcase name="case-{index:06d}"/>'
                            for index in range(1, count + 1)) +
                    '</testsuite>\n').encode()
            else:
                content = (json.dumps({
                    "schema": "RC6_R206_PUBLIC_JSON_V1",
                    "status": "PRIVATE_SOURCE_UNATTESTED", "source_path": name,
                    "private_raw_size_bytes": 67,
                    "private_raw_sha256_unattested": "0" * 64,
                    "synthetic_fixture_only": True, "release_go": False,
                    "real_orders_sent": 0, "catalog_count": 14611,
                    "ready_fixture_count": 9902,
                    "required_generations": _NATIVE_GENERATIONS[name],
                }, sort_keys=True, separators=(",", ":")) + "\n").encode()
            header = tarfile.TarInfo("./" + name)
            header.uid = header.gid = 1000
            header.mode = 0o600
            header.size = len(content)
            tf.addfile(header, io.BytesIO(content))
    archive = tmp_path / "native-private-preview.tar"
    archive.write_bytes(stream.getvalue())
    return archive, raw_root


def test_release_native_proof_builder_parses_existing_trusted_public_tar(tmp_path):
    """A real R206 public TAR is parsed; no raw private JUnit is uploaded."""
    archive, raw_root = _synthetic_r206_public_tar(tmp_path)
    result = byte_gate.build_release_native_proof(
        archive, private_junit_root=raw_root,
        candidate_sha=SHA, candidate_tree_sha=TREE,
        trusted_main_sha=PUBLISHER_MAIN_SHA, image_id=_IMAGE_ID,
        run_id=RUN_ID, run_attempt=RUN_ATTEMPT,
        source_cid="1" * 64, collector_cid="2" * 64,
    )
    assert result["native_case_counts"]["native-bound.xml"] == 9
    assert result["native_generations"] == _NATIVE_GENERATIONS
    assert result["native_tar_sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert result["stage_exit_codes"] == {name: 0 for name in (
        "cases", "catalog", "session", "cadence-fresh", "cadence-legacy")}
    assert result["image_id"] == _IMAGE_ID
    assert result["private_raw_uploaded"] is False


def test_native_proof_cli_seals_only_bounded_private_receipt(tmp_path):
    archive, raw_root = _synthetic_r206_public_tar(tmp_path)
    proof = tmp_path / "native-proof.json"
    result = subprocess.run([
        sys.executable, "-I", str(byte_gate.__file__), "native-proof",
        "--native-tar", str(archive), "--private-junit-root", str(raw_root),
        "--candidate-sha", SHA,
        "--candidate-tree-sha", TREE, "--trusted-main-sha", PUBLISHER_MAIN_SHA,
        "--image-id", _IMAGE_ID, "--run-id", str(RUN_ID),
        "--run-attempt", str(RUN_ATTEMPT),
        "--source-cid", "1" * 64, "--collector-cid", "2" * 64,
        "--receipt-out", str(proof),
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "SAME_IMAGE_NATIVE=VALIDATED" in result.stdout
    assert "case-000001" not in result.stdout
    assert json.loads(proof.read_text())["image_id"] == _IMAGE_ID
    assert proof.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "native-private-preview.tar", "native-proof.json", "private-raw-junit"]


def test_native_proof_cli_rejects_invalid_recycled_cid_without_receipt(tmp_path):
    archive, raw_root = _synthetic_r206_public_tar(tmp_path)
    proof = tmp_path / "native-proof.json"
    result = subprocess.run([
        sys.executable, "-I", str(byte_gate.__file__), "native-proof",
        "--native-tar", str(archive), "--private-junit-root", str(raw_root),
        "--candidate-sha", SHA,
        "--candidate-tree-sha", TREE, "--trusted-main-sha", PUBLISHER_MAIN_SHA,
        "--image-id", _IMAGE_ID, "--run-id", str(RUN_ID),
        "--run-attempt", str(RUN_ATTEMPT),
        "--source-cid", "1" * 64, "--collector-cid", "1" * 64,
        "--receipt-out", str(proof),
    ], capture_output=True, text=True, check=False)
    assert result.returncode == 79
    assert not proof.exists()
    assert "HOLD79" in result.stderr
    assert "native-private-preview.tar" not in result.stderr


def test_trusted_native_normalizes_copied_0644_to_private_0600_after_fd_validation(tmp_path):
    """Docker cp may retain a source file's 0644 mode despite a private parent."""
    archive, raw_root = _synthetic_r206_public_tar(tmp_path)
    for name in _NATIVE_XML_PATHS:
        (raw_root / name).chmod(0o644)
    result = byte_gate.build_release_native_proof(
        archive, private_junit_root=raw_root,
        candidate_sha=SHA, candidate_tree_sha=TREE,
        trusted_main_sha=PUBLISHER_MAIN_SHA, image_id=_IMAGE_ID,
        run_id=RUN_ID, run_attempt=RUN_ATTEMPT,
        source_cid="1" * 64, collector_cid="2" * 64,
    )
    assert result["status"] == "SAME_IMAGE_TESTS_PASS"
    assert all((raw_root / name).stat().st_mode & 0o777 == 0o600
               for name in _NATIVE_XML_PATHS)


@pytest.mark.parametrize("attack", [
    "skipped", "failure", "digest_mismatch", "entity", "missing", "symlink",
    "oversize",
])
def test_trusted_native_builder_rejects_private_junit_not_matching_public_projection(
    tmp_path, attack,
):
    archive, raw_root = _synthetic_r206_public_tar(tmp_path)
    target = raw_root / "native-bound.xml"
    raw = target.read_bytes()
    if attack == "skipped":
        target.write_bytes(raw.replace(b'skipped="0"', b'skipped="1"'))
    elif attack == "failure":
        target.write_bytes(raw.replace(b'failures="0"', b'failures="1"'))
    elif attack == "digest_mismatch":
        target.write_bytes(raw.replace(b'name="case-0"', b'name="case-X"'))
    elif attack == "entity":
        target.write_bytes(b'<!DOCTYPE testsuites [<!ENTITY leak SYSTEM "file:///etc/passwd">]>' + raw)
    elif attack == "missing":
        target.unlink()
    elif attack == "symlink":
        target.unlink()
        target.symlink_to(raw_root / "session/dashboard_auth_52.xml")
    elif attack == "oversize":
        target.write_bytes(b"X" * (8 * 1024 * 1024 + 1))
    with pytest.raises(ArtifactByteEvidenceRed, match="^RELEASE_NATIVE_TAR_RED$"):
        byte_gate.build_release_native_proof(
            archive, private_junit_root=raw_root,
            candidate_sha=SHA, candidate_tree_sha=TREE,
            trusted_main_sha=PUBLISHER_MAIN_SHA, image_id=_IMAGE_ID,
            run_id=RUN_ID, run_attempt=RUN_ATTEMPT,
            source_cid="1" * 64, collector_cid="2" * 64,
        )


def test_trusted_seal_generates_complete_sorted_release_manifest_in_private_stage(tmp_path):
    stage = tmp_path / "owned"
    stage.mkdir(mode=0o700)
    for name, data in RELEASE_FILES.items():
        (stage / name).write_bytes(data)
    result = byte_gate.seal_release_directory(
        stage, candidate_sha=SHA, candidate_tree_sha=TREE,
        publisher_main_sha=PUBLISHER_MAIN_SHA, run_id=RUN_ID,
        run_attempt=RUN_ATTEMPT,
    )
    assert result["artifact_name"] == RELEASE_NAME
    assert result["file_count"] == len(RELEASE_FILES)
    assert [row["path"] for row in result["files"]] == sorted(RELEASE_FILES)
    assert result["total_size_bytes"] == sum(map(len, RELEASE_FILES.values()))
    assert json.loads((stage / MANIFEST_NAME).read_text()) == result
    assert (stage / MANIFEST_NAME).stat().st_mode & 0o777 == 0o600
    with pytest.raises(ArtifactByteEvidenceRed, match="^STAGE_EXTRA_FILE$"):
        byte_gate.seal_release_directory(
            stage, candidate_sha=SHA, candidate_tree_sha=TREE,
            publisher_main_sha=PUBLISHER_MAIN_SHA, run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
        )


def test_trusted_release_seal_rejects_stage_injection_or_symlink(tmp_path):
    stage = tmp_path / "owned"
    stage.mkdir(mode=0o700)
    for name, data in RELEASE_FILES.items():
        (stage / name).write_bytes(data)
    (stage / "private-token.txt").write_text("secret")
    with pytest.raises(ArtifactByteEvidenceRed, match="^STAGE_EXTRA_FILE$"):
        byte_gate.seal_release_directory(
            stage, candidate_sha=SHA, candidate_tree_sha=TREE,
            publisher_main_sha=PUBLISHER_MAIN_SHA, run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
        )
    (stage / "private-token.txt").unlink()
    (stage / "porota-predeploy-image.tar.gz").unlink()
    (stage / "porota-predeploy-image.tar.gz").symlink_to(tmp_path / "missing")
    with pytest.raises(ArtifactByteEvidenceRed, match="^STAGE_FILE_UNSAFE$"):
        byte_gate.seal_release_directory(
            stage, candidate_sha=SHA, candidate_tree_sha=TREE,
            publisher_main_sha=PUBLISHER_MAIN_SHA, run_id=RUN_ID,
            run_attempt=RUN_ATTEMPT,
        )


OWNER_GO = {
    "operation": "OFFHOST_BUILD_ONCE_NO_DEPLOY",
    "issue": 1093, "candidate_sha": SHA, "candidate_tree_sha": TREE,
    "trusted_main_sha": PUBLISHER_MAIN_SHA,
    "r206_publisher_run_id": 581, "r206_publisher_attempt": 2,
    "r206_audit_run_id": 582, "r206_audit_attempt": 1,
}


def _owner_comment():
    return {
        "id": 1234, "body": "RC6_R251_BUILD_ONCE_GO_V1=" + json.dumps(OWNER_GO, sort_keys=True),
        "user": {"login": "mbalbo2023"}, "author_association": "OWNER",
        "issue_url": "https://api.github.com/repos/mbalbo2023/Porota-trading/issues/1093",
        "created_at": "2026-11-12T12:00:00Z",
        "updated_at": "2026-11-12T12:00:00Z",
    }


def test_owner_go_requires_independent_unedited_exact_sha_and_attempt():
    byte_gate.verify_release_owner_go_comment(_owner_comment(), expected_comment_id=1234,
        candidate_sha=SHA, candidate_tree_sha=TREE,
        trusted_main_sha=PUBLISHER_MAIN_SHA,
        r206_publisher_run_id=581, r206_publisher_attempt=2,
        r206_audit_run_id=582, r206_audit_attempt=1)


@pytest.mark.parametrize("mutation", [
    lambda c: c["user"].update(login="attacker"),
    lambda c: c.update(updated_at="2026-11-12T12:01:00Z"),
    lambda c: c.update(issue_url="https://api.github.com/repos/other/repo/issues/1093"),
    lambda c: c.update(id=999),
    lambda c: c.update(body=c["body"].replace('"r206_publisher_attempt": 2',
                                                 '"r206_publisher_attempt": 1')),
    lambda c: c.update(body=c["body"].replace('"candidate_sha": "' + SHA + '"',
                                                 '"candidate_sha": "' + TREE + '"')),
    lambda c: c.update(body="APPROVED_BUILD_ONCE=true"),
])
def test_forged_edited_or_attempt_mismatched_go_comments_fail_closed(mutation):
    comment = _owner_comment()
    mutation(comment)
    with pytest.raises(ArtifactByteEvidenceRed, match="^OWNER_GO_INVALID$"):
        byte_gate.verify_release_owner_go_comment(comment, expected_comment_id=1234,
            candidate_sha=SHA, candidate_tree_sha=TREE,
            trusted_main_sha=PUBLISHER_MAIN_SHA,
            r206_publisher_run_id=581, r206_publisher_attempt=2,
            r206_audit_run_id=582, r206_audit_attempt=1)


def _e_receipt_zip(tmp_path: Path, *, changed=None, extra_member=None):
    receipt = {
        "schema_version": 1,
        "status": "RELEASE_BYTES_VERIFIED_POSTCOMPLETION",
        "repository": "mbalbo2023/Porota-trading",
        "candidate_sha": SHA, "candidate_tree_sha": TREE,
        "publisher_main_sha": PUBLISHER_MAIN_SHA,
        "publisher_workflow_path": RELEASE_PATH,
        "publisher_run_id": RUN_ID, "publisher_run_attempt": RUN_ATTEMPT,
        "release_artifact_id": ARTIFACT_ID,
        "release_artifact_name": RELEASE_NAME,
        "release_artifact_digest": "sha256:" + "d" * 64,
        "release_artifact_size_bytes": 512,
        "file_count": 7, "total_size_bytes": 1024,
        "release_manifest_verified": True,
        "r144_release_go": False, "runtime_verified": False,
        "real_orders_physical_verified": False,
    }
    if changed:
        changed(receipt)
    archive = tmp_path / "postcompleted.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("rc6-r251-release-postcompletion-v1.json", json.dumps(receipt))
        if extra_member:
            zf.writestr(extra_member, b"rogue")
    return archive, receipt, hashlib.sha256(archive.read_bytes()).hexdigest()


def test_postcompletion_receipt_reader_reuses_existing_verifier_and_returns_frozen_identity(tmp_path):
    archive, receipt, digest = _e_receipt_zip(tmp_path)
    parsed = byte_gate.verify_postcompletion_receipt_zip(
        archive, expected_digest="sha256:" + digest,
        expected_size_bytes=archive.stat().st_size)
    assert parsed == receipt
    assert parsed["r144_release_go"] is False


@pytest.mark.parametrize("changed,extra_member,reason", [
    (lambda r: r.update(status="GREEN"), None, "POST_RECEIPT_SCHEMA_INVALID"),
    (lambda r: r.update(r144_release_go=True), None, "POST_RECEIPT_SCHEMA_INVALID"),
    (lambda r: r.update(release_artifact_id=0), None, "POST_RECEIPT_SCHEMA_INVALID"),
    (lambda r: r.update(publisher_main_sha=SHA[:-1]), None, "POST_RECEIPT_SCHEMA_INVALID"),
    (lambda r: r.pop("candidate_tree_sha"), None, "POST_RECEIPT_SCHEMA_INVALID"),
    (None, "shadow-token.txt", "POST_RECEIPT_ZIP_MEMBERS_INVALID"),
])
def test_postcompletion_receipt_zip_rejects_unsafe_identity_or_extra_files(
    tmp_path, changed, extra_member, reason,
):
    archive, _, digest = _e_receipt_zip(tmp_path, changed=changed, extra_member=extra_member)
    with pytest.raises(ArtifactByteEvidenceRed, match=f"^{reason}$"):
        byte_gate.verify_postcompletion_receipt_zip(
            archive, expected_digest=digest, expected_size_bytes=archive.stat().st_size)


def test_postcompletion_receipt_zip_rejects_mutated_raw_digest(tmp_path):
    archive, _, digest = _e_receipt_zip(tmp_path)
    with archive.open("ab") as stream:
        stream.write(b"unsigned-extra")
    with pytest.raises(ArtifactByteEvidenceRed, match="^POST_RECEIPT_ZIP_DIGEST_MISMATCH$"):
        byte_gate.verify_postcompletion_receipt_zip(
            archive, expected_digest=digest, expected_size_bytes=archive.stat().st_size)


class _FakeRest:
    def __init__(self, receipts):
        self.receipts = receipts

    def get_json(self, path):
        return self.receipts[path]


def _source_rest():
    repo = "/repos/mbalbo2023/Porota-trading"
    audited = {
        "status": "POSTCOMPLETION_PUBLIC_PROBE_BYTES_MATCH",
        "candidate_sha": SHA, "candidate_tree_sha": TREE,
        "publisher_main_sha": PUBLISHER_MAIN_SHA,
        "publisher_run_id": 581, "publisher_attempt": 2,
        "public_artifact_contents_verified": True,
    }
    publisher = {"id": 581, "run_attempt": 2, "head_sha": PUBLISHER_MAIN_SHA}
    common = {
        "id": 582, "run_attempt": 1, "name": byte_gate.R206_AUDIT_NAME,
        "event": "workflow_run", "head_sha": PUBLISHER_MAIN_SHA,
        "head_branch": "main", "path": byte_gate.R206_AUDIT_WORKFLOW,
        "status": "completed", "conclusion": "success",
        "repository": {"full_name": "mbalbo2023/Porota-trading"},
    }
    receipts = {
        f"{repo}/issues/comments/1234": _owner_comment(),
        f"{repo}/pulls/1087": {
            "number": 1087, "state": "open",
            "head": {"sha": SHA, "ref": byte_gate.RELEASE_CANDIDATE_BRANCH,
                     "repo": {"full_name": "mbalbo2023/Porota-trading"}},
            "base": {"ref": byte_gate.RELEASE_PRODUCT_BRANCH,
                     "repo": {"full_name": "mbalbo2023/Porota-trading"}},
        },
        f"{repo}/git/commits/{SHA}": {"sha": SHA, "tree": {"sha": TREE}},
        f"{repo}/actions/runs/582": dict(common),
        f"{repo}/actions/runs/582/attempts/1": dict(common),
        f"{repo}/actions/runs/581": publisher,
    }
    return _FakeRest(receipts), audited


def _authorize_source(client):
    return byte_gate.authorize_release_source(
        client, candidate_sha=SHA, candidate_tree_sha=TREE,
        trusted_main_sha=PUBLISHER_MAIN_SHA, owner_comment_id=1234,
        r206_publisher_run_id=581, r206_publisher_attempt=2,
        r206_audit_run_id=582, r206_audit_attempt=1)


def test_release_preflight_reaudits_R206_B_and_binds_owner_C_and_PR(monkeypatch):
    rest, audited = _source_rest()
    def audit(*args, **kwargs):
        assert kwargs["expected_publisher_run_id"] == 581
        assert kwargs["expected_publisher_attempt"] == 2
        return audited
    monkeypatch.setattr(byte_gate, "_load_trusted_r206_module", lambda: SimpleNamespace(
        audit_publisher_from_github=audit, PostrunEvidenceRed=ValueError))
    result = _authorize_source(rest)
    assert result["status"] == "OFFHOST_BUILD_ONCE_PREREQUISITES_VERIFIED"
    assert result["public_probe_bytes_reaudited"] is True
    assert result["r206_c_to_b_original_webhook_edge_verified"] is False
    assert result["release_go"] is False


@pytest.mark.parametrize("mutator,reason", [
    (lambda m: m["/repos/mbalbo2023/Porota-trading/pulls/1087"]["head"].update(sha=TREE),
     "SOURCE_CANDIDATE_IDENTITY_RED"),
    (lambda m: m["/repos/mbalbo2023/Porota-trading/actions/runs/582/attempts/1"].update(run_attempt=2),
     "R206_C_IDENTITY_RED"),
    (lambda m: m["/repos/mbalbo2023/Porota-trading/actions/runs/582"].update(
        path=byte_gate.R206_AUDIT_WORKFLOW + "@refs/heads/evil"), "R206_C_IDENTITY_RED"),
    (lambda m: m["/repos/mbalbo2023/Porota-trading/issues/comments/1234"].update(
        updated_at="2026-11-12T13:00:00Z"), "OWNER_GO_INVALID"),
])
def test_release_preflight_rejects_stale_sha_forged_C_attempt_and_edited_owner(
    monkeypatch, mutator, reason,
):
    rest, audited = _source_rest()
    mutator(rest.receipts)
    monkeypatch.setattr(byte_gate, "_load_trusted_r206_module", lambda: SimpleNamespace(
        audit_publisher_from_github=lambda *args, **kwargs: audited,
        PostrunEvidenceRed=ValueError))
    with pytest.raises(ArtifactByteEvidenceRed, match=f"^{reason}$"):
        _authorize_source(rest)


def _e_audit_rest(tmp_path):
    evidence = _release_evidence(tmp_path)
    repo = "/repos/mbalbo2023/Porota-trading"
    run = dict(evidence["run_receipt"])
    run["head_repository"] = {"full_name": "mbalbo2023/Porota-trading"}
    attempt = dict(run)
    attempt.update({
        "run_started_at": "2026-11-12T12:00:00Z",
        "created_at": "2026-11-12T12:01:00Z",
        "updated_at": "2026-11-12T12:05:00Z",
    })
    artifact = dict(evidence["artifact_receipt"])
    artifact["created_at"] = "2026-11-12T12:03:00Z"
    receipts = {
        f"{repo}/actions/runs/{RUN_ID}": run,
        f"{repo}/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}": attempt,
        f"{repo}/actions/runs/{RUN_ID}/artifacts?per_page=100": {
            "total_count": 1, "artifacts": [artifact],
        },
        f"{repo}/actions/artifacts/{ARTIFACT_ID}": artifact,
        f"{repo}/pulls/1087": {
            "number": 1087, "state": "open",
            "head": {"sha": SHA, "ref": byte_gate.RELEASE_CANDIDATE_BRANCH},
            "base": {"ref": byte_gate.RELEASE_PRODUCT_BRANCH},
        },
        f"{repo}/git/commits/{SHA}": {"sha": SHA, "tree": {"sha": TREE}},
    }
    event = {"action": "completed",
             "repository": {"full_name": "mbalbo2023/Porota-trading"},
             "workflow_run": dict(run)}
    return _FakeRest(receipts), event, evidence["archive"]


def test_e_postcompletion_authenticates_seven_real_zip_members_and_rest(monkeypatch, tmp_path):
    rest, event, archive = _e_audit_rest(tmp_path)
    monkeypatch.setattr(byte_gate, "download_release_zip",
        lambda client, artifact_id, path: path.write_bytes(archive.read_bytes()))
    receipt = byte_gate.audit_release_postcompletion(
        rest, event=event, publisher_run_id=RUN_ID,
        publisher_run_attempt=RUN_ATTEMPT,
        expected_publisher_sha=PUBLISHER_MAIN_SHA)
    assert receipt["status"] == "RELEASE_BYTES_VERIFIED_POSTCOMPLETION"
    assert receipt["file_count"] == 7
    assert receipt["release_artifact_id"] == ARTIFACT_ID
    assert receipt["release_artifact_digest"].startswith("sha256:")
    assert receipt["r144_release_go"] is False
    assert receipt["real_orders_physical_verified"] is False


@pytest.mark.parametrize("mutator,reason", [
    (lambda r, e: r.receipts["/repos/mbalbo2023/Porota-trading/pulls/1087"]["head"].update(sha=TREE),
     "POST_CANDIDATE_STALE"),
    (lambda r, e: r.receipts[f"/repos/mbalbo2023/Porota-trading/actions/runs/{RUN_ID}/attempts/{RUN_ATTEMPT}"].update(run_attempt=2),
     "POST_ATTEMPT_IDENTITY_RED"),
    (lambda r, e: r.receipts[f"/repos/mbalbo2023/Porota-trading/actions/runs/{RUN_ID}/artifacts?per_page=100"]["artifacts"][0].update(created_at="2026-11-12T11:00:00Z"),
     "POST_ARTIFACT_ATTEMPT_REPLAY"),
    (lambda r, e: e["workflow_run"].update(head_sha=SHA),
     "POST_EVENT_IDENTITY_RED"),
])
def test_e_rejects_stale_candidate_rerun_attempt_replay_or_event_spoof(
    monkeypatch, tmp_path, mutator, reason,
):
    rest, event, archive = _e_audit_rest(tmp_path)
    mutator(rest, event)
    monkeypatch.setattr(byte_gate, "download_release_zip",
        lambda client, artifact_id, path: path.write_bytes(archive.read_bytes()))
    with pytest.raises(ArtifactByteEvidenceRed, match=f"^{reason}$"):
        byte_gate.audit_release_postcompletion(
            rest, event=event, publisher_run_id=RUN_ID,
            publisher_run_attempt=RUN_ATTEMPT,
            expected_publisher_sha=PUBLISHER_MAIN_SHA)


def _declare_extra(manifest: dict, name: str, data: bytes) -> None:
    manifest["files"].append({
        "path": name,
        "type": "file",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    })
    manifest["files"].sort(key=lambda item: item["path"])
    manifest["file_count"] += 1
    manifest["total_size_bytes"] += len(data)


def test_exact_bytes_and_same_run_bindings_are_accepted_without_release_go(tmp_path):
    evidence = _evidence(tmp_path)
    verified = verify_artifact_bytes(**evidence)
    assert verified == {
        "schema_version": 1,
        "status": "BYTES_MATCH_EXPECTED",
        "candidate_sha": SHA,
        "candidate_tree_sha": TREE,
        "run_id": RUN_ID,
        "run_attempt": RUN_ATTEMPT,
        "artifact_id": ARTIFACT_ID,
        "workflow_path": ".github/workflows/porota-predeploy-v2.yml",
        "artifact_digest": "sha256:" + evidence["expected_digest"],
        "file_count": len(PAYLOAD),
        "total_size_bytes": sum(map(len, PAYLOAD.values())),
        "artifact_contents_verified_by_this_guard": True,
        "trusted_bootstrap_verified": False,
        "owner_go_verified": False,
        "automatic_go": False,
        "runtime_verified": False,
    }


def test_explicit_offhost_workflow_path_requires_matching_authenticated_run(tmp_path):
    evidence = _offhost_evidence(tmp_path)
    del evidence["expected_workflow_path"]
    _reject(evidence, "ARTIFACT_NAME_MISMATCH")
    evidence["expected_workflow_path"] = OFFHOST_PATH
    verified = verify_artifact_bytes(**evidence)
    assert verified["status"] == "BYTES_MATCH_EXPECTED"
    assert verified["automatic_go"] is False


@pytest.mark.parametrize("wrong_name", [
    NAME,
    f"rc6-r163-offhost-{RUN_ID + 1}-{RUN_ATTEMPT}-{SHA}",
    f"rc6-r163-offhost-{RUN_ID}-{RUN_ATTEMPT + 1}-{SHA}",
    f"rc6-r163-offhost-{RUN_ID}-{RUN_ATTEMPT}-{'c' * 40}",
])
def test_offhost_artifact_name_cannot_spoof_release_or_other_run(tmp_path, wrong_name):
    evidence = _offhost_evidence(tmp_path)
    evidence["artifact_receipt"]["name"] = wrong_name
    _reject(evidence, "ARTIFACT_NAME_MISMATCH")


def test_offhost_manifest_name_must_match_unique_rest_bound_identity(tmp_path):
    evidence = _offhost_evidence(tmp_path)
    _rewrite(evidence, manifest_change=lambda manifest: manifest.__setitem__(
        "artifact_name", NAME))
    _reject(evidence, "MANIFEST_ARTIFACT_NAME_MISMATCH")


@pytest.mark.parametrize("receipt,key,changed,reason", [
    ("artifact_receipt", "id", ARTIFACT_ID + 1, "ARTIFACT_ID_MISMATCH"),
    ("run_receipt", "id", RUN_ID + 1, "RUN_ID_MISMATCH"),
    ("run_receipt", "run_attempt", RUN_ATTEMPT + 1, "RUN_ATTEMPT_MISMATCH"),
])
def test_offhost_rest_run_attempt_and_artifact_id_cannot_be_substituted(
    tmp_path, receipt, key, changed, reason,
):
    evidence = _offhost_evidence(tmp_path)
    evidence[receipt][key] = changed
    _reject(evidence, reason)


@pytest.mark.parametrize("wrong_path", [
    "the-repository-checkout/scripts/not-a-workflow.yml",
    ".github/workflows/rc6-r163-offhost-guard79-proof.yml/../../override.yml",
    [],
])
def test_expected_workflow_override_fails_if_not_strictly_allowlisted(tmp_path, wrong_path):
    evidence = _evidence(tmp_path)
    evidence["expected_workflow_path"] = wrong_path
    _reject(evidence, "EXPECTED_WORKFLOW_INVALID")


def test_zip_byte_mutation_is_rejected_against_trusted_digest(tmp_path):
    evidence = _evidence(tmp_path)
    with evidence["archive"].open("ab") as stream:
        stream.write(b"attacker appended bytes")
    _reject(evidence, "ZIP_DIGEST_MISMATCH")


def test_payload_mutation_is_rejected_even_with_consistent_zip_digest(tmp_path):
    evidence = _evidence(tmp_path)
    _rewrite(evidence, replacement=("porota-deploy-bundle-v2.tgz", b"altered"))
    _reject(evidence, "FILE_SIZE_MISMATCH")


def test_same_length_payload_mutation_is_rejected_by_per_file_sha(tmp_path):
    evidence = _evidence(tmp_path)
    original = PAYLOAD["porota-deploy-bundle-v2.tgz"]
    mutated = original[:-1] + (b"X" if original[-1:] != b"X" else b"Y")
    assert len(mutated) == len(original)
    _rewrite(evidence, replacement=("porota-deploy-bundle-v2.tgz", mutated))
    _reject(evidence, "FILE_DIGEST_MISMATCH")


@pytest.mark.parametrize("path,wrong,reason", [
    (("artifact_receipt", "id"), ARTIFACT_ID + 1, "ARTIFACT_ID_MISMATCH"),
    (("artifact_receipt", "workflow_run", "id"), RUN_ID + 1, "ARTIFACT_RUN_MISMATCH"),
    (("artifact_receipt", "workflow_run", "head_sha"), "c" * 40, "ARTIFACT_SHA_MISMATCH"),
    (("artifact_receipt", "digest"), "sha256:" + "f" * 64, "ARTIFACT_DIGEST_MISMATCH"),
    (("artifact_receipt", "name"), "wrong-artifact", "ARTIFACT_NAME_MISMATCH"),
    (("artifact_receipt", "expired"), True, "ARTIFACT_EXPIRED"),
    (("run_receipt", "id"), RUN_ID + 1, "RUN_ID_MISMATCH"),
    (("run_receipt", "head_sha"), "c" * 40, "RUN_SHA_MISMATCH"),
    (("run_receipt", "conclusion"), "failure", "RUN_NOT_SUCCESS"),
    (("run_receipt", "path"), ".github/workflows/deploy.yml", "RUN_WORKFLOW_MISMATCH"),
    (("run_receipt", "repository", "full_name"), "foreign/Porota-trading", "RUN_REPO_MISMATCH"),
    (("artifact_receipt", "size_in_bytes"), 1, "ARTIFACT_SIZE_MISMATCH"),
    (("artifact_receipt", "id"), True, "ARTIFACT_ID_MISMATCH"),
    (("run_receipt", "id"), True, "RUN_ID_MISMATCH"),
    (("run_receipt", "run_attempt"), RUN_ATTEMPT + 1, "RUN_ATTEMPT_MISMATCH"),
])
def test_authenticated_rest_binding_must_match_exact_identity(tmp_path, path, wrong, reason):
    evidence = _evidence(tmp_path)
    target = evidence
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = wrong
    _reject(evidence, reason)


@pytest.mark.parametrize("path,reason", [
    (("artifact_receipt", "digest"), "ARTIFACT_DIGEST_MISMATCH"),
    (("artifact_receipt", "workflow_run"), "ARTIFACT_RUN_MISMATCH"),
    (("run_receipt", "repository"), "RUN_REPO_MISMATCH"),
    (("run_receipt", "conclusion"), "RUN_NOT_SUCCESS"),
])
def test_missing_rest_evidence_fails_closed(tmp_path, path, reason):
    evidence = _evidence(tmp_path)
    target = evidence
    for key in path[:-1]:
        target = target[key]
    del target[path[-1]]
    _reject(evidence, reason)


def test_same_workflow_in_progress_cannot_self_attest_success(tmp_path):
    evidence = _evidence(tmp_path)
    evidence["run_receipt"]["status"] = "in_progress"
    evidence["run_receipt"]["conclusion"] = None
    _reject(evidence, "RUN_NOT_SUCCESS")


def test_manifest_candidate_tree_and_run_are_checked_independently(tmp_path):
    for key, value, reason in (
        ("candidate_sha", "c" * 40, "MANIFEST_SHA_MISMATCH"),
        ("candidate_tree_sha", "d" * 40, "MANIFEST_TREE_MISMATCH"),
        ("run_id", RUN_ID + 1, "MANIFEST_RUN_MISMATCH"),
        ("run_attempt", RUN_ATTEMPT + 1, "MANIFEST_ATTEMPT_MISMATCH"),
    ):
        evidence = _evidence(tmp_path)
        _rewrite(evidence, manifest_change=lambda m: m.__setitem__(key, value))
        _reject(evidence, reason)


@pytest.mark.parametrize("key,value,reason", [
    ("file_count", 999, "MANIFEST_COUNT_MISMATCH"),
    ("total_size_bytes", 1, "MANIFEST_SIZE_MISMATCH"),
    ("file_count", True, "MANIFEST_COUNT_MISMATCH"),
])
def test_manifest_count_and_size_claims_are_verified(tmp_path, key, value, reason):
    evidence = _evidence(tmp_path)
    _rewrite(evidence, manifest_change=lambda m: m.__setitem__(key, value))
    _reject(evidence, reason)


def test_manifest_file_hash_must_have_exact_lowercase_hex_type(tmp_path):
    evidence = _evidence(tmp_path)
    _rewrite(evidence, manifest_change=lambda m: m["files"][0].__setitem__(
        "sha256", "BAD_DIGEST"))
    _reject(evidence, "MANIFEST_FILE_DIGEST_INVALID")


def test_manifest_file_hash_must_be_string_not_numeric(tmp_path):
    evidence = _evidence(tmp_path)
    _rewrite(evidence, manifest_change=lambda m: m["files"][0].__setitem__(
        "sha256", int("1" * 64)))
    _reject(evidence, "MANIFEST_FILE_DIGEST_INVALID")


def test_undeclared_zip_member_is_rejected(tmp_path):
    evidence = _evidence(tmp_path)
    _rewrite(evidence, extra=("undeclared.txt", b"not in manifest"))
    _reject(evidence, "ZIP_MEMBERS_UNDECLARED")


def test_zip_duplicate_name_is_rejected_before_reading_ambiguous_content(tmp_path):
    evidence = _evidence(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(evidence["archive"], "a") as archive:
            archive.writestr("porota-deploy-bundle-v2.tgz", b"duplicate")
    _reseal(evidence)
    _reject(evidence, "ZIP_MEMBER_DUPLICATE")


@pytest.mark.parametrize("unsafe_name", ["../escape", "/root/escape", "dir/../../escape", "dir\\evil"])
def test_zip_member_path_escape_fails_closed(tmp_path, unsafe_name):
    evidence = _evidence(tmp_path)
    _rewrite(evidence, extra=(unsafe_name, b"escape"))
    _reject(evidence, "ZIP_MEMBER_PATH_UNSAFE")


def test_zip_symlink_fails_closed_even_if_archive_digest_matches(tmp_path):
    evidence = _evidence(tmp_path)
    link = zipfile.ZipInfo("linked-data")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(evidence["archive"], "a") as archive:
        archive.writestr(link, "../secrets")
    _reseal(evidence)
    _reject(evidence, "ZIP_MEMBER_TYPE_UNSAFE")


def test_archive_path_symlink_is_rejected(tmp_path):
    evidence = _evidence(tmp_path)
    alias = tmp_path / "alias.zip"
    alias.symlink_to(evidence["archive"])
    evidence["archive"] = alias
    _reject(evidence, "ARCHIVE_NOT_REGULAR")


def test_small_size_budget_fails_before_decompression(tmp_path):
    evidence = _evidence(tmp_path)
    evidence["max_archive_bytes"] = 10
    _reject(evidence, "ARCHIVE_SIZE_LIMIT")


def test_uncompressed_zip_budget_fails_before_payload_extraction(tmp_path, monkeypatch):
    evidence = _evidence(tmp_path)
    monkeypatch.setattr(byte_gate, "MAX_UNCOMPRESSED_BYTES", 16)
    _reject(evidence, "ZIP_UNCOMPRESSED_LIMIT")


def test_resealed_high_expansion_zip_bomb_is_rejected_before_decompression(tmp_path):
    evidence = _evidence(tmp_path)
    path = "porota-deploy-bundle-v2.tgz"
    attack = b"\0" * (8 * 1024 * 1024)

    def reseal_manifest(manifest):
        row = next(item for item in manifest["files"] if item["path"] == path)
        manifest["total_size_bytes"] += len(attack) - row["size_bytes"]
        row["size_bytes"] = len(attack)
        row["sha256"] = hashlib.sha256(attack).hexdigest()

    _rewrite(evidence, manifest_change=reseal_manifest, replacement=(path, attack))
    assert evidence["archive"].stat().st_size < 16 * 1024
    _reject(evidence, "ZIP_EXPANSION_RATIO_LIMIT")


@pytest.mark.parametrize("unsafe_name", [
    "POROTA-deploy-bundle-v2.tgz",
    "porota-deploy-bundle-v2.tgz/child",
])
def test_casefold_or_file_directory_collision_is_rejected(tmp_path, unsafe_name):
    evidence = _evidence(tmp_path)
    data = b"ambiguous consumer path"
    _rewrite(evidence, manifest_change=lambda m: _declare_extra(m, unsafe_name, data),
             extra=(unsafe_name, data))
    _reject(evidence, "ZIP_MEMBER_COLLISION")


def test_manifest_cannot_pretend_to_contain_postupload_artifact_id(tmp_path):
    evidence = _evidence(tmp_path)
    _rewrite(evidence, manifest_change=lambda m: m.__setitem__("artifact_id", ARTIFACT_ID))
    _reject(evidence, "MANIFEST_SCHEMA_INVALID")


def test_cli_reports_byte_match_without_trusted_or_deploy_claim(tmp_path):
    evidence = _evidence(tmp_path)
    artifact_file = tmp_path / "artifact-receipt.json"
    run_file = tmp_path / "run-receipt.json"
    artifact_file.write_text(json.dumps(evidence["artifact_receipt"]))
    run_file.write_text(json.dumps(evidence["run_receipt"]))
    cmd = [
        sys.executable, "scripts/rc6_r163_artifact_byte_verifier.py",
        "--archive", str(evidence["archive"]),
        "--artifact-receipt", str(artifact_file),
        "--run-receipt", str(run_file),
        "--candidate-sha", SHA, "--candidate-tree-sha", TREE,
        "--run-id", str(RUN_ID), "--run-attempt", str(RUN_ATTEMPT),
        "--artifact-id", str(ARTIFACT_ID), "--digest", evidence["expected_digest"],
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["status"] == "BYTES_MATCH_EXPECTED"
    assert receipt["artifact_contents_verified_by_this_guard"] is True
    assert receipt["trusted_bootstrap_verified"] is False
    assert receipt["automatic_go"] is False

    artifact_file.write_text(json.dumps(evidence["artifact_receipt"] | {
        "digest": "sha256:" + "0" * 64,
    }))
    refused = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert refused.returncode == 79
    assert "RC6_ARTIFACT_BYTE_GATE=RED|reason=ARTIFACT_DIGEST_MISMATCH" in refused.stderr
    assert "BYTES_MATCH_EXPECTED" not in refused.stdout


def test_cli_offhost_workflow_requires_explicit_allowlisted_path(tmp_path):
    evidence = _offhost_evidence(tmp_path)
    path = OFFHOST_PATH
    artifact_file = tmp_path / "artifact.json"
    artifact_file.write_text(json.dumps(evidence["artifact_receipt"]))
    run_file = tmp_path / "run.json"
    run_file.write_text(json.dumps(evidence["run_receipt"]))
    cmd = [
        sys.executable, "scripts/rc6_r163_artifact_byte_verifier.py",
        "--archive", str(evidence["archive"]),
        "--artifact-receipt", str(artifact_file),
        "--run-receipt", str(run_file),
        "--candidate-sha", SHA, "--candidate-tree-sha", TREE,
        "--run-id", str(RUN_ID), "--run-attempt", str(RUN_ATTEMPT),
        "--artifact-id", str(ARTIFACT_ID), "--digest", evidence["expected_digest"],
    ]
    default = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert default.returncode == 79
    assert "reason=ARTIFACT_NAME_MISMATCH" in default.stderr
    explicit = subprocess.run(
        cmd + ["--expected-workflow-path", path],
        capture_output=True, text=True, timeout=10,
    )
    assert explicit.returncode == 0, explicit.stderr
    receipt = json.loads(explicit.stdout)
    assert receipt["workflow_path"] == path
    assert receipt["automatic_go"] is False


def test_rest_receipt_path_swap_before_read_cannot_redirect_data(tmp_path, monkeypatch):
    source = tmp_path / "trusted.json"
    attacker = tmp_path / "attacker.json"
    source.write_text('{"id":881}')
    attacker.write_text('{"id":999}')
    original_read_bytes = Path.read_bytes

    def swap_to_symlink_before_read(path):
        if path == source:
            source.rename(tmp_path / "trusted-original.json")
            source.symlink_to(attacker)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", swap_to_symlink_before_read)
    receipt = byte_gate._json_receipt(source)
    assert receipt == {"id": 881}


def test_rest_receipt_path_rotation_after_open_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "trusted.json"
    attacker = tmp_path / "attacker.json"
    source.write_text('{"id":881}')
    attacker.write_text('{"id":999}')
    original_open = byte_gate.os.open

    def rotate_after_open(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if Path(path) == source:
            source.rename(tmp_path / "trusted-original.json")
            source.symlink_to(attacker)
        return fd

    monkeypatch.setattr(byte_gate.os, "open", rotate_after_open)
    with pytest.raises(ArtifactByteEvidenceRed, match="^REST_RECEIPT_FILE_UNSAFE$"):
        byte_gate._json_receipt(source)


def test_rest_receipt_rejects_float_overflow_even_in_unreferenced_field(tmp_path):
    source = tmp_path / "artifact-rest.json"
    source.write_text('{"id":881,"unexpected_float":1e999}')
    with pytest.raises(ArtifactByteEvidenceRed, match="^JSON_NONFINITE$"):
        byte_gate._json_receipt(source)


def test_rest_receipt_rejects_duplicate_json_keys(tmp_path):
    source = tmp_path / "artifact-rest.json"
    source.write_text('{"id":881,"id":999}')
    with pytest.raises(ArtifactByteEvidenceRed, match="^JSON_DUPLICATE_KEY$"):
        byte_gate._json_receipt(source)
