"""Installed R367 entrypoint and the signed-to-private transaction boundary."""
import base64
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

if __name__ == "__main__" and "--run-isolated" in sys.argv:
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parent)]

from scripts import rc6_genesis_custodian as custody
from scripts import rc6_genesis_host_transaction as host
from scripts import rc6_r326_root_bundle_provenance as r326


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 14, 20, tzinfo=timezone.utc)
MAIN = "1" * 40
BIND = {"trusted_main_sha": MAIN, "candidate_sha": "a"*40, "candidate_tree": "b"*40,
        "host_identity_sha256": "7"*64, "run_id": "901", "run_attempt": "1",
        "prepare_go_id": 35, "prepare_nonce": "2"*32}
ORIGIN = {"binding": BIND, "lease_id": 34, "prepare_go_id": 35, "nonce": "2"*32,
          "issued_at": "2026-11-14T19:59:50Z", "expires_at": "2026-11-14T20:03:00Z",
          "actor_id": "151845499", "dispatch_run_id": "901", "dispatch_run_attempt": "1",
          "host_identity_token": "private.credential.fixture", "custodian_sha256": "8"*64}


def _wire(step, auth, release=None):
    return custody._json({"schema": "RC6_R367_HOST_REQUEST_V1",
                          "operation": "PREPARE_ENV" if step in ("INITIAL", "POST_D_E") else "APPLY_GENESIS",
                          "step": step, "authority": auth, "release": release})


def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _release():
    # R326 and R321 fixtures use the actual production shapes. This code exercises
    # the same four-source handoff as R340 without calling GitHub.
    from test_rc6_r326_root_bundle_provenance import fixture
    from test_rc6_r321_root_install_actions import install_go_fixture
    helper = (ROOT / "scripts/rc6_oci_host_helper.py").read_bytes()
    boundary, bundle, outer = fixture(helper)
    sources = r326.extract_bound_sources(boundary, bundle, outer)
    records = json.loads(outer)["files"]
    boundary = replace(boundary, source_sha256=tuple((r["path"], r["sha256"]) for r in records))
    d = dict(run_id=boundary.release_run_id, run_attempt=boundary.release_run_attempt,
             artifact_id=boundary.release_artifact_id, artifact_digest=boundary.artifact_digest)
    e = dict(run_id=705, run_attempt=2, artifact_id=706, artifact_digest="sha256:" + "8" * 64)
    # Existing R321 fixture independently specifies the complete audited E schema.
    fixture_values = install_go_fixture()
    original = next(item for item in fixture_values if type(item) is bytes and
                    b'"status":"RELEASE_BYTES_VERIFIED_POSTCOMPLETION"' in item)
    eraw = custody._json(dict(json.loads(original), candidate_sha=boundary.candidate_sha,
        candidate_tree_sha=boundary.candidate_tree, publisher_main_sha=MAIN,
        publisher_run_id=d["run_id"], publisher_run_attempt=d["run_attempt"],
        release_artifact_id=d["artifact_id"], release_artifact_digest=d["artifact_digest"],
        release_artifact_name="rc6-r251-release-101-1-" + boundary.candidate_sha))
    material = dict(status="FOUR_SOURCES_BOUND_OFFHOST", image_id=boundary.image_id, release=d,
                    postcompletion=e, postcompletion_receipt_sha256=custody._digest(eraw),
                    sources={row["path"]: {"sha256": row["sha256"], "bytes": row["bytes"],
                                          "git_blob_sha": dict(boundary.git_blobs)[row["path"]]} for row in records})
    receipt = dict(status="OFFHOST_D_E_BYTES_AUTHENTICATED", publisher_main_sha=MAIN,
        publisher_main_tree_sha="3"*40, candidate_sha=boundary.candidate_sha, candidate_tree_sha=boundary.candidate_tree,
        release_run_id=d["run_id"], release_artifact_id=d["artifact_id"], release_artifact_digest=d["artifact_digest"],
        trusted_main_audit_run_id=e["run_id"], artifact_contents_verified_by_this_guard=True, owner_go_verified=False,
        host_control_verified=False, root_env_genesis_verified=False, deploy_authorized=False,
        real_orders_allowed=False, runtime_verified=False, source_material=material)
    return dict(boundary=asdict(boundary), source_b64={role: _b64(raw) for role, raw in sources.items()},
                postcompletion_receipt_b64=_b64(eraw), receipt=receipt)


class HostTransactionTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for target, name, value in ((custody, "_FS_ROOT", str(self.root)), (custody, "_ROOT_UID", os.getuid()),
            (custody, "_ROOT_GID", os.getgid()), (custody.r337, "_ROOT_UID", os.getuid()),
            (custody, "_daemon_id", lambda: "private-fixture-daemon"), (custody, "_now", lambda: NOW)):
            self.stack.enter_context(patch.object(target, name, value))
        legacy = self.root / custody.r337.LEGACY_DIRECTORY.lstrip("/")
        legacy.mkdir(parents=True)
        (self.root / custody._VAULT.lstrip("/")).mkdir(parents=True, mode=0o700)
        for role, content in (("dashboard", b"ENVIRONMENT=PRODUCTION\nDASHBOARD_PASSWORD=fixture-opaque\n"),
                              ("observer", b"PAPER_ACTIVE_SYMBOL_LIMIT=6\n")):
            p = legacy / custody.r337._LEGACY_NAMES[role]
            p.write_bytes(content)
            p.chmod(0o600)
        def read(path, *, expected_mode):
            leaf = self.root / path.lstrip("/")
            self.assertEqual(stat.S_IMODE(leaf.stat().st_mode), expected_mode)
            return leaf.read_bytes()
        self.stack.enter_context(patch.object(custody.r271, "_read_root_owned", read))
        self.authorize = Mock(return_value=True)
        self.identity = ModuleType("scripts.rc6_genesis_host_identity")
        self.identity.signed_authority = Mock(return_value=self.authorize)
        self.stack.enter_context(patch.dict(sys.modules, {"scripts.rc6_genesis_host_identity": self.identity}))
        self.stack.enter_context(patch.object(host, "_installed_package",
                                               return_value=None))

    def invoke(self, step, auth, release=None):
        raw = _wire(step, auth, release)
        verb = "prepare-env" if step in ("INITIAL", "POST_D_E") else "apply"
        output = host._transaction(verb, raw)
        public = json.loads(output)
        self.assertEqual(public["request_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(output, custody._json(public))
        return public

    def test_initial_uses_fresh_authority_and_only_returns_public_handles(self):
        result = self.invoke("INITIAL", ORIGIN)
        self.assertEqual(set(result), {"schema", "step", "request_sha256", "proposal_id", "commitment"})
        self.assertEqual(self.identity.signed_authority.call_count, 1)
        self.assertGreaterEqual(self.authorize.call_count, 3)
        self.authorize.return_value = False
        with self.assertRaises(Exception):
            self.invoke("INITIAL", ORIGIN)
        self.assertEqual(len(list(self.root.rglob("proposal.json"))), 1)

    def test_unarmed_requests_rejected_before_writes_and_silent_entrypoint(self):
        for raw in (b"{}", b'{"step":1,"step":2}\n', b"x"*1048577,
                    _wire("INITIAL", ORIGIN) + b" ", _wire("SEAL_ENV", ORIGIN, {}),
                    _wire("INITIAL", ORIGIN, {}), _wire("POST_D_E", ORIGIN),
                    _wire("stage", ORIGIN), custody._json(dict(json.loads(_wire("INITIAL", ORIGIN)), arbitrary="secret"))):
            with self.subTest(size=len(raw)):
                stdin, stdout = SimpleNamespace(buffer=io.BytesIO(raw)), SimpleNamespace(buffer=io.BytesIO())
                with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
                    self.assertEqual(host.host_main("prepare-env"), 79)
                self.assertEqual(stdout.buffer.getvalue(), b"")
        self.assertFalse(list(self.root.rglob("proposal.json")))

    def test_installed_package_requires_isolated_exact_path_root_and_fixed_bytes(self):
        self.stack.close()
        with tempfile.TemporaryDirectory() as folder, ExitStack() as checks:
            root = Path(folder)
            binary = root / host._PACKAGE.lstrip("/")
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"installed fixture")
            binary.chmod(0o555)
            digest = custody._digest(binary.read_bytes())
            checks.enter_context(patch.object(custody, "_FS_ROOT", folder))
            checks.enter_context(patch.object(custody, "_ROOT_UID", os.getuid()))
            checks.enter_context(patch.object(custody, "_ROOT_GID", os.getgid()))
            loader = SimpleNamespace(archive=host._PACKAGE, prefix="scripts/")
            checks.enter_context(patch.object(host, "zipimporter", SimpleNamespace))
            checks.enter_context(patch.object(host, "__spec__", SimpleNamespace(loader=loader)))
            checks.enter_context(patch.object(host, "__file__", host._PACKAGE + "/scripts/rc6_genesis_host_transaction.py"))
            checks.enter_context(patch.object(sys, "argv", [host._PACKAGE, "apply"]))
            checks.enter_context(patch.object(sys, "flags", SimpleNamespace(isolated=1)))
            with patch.object(os, "geteuid", return_value=0):
                host._installed_package(digest)
                with self.assertRaises(Exception):
                    host._installed_package("a"*64)
                binary.chmod(0o755)
                with self.assertRaises(Exception):
                    host._installed_package(digest)
                binary.chmod(0o555)
                with patch.object(sys, "argv", ["./custodian", "apply"]), self.assertRaises(Exception):
                    host._installed_package(digest)
                with patch.object(sys, "flags", SimpleNamespace(isolated=0)), self.assertRaises(Exception):
                    host._installed_package(digest)
                binary.rename(binary.with_suffix(".real"))
                binary.symlink_to(binary.with_suffix(".real"))
                with self.assertRaises(Exception):
                    host._installed_package(digest)
            with patch.object(os, "geteuid", return_value=1000), self.assertRaises(Exception):
                host._installed_package(digest)

    def test_real_r340_and_runner_wire_pass_the_host_source_boundary(self):
        from scripts import rc6_genesis_control_actions as runner
        from scripts import rc6_r340_trusted_root_env_controller as r340
        from test_rc6_r340_trusted_root_env_controller import authenticated_material_fixture
        with tempfile.TemporaryDirectory() as temporary:
            github, event, validator, _, _, _, _ = authenticated_material_fixture(Path(temporary))
            kept = []
            report = r340.verify_authenticated_release(event, checkout_sha=event["GITHUB_SHA"], github=github,
                trusted_release=validator, include_material=True, consume_material=kept.append)
            sent = runner._release_wire(report, kept[0])
            binding = dict(BIND, trusted_main_sha=event["GITHUB_SHA"], candidate_sha=report["candidate_sha"],
                           candidate_tree=report["candidate_tree_sha"])
            bound, four = host._bound_release(json.loads(custody._json(sent)), binding)
            self.assertEqual(bound, kept[0].boundary)
            self.assertEqual(dict(four), dict(kept[0].source_bytes))

    def test_package_alias_and_bad_host_identity_produce_no_output_or_state(self):
        accepted = _wire("INITIAL", ORIGIN)
        def denied(_request):
            raise ValueError("private.credential.must.not.leak")
        self.identity.signed_authority.side_effect = denied
        stdin, stdout = SimpleNamespace(buffer=io.BytesIO(accepted)), SimpleNamespace(buffer=io.BytesIO())
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
            self.assertEqual(host.host_main("prepare-env"), 79)
        self.assertEqual(stdout.buffer.getvalue(), b"")
        self.assertFalse(list(self.root.rglob("proposal.json")))

    def test_source_e_pin_and_boundary_drift_holds_before_any_consumer(self):
        initial = self.invoke("INITIAL", ORIGIN)
        auth = dict(ORIGIN, proposal_id=initial["proposal_id"], commitment=initial["commitment"],
                    dispatch_run_id="902", prepare_go_id=42, initial_prepare_go_id=35,
                    approval_expires_at="2026-11-14T21:00:00Z")
        release = _release()
        for part in ("bytes", "sha", "git", "E", "receipt_id", "candidate", "extra", "D", "source_role"):
            wire = deepcopy(release)
            if part == "bytes":
                wire["source_b64"]["helper"] = _b64(b"raise SystemExit(0)")
            elif part in ("sha", "git"):
                name = {"sha": "source_sha256", "git": "git_blobs"}[part]
                rows = list(wire["boundary"][name])
                rows[0] = (rows[0][0], "0" * len(rows[0][1]))
                wire["boundary"][name] = rows
            elif part == "E":
                raw = json.loads(base64.b64decode(wire["postcompletion_receipt_b64"]))
                raw["publisher_run_id"] += 1
                encoded = custody._json(raw)
                wire["postcompletion_receipt_b64"] = _b64(encoded)
                wire["receipt"]["source_material"]["postcompletion_receipt_sha256"] = custody._digest(encoded)
            elif part == "receipt_id":
                wire["receipt"]["trusted_main_audit_run_id"] += 1
            elif part == "candidate":
                wire["boundary"]["candidate_sha"] = "0"*40
            elif part == "extra":
                wire["receipt"]["release_go"] = True
            elif part == "D":
                wire["receipt"]["source_material"]["release"]["run_attempt"] += 1
            else:
                wire["source_b64"]["foreign.py"] = "eA=="
            with self.subTest(part=part), patch.object(r326, "_trusted_consumer") as consumer:
                with self.assertRaises(Exception):
                    self.invoke("POST_D_E", auth, wire)
                consumer.assert_not_called()
        self.assertFalse(list(self.root.rglob("projection.json")))

    def test_signed_rsa_request_and_public_ledger_samples_reach_only_initial_custody(self):
        import importlib
        import importlib.util
        sys.modules.pop("scripts.rc6_genesis_host_identity")
        identity = importlib.import_module("scripts.rc6_genesis_host_identity")
        package = importlib.import_module("scripts.rc6_genesis_custodian_package")
        from test_rc6_genesis_host_identity import IdentityTest
        IdentityTest.setUpClass()
        self.addCleanup(IdentityTest.doClassCleanups)
        signer = IdentityTest()
        signer.setUp()
        self.addCleanup(signer.doCleanups)
        binding = dict(BIND, run_id="9", run_attempt="2", host_identity_sha256=identity.HOST,
                       trusted_main_sha=signer.claims["sha"])
        authority = dict(ORIGIN, **{k: signer.request["authority"][k] for k in
            ("issued_at", "expires_at", "public_versions", "dispatch_run_id", "dispatch_run_attempt")},
            binding=binding, custodian_sha256=custody._digest(package.build_custodian()))
        authority.pop("host_identity_token")
        unsigned = _wire("INITIAL", authority)
        signer.request = json.loads(unsigned)
        authority["host_identity_token"] = signer.token(audience="urn:porota:r367:" + custody._digest(unsigned))
        records = signer.public_responses()
        signed = _wire("INITIAL", authority)
        with patch.object(identity, "_fetch", side_effect=records.__getitem__) as public:
            receipt = json.loads(host._transaction("prepare-env", signed))
            self.assertEqual(public.call_count, 4)
            forged = json.loads(signed)
            forged["authority"]["nonce"] = "a"*32
            with self.assertRaises(identity.IdentityHold):
                host._transaction("prepare-env", custody._json(forged))
        self.assertEqual(receipt["request_sha256"], custody._digest(signed))
        self.assertEqual(len(list(self.root.rglob("proposal.json"))), 1)

    def test_five_steps_through_real_custody_and_r326_in_isolated_python(self):
        if "--run-isolated" not in sys.argv:
            run = subprocess.run([sys.executable, "-IB", str(Path(__file__).resolve()), "--run-isolated"],
                                 capture_output=True, timeout=25)
            self.assertEqual(run.returncode, 0, run.stderr.decode()[-3000:])
            return
        initial = self.invoke("INITIAL", ORIGIN)
        origin = {k: initial[k] for k in ("proposal_id", "commitment")}
        post = dict(ORIGIN, **origin, dispatch_run_id="902", prepare_go_id=42, initial_prepare_go_id=35,
                    approval_expires_at="2026-11-14T21:00:00Z")
        release = _release()
        # On interpreters whose actual stdlib R326 refuses, the same installed
        # operation must HOLD before persisting a profile. 3.11/3.12 run all steps.
        try:
            r326._verify_standard_library()
        except r326.ProvenanceHold as error:
            self.assertEqual(str(error), "RC6_R326_HOLD79:STDLIB_DEPENDENCY_UNVERIFIED")
            with self.assertRaisesRegex(r326.ProvenanceHold, "STDLIB_DEPENDENCY_UNVERIFIED"):
                self.invoke("POST_D_E", post, release)
            self.assertFalse(list(self.root.rglob("projection.json")))
            return
        frozen = self.invoke("POST_D_E", post, release)
        data = dict(ORIGIN, **origin, dispatch_run_id="903", generation_commitment=frozen["generation_commitment"],
                    root_manifest_sha256=frozen["manifest_sha256"], nonce="6"*32, prepare_go_id=35,
                    lease_id=47, pin_go_id=48, install_go_id=49, env_go_id=50)
        sealed = self.invoke("SEAL_ENV", data)
        self.assertTrue(any(call.kwargs == {"force": True} for call in self.authorize.call_args_list))
        self.assertEqual(set(frozen), set(sealed))
        self.assertEqual(sealed["manifest_b64"], frozen["manifest_b64"])
        raw = base64.b64decode(sealed["manifest_b64"])
        real_install = __import__("scripts.rc6_genesis_host_install", fromlist=["install"])
        def deliver(auth, files, *, check_authority, verify_env, final_authority):
            self.assertEqual(set(files), set(real_install.r280.SOURCE_NAMES.values()) | {"manifest.json"})
            self.assertTrue(check_authority())
            self.assertTrue(final_authority())
            self.assertIsNone(verify_env())
            approval = json.loads(files["approval.json"])
            self.assertEqual((approval["run_id"], approval["run_attempt"]), ("101","1"))
            for secret in ("private-fixture-daemon", "fixture-opaque", approval["generation"]):
                self.assertNotIn(secret.encode(), custody._json(frozen))
            self.assertNotIn("dashboard_env_sha256", raw.decode())
            return real_install._RESULT
        with patch.object(real_install, "verify_transition", return_value=[True]) as verify, \
             patch.object(real_install, "install", side_effect=deliver) as install:
            self.assertEqual(self.invoke("PIN", data)["step"], "PIN")
            verify.assert_called_with(data, raw)
            self.assertEqual(self.invoke("INSTALL", data)["step"], "INSTALL")
            install.assert_called_once()
            install.side_effect = None
            install.return_value = "unverified"
            with self.assertRaises(Exception):
                self.invoke("INSTALL", data)
        for mutated in (dict(data, root_manifest_sha256="f"*64), dict(data, generation_commitment="e"*64),
                        dict(data, binding=dict(BIND, trusted_main_sha="0"*40))):
            with self.assertRaises(Exception):
                self.invoke("PIN", mutated)
        with self.assertRaises(Exception):
            self.invoke("INSTALL", data, _release())


if __name__ == "__main__":
    if "--run-isolated" in sys.argv:
        test = HostTransactionTest("test_five_steps_through_real_custody_and_r326_in_isolated_python")
        result = unittest.TextTestRunner().run(unittest.TestSuite([test]))
        raise SystemExit(0 if result.wasSuccessful() else 1)
    unittest.main()
