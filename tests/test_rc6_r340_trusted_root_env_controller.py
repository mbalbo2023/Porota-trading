"""Focused OFFHOST regressions for R340; no ROOT/SSH or Docker fixtures."""

from copy import deepcopy
import unittest
import hashlib
import json
from functools import partial
from unittest.mock import patch

import pytest

from scripts import rc6_r340_trusted_root_env_controller as controller
from scripts import rc6_r326_root_bundle_provenance as provenance

from scripts.rc6_r340_trusted_root_env_controller import (
    ControlHold,
    verify_authenticated_release,
)


MAIN = "a" * 40
MAIN_TREE = "b" * 40
CANDIDATE = "c" * 40
CANDIDATE_TREE = "d" * 40
REPO = "mbalbo2023/Porota-trading"
PREFIX = "/repos/" + REPO
EVENT = {
    "GITHUB_EVENT_NAME": "workflow_dispatch",
    "GITHUB_REPOSITORY": REPO,
    "GITHUB_REF": "refs/heads/main",
    "GITHUB_ACTOR": "mbalbo2023",
    "GITHUB_TRIGGERING_ACTOR": "mbalbo2023",
    "GITHUB_SHA": MAIN,
}


def authenticated_material_fixture(tmp_path, *, canonical_e=True, e_padding=0):
    from test_rc6_r252_trusted_receipt_bridge import (
        real_material_fixture, S, T, M, API, guard, real_r251_bytes)
    reader, payload, raw_e, boundary, d_zip, e_zip = real_material_fixture(
        tmp_path, canonical_e=canonical_e, e_padding=e_padding)
    reader.records[API + "/git/ref/heads/main"] = {
        "ref": "refs/heads/main", "object": {"type": "commit", "sha": M}}
    reader.records[API + "/git/commits/" + M] = {"sha": M, "tree": {"sha": "8" * 40}}
    blobs = dict(boundary.git_blobs)
    scripts_tree = "9" * 40
    def entry(path, kind, mode, sha):
        return {"path": path, "type": kind, "mode": mode, "sha": sha}
    reader.records[API + "/git/trees/" + T] = {
        "sha": T, "truncated": False, "tree": [
            entry("scripts", "tree", "040000", scripts_tree),
            *[entry(name, "blob", "100644", blobs[name]) for name in
              ("cf_intraday_scalping.py", "co_market_sessions_hf6.py")]]}
    reader.records[API + "/git/trees/" + scripts_tree] = {
        "sha": scripts_tree, "truncated": False, "tree": [
            entry(name.removeprefix("scripts/"), "blob", "100644", blobs[name])
            for name in blobs if name.startswith("scripts/")]}
    event = {**EVENT, "GITHUB_SHA": M}
    release = partial(guard.verify_trusted_release, byte_verifier=real_r251_bytes)
    return reader, event, release, payload, raw_e, d_zip, e_zip


def test_real_d_e_material_reaches_r326_and_only_final_hash_projection(tmp_path):
    reader, event, release, payload, raw_e, d_zip, e_zip = authenticated_material_fixture(tmp_path)
    from test_rc6_r252_trusted_receipt_bridge import D_ID, D_ATTEMPT, D_ART, E_ID, E_ATTEMPT, E_ART
    try:
        proof = verify_authenticated_release(event, checkout_sha=event["GITHUB_SHA"],
                    github=reader, trusted_release=release, include_material=True)
    except TypeError as exc:
        pytest.fail("R340 material integration is unavailable: " + str(exc))
    material = proof["source_material"]
    assert material["status"] == "FOUR_SOURCES_BOUND_OFFHOST"
    assert material["postcompletion_receipt_sha256"] == hashlib.sha256(raw_e).hexdigest()
    assert material.get("release") == {
        "run_id": D_ID, "run_attempt": D_ATTEMPT, "artifact_id": D_ART,
        "artifact_digest": "sha256:" + hashlib.sha256(d_zip.read_bytes()).hexdigest()}
    assert material.get("postcompletion") == {
        "run_id": E_ID, "run_attempt": E_ATTEMPT, "artifact_id": E_ART,
        "artifact_digest": "sha256:" + hashlib.sha256(e_zip.read_bytes()).hexdigest()}
    assert material["postcompletion"]["artifact_digest"] != "sha256:" + material["postcompletion_receipt_sha256"]
    assert material["image_id"] == json.loads(payload["porota-frozen-candidate.json"])["image_id"]
    assert set(material["sources"]) == {
        "scripts/rc6_oci_host_helper.py", "scripts/rc6_runtime_oci_custody.py",
        "cf_intraday_scalping.py", "co_market_sessions_hf6.py"}
    assert all(row["bytes"] > 0 and len(row["sha256"]) == 64 and len(row["git_blob_sha"]) == 40
               for row in material["sources"].values())
    assert reader.downloads == [402, 401]
    assert "bundle_bytes" not in json.dumps(proof)
    for key in ("owner_go_verified", "host_control_verified", "root_env_genesis_verified",
                "deploy_authorized", "real_orders_allowed", "runtime_verified"):
        assert proof[key] is False


@pytest.mark.parametrize("drift", [False, True])
def test_normal_verify_offhost_cli_consumes_material_or_redacts_drift(tmp_path, capsys, drift):
    from test_rc6_r252_trusted_receipt_bridge import real_r251_bytes
    from scripts import rc6_ci_release_required_guard as r252
    from scripts import rc6_r163_trusted_postrun_byte_audit as r163
    reader, event, _, _, _, _, _ = authenticated_material_fixture(tmp_path)
    if drift:
        path = PREFIX + "/git/ref/heads/main"
        reader.after_reads[path] = {"ref": "refs/heads/main", "object": {
            "type": "commit", "sha": "0" * 40}}
    with patch.dict(controller.os.environ, {**event, "GITHUB_TOKEN": "test-read-token"}, clear=True), \
         patch.object(controller, "_checkout_head", return_value=event["GITHUB_SHA"]), \
         patch.object(r163, "GitHubReadOnlyREST", return_value=reader), \
         patch.object(r252, "_load_trusted_module", return_value=real_r251_bytes):
        assert controller.main(["--verify-offhost"]) == (79 if drift else 0)
    output = capsys.readouterr().out
    if drift:
        assert "GITHUB_HEAD_CHANGED_DURING_AUTH" in output
        assert "source_material" not in output and "git_blob_sha" not in output
        assert "bundle_bytes" not in output
        return
    proof = json.loads(output)
    assert len(proof.get("source_material", {}).get("sources", {})) == 4


@pytest.mark.parametrize("mutation", ["blob", "truncated", "tree", "mode", "duplicate", "d_zip", "attempt"])
def test_material_rejects_git_or_artifact_mismatch(tmp_path, mutation):
    reader, event, release, _, _, d_zip, _ = authenticated_material_fixture(tmp_path)
    from test_rc6_r252_trusted_receipt_bridge import T, API, D_ID
    root = reader.records[API + "/git/trees/" + T]
    if mutation == "blob":
        root["tree"][1]["sha"] = "0" * 40
    elif mutation == "truncated":
        root["truncated"] = True
    elif mutation == "tree":
        root["sha"] = "0" * 40
    elif mutation == "mode":
        root["tree"][1]["mode"] = "120000"
    elif mutation == "duplicate":
        root["tree"].append(deepcopy(root["tree"][1]))
    elif mutation == "d_zip":
        d_zip.write_bytes(d_zip.read_bytes() + b"unbound bytes")
    else:
        reader.records[API + f"/actions/runs/{D_ID}"]["run_attempt"] += 1
    try:
        with pytest.raises(ControlHold):
            verify_authenticated_release(event, checkout_sha=event["GITHUB_SHA"],
                        github=reader, trusted_release=release, include_material=True)
    except TypeError as exc:
        pytest.fail("R340 material integration is unavailable: " + str(exc))


def test_final_source_recheck_precedes_r326_consumption(tmp_path):
    reader, event, release, _, _, _, _ = authenticated_material_fixture(tmp_path)
    from test_rc6_r252_trusted_receipt_bridge import API
    path = API + "/git/ref/heads/main"
    reader.after_reads[path] = {"ref": "refs/heads/main", "object": {"type": "commit", "sha": "0" * 40}}
    with patch.object(provenance, "extract_bound_sources", wraps=provenance.extract_bound_sources) as extract:
        try:
            with pytest.raises(ControlHold, match="GITHUB_HEAD_CHANGED_DURING_AUTH"):
                verify_authenticated_release(event, checkout_sha=event["GITHUB_SHA"],
                            github=reader, trusted_release=release, include_material=True)
        except TypeError as exc:
            pytest.fail("R340 material integration is unavailable: " + str(exc))
        assert extract.call_count == 0


@pytest.mark.parametrize("canonical_e,e_padding", [(False, 0), (True, 32768)])
def test_material_projection_requires_original_canonical_bounded_e(tmp_path, canonical_e, e_padding):
    reader, event, release, _, _, _, _ = authenticated_material_fixture(
        tmp_path, canonical_e=canonical_e, e_padding=e_padding)
    with pytest.raises(ControlHold, match="RELEASE_E_MATERIAL_FORMAT"):
        verify_authenticated_release(event, checkout_sha=event["GITHUB_SHA"],
                    github=reader, trusted_release=release, include_material=True)


class StubREST:
    def __init__(self):
        self.records = {
            PREFIX + "/git/ref/heads/main": {
                "ref": "refs/heads/main", "object": {"type": "commit", "sha": MAIN}
            },
            PREFIX + "/git/commits/" + MAIN: {
                "sha": MAIN, "tree": {"sha": MAIN_TREE}
            },
            PREFIX + "/git/commits/" + CANDIDATE: {
                "sha": CANDIDATE, "tree": {"sha": CANDIDATE_TREE}
            },
            PREFIX + "/pulls/1087": {
                "number": 1087, "state": "open", "merged": False,
                "draft": False,
                "head": {
                    "sha": CANDIDATE, "ref": "candidate-reviewed",
                    "repo": {"full_name": REPO},
                },
                "base": {
                    "ref": "deploy/rc6-pr69-isolated-20260915",
                    "sha": "e" * 40, "repo": {"full_name": REPO},
                },
            },
        }
        self.calls = []
        self.change_second_pr = False

    def get_json(self, path):
        self.calls.append(path)
        if self.change_second_pr and path.endswith("/pulls/1087") and self.calls.count(path) > 1:
            result = deepcopy(self.records[path])
            result["head"]["sha"] = "e" * 40
            return result
        return deepcopy(self.records[path])


def verified_r252(data, *, github):
    assert data == {
        "RC6_BRIDGE_REPOSITORY": REPO,
        "RC6_BRIDGE_MODE": "PR_CANDIDATE",
        "RC6_BRIDGE_CANDIDATE_SHA": CANDIDATE,
        "RC6_BRIDGE_CANDIDATE_TREE_SHA": CANDIDATE_TREE,
        "RC6_BRIDGE_PUBLISHER_MAIN_SHA": MAIN,
        "RC6_BRIDGE_E_RUN_ID": "",
        "RC6_BRIDGE_E_RUN_ATTEMPT": "",
        "RC6_BRIDGE_E_ARTIFACT_ID": "",
        "RC6_BRIDGE_PRODUCT_SHA": "",
        "RC6_BRIDGE_PRODUCT_REF": "",
    }
    assert isinstance(github, StubREST)
    return {
        "status": "TRUSTED_RELEASE_BYTES_BOUND",
        "mode": "PR_CANDIDATE",
        "candidate_sha": CANDIDATE,
        "candidate_tree_sha": CANDIDATE_TREE,
        "product_sha": "",
        "release_run_id": 111,
        "release_run_attempt": 1,
        "release_artifact_id": 222,
        "release_artifact_digest": "sha256:" + "f" * 64,
        "trusted_main_audit_run_id": 333,
        "trusted_main_audit_run_attempt": 1,
        "artifact_contents_verified_by_this_guard": True,
        "owner_go_verified": False,
        "runtime_verified": False,
        "automatic_go": False,
    }


class R340OffhostTests(unittest.TestCase):
    def setUp(self):
        self.rest = StubREST()
        self.event = dict(EVENT)

    def test_exact_trusted_release_accepted_only_as_offhost_evidence(self):
        proof = verify_authenticated_release(
            self.event, checkout_sha=MAIN, github=self.rest,
            trusted_release=verified_r252,
        )
        self.assertEqual(proof["status"], "OFFHOST_D_E_BYTES_AUTHENTICATED")
        self.assertEqual(proof["candidate_sha"], CANDIDATE)
        self.assertEqual(proof["publisher_main_sha"], MAIN)
        self.assertFalse(proof["owner_go_verified"])
        self.assertFalse(proof["host_control_verified"])
        self.assertFalse(proof["deploy_authorized"])
        self.assertFalse(proof["real_orders_allowed"])
        self.assertEqual(self.rest.calls.count(PREFIX + "/pulls/1087"), 2)

    def test_wrong_actor_or_rerun_actor_rejected(self):
        for field in ("GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR"):
            with self.subTest(field=field), self.assertRaises(ControlHold):
                ev = dict(self.event)
                ev[field] = "anybody"
                verify_authenticated_release(ev, checkout_sha=MAIN, github=self.rest,
                                             trusted_release=verified_r252)

    def test_workflow_branch_checkout_mismatch_rejected(self):
        for field, value in (("GITHUB_REF", "refs/heads/product"),
                             ("GITHUB_SHA", "0" * 40),
                             ("GITHUB_EVENT_NAME", "pull_request")):
            with self.subTest(field=field), self.assertRaises(ControlHold):
                ev = dict(self.event)
                ev[field] = value
                verify_authenticated_release(ev, checkout_sha=MAIN, github=self.rest,
                                             trusted_release=verified_r252)

    def test_main_must_still_point_to_exact_checkout(self):
        self.rest.records[PREFIX + "/git/ref/heads/main"]["object"]["sha"] = "0" * 40
        with self.assertRaises(ControlHold):
            verify_authenticated_release(self.event, checkout_sha=MAIN,
                                         github=self.rest, trusted_release=verified_r252)

    def test_pr_draft_or_other_candidate_rejected(self):
        for field, value in (("draft", True), ("state", "closed")):
            with self.subTest(field=field), self.assertRaises(ControlHold):
                rest = StubREST()
                rest.records[PREFIX + "/pulls/1087"][field] = value
                verify_authenticated_release(self.event, checkout_sha=MAIN,
                                             github=rest, trusted_release=verified_r252)

    def test_initial_head_and_base_repositories_must_be_exact_mappings(self):
        invalid = ("foreign", "missing", None, "malformed", [], {},
                   {"full_name": None}, {"full_name": "attacker/fork"})
        for side in ("head", "base"):
            for value in invalid:
                with self.subTest(side=side, repo=value):
                    rest = StubREST()
                    part = rest.records[PREFIX + "/pulls/1087"][side]
                    if value == "missing":
                        del part["repo"]
                    else:
                        part["repo"] = value
                    called = []
                    def release(data, *, github):
                        called.append(True)
                        return verified_r252(data, github=github)
                    with self.assertRaisesRegex(ControlHold,
                                                "SINGLE_CANDIDATE_PR_NOT_READY"):
                        verify_authenticated_release(
                            self.event, checkout_sha=MAIN, github=rest,
                            trusted_release=release)
                    self.assertFalse(called)

    def test_head_drift_between_authentication_and_receipt_rejected(self):
        self.rest.change_second_pr = True
        with self.assertRaises(ControlHold):
            verify_authenticated_release(self.event, checkout_sha=MAIN,
                                         github=self.rest, trusted_release=verified_r252)

    def test_volatile_rest_metadata_during_byte_authentication_is_ignored(self):
        # A PR's mergeable and updated_at can change without changing its
        # authenticated Git identity; commit verification metadata can too.
        def metadata_only(data, *, github):
            proof = verified_r252(data, github=github)
            github.records[PREFIX + "/pulls/1087"]["mergeable"] = False
            github.records[PREFIX + "/pulls/1087"]["updated_at"] = "2026-11-13T23:59:59Z"
            github.records[PREFIX + "/git/ref/heads/main"]["url"] = "metadata-only"
            github.records[PREFIX + "/git/commits/" + MAIN]["verification"] = {
                "verified": True,
            }
            return proof

        result = verify_authenticated_release(
            self.event, checkout_sha=MAIN, github=self.rest,
            trusted_release=metadata_only,
        )
        self.assertEqual(result["status"], "OFFHOST_D_E_BYTES_AUTHENTICATED")

    def test_authority_identity_drift_during_byte_authentication_is_rejected(self):
        # A change to any selected source, unlike REST presentation metadata,
        # must still stop the gate even when the R252 proof was valid.
        drift_cases = (
            ("head_sha", PREFIX + "/pulls/1087", ("head", "sha"), "0" * 40),
            ("head_repo", PREFIX + "/pulls/1087", ("head", "repo", "full_name"), "attacker/fork"),
            ("base_repo", PREFIX + "/pulls/1087", ("base", "repo", "full_name"), "attacker/fork"),
            ("base_sha", PREFIX + "/pulls/1087", ("base", "sha"), "0" * 40),
            ("base_ref", PREFIX + "/pulls/1087", ("base", "ref"), "wrong-product"),
            ("draft", PREFIX + "/pulls/1087", ("draft",), True),
            ("state", PREFIX + "/pulls/1087", ("state",), "closed"),
            ("main_ref", PREFIX + "/git/ref/heads/main", ("object", "sha"), "0" * 40),
            ("main_tree", PREFIX + "/git/commits/" + MAIN, ("tree", "sha"), "0" * 40),
            ("candidate_tree", PREFIX + "/git/commits/" + CANDIDATE,
             ("tree", "sha"), "0" * 40),
        )
        for label, source, keys, replacement in drift_cases:
            with self.subTest(drift=label):
                rest = StubREST()

                def change_identity(data, *, github):
                    proof = verified_r252(data, github=github)
                    target = github.records[source]
                    for key in keys[:-1]:
                        target = target[key]
                    target[keys[-1]] = replacement
                    return proof

                with self.assertRaisesRegex(ControlHold,
                                            "GITHUB_HEAD_CHANGED_DURING_AUTH"):
                    verify_authenticated_release(
                        self.event, checkout_sha=MAIN, github=rest,
                        trusted_release=change_identity,
                    )

    def test_r252_receipt_must_prove_downloaded_bytes(self):
        def liar(data, *, github):
            proof = verified_r252(data, github=github)
            proof["artifact_contents_verified_by_this_guard"] = False
            return proof

        with self.assertRaises(ControlHold):
            verify_authenticated_release(self.event, checkout_sha=MAIN,
                                         github=self.rest, trusted_release=liar)

    def test_r252_not_allowed_to_assert_owner_or_runtime_go(self):
        for field in ("owner_go_verified", "runtime_verified", "automatic_go"):
            with self.subTest(field=field), self.assertRaises(ControlHold):
                def liar(data, *, github):
                    proof = verified_r252(data, github=github)
                    proof[field] = True
                    return proof
                verify_authenticated_release(self.event, checkout_sha=MAIN,
                                             github=self.rest, trusted_release=liar)

    def test_missing_or_ambiguous_release_e_is_hard_hold(self):
        def red(data, *, github):
            raise ValueError("cannot select a unique E")

        with self.assertRaises(ControlHold):
            verify_authenticated_release(self.event, checkout_sha=MAIN,
                                         github=self.rest, trusted_release=red)

    def test_no_rest_adapter_is_hard_hold(self):
        with self.assertRaises(ControlHold):
            verify_authenticated_release(self.event, checkout_sha=MAIN,
                                         github=object(), trusted_release=verified_r252)


if __name__ == "__main__":
    unittest.main()
