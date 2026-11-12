"""R252 trusted-main PR/product receipt bridge: offline identity/TDD negatives.

The GitHub REST reader and the independently tested R251 byte verifier are
the external boundaries.  Fakes exercise the bridge's real identity logic;
they are never a replacement for an actual R251 ZIP-byte integration run.
"""

from __future__ import annotations

from copy import deepcopy
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from scripts import rc6_ci_release_required_guard as guard


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


def test_no_r251_import_never_accepts_unaudited_data():
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


def test_real_python_isolated_import_only_from_trusted_sibling_file():
    # The exact invocation that R253 will use must not rely on the Python
    # script's checkout root being implicitly injected into -I sys.path.
    source = Path(guard.__file__).resolve()
    statement = (
        "import importlib.util,sys; "
        f"s=importlib.util.spec_from_file_location('trusted_release_guard',{str(source)!r}); "
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m); "
        "assert m._load_trusted_module('rc6_r163_trusted_postrun_byte_audit.py').GitHubReadOnlyREST"
    )
    p = subprocess.run([sys.executable, "-I", "-B", "-c", statement],
                       capture_output=True, text=True, check=False)
    assert p.returncode == 0, p.stderr


def test_real_python_isolated_cli_fail_closed_missing_r251_no_traceback():
    source = Path(guard.__file__).resolve()
    env = {**os.environ, **inputs("PRODUCT_POSTMERGE"),
           "GITHUB_EVENT_NAME": "push", "GITHUB_REF": f"refs/heads/{PRODUCT}",
           "GITHUB_SHA": P, "GITHUB_REPOSITORY": REPO,
           "GITHUB_TOKEN": "test-only-not-used-because-r251-unavailable"}
    p = subprocess.run([sys.executable, "-I", "-B", str(source),
                        "--trusted-release-mode", "PRODUCT_POSTMERGE"],
                       env=env, capture_output=True, text=True, check=False)
    assert p.returncode == 79
    assert p.stdout == ""
    assert "HOLD79" in p.stderr
    assert "Traceback" not in p.stderr and "ModuleNotFoundError" not in p.stderr
