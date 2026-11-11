"""OFFHOST tests for a proposed, not-yet-trusted artifact byte gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat
import subprocess
import sys
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
