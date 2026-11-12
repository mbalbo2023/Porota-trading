"""R252 trusted-main PR/product receipt bridge: offline identity/TDD negatives.

The GitHub REST reader and the independently tested R251 byte verifier are
the external boundaries.  Fakes exercise the bridge's real identity logic;
they are never a replacement for an actual R251 ZIP-byte integration run.
"""

from __future__ import annotations

from copy import deepcopy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import zipfile

import pytest

from scripts import rc6_ci_release_required_guard as guard
from scripts import rc6_r163_artifact_byte_verifier as real_r251_bytes


REPO = "mbalbo2023/Porota-trading"
PRODUCT = "deploy/rc6-pr69-isolated-20260915"
CANDIDATE_BRANCH = "integration/rc6-a1115-r87-unified-paper-candidate-20261110"
S = "a" * 40
T = "b" * 40
M = "c" * 40
P = "d" * 40
D_ID, D_ATTEMPT, D_ART = 301, 2, 401
E_ID, E_ATTEMPT, E_ART = 302, 1, 402
D_DIGEST = "sha256:" + "e" * 64
E_DIGEST = "sha256:" + "f" * 64
D_NAME = f"rc6-r251-release-{D_ID}-{D_ATTEMPT}-{S}"
E_NAME = f"rc6-r251-postcompletion-{D_ID}-{D_ATTEMPT}-{S}"
D_PATH = ".github/workflows/rc6-r251-trusted-release-build.yml"
E_PATH = ".github/workflows/rc6-r251-trusted-release-postcompletion.yml"
D_WORKFLOW_NAME = "RC6 R251 Trusted Release Build (OFFHOST ONLY)"
E_WORKFLOW_NAME = "RC6 R251 Trusted Release Postcompletion Byte Audit (READ ONLY)"
API = f"/repos/{REPO}"
E_RUNS_PATH = (f"{API}/actions/workflows/rc6-r251-trusted-release-postcompletion.yml/runs"
               f"?event=workflow_run&head_sha={M}&per_page=100&page=1")
E_HINTS = ("RC6_BRIDGE_E_RUN_ID", "RC6_BRIDGE_E_RUN_ATTEMPT",
           "RC6_BRIDGE_E_ARTIFACT_ID")


def inputs(mode="PR_CANDIDATE"):
    return {
        "RC6_BRIDGE_REPOSITORY": REPO,
        "RC6_BRIDGE_MODE": mode,
        "RC6_BRIDGE_CANDIDATE_SHA": S,
        "RC6_BRIDGE_CANDIDATE_TREE_SHA": T,
        "RC6_BRIDGE_PUBLISHER_MAIN_SHA": M,
        "RC6_BRIDGE_E_RUN_ID": str(E_ID),
        "RC6_BRIDGE_E_RUN_ATTEMPT": str(E_ATTEMPT),
        "RC6_BRIDGE_E_ARTIFACT_ID": str(E_ART),
        "RC6_BRIDGE_PRODUCT_SHA": P if mode == "PRODUCT_POSTMERGE" else "",
        "RC6_BRIDGE_PRODUCT_REF": f"refs/heads/{PRODUCT}" if mode == "PRODUCT_POSTMERGE" else "",
    }


def receipt():
    return {
        "schema_version": 1,
        "status": "RELEASE_BYTES_VERIFIED_POSTCOMPLETION",
        "repository": REPO,
        "candidate_sha": S,
        "candidate_tree_sha": T,
        "publisher_main_sha": M,
        "publisher_workflow_path": D_PATH,
        "publisher_run_id": D_ID,
        "publisher_run_attempt": D_ATTEMPT,
        "release_artifact_id": D_ART,
        "release_artifact_name": D_NAME,
        "release_artifact_digest": D_DIGEST,
        "release_artifact_size_bytes": 100,
        "file_count": 7,
        "total_size_bytes": 200,
        "release_manifest_verified": True,
        "r144_release_go": False,
        "runtime_verified": False,
        "real_orders_physical_verified": False,
    }


def run(run_id, attempt, path, name, event, start, end):
    return {
        "id": run_id, "run_attempt": attempt, "status": "completed",
        "conclusion": "success", "event": event, "path": path, "name": name,
        "head_sha": M, "head_branch": "main",
        "repository": {"full_name": REPO},
        "head_repository": {"full_name": REPO},
        "run_started_at": start, "updated_at": end,
    }


def artifact(artifact_id, run_id, name, digest, timestamp):
    return {
        "id": artifact_id, "name": name, "digest": digest,
        "size_in_bytes": 50 if artifact_id == E_ART else 100,
        "expired": False, "created_at": timestamp,
        "workflow_run": {"id": run_id, "head_sha": M},
    }


def github_records(mode="PR_CANDIDATE"):
    pr = {
        "number": 1087, "state": "open" if mode == "PR_CANDIDATE" else "closed",
        "draft": False, "merged": mode == "PRODUCT_POSTMERGE",
        "merge_commit_sha": P if mode == "PRODUCT_POSTMERGE" else None,
        "head": {"sha": S, "ref": CANDIDATE_BRANCH, "repo": {"full_name": REPO}},
        "base": {"ref": PRODUCT, "repo": {"full_name": REPO}},
    }
    return {
        E_RUNS_PATH: {
            "total_count": 1,
            "workflow_runs": [run(E_ID, E_ATTEMPT, E_PATH, E_WORKFLOW_NAME,
                "workflow_run", "2026-11-12T13:00:00Z", "2026-11-12T13:10:00Z")],
        },
        f"{API}/actions/runs/{E_ID}": run(E_ID, E_ATTEMPT, E_PATH,
            E_WORKFLOW_NAME, "workflow_run", "2026-11-12T13:00:00Z", "2026-11-12T13:10:00Z"),
        f"{API}/actions/runs/{E_ID}/attempts/{E_ATTEMPT}": run(E_ID, E_ATTEMPT, E_PATH,
            E_WORKFLOW_NAME, "workflow_run", "2026-11-12T13:00:00Z", "2026-11-12T13:10:00Z"),
        f"{API}/actions/artifacts/{E_ART}": artifact(E_ART, E_ID, E_NAME,
            E_DIGEST, "2026-11-12T13:05:00Z"),
        f"{API}/actions/runs/{E_ID}/artifacts?per_page=100": {
            "total_count": 1, "artifacts": [artifact(E_ART, E_ID, E_NAME,
                E_DIGEST, "2026-11-12T13:05:00Z")],
        },
        f"{API}/actions/runs/{D_ID}": run(D_ID, D_ATTEMPT, D_PATH,
            D_WORKFLOW_NAME, "workflow_dispatch", "2026-11-12T12:00:00Z", "2026-11-12T12:45:00Z"),
        f"{API}/actions/runs/{D_ID}/attempts/{D_ATTEMPT}": run(D_ID, D_ATTEMPT, D_PATH,
            D_WORKFLOW_NAME, "workflow_dispatch", "2026-11-12T12:00:00Z", "2026-11-12T12:45:00Z"),
        f"{API}/actions/artifacts/{D_ART}": artifact(D_ART, D_ID, D_NAME,
            D_DIGEST, "2026-11-12T12:30:00Z"),
        f"{API}/actions/runs/{D_ID}/artifacts?per_page=100": {
            "total_count": 1, "artifacts": [artifact(D_ART, D_ID, D_NAME,
                D_DIGEST, "2026-11-12T12:30:00Z")],
        },
        f"{API}/git/commits/{S}": {"sha": S, "tree": {"sha": T}},
        f"{API}/pulls/1087": pr,
        f"{API}/commits/{P}": {
            "sha": P, "parents": [{"sha": "1" * 40}, {"sha": S}],
            "commit": {"tree": {"sha": T}},
        },
    }


class FakeGitHub:
    def __init__(self, records):
        self.records = deepcopy(records)
        self.requests = []
        self.after_reads = {}

    def get(self, path):
        self.requests.append(path)
        if path not in self.records:
            raise RuntimeError("API_FIXTURE_MISSING")
        value = self.records[path]
        if path in self.after_reads and self.requests.count(path) >= 2:
            value = self.after_reads[path]
        return deepcopy(value)

    def download_artifact(self, artifact_id):
        if artifact_id not in {D_ART, E_ART}:
            raise RuntimeError("UNKNOWN_ARTIFACT")
        return Path(f"/fake/github-artifact-{artifact_id}.zip")


class FakeIndependentR251Verifier:
    """Only simulates verified R251 outputs; bridge identity code is real."""

    RELEASE_WORKFLOW = D_PATH

    def __init__(self, postreceipt=None):
        self.postreceipt = deepcopy(postreceipt or receipt())
        self.e_calls = []
        self.d_calls = []
        self.reject = False

    def verify_postcompletion_receipt_zip(self, archive, *, expected_digest, expected_size_bytes):
        self.e_calls.append((archive, expected_digest, expected_size_bytes))
        if self.reject:
            raise ValueError("BAD_E_ZIP_BYTES")
        return deepcopy(self.postreceipt)

    def verify_artifact_bytes(self, archive, **kwargs):
        self.d_calls.append((archive, deepcopy(kwargs)))
        if self.reject:
            raise ValueError("BAD_D_ZIP_BYTES")
        return {
            "schema_version": 1, "status": "BYTES_MATCH_EXPECTED",
            "candidate_sha": S, "candidate_tree_sha": T,
            "publisher_main_sha": M, "artifact_id": D_ART,
            "artifact_digest": D_DIGEST,
            "run_id": D_ID, "run_attempt": D_ATTEMPT,
            "artifact_contents_verified_by_this_guard": True,
            "automatic_go": False, "owner_go_verified": False,
            "runtime_verified": False,
        }


def bridge(mode="PR_CANDIDATE", *, changes=None, mutate=None, verifier=None):
    values = inputs(mode)
    values.update(changes or {})
    records = github_records(mode)
    if mutate:
        mutate(records)
    reader = FakeGitHub(records)
    independent = verifier or FakeIndependentR251Verifier()
    result = guard.verify_trusted_release(values, github=reader,
                                          byte_verifier=independent)
    return result, reader, independent


def undisclosed_e_hints():
    return {key: "" for key in E_HINTS}


@pytest.mark.parametrize("mode", ["PR_CANDIDATE", "PRODUCT_POSTMERGE"])
def test_single_completed_e_discovered_without_repository_variables(mode):
    result, reader, _ = bridge(mode, changes=undisclosed_e_hints())
    assert result["status"] == "TRUSTED_RELEASE_BYTES_BOUND"
    assert result["trusted_main_audit_run_id"] == E_ID
    assert E_RUNS_PATH in reader.requests


@pytest.mark.parametrize("changes", [
    {"RC6_BRIDGE_E_RUN_ID": ""},
    {"RC6_BRIDGE_E_RUN_ATTEMPT": ""},
    {"RC6_BRIDGE_E_ARTIFACT_ID": ""},
    {"RC6_BRIDGE_E_RUN_ID": "", "RC6_BRIDGE_E_RUN_ATTEMPT": ""},
])
def test_partial_explicit_e_hints_rejected_instead_of_silent_discovery(changes):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(changes=changes)


@pytest.mark.parametrize("mutation", [
    lambda r: r[E_RUNS_PATH].update(total_count=0, workflow_runs=[]),
    lambda r: r[E_RUNS_PATH].update(total_count=101),
    lambda r: r[E_RUNS_PATH]["workflow_runs"][0].update(conclusion="failure"),
    lambda r: r[E_RUNS_PATH]["workflow_runs"][0].update(path="other.yml"),
    lambda r: r[E_RUNS_PATH]["workflow_runs"][0].update(head_sha=S),
    lambda r: r[f"{API}/actions/runs/{E_ID}/artifacts?per_page=100"]["artifacts"][0].update(expired=True),
    lambda r: r[f"{API}/actions/runs/{E_ID}/artifacts?per_page=100"]["artifacts"][0].update(name="latest-e.zip"),
])
def test_autodiscovery_fails_closed_on_no_match_bad_listing_or_cap(mutation):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(changes=undisclosed_e_hints(), mutate=mutation)


def test_autodiscovery_rejects_two_successful_e_runs_for_same_candidate():
    def add_second(records):
        other_id, other_art = E_ID + 10, E_ART + 10
        other_run = deepcopy(records[f"{API}/actions/runs/{E_ID}"])
        other_run["id"] = other_id
        records[E_RUNS_PATH]["workflow_runs"].append(other_run)
        records[E_RUNS_PATH]["total_count"] = 2
        records[f"{API}/actions/runs/{other_id}"] = other_run
        second = deepcopy(records[f"{API}/actions/artifacts/{E_ART}"])
        second["id"] = other_art
        second["workflow_run"]["id"] = other_id
        records[f"{API}/actions/runs/{other_id}/artifacts?per_page=100"] = {
            "total_count": 1, "artifacts": [second],
        }

    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(changes=undisclosed_e_hints(), mutate=add_second)


@pytest.mark.parametrize("mode", ["PR_CANDIDATE", "PRODUCT_POSTMERGE"])
def test_e_discovery_listing_reopened_to_reject_replay(mode):
    data = inputs(mode)
    data.update(undisclosed_e_hints())
    reader = FakeGitHub(github_records(mode))
    replayed = deepcopy(reader.records[E_RUNS_PATH])
    replayed["workflow_runs"][0]["run_attempt"] += 1
    reader.after_reads[E_RUNS_PATH] = replayed
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        guard.verify_trusted_release(
            data, github=reader, byte_verifier=FakeIndependentR251Verifier())


def test_e_discovery_cannot_overwrite_snapshot_when_artifact_metadata_changes():
    data = inputs()
    data.update(undisclosed_e_hints())
    reader = FakeGitHub(github_records())
    artifact_path = f"{API}/actions/artifacts/{E_ART}"
    listing_path = f"{API}/actions/runs/{E_ID}/artifacts?per_page=100"
    changed = deepcopy(reader.records[artifact_path])
    changed["digest"] = "sha256:" + "8" * 64
    listing = deepcopy(reader.records[listing_path])
    listing["artifacts"] = [changed]
    # E artifact API has advanced to the new state before validation, while
    # discovery saw the prior list. Without comparing the second list against
    # the first, both normal and final re-GET would agree on the new state.
    reader.records[artifact_path] = changed
    reader.after_reads[listing_path] = listing
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        guard.verify_trusted_release(
            data, github=reader, byte_verifier=FakeIndependentR251Verifier())


def test_e_discovery_malformed_other_run_fails_even_if_one_valid_e_exists():
    def add_malformed_row(records):
        records[E_RUNS_PATH]["workflow_runs"].append(None)
        records[E_RUNS_PATH]["total_count"] = 2

    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(changes=undisclosed_e_hints(), mutate=add_malformed_row)


def test_product_postmerge_can_derive_second_parent_and_tree_without_variables():
    result, reader, _ = bridge(
        "PRODUCT_POSTMERGE", changes={
            **undisclosed_e_hints(),
            "RC6_BRIDGE_CANDIDATE_SHA": "",
            "RC6_BRIDGE_CANDIDATE_TREE_SHA": "",
        })
    assert (result["candidate_sha"], result["candidate_tree_sha"]) == (S, T)
    assert reader.requests.count(f"{API}/commits/{P}") >= 2


@pytest.mark.parametrize("changes", [
    {"RC6_BRIDGE_CANDIDATE_SHA": ""},
    {"RC6_BRIDGE_CANDIDATE_TREE_SHA": ""},
])
def test_partial_product_candidate_parent_or_tree_is_rejected(changes):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge("PRODUCT_POSTMERGE", changes=changes)


@pytest.mark.parametrize("mutation", [
    lambda r: r[f"{API}/commits/{P}"].update(parents=[{"sha": S}]),
    lambda r: r[f"{API}/commits/{P}"].update(parents=[{"sha": S}, {"sha": "x"}]),
    lambda r: r[f"{API}/commits/{P}"]["commit"]["tree"].update(sha="x"),
])
def test_product_no_variables_still_requires_real_two_parent_sha_tree(mutation):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge("PRODUCT_POSTMERGE", changes={
            "RC6_BRIDGE_CANDIDATE_SHA": "",
            "RC6_BRIDGE_CANDIDATE_TREE_SHA": "",
        }, mutate=mutation)


def test_discovery_invalid_artifact_digest_type_is_hold_not_traceback():
    def wrong_type(records):
        listing = records[f"{API}/actions/runs/{E_ID}/artifacts?per_page=100"]
        listing["artifacts"][0]["digest"] = 42

    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(changes=undisclosed_e_hints(), mutate=wrong_type)


@pytest.mark.parametrize("mode", ["PR_CANDIDATE", "PRODUCT_POSTMERGE"])
def test_exact_authenticated_completed_e_binds_d_artifact_no_host_go(mode):
    result, reader, verifier = bridge(mode)
    assert result["status"] == "TRUSTED_RELEASE_BYTES_BOUND"
    assert result["mode"] == mode
    assert result["candidate_sha"] == S and result["candidate_tree_sha"] == T
    assert result["release_artifact_id"] == D_ART
    assert result["release_artifact_digest"] == D_DIGEST
    assert result["trusted_main_audit_run_id"] == E_ID
    assert result["trusted_main_audit_run_attempt"] == E_ATTEMPT
    assert result["artifact_contents_verified_by_this_guard"] is True
    for forbidden in ("owner_go_verified", "runtime_verified", "automatic_go"):
        assert result[forbidden] is False
    assert set(result) == {
        "status", "mode", "candidate_sha", "candidate_tree_sha", "product_sha",
        "release_artifact_id", "release_artifact_digest", "release_run_id",
        "release_run_attempt", "trusted_main_audit_run_id",
        "trusted_main_audit_run_attempt", "artifact_contents_verified_by_this_guard",
        "owner_go_verified", "runtime_verified", "automatic_go",
    }
    assert result["product_sha"] == (P if mode == "PRODUCT_POSTMERGE" else "")
    assert verifier.e_calls == [(Path(f"/fake/github-artifact-{E_ART}.zip"), E_DIGEST, 50)]
    assert len(verifier.d_calls) == 1
    d_archive, d_args = verifier.d_calls[0]
    assert d_archive == Path(f"/fake/github-artifact-{D_ART}.zip")
    assert d_args["expected_run_id"] == D_ID
    assert d_args["expected_run_attempt"] == D_ATTEMPT
    assert d_args["expected_artifact_id"] == D_ART
    assert d_args["expected_digest"] == D_DIGEST
    assert d_args["expected_candidate_sha"] == S
    assert d_args["expected_tree_sha"] == T
    assert d_args["expected_publisher_sha"] == M
    assert reader.requests.count(f"{API}/actions/runs/{E_ID}") >= 2
    assert reader.requests.count(f"{API}/actions/artifacts/{E_ART}") >= 2


def test_trusted_bridge_consumes_actual_r251_e_receipt_zip(tmp_path):
    # Real R251 ZIP reader, synthetic authenticated REST metadata and a fake
    # D ZIP boundary: D's complete byte verifier is covered by R251's own TDD.
    # This test catches E schema/API drift that fake verifier rows cannot.
    archive = tmp_path / "real-r251-postcompletion.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("rc6-r251-release-postcompletion-v1.json",
                    json.dumps(receipt(), sort_keys=True))
    digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
    size = archive.stat().st_size
    records = github_records()
    records[f"{API}/actions/artifacts/{E_ART}"].update(
        {"digest": digest, "size_in_bytes": size})
    records[f"{API}/actions/runs/{E_ID}/artifacts?per_page=100"]["artifacts"][0].update(
        {"digest": digest, "size_in_bytes": size})
    reader = FakeGitHub(records)
    reader.download_artifact = lambda artifact_id: (
        archive if artifact_id == E_ART else Path("/fake/verified-r251-d.zip"))
    independent = FakeIndependentR251Verifier()
    independent.verify_postcompletion_receipt_zip = real_r251_bytes.verify_postcompletion_receipt_zip
    result = guard.verify_trusted_release(inputs(), github=reader,
                                          byte_verifier=independent)
    assert result["status"] == "TRUSTED_RELEASE_BYTES_BOUND"
    assert result["release_artifact_id"] == D_ART
    assert result["owner_go_verified"] is False


def test_r252_real_d_and_e_zip_gate_rejects_resealed_missing_same_image_native(tmp_path):
    """R252 must invoke the actual D/E byte verifier, including native_proof."""
    from test_rc6_r163_artifact_byte_verifier import RELEASE_FILES

    stage = tmp_path / "seven-member-d"
    stage.mkdir(mode=0o700)
    for name, raw in RELEASE_FILES.items():
        if name == "porota-image-build-receipt.json":
            build_receipt = json.loads(raw)
            build_receipt["native_proof"].update(run_id=D_ID, run_attempt=D_ATTEMPT)
            raw = json.dumps(build_receipt, sort_keys=True).encode()
        (stage / name).write_bytes(raw)
    real_r251_bytes.seal_release_directory(
        stage, candidate_sha=S, candidate_tree_sha=T,
        publisher_main_sha=M, run_id=D_ID, run_attempt=D_ATTEMPT)

    d_zip, e_zip = tmp_path / "real-d.zip", tmp_path / "real-e.zip"
    e_receipt = receipt()
    records = github_records()

    def publish_fixture():
        with zipfile.ZipFile(d_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(stage.iterdir()):
                zf.write(path, arcname=path.name)
        d_digest = "sha256:" + hashlib.sha256(d_zip.read_bytes()).hexdigest()
        e_receipt["release_artifact_digest"] = d_digest
        e_receipt["release_artifact_size_bytes"] = d_zip.stat().st_size
        e_receipt["total_size_bytes"] = sum(
            path.stat().st_size for path in stage.iterdir()
            if path.name != real_r251_bytes.MANIFEST_NAME)
        with zipfile.ZipFile(e_zip, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("rc6-r251-release-postcompletion-v1.json",
                        json.dumps(e_receipt, sort_keys=True))
        e_digest = "sha256:" + hashlib.sha256(e_zip.read_bytes()).hexdigest()
        for artifact_id, digest, size, run_id in (
            (D_ART, d_digest, d_zip.stat().st_size, D_ID),
            (E_ART, e_digest, e_zip.stat().st_size, E_ID),
        ):
            records[f"{API}/actions/artifacts/{artifact_id}"].update(
                digest=digest, size_in_bytes=size)
            records[f"{API}/actions/runs/{run_id}/artifacts?per_page=100"]["artifacts"][0].update(
                digest=digest, size_in_bytes=size)

    def bridge_with_real_bytes():
        github = FakeGitHub(records)
        github.download_artifact = lambda artifact_id: d_zip if artifact_id == D_ART else e_zip
        return guard.verify_trusted_release(
            inputs(), github=github, byte_verifier=real_r251_bytes)

    publish_fixture()
    assert bridge_with_real_bytes()["status"] == "TRUSTED_RELEASE_BYTES_BOUND"

    # Re-seal the manifest and both GitHub artifact digests as an attacker
    # would. The inner receipt is still missing its mandatory native proof.
    receipt_file = stage / "porota-image-build-receipt.json"
    build_receipt = json.loads(receipt_file.read_text())
    build_receipt.pop("native_proof")
    replacement = json.dumps(build_receipt, sort_keys=True).encode()
    receipt_file.write_bytes(replacement)
    manifest_file = stage / real_r251_bytes.MANIFEST_NAME
    manifest = json.loads(manifest_file.read_text())
    for row in manifest["files"]:
        if row["path"] == receipt_file.name:
            manifest["total_size_bytes"] += len(replacement) - row["size_bytes"]
            row.update(size_bytes=len(replacement),
                       sha256=hashlib.sha256(replacement).hexdigest())
    manifest_file.write_text(json.dumps(manifest, sort_keys=True))
    publish_fixture()
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge_with_real_bytes()


@pytest.mark.parametrize("bad", [
    {"RC6_BRIDGE_REPOSITORY": "someone/else"},
    {"RC6_BRIDGE_CANDIDATE_SHA": "z" * 40},
    {"RC6_BRIDGE_CANDIDATE_TREE_SHA": "0" * 39},
    {"RC6_BRIDGE_PUBLISHER_MAIN_SHA": ""},
    {"RC6_BRIDGE_E_RUN_ID": "0"},
    {"RC6_BRIDGE_E_RUN_ATTEMPT": "0"},
    {"RC6_BRIDGE_E_ARTIFACT_ID": "-1"},
    {"RC6_BRIDGE_PRODUCT_SHA": P},
    {"RC6_BRIDGE_PRODUCT_REF": "refs/heads/main"},
    {"RC6_BRIDGE_UNREVIEWED": "true"},
])
def test_bad_selectors_and_unreviewed_fields_red(bad):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(changes=bad)


@pytest.mark.parametrize("key,value", [
    ("status", "queued"), ("conclusion", "failure"), ("event", "push"),
    ("path", ".github/workflows/other.yml"), ("head_sha", S),
    ("head_branch", CANDIDATE_BRANCH), ("run_attempt", 2),
])
def test_e_run_must_be_completed_exact_trusted_main(key, value):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(mutate=lambda data: data[f"{API}/actions/runs/{E_ID}"].update({key: value}))


@pytest.mark.parametrize("key,value", [
    ("expired", True), ("digest", "sha256:" + "0" * 64),
    ("id", E_ART + 1), ("name", "release-latest"),
    ("created_at", "2026-11-12T12:59:59Z"),
])
def test_e_rest_artifact_identity_and_attempt_window_required(key, value):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(mutate=lambda data: data[f"{API}/actions/artifacts/{E_ART}"].update({key: value}))


@pytest.mark.parametrize("run_id,artifact_id", [(E_ID, E_ART), (D_ID, D_ART)])
def test_release_workflow_has_exactly_one_artifact_no_name_alias(run_id, artifact_id):
    path = f"{API}/actions/runs/{run_id}/artifacts?per_page=100"

    def add_alias(records):
        listing = records[path]
        second = deepcopy(listing["artifacts"][0])
        second["id"] = artifact_id + 100
        second["name"] = "ambiguous-debug-sidecar"
        listing["artifacts"].append(second)
        listing["total_count"] = 2

    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(mutate=add_alias)


@pytest.mark.parametrize("path,key,value", [
    (f"{API}/actions/artifacts/{E_ART}", "digest", 123),
    (f"{API}/actions/artifacts/{D_ART}", "digest", []),
    (f"{API}/actions/runs/{E_ID}", "path", []),
    (f"{API}/actions/runs/{D_ID}", "path", {"workflow": D_PATH}),
])
def test_malformed_rest_types_fail_with_sanitized_red(path, key, value):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(mutate=lambda data: data[path].update({key: value}))


@pytest.mark.parametrize("key,value", [
    ("publisher_run_attempt", 1), ("release_artifact_id", 900),
    ("candidate_sha", "0" * 40), ("candidate_tree_sha", "0" * 40),
    ("publisher_main_sha", S), ("release_artifact_digest", "sha256:" + "0" * 64),
    ("r144_release_go", True), ("runtime_verified", True),
    ("real_orders_physical_verified", True),
])
def test_e_receipt_must_bind_exact_d_and_never_assert_host_go(key, value):
    bad = receipt()
    bad[key] = value
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(verifier=FakeIndependentR251Verifier(bad))


def test_r251_byte_verifier_error_cannot_be_bypassed():
    verifier = FakeIndependentR251Verifier()
    verifier.reject = True
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(verifier=verifier)


@pytest.mark.parametrize("key,value", [
    ("draft", True), ("state", "closed"),
    ("head", {"sha": "0" * 40, "ref": CANDIDATE_BRANCH,
              "repo": {"full_name": REPO}}),
])
def test_stale_or_draft_pr_rejected(key, value):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(mutate=lambda data: data[f"{API}/pulls/1087"].update({key: value}))


@pytest.mark.parametrize("key,value", [
    ("parents", [{"sha": "1" * 40}]),
    ("parents", [{"sha": S}, {"sha": "0" * 40}]),
    ("parents", [{"sha": "1" * 40}, {"sha": S}, {"sha": S}]),
    ("commit", {"tree": {"sha": "0" * 40}}),
])
def test_product_requires_exact_two_parent_merge_with_identical_tree(key, value):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge("PRODUCT_POSTMERGE", mutate=lambda data: data[f"{API}/commits/{P}"].update({key: value}))


def test_e_run_can_not_mutate_between_first_and_final_get():
    reader = FakeGitHub(github_records())
    stale = deepcopy(reader.records[f"{API}/actions/runs/{E_ID}"])
    stale["run_attempt"] = 2
    reader.after_reads[f"{API}/actions/runs/{E_ID}"] = stale
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        guard.verify_trusted_release(inputs(), github=reader,
            byte_verifier=FakeIndependentR251Verifier())


@pytest.mark.parametrize("path,field,value", [
    (f"{API}/actions/runs/{E_ID}/attempts/{E_ATTEMPT}", "status", "queued"),
    (f"{API}/actions/runs/{D_ID}/attempts/{D_ATTEMPT}", "run_attempt", 1),
    (f"{API}/actions/runs/{D_ID}/attempts/{D_ATTEMPT}", "head_sha", S),
])
def test_completed_run_requires_independently_reopened_exact_attempt(path, field, value):
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge(mutate=lambda data: data[path].update({field: value}))


def test_product_pr_must_be_merged_by_exact_product_commit():
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        bridge("PRODUCT_POSTMERGE", mutate=lambda data:
            data[f"{API}/pulls/1087"].update({"merge_commit_sha": "0" * 40}))


def test_missing_or_invalid_r251_bytes_never_accepts_unaudited_data():
    with pytest.raises(guard.RequiredReleaseEvidenceRed):
        guard.verify_trusted_release(inputs(), github=FakeGitHub(github_records()))


@pytest.mark.parametrize("mode,event,ref,sha", [
    ("PR_CANDIDATE", "push", "refs/pull/1087/merge", ""),
    ("PR_CANDIDATE", "pull_request", "refs/heads/main", ""),
    ("PRODUCT_POSTMERGE", "pull_request", f"refs/heads/{PRODUCT}", P),
    ("PRODUCT_POSTMERGE", "push", "refs/heads/main", P),
    ("PRODUCT_POSTMERGE", "push", f"refs/heads/{PRODUCT}", "0" * 40),
])
def test_cli_rejects_wrong_event_ref_or_product_sha(mode, event, ref, sha):
    env = {**inputs(mode), "GITHUB_TOKEN": "private-example-token",
           "GITHUB_EVENT_NAME": event, "GITHUB_REF": ref,
           "GITHUB_SHA": sha, "GITHUB_REPOSITORY": REPO}
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, env, clear=True), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert guard.main(["--trusted-release-mode", mode]) == 79
    assert stdout.getvalue() == ""
    assert "HOLD79" in stderr.getvalue()
    assert "private-example-token" not in stderr.getvalue()


def test_cli_without_e_or_r251_dependencies_is_hard_hold79():
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, {
        **inputs(), "GITHUB_EVENT_NAME": "pull_request", "GITHUB_REF": "refs/pull/1087/merge",
        "GITHUB_SHA": "", "GITHUB_REPOSITORY": REPO,
        # No GitHub token: do not even contact REST or invent success.
    }, clear=True), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert guard.main(["--trusted-release-mode", "PR_CANDIDATE"]) == 79
    assert stdout.getvalue() == ""
    assert "HOLD79" in stderr.getvalue()


def test_cli_publisher_is_derived_from_exact_trusted_checkout_not_repo_variables():
    selected = inputs()
    selected["RC6_BRIDGE_PUBLISHER_MAIN_SHA"] = ""
    selected.update(undisclosed_e_hints())
    env = {**selected, "GITHUB_TOKEN": "test-token-not-printed",
           "GITHUB_EVENT_NAME": "pull_request",
           "GITHUB_REF": "refs/pull/1087/merge", "GITHUB_SHA": S,
           "GITHUB_REPOSITORY": REPO}
    captures = []

    class RestFactory:
        def __init__(self, token):
            assert token == "test-token-not-printed"

    def inspected(data, *, github):
        captures.append(dict(data))
        assert isinstance(github, RestFactory)
        return {"status": "TRUSTED_RECEIPT_TEST_ONLY"}

    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.dict(os.environ, env, clear=True), \
            patch.object(guard, "_trusted_checkout_sha", return_value=M), \
            patch.object(guard, "_load_trusted_module", return_value=type(
                "ReviewedTransport", (), {"GitHubReadOnlyREST": RestFactory})), \
            patch.object(guard, "verify_trusted_release", side_effect=inspected), \
            contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert guard.main(["--trusted-release-mode", "PR_CANDIDATE"]) == 0
    assert captures[0]["RC6_BRIDGE_PUBLISHER_MAIN_SHA"] == M
    assert all(captures[0][key] == "" for key in E_HINTS)
    assert json.loads(stdout.getvalue())["status"] == "TRUSTED_RECEIPT_TEST_ONLY"
    assert stderr.getvalue() == ""


def test_actual_trusted_checkout_sha_matches_immutable_git_head():
    # The same root is checked out at an exact SHA by R253/R254 before this
    # script runs with -I; deriving it here needs no mutable repo variable.
    root = Path(guard.__file__).resolve().parents[1]
    read = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True)
    assert guard._trusted_checkout_sha() == read.stdout.strip()


def test_cli_publisher_hint_mismatch_is_hold79_before_rest_call():
    env = {**inputs(), "GITHUB_TOKEN": "test-token-not-printed",
           "GITHUB_EVENT_NAME": "pull_request",
           "GITHUB_REF": "refs/pull/1087/merge", "GITHUB_SHA": S,
           "GITHUB_REPOSITORY": REPO}
    stderr = io.StringIO()
    with patch.dict(os.environ, env, clear=True), \
            patch.object(guard, "_trusted_checkout_sha", return_value="1" * 40), \
            patch.object(guard, "_load_trusted_module", side_effect=AssertionError(
                "REST must not run")), contextlib.redirect_stderr(stderr):
        assert guard.main(["--trusted-release-mode", "PR_CANDIDATE"]) == 79
    assert "HOLD79" in stderr.getvalue()


def test_real_python_isolated_import_only_from_trusted_sibling_file():
    # The exact invocation that R253 will use must not rely on the Python
    # script's checkout root being implicitly injected into -I sys.path.
    source = Path(guard.__file__).resolve()
    statement = (
        "import importlib.util,sys; "
        f"s=importlib.util.spec_from_file_location('trusted_release_guard',{str(source)!r}); "
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m); "
        "assert m._load_trusted_module('rc6_r163_trusted_postrun_byte_audit.py').GitHubReadOnlyREST; "
        "assert m._load_trusted_module('rc6_r163_artifact_byte_verifier.py').verify_artifact_bytes"
    )
    p = subprocess.run([sys.executable, "-I", "-B", "-c", statement],
                       capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stderr


def test_real_python_isolated_cli_invalid_selector_holds_without_network():
    source = Path(guard.__file__).resolve()
    selected = inputs("PRODUCT_POSTMERGE")
    selected["RC6_BRIDGE_E_RUN_ID"] = "0"  # pre-network parser defense, before R251 import
    env = {**os.environ, **selected,
           "GITHUB_EVENT_NAME": "push", "GITHUB_REF": f"refs/heads/{PRODUCT}",
           "GITHUB_SHA": P, "GITHUB_REPOSITORY": REPO,
           "GITHUB_TOKEN": "test-only-never-used-on-invalid-selector"}
    p = subprocess.run([sys.executable, "-I", "-B", str(source),
                        "--trusted-release-mode", "PRODUCT_POSTMERGE"],
                       env=env, capture_output=True, text=True, check=False)
    assert p.returncode == 79
    assert p.stdout == ""
    assert "HOLD79" in p.stderr
    assert "Traceback" not in p.stderr and "ModuleNotFoundError" not in p.stderr
