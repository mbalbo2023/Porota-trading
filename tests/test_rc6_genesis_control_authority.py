"""R367 control-plane authority with independent owner decisions and Actions artifacts."""
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import importlib
import importlib.util
import io
import json
import unittest
from unittest.mock import patch
import zipfile

from scripts import rc6_ci_release_required_guard as r252
from scripts import rc6_r305_manifest_witness_offhost as witness
from scripts import rc6_r305_root_pin_transition_guard as r305
from scripts import rc6_r321_root_install_actions as r321


MAIN, MT, CAND, TREE = (c * 40 for c in "abcd")
HOST, NONCE = "e" * 64, "1" * 32
PROPOSAL, COMMIT, GENERATION = "a" * 32, "2" * 64, "3" * 64
L1, G1, L2, G2, L3, PIN, INSTALL, ENV = range(7100000001, 7100000009)
PREP_RUN, POST_RUN, W_RUN, D_RUN, E_RUN = range(901, 906)
W_ART, D_ART, E_ART = 970, 971, 972
PAPER = {"paper_mode": "PRODUCTION_PAPER", "real_orders_allowed": False}
BASE = {"trusted_main_sha": MAIN, "candidate_sha": CAND, "candidate_tree": TREE,
        "host_identity_source": "PROTECTED_DO_PINS", "host_identity_sha256": HOST}
SCOPE = {**BASE, "nonce": NONCE}
RUNFLOW = ".github/workflows/rc6-r321-root-install-approved.yml"
PREP_MARK = "RC6_R367_PREPARE_GO_V1="
ENV_MARK = "RC6_R367_ENV_GO_V1="
INSTALL_MARK = "RC6_R367_ROOT_INSTALL_GO_V2="
LEASE_MARK = "RC6_R367_GENESIS_DEPLOY_OWNER_V1="
REPO = r252.REPO
API = "https://api.github.com/repos/" + REPO


def enc(doc):
    return (json.dumps(doc, sort_keys=True, separators=(",", ":")) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def zipped(name, raw):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr(name, raw)
    return target.getvalue()


def stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def row(ident, issue, marker, doc, date):
    return {"id": ident, "issue_url": f"{API}/issues/{issue}", "user": {
        "id": 151845499, "login": "mbalbo2023"}, "author_association": "OWNER",
        "created_at": date, "updated_at": date, "body": marker + enc(doc).decode().rstrip("\n")
        + ("\n" if marker == r305.OWNER_GO_MARKER else "")}


class ControlTest(unittest.TestCase):
    def setUp(self):
        module = "scripts.rc6_genesis_control_authority"
        self.assertIsNotNone(importlib.util.find_spec(module), "no authenticated R367 Actions entry point")
        self.auth = importlib.import_module(module)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.start = stamp(self.now - timedelta(minutes=1))
        self.end = stamp(self.now + timedelta(minutes=8))
        self.long_end = stamp(self.now + timedelta(minutes=32))
        self.env = {"GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": MAIN, "GITHUB_ACTOR": "mbalbo2023",
            "GITHUB_TRIGGERING_ACTOR": "mbalbo2023", "GITHUB_ACTOR_ID": "151845499",
            "GITHUB_TOKEN": "opaque", "DO_USER": "porotaadmin", "RC6_EXPECTED_DO_ID": "1234567",
            "RC6_EXPECTED_DO_REGION": "nyc1", "GITHUB_RUN_ID": str(PREP_RUN),
            "GITHUB_RUN_ATTEMPT": "1", "RC6_R321_OPERATION": "PREPARE_ENV",
            "RC6_R367_PREPARE_PHASE": "INITIAL", "RC6_R367_LEASE_ID": str(L1),
            "RC6_R367_PREPARE_GO_ID": str(G1)}
        self.docs = {"/git/ref/heads/main": {"ref": "refs/heads/main", "object": {"type": "commit", "sha": MAIN}},
            "/git/commits/" + MAIN: {"sha": MAIN, "tree": {"sha": MT}},
            "/git/commits/" + CAND: {"sha": CAND, "tree": {"sha": TREE}},
            "/pulls/1087": {"number": 1087, "draft": False, "state": "open", "merged": False,
                 "head": {"sha": CAND, "ref": r252.CANDIDATE_BRANCH, "repo": {"full_name": REPO}},
                 "base": {"ref": r252.PRODUCT, "repo": {"full_name": REPO}}}}
        self.docs["/issues/1093"] = {"number":1093,"url":API+"/issues/1093",
            "comments":232,"updated_at":self.start}
        self.zips, self.calls = {}, []
        self.mutate = lambda path, doc: doc
        github = self
        self.patch("legacy._GitHub", return_value=github)
        self.patch("_WitnessReader.get_zip", side_effect=self.get_zip)
        self.patch("legacy.host_identity_sha256", return_value=HOST)
        self.patch("r340._checkout_head", return_value=MAIN)
        # The real cross-operation ledger has its own historical/pagination tests.
        self.real_ledger = self.auth.legacy.verify_global_lease
        self.ledger = self.patch("legacy.verify_global_lease", return_value=(5, self.start))
        self.real_version = self.auth.legacy._ledger_version
        self.ledger_version = self.patch("legacy._ledger_version", return_value=(5, self.start))
        self.lease(L1, "PREPARE_ENV")
        self.initial = {**SCOPE, **PAPER, "operation": "PREPARE_ENV", "phase": "INITIAL",
            "lease_comment_id": L1, "expires_at": self.end}
        self.comment(G1, PREP_MARK, self.initial)

    def patch(self, name, **kwargs):
        item = patch("scripts.rc6_genesis_control_authority." + name, **kwargs)
        self.addCleanup(item.stop)
        return item.start()

    def read(self, path, limit=65536):
        self.calls.append(path)
        return copy.deepcopy(self.mutate(path, self.docs[path]))

    def get_zip(self, ident):
        return self.zips[ident]

    def comment(self, ident, marker, doc):
        self.docs[f"/issues/comments/{ident}"] = row(ident, 1093, marker, doc, self.start)

    def lease(self, ident, operation):
        self.docs[f"/issues/comments/{ident}"] = row(ident, 980, LEASE_MARK, {
            **SCOPE, "operation": operation, "status": "ACTIVE"}, self.start)

    def action(self, ident, path, name, event="workflow_dispatch"):
        record = {"id": ident, "run_attempt": 1, "status": "completed", "conclusion": "success",
            "event": event, "path": path, "name": name, "head_sha": MAIN, "head_branch": "main",
            "repository": {"full_name": REPO}, "head_repository": {"full_name": REPO},
            "run_started_at": self.start, "updated_at": self.start,
            "actor": {"login": "mbalbo2023", "id": 151845499},
            "triggering_actor": {"login": "mbalbo2023", "id": 151845499}}
        self.docs[f"/actions/runs/{ident}"] = record
        self.docs[f"/actions/runs/{ident}/attempts/1"] = record.copy()

    def artifact(self, rid, ident, name, sha, size=456):
        art = {"id": ident, "name": name, "digest": sha, "size_in_bytes": size,
            "expired": False, "created_at": self.start, "workflow_run": {"id": rid, "head_sha": MAIN}}
        self.docs[f"/actions/artifacts/{ident}"] = art
        self.docs[f"/actions/runs/{rid}/artifacts?per_page=100"] = {"total_count": 1, "artifacts": [art]}

    def apply_inputs(self):
        from scripts import rc6_r163_artifact_byte_verifier as rv
        self.env.update(RC6_R321_OPERATION="APPLY_GENESIS", RC6_R367_LEASE_ID=str(L3),
            RC6_R367_PIN_GO_ID=str(PIN), RC6_R367_INSTALL_GO_ID=str(INSTALL),
            RC6_R367_ENV_GO_ID=str(ENV), GITHUB_RUN_ID="955")
        for rid in (PREP_RUN, POST_RUN):
            self.action(rid, RUNFLOW, "RC6 R321 ROOT evidence and file observation")
        dsha, esha = "sha256:" + "4"*64, "sha256:" + "5"*64
        self.action(D_RUN, r252.D_PATH, r252.D_NAME)
        self.action(E_RUN, r252.E_PATH, r252.E_NAME, "workflow_run")
        self.artifact(D_RUN, D_ART, f"rc6-r251-release-{D_RUN}-1-{CAND}", dsha)
        self.artifact(E_RUN, E_ART, f"rc6-r251-postcompletion-{D_RUN}-1-{CAND}", esha)
        receipt = {"schema_version": 1, "status": "RELEASE_BYTES_VERIFIED_POSTCOMPLETION",
            "repository": REPO, "candidate_sha": CAND, "candidate_tree_sha": TREE,
            "publisher_workflow_path": rv.RELEASE_WORKFLOW, "publisher_run_id": D_RUN,
            "publisher_run_attempt": 1, "publisher_main_sha": MAIN, "release_artifact_id": D_ART,
            "release_artifact_name": f"rc6-r251-release-{D_RUN}-1-{CAND}",
            "release_artifact_digest": dsha, "release_artifact_size_bytes": 456,
            "file_count": len(rv.RELEASE_PAYLOAD_FILES), "total_size_bytes": 99,
            "release_manifest_verified": True, "r144_release_go": False,
            "runtime_verified": False, "real_orders_physical_verified": False}
        self.assertEqual(set(receipt), rv.POSTCOMPLETION_RECEIPT_KEYS)
        eraw = enc(receipt)
        self.release = {"status": "OFFHOST_D_E_BYTES_AUTHENTICATED", "publisher_main_sha": MAIN,
            "publisher_main_tree_sha": MT, "candidate_sha": CAND, "candidate_tree_sha": TREE,
            "artifact_contents_verified_by_this_guard": True, "release_e_receipt_raw": eraw,
            "release_run_id": D_RUN, "release_artifact_id": D_ART, "release_artifact_digest": dsha,
            "trusted_main_audit_run_id": E_RUN, "source_material": {"status": "FOUR_SOURCES_BOUND_OFFHOST",
            "release": {"run_id": D_RUN, "run_attempt": 1, "artifact_id": D_ART, "artifact_digest": dsha},
            "postcompletion": {"run_id": E_RUN, "run_attempt": 1, "artifact_id": E_ART, "artifact_digest": esha},
            "postcompletion_receipt_sha256": digest(eraw)}}
        self.bundle = {f"release_{prefix}_{suffix}": (str(data[key]) if suffix.startswith("run_") else data[key])
            for prefix, keyname in (("d", "release"), ("e", "postcompletion"))
            for data in [self.release["source_material"][keyname]]
            for suffix, key in (("run_id", "run_id"), ("run_attempt", "run_attempt"), ("artifact_id", "artifact_id"),
                               ("artifact_digest", "artifact_digest"))}
        self.bundle["release_e_receipt_sha256"] = digest(eraw)
        self.origin = {"prepare_go_id": G1, "prepare_run_id": str(PREP_RUN), "prepare_run_attempt": "1",
            "proposal_id": PROPOSAL, "commitment": COMMIT}
        self.lease(L2, "PREPARE_ENV")
        self.post = {**self.initial, **self.origin, **self.bundle,
                     "phase": "POST_D_E", "lease_comment_id": L2, "approval_expires_at": self.long_end}
        self.comment(G2, PREP_MARK, self.post)
        self.lease(L3, "APPLY_GENESIS")
        manifest = enc({"schema": "RC6_ROOT_INSTALL_MANIFEST_V1", "candidate_sha": CAND,
                      "candidate_tree": TREE, **PAPER, "files": {k: "6"*64 for k in r305._ROLES}})
        mh = digest(manifest)
        wraw = witness.build_witness(manifest, expected_sha256=mh, main_sha=MAIN, main_tree=MT,
            run_id=str(W_RUN), run_attempt="1", candidate_tree_getter=lambda _sha: TREE)
        archive = zipped(witness.ARTIFACT_FILE, wraw)
        self.zips[W_ART] = archive
        self.action(W_RUN, ".github/workflows/rc6-r305-root-manifest-witness.yml", "RC6 R305 Root manifest witness")
        self.artifact(W_RUN, W_ART, "rc6-r305-root-manifest-digest-witness", "sha256:" + digest(archive), len(archive))
        link = {"root_manifest_sha256": mh, "witness_artifact_sha256": digest(wraw),
              "witness_run_id": str(W_RUN), "witness_run_attempt": "1"}
        self.pin = {"operation": "RC6_ROOT_PIN_NEW", "issue": 1093, "trusted_main_sha": MAIN,
            "candidate_sha": CAND, "candidate_tree": TREE, "manifest_sha256": mh,
            "witness_artifact_sha256": digest(wraw), "witness_run_id": str(W_RUN), "witness_run_attempt": "1",
            **PAPER, "host_control_verified": False, "host_identity_source": "PROTECTED_DO_PINS",
            "expires_at": self.long_end}
        self.comment(PIN, r305.OWNER_GO_MARKER, self.pin)
        self.install = {**SCOPE, **PAPER, **self.origin, **self.bundle, **link,
            "operation": "RC6_ROOT_INSTALL_NEW_FILES_ONLY", "issue": 1093, "trusted_main_tree": MT,
            "host_control_verified": False, "generation_commitment": GENERATION,
            "lease_comment_id": L3, "r305_pin_owner_go_id": PIN, "expires_at": self.long_end}
        self.comment(INSTALL, INSTALL_MARK, self.install)
        self.envgo = {**SCOPE, **PAPER, **self.origin, **link, "generation_commitment": GENERATION,
            "operation": "SEAL_ENV_PAIR", "lease_comment_id": L3, "pin_go_id": PIN, "install_go_id": INSTALL,
            "post_prepare_go_id": G2, "post_prepare_run_id": str(POST_RUN), "post_prepare_run_attempt": "1",
            "expires_at": self.end}
        self.comment(ENV, ENV_MARK, self.envgo)
        return wraw

    def test_initial_prepare_authenticates_branch_owner_and_global_scope(self):
        record = self.auth.verify_prepare(self.env)
        self.assertEqual(record["binding"], {"trusted_main_sha": MAIN, "candidate_sha": CAND,
            "candidate_tree": TREE, "host_identity_sha256": HOST, "run_id": str(PREP_RUN), "run_attempt": "1",
            "prepare_go_id": G1, "prepare_nonce": NONCE})
        self.assertEqual(record["prepare_go_id"], G1)
        self.assertEqual(record["nonce"], NONCE)
        self.assertEqual(self.ledger.call_args.kwargs["scope"], {**SCOPE, "operation": "PREPARE_ENV"})

    def test_prepare_rejects_foreign_owner_and_ledger_revocation(self):
        self.docs[f"/issues/comments/{G1}"]["user"]["id"] += 1
        with self.assertRaises(self.auth.ControlHold):
            self.auth.verify_prepare(self.env)
        self.comment(G1, PREP_MARK, self.initial)
        self.ledger.side_effect = self.auth.ControlHold("LEASE_CONFLICT")
        with self.assertRaises(self.auth.ControlHold):
            self.auth.verify_prepare(self.env)

    def test_apply_requires_all_three_independent_decisions_and_checks_both_witness_digests(self):
        raw = self.apply_inputs()
        got = self.auth.verify_apply(self.env, self.release)
        self.assertEqual(got["witness_raw"], raw)
        self.assertEqual(got["generation_commitment"], GENERATION)
        self.assertNotIn("generation", got)
        self.assertEqual(got["binding"]["run_id"], str(PREP_RUN))
        self.assertEqual(got["env_go_id"], ENV)
        self.assertEqual(got["root_manifest_sha256"], self.envgo["root_manifest_sha256"])
        self.assertNotEqual(got["witness_artifact_sha256"], digest(self.zips[W_ART]))

    def test_post_prepare_reuses_original_binding_but_requires_fresh_own_go(self):
        self.apply_inputs()
        self.env.update(RC6_R321_OPERATION="PREPARE_ENV", RC6_R367_PREPARE_PHASE="POST_D_E",
            RC6_R367_LEASE_ID=str(L2), RC6_R367_PREPARE_GO_ID=str(G2), GITHUB_RUN_ID=str(POST_RUN))
        self.assertEqual(self.auth.verify_prepare(self.env, self.release)["binding"]["run_id"], str(PREP_RUN))
        self.post["commitment"] = "invalid"
        self.comment(G2, PREP_MARK, self.post)
        with self.assertRaises(self.auth.ControlHold):
            self.auth.verify_prepare(self.env, self.release)

    def test_apply_rejects_host_rebind_confused_go_artifact_or_edited_proposal(self):
        self.apply_inputs()
        mutate = (
            (INSTALL, INSTALL_MARK, self.install, "host_identity_sha256", "9"*64),
            (PIN, r305.OWNER_GO_MARKER, self.pin, "operation", "RC6_ROOT_PIN_RESUME"),
            (ENV, ENV_MARK, self.envgo, "install_go_id", PIN),
            (ENV, ENV_MARK, self.envgo, "generation_commitment", "4"*64),
            (G2, PREP_MARK, self.post, "proposal_id", "f"*32),
            (INSTALL, INSTALL_MARK, self.install, "witness_artifact_sha256", digest(self.zips[W_ART])),
            (INSTALL, INSTALL_MARK, self.install, "release_e_receipt_sha256", "e"*64),
            (INSTALL, INSTALL_MARK, self.install, "generation", GENERATION[: 32]))
        for ident, marker, original, key, value in mutate:
            with self.subTest(key=key):
                self.comment(ident, marker, {**original, key: value})
                with self.assertRaises(self.auth.ControlHold):
                    self.auth.verify_apply(self.env, self.release)
                self.comment(ident, marker, original)
        self.zips[W_ART] = zipped(witness.ARTIFACT_FILE, b"forged")
        with self.assertRaises(self.auth.ControlHold):
            self.auth.verify_apply(self.env, self.release)

    def test_operator_selectors_cannot_redirect_a_frozen_prepare_or_apply(self):
        self.apply_inputs()
        for key, bad in (("RC6_R367_PREPARE_ID", "b"*32), ("GITHUB_ACTOR_ID", "123"),
                        ("GITHUB_TRIGGERING_ACTOR", "other"), ("RC6_R367_LEASE_ID", str(L1))):
            with self.subTest(key=key), patch.dict(self.env, {key: bad}):
                with self.assertRaises(self.auth.ControlHold):
                    self.auth.verify_apply(self.env, self.release)
        with patch.dict(self.env, {"RC6_R367_PREPARE_PHASE": "POST_D_E", "RC6_R321_OPERATION": "PREPARE_ENV",
                            "RC6_R367_LEASE_ID": str(L2), "RC6_R367_PREPARE_GO_ID": str(G2),
                            "RC6_R367_PREPARE_ID": "b"*32}):
            with self.assertRaises(self.auth.ControlHold):
                self.auth.verify_prepare(self.env, self.release)

    def test_expiry_after_slow_reads_and_order_of_go_are_checked_at_the_last_boundary(self):
        self.apply_inputs()
        with patch.object(self.auth, "_now", side_effect=(self.now, self.now + timedelta(minutes=15))):
            with self.assertRaises(self.auth.ControlHold):
                self.auth.verify_apply(self.env, self.release)
        self.envgo["expires_at"] = self.long_end
        self.comment(ENV, ENV_MARK, self.envgo)
        with self.assertRaises(self.auth.ControlHold):
            self.auth.verify_apply(self.env, self.release)

    def test_historical_prepare_cannot_refer_to_a_run_before_its_go_or_on_another_attempt(self):
        self.apply_inputs()
        for rid in (PREP_RUN, POST_RUN):
            with self.subTest(rid=rid):
                path = f"/actions/runs/{rid}"
                when = stamp(self.now-timedelta(hours=3))
                with patch.dict(self.docs[path], {"run_started_at": when, "updated_at": when}), \
                     patch.dict(self.docs[path + "/attempts/1"], {"run_started_at": when, "updated_at": when}):
                    with self.assertRaises(self.auth.ControlHold):
                        self.auth.verify_apply(self.env, self.release)

    def test_wrong_witness_zip_json_ambiguity_or_live_release_attempt_is_held(self):
        self.apply_inputs()
        for rid, aid in ((D_RUN, D_ART), (E_RUN, E_ART), (W_RUN, W_ART)):
            path = f"/actions/runs/{rid}/artifacts?per_page=100"
            with self.subTest(rid=rid), patch.dict(self.docs[path], {"total_count": 2}):
                with self.assertRaises(self.auth.ControlHold):
                    self.auth.verify_apply(self.env, self.release)
            with patch.dict(self.docs[f"/actions/artifacts/{aid}"], {"expired": True}):
                with self.assertRaises(self.auth.ControlHold):
                    self.auth.verify_apply(self.env, self.release)

    def test_nonce_cannot_change_between_lease_and_final_owner_decisions(self):
        self.apply_inputs()
        self.envgo["nonce"] = "8"*32
        self.comment(ENV, ENV_MARK, self.envgo)
        with self.assertRaises(self.auth.ControlHold):
            self.auth.verify_apply(self.env, self.release)

    def test_real_host_witness_reader_limits_the_http_body_before_buffering(self):
        self.apply_inputs()
        base = self.auth._WitnessReader
        inst = base("test-token")
        sizes = []
        class Response(io.BytesIO):
            status = 200
            def read(self, size=-1):
                sizes.append(size)
                return super().read(size)
        from scripts import rc6_r163_trusted_postrun_byte_audit as r163
        with patch.object(inst._opener, "open", return_value=Response(b"x"*20000)):
            with self.assertRaises(r163.PostrunEvidenceRed):
                r163.GitHubReadOnlyREST.get_zip(inst, W_ART)
        self.assertEqual(sizes, [16385])

    def test_later_phases_use_initial_go_nonce_even_when_apply_has_a_new_nonce(self):
        self.apply_inputs()
        old = "7"*32
        self.comment(G1, PREP_MARK, {**self.initial, "nonce": old})
        reply = self.auth.verify_apply(self.env, self.release)
        self.assertEqual(reply["binding"]["prepare_go_id"], G1)
        self.assertEqual(reply["binding"]["prepare_nonce"], old)
        self.assertEqual(reply["nonce"], NONCE)

    def test_same_validator_reads_the_real_shared_ledger_and_denies_later_release(self):
        lease = self.docs[f"/issues/comments/{L1}"]
        cutoff = self.auth.legacy.HISTORY_CUTOFF
        base_comment = row(cutoff, 980, "", {}, self.start)
        base_comment["body"] = "Fin de reconciliación."
        rows = [base_comment, lease]
        response = {"id": 6216186179, "number": 980, "url": API + "/issues/980", "comments": len(rows), "updated_at": self.start}
        self.docs["/issues/980"] = response
        self.docs["/issues/980/comments?per_page=100&page=1"] = rows
        with patch.object(self.auth.legacy, "LEGACY_EVENTS", {}):
            self.ledger.side_effect = self.real_ledger
            self.ledger_version.side_effect = self.real_version
            self.assertEqual(self.auth.verify_prepare(self.env)["lease_id"], L1)
            release = row(L1 + 44, 980, LEASE_MARK, {"status": "RELEASED", "lease_comment_id": L1}, self.start)
            rows.append(release)
            response["comments"] = len(rows)
            with self.assertRaises(self.auth.ControlHold):
                self.auth.verify_prepare(self.env)

    def test_late_current_main_and_candidate_drift_do_not_acknowledge_a_mutation(self):
        self.apply_inputs()
        counter = 0
        def swap(path, doc):
            nonlocal counter
            if path == "/pulls/1087":
                counter+=1
                if counter==2:
                    doc = copy.deepcopy(doc)
                    doc["head"]["sha"] = "0"*40
            return doc
        self.mutate = swap
        with self.assertRaises(self.auth.ControlHold):
            self.auth.verify_apply(self.env, self.release, initial=False)

    def test_signed_host_versions_are_sampled_around_go_and_ledger_reads(self):
        got = self.auth.verify_prepare(self.env)
        self.assertEqual(got["public_versions"],{
            "980":{"comments":5,"updated_at":self.start},
            "1093":{"comments":232,"updated_at":self.start}})
        reads=0
        def invalidate(path,doc):
            nonlocal reads
            if path=="/issues/1093":
                reads+=1
                if reads>1:
                    return {**doc,"comments":233}
            return doc
        self.mutate=invalidate
        with self.assertRaises(self.auth.ControlHold):
            self.auth.verify_prepare(self.env)
