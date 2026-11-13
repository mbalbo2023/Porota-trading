"""Focused OFFHOST regressions for R340; no ROOT/SSH or Docker fixtures."""

from copy import deepcopy
import unittest

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
                "head": {"sha": CANDIDATE},
                "base": {"ref": "deploy/rc6-pr69-isolated-20260915"},
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

    def test_head_drift_between_authentication_and_receipt_rejected(self):
        self.rest.change_second_pr = True
        with self.assertRaises(ControlHold):
            verify_authenticated_release(self.event, checkout_sha=MAIN,
                                         github=self.rest, trusted_release=verified_r252)

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
