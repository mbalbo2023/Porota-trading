"""R277 offline tests: never contact SSH, sudo, Docker or the Droplet."""

from contextlib import redirect_stdout
from pathlib import Path
import io
import json
import tempfile
import unittest
from unittest.mock import patch

from scripts import rc6_host_preflight_readonly as preflight


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/rc6-host-physical-preflight.yml"
SHA = "a" * 40
TREE = "b" * 40
GENERATION = "c" * 32
ARTIFACT = "sha256:" + "d" * 64
IMAGE = "sha256:" + "e" * 64
HELPER = "f" * 64
DAEMON = "daemon-rootful-rc6"
SYNTHETIC_DO_ID = "987654321"
SYNTHETIC_DO_REGION = "lab9"


def expected_env(phase="POSTSEAL"):
    values = {
        "RC6_PREFLIGHT_PHASE": phase,
        "RC6_EXPECTED_DO_ID": SYNTHETIC_DO_ID,
        "RC6_EXPECTED_DO_REGION": SYNTHETIC_DO_REGION,
        "RC6_EXPECTED_CANDIDATE_SHA": SHA,
        "RC6_EXPECTED_CANDIDATE_TREE": TREE,
        "RC6_EXPECTED_GENERATION": GENERATION,
        "RC6_EXPECTED_ARTIFACT_DIGEST": ARTIFACT,
        "RC6_EXPECTED_IMAGE_ID": IMAGE,
        "RC6_EXPECTED_DAEMON_ID": DAEMON,
    }
    if phase == "BOOTSTRAP":
        return {"RC6_PREFLIGHT_PHASE": phase,
                "RC6_EXPECTED_DO_ID": SYNTHETIC_DO_ID,
                "RC6_EXPECTED_DO_REGION": SYNTHETIC_DO_REGION}
    if phase == "PREBIRTH":
        for key in ("RC6_EXPECTED_GENERATION", "RC6_EXPECTED_ARTIFACT_DIGEST",
                    "RC6_EXPECTED_IMAGE_ID", "RC6_EXPECTED_DAEMON_ID"):
            values.pop(key)
    return values


def receipts():
    prebirth = {
        "schema": "RC6_PREBIRTH_QUIESCENCE_V1", "status": "OK",
        "candidate_sha": SHA, "runtime_writers": "QUIESCENT",
        "paper_mode": "PRODUCTION_PAPER", "real_orders_sent": 0,
        "ppi_watch_accessed": False,
    }
    current = {
        "schema": "RC6_POSTSEAL_CUSTODY_V1", "status": "OK",
        "candidate_sha": SHA, "candidate_tree": TREE,
        "generation": GENERATION, "artifact_digest": ARTIFACT,
        "image_id": IMAGE, "daemon_id": DAEMON,
        "declared_paper_mode": "PRODUCTION_PAPER",
        "roles": {role: {"cid": str(index) * 64, "state": "running"}
                  for index, role in enumerate(("dashboard", "observer", "critical"), 1)},
    }
    return prebirth, current


def write_receipts(folder, prebirth=None, current=None, phase="POSTSEAL"):
    normal_prebirth, normal_current = receipts()
    evidence = {
        "droplet_id.txt": SYNTHETIC_DO_ID + "\n",
        "region.txt": SYNTHETIC_DO_REGION + "\n",
        "helper.stat": "0|755|1|regular file\n",
        "helper.sha": HELPER + "  /usr/local/sbin/porota-rc6-oci-custody\n",
    }
    if phase == "PREBIRTH":
        evidence["prebirth.json"] = json.dumps(normal_prebirth if prebirth is None else prebirth)
    elif phase == "POSTSEAL":
        evidence["current.json"] = json.dumps(normal_current if current is None else current)
    for name, value in evidence.items():
        (folder / name).write_text(value, encoding="utf-8")


class HostPreflightReadonlyTest(unittest.TestCase):
    def setUp(self):
        self.env = expected_env()
        pin = preflight.TRUSTED_HELPER_SHA256
        preflight.TRUSTED_HELPER_SHA256 = HELPER
        self.addCleanup(setattr, preflight, "TRUSTED_HELPER_SHA256", pin)

    def validate(self, folder, phase="POSTSEAL"):
        return preflight.validate_evidence(
            folder, preflight.validate_expected(expected_env(phase)))

    def test_bootstrap_has_no_release_selectors_and_never_requires_newborn_cids(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_receipts(folder, phase="BOOTSTRAP")
            bootstrap = expected_env("BOOTSTRAP")
            result = preflight.validate_evidence(folder, preflight.validate_expected(bootstrap))
            self.assertEqual(result["phase"], "BOOTSTRAP")
            self.assertEqual(result["status"], "BOOTSTRAP_OBSERVATION_ONLY")
            self.assertEqual(result["helper_sha256_observed"], HELPER)
            self.assertEqual(result["real_orders_sent"], "NO_VERIFICADO")
            self.assertNotIn("role_cid_fingerprints", result)
            self.assertIs(result["host_control_verified"], False)

    def test_prebirth_requires_paper_zero_but_not_postdeploy_cids_or_image(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_receipts(folder, phase="PREBIRTH")
            selectors = {"RC6_PREFLIGHT_PHASE": "PREBIRTH",
                         "RC6_EXPECTED_DO_ID": SYNTHETIC_DO_ID,
                         "RC6_EXPECTED_DO_REGION": SYNTHETIC_DO_REGION,
                         "RC6_EXPECTED_CANDIDATE_SHA": SHA,
                         "RC6_EXPECTED_CANDIDATE_TREE": TREE}
            with patch.object(preflight, "TRUSTED_HELPER_SHA256", HELPER):
                report = preflight.validate_evidence(
                    folder, preflight.validate_expected(selectors))
            self.assertEqual(report["phase"], "PREBIRTH")
            self.assertEqual(report["paper_mode"], "PRODUCTION_PAPER")
            self.assertEqual(report["real_orders_sent"], 0)
            self.assertIs(report["ppi_watch_accessed"], False)
            self.assertNotIn("generation", report)
            self.assertNotIn("role_cid_fingerprints", report)

    def test_postseal_requires_three_exact_cids_without_prebirth_or_paper_zero_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_receipts(folder)
            selectors = dict(expected_env(), RC6_PREFLIGHT_PHASE="POSTSEAL")
            with patch.object(preflight, "TRUSTED_HELPER_SHA256", HELPER):
                report = preflight.validate_evidence(
                    folder, preflight.validate_expected(selectors))
            self.assertEqual(report["phase"], "POSTSEAL")
            self.assertEqual(len(report["role_cid_fingerprints"]), 3)
            self.assertEqual(report["real_orders_sent"], "NO_VERIFICADO")
            self.assertEqual(report["ppi_watch_accessed"], "NO_VERIFICADO")
            self.assertIs(report["runtime_verified"], False)

    def test_unpinned_helper_cannot_be_self_authorized_by_dispatch_input(self):
        selectors = {"RC6_PREFLIGHT_PHASE": "PREBIRTH",
                     "RC6_EXPECTED_DO_ID": SYNTHETIC_DO_ID,
                     "RC6_EXPECTED_DO_REGION": SYNTHETIC_DO_REGION,
                     "RC6_EXPECTED_CANDIDATE_SHA": SHA,
                     "RC6_EXPECTED_CANDIDATE_TREE": TREE,
                     "RC6_EXPECTED_HELPER_SHA256": HELPER}
        with patch.object(preflight, "TRUSTED_HELPER_SHA256", None):
            with self.assertRaises(preflight.PreflightHold):
                preflight.validate_expected(selectors)
        selectors.pop("RC6_EXPECTED_HELPER_SHA256")
        with patch.object(preflight, "TRUSTED_HELPER_SHA256", None):
            with self.assertRaises(preflight.PreflightHold) as caught:
                preflight.validate_expected(selectors)
        self.assertEqual(caught.exception.reason,
                         "ROOT_HELPER_TRUSTED_MAIN_PIN_ABSENT")

    def test_optional_phase_selectors_cannot_skip_or_cross_phase(self):
        rejected = (
            dict(expected_env("BOOTSTRAP"), RC6_EXPECTED_GENERATION=GENERATION),
            {key: value for key, value in expected_env("PREBIRTH").items()
             if key != "RC6_EXPECTED_CANDIDATE_TREE"},
            dict(expected_env("PREBIRTH"), RC6_EXPECTED_IMAGE_ID=IMAGE),
            {key: value for key, value in expected_env().items()
             if key != "RC6_EXPECTED_GENERATION"},
        )
        for item in rejected:
            with self.subTest(item=item):
                with self.assertRaises(preflight.PreflightHold):
                    preflight.validate_expected(item)

    def test_cross_phase_json_never_reuses_future_or_prior_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_receipts(folder, phase="PREBIRTH")
            (folder / "current.json").write_text(json.dumps(receipts()[1]))
            with self.assertRaises(preflight.PreflightHold) as caught:
                self.validate(folder, phase="PREBIRTH")
            self.assertEqual(caught.exception.reason,
                             "CROSS_PHASE_RECEIPT_FORBIDDEN")

    def test_exact_postseal_three_distinct_cids_never_claims_fresh_paper_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_receipts(folder)
            report = self.validate(folder)
            self.assertEqual(report["schema"], "RC6_HOST_PHYSICAL_PREFLIGHT_V1")
            self.assertEqual(report["status"], "POSTSEAL_EVIDENCE_ONLY")
            self.assertEqual(report["candidate_sha"], SHA)
            self.assertEqual(report["artifact_digest"], ARTIFACT)
            self.assertEqual(report["paper_mode"], "NO_VERIFICADO")
            self.assertEqual(report["real_orders_sent"], "NO_VERIFICADO")
            self.assertEqual(report["ppi_watch_accessed"], "NO_VERIFICADO")
            self.assertIs(report["host_control_verified"], False)
            self.assertIs(report["runtime_verified"], False)
            self.assertIs(report["deploy_authorized"], False)
            self.assertEqual(set(report["role_cid_fingerprints"]),
                             {"dashboard", "observer", "critical"})
            for value in report["role_cid_fingerprints"].values():
                self.assertEqual(len(value), 16)
            self.assertNotIn("1" * 64, json.dumps(report))
            self.assertNotIn("2" * 64, json.dumps(report))
            self.assertNotIn("3" * 64, json.dumps(report))

    def test_malformed_or_injected_selectors_hold_before_ssh(self):
        for key, value in (
            ("RC6_EXPECTED_CANDIDATE_SHA", "a" * 39),
            ("RC6_EXPECTED_CANDIDATE_SHA", "a" * 40 + ";id"),
            ("RC6_EXPECTED_CANDIDATE_TREE", "b" * 39 + "\n"),
            ("RC6_EXPECTED_GENERATION", "c" * 40),
            ("RC6_EXPECTED_ARTIFACT_DIGEST", "sha256:" + "d" * 63),
            ("RC6_EXPECTED_IMAGE_ID", "sha256:" + "e" * 64 + "/bin/sh"),
            ("RC6_EXPECTED_DAEMON_ID", "daemon;touch /tmp/bypass"),
            ("RC6_EXPECTED_HELPER_SHA256", "f" * 65),
        ):
            with self.subTest(key=key, value=value):
                env = dict(self.env, **{key: value})
                with self.assertRaises(preflight.PreflightHold):
                    preflight.validate_expected(env)

    def test_no_host_proof_missing_corrupt_or_aliased_file_holds(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_receipts(folder)
            for name, spoofed in (
                ("droplet_id.txt", "987654322"),
                ("region.txt", "ams3"),
                ("helper.stat", "1000|777|1|regular file"),
                ("helper.stat", "0|775|1|regular file"),
                ("helper.stat", "0|755|2|regular file"),
                ("helper.stat", "0|755|1|symbolic link"),
                ("helper.sha", "0" * 64 + "  /usr/local/sbin/porota-rc6-oci-custody"),
            ):
                with self.subTest(name=name, spoofed=spoofed):
                    path = folder / name
                    original = path.read_text()
                    path.write_text(spoofed)
                    with self.assertRaises(preflight.PreflightHold):
                        self.validate(folder)
                    path.write_text(original)
            (folder / "current.json").unlink()
            with self.assertRaises(preflight.PreflightHold):
                self.validate(folder)

    def test_runner_symlink_capture_is_never_accepted_as_host_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_receipts(folder)
            path = folder / "helper.sha"
            original = path.read_bytes()
            path.unlink()
            target = folder / "untrusted"
            target.write_bytes(original)
            path.symlink_to(target)
            with self.assertRaises(preflight.PreflightHold) as caught:
                self.validate(folder)
            self.assertEqual(caught.exception.reason, "EVIDENCE_FILE_UNVERIFIED")

    def test_prebirth_rejects_missing_extra_forged_paper_zero_and_watch(self):
        before, current = receipts()
        rejected = (
            {**before, "candidate_sha": TREE},
            {**before, "runtime_writers": "ACTIVE"},
            {**before, "paper_mode": "LIVE"},
            {**before, "real_orders_sent": False},
            {**before, "real_orders_sent": 0.0},
            {**before, "real_orders_sent": "0"},
            {**before, "ppi_watch_accessed": True},
            {**before, "extra": "root"},
            {key: value for key, value in before.items() if key != "status"},
        )
        for document in rejected:
            with self.subTest(document=document):
                with tempfile.TemporaryDirectory() as temp:
                    folder = Path(temp)
                    write_receipts(folder, prebirth=document, phase="PREBIRTH")
                    with self.assertRaises(preflight.PreflightHold):
                        self.validate(folder, phase="PREBIRTH")

    def test_current_rejects_wrong_generation_roles_cids_mode_or_additional_fields(self):
        before, current = receipts()
        rejected = (
            {**current, "candidate_tree": SHA},
            {**current, "generation": "f" * 32},
            {**current, "daemon_id": "new-daemon"},
            {**current, "declared_paper_mode": "LIVE"},
            {**current, "roles": {"dashboard": current["roles"]["dashboard"]}},
            {**current, "roles": {**current["roles"], "critical": current["roles"]["dashboard"]}},
            {**current, "roles": {**current["roles"], "observer": {
                "cid": "not-an-id", "state": "running"}}},
            {**current, "roles": {**current["roles"], "critical": {
                "cid": "3" * 64, "state": "exited"}}},
            {**current, "forged": True},
        )
        for document in rejected:
            with self.subTest(document=document):
                with tempfile.TemporaryDirectory() as temp:
                    folder = Path(temp)
                    write_receipts(folder, current=document)
                    with self.assertRaises(preflight.PreflightHold):
                        self.validate(folder)

    def test_json_duplicate_keys_nonfinite_and_oversized_hold(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_receipts(folder, phase="PREBIRTH")
            path = folder / "prebirth.json"
            for source in (
                '{"schema":"ok","schema":"again"}',
                '{"status":NaN}',
                "{}" + (" " * 2200),
                '{"schema": 1}garbage',
            ):
                path.write_text(source)
                with self.subTest(source=source[:45]):
                    with self.assertRaises(preflight.PreflightHold):
                        self.validate(folder, phase="PREBIRTH")

    def test_cli_missing_evidence_returns_sanitized_hold79(self):
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            with redirect_stdout(output):
                code = preflight.main(["--verify", "--evidence-dir", temp],
                                      env=self.env)
            self.assertEqual(code, 79)
            self.assertIn("HOLD79", output.getvalue())
            self.assertNotIn(DAEMON, output.getvalue())

    def test_cli_emits_only_sanitized_json_and_never_authorizes_host(self):
        with tempfile.TemporaryDirectory() as temp:
            write_receipts(Path(temp))
            output = io.StringIO()
            with redirect_stdout(output):
                code = preflight.main(["--verify", "--evidence-dir", temp],
                                      env=self.env)
            self.assertEqual(code, 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report["status"], "POSTSEAL_EVIDENCE_ONLY")
            self.assertIs(report["host_control_verified"], False)
            self.assertIs(report["deploy_authorized"], False)
            self.assertNotIn(DAEMON, output.getvalue())
            self.assertNotIn('"cid":', output.getvalue())

    def test_do_host_identity_is_a_separate_gate_before_any_privileged_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_receipts(folder)
            output = io.StringIO()
            with redirect_stdout(output):
                code = preflight.main(["--check-host", "--evidence-dir", temp],
                                      env=self.env)
            self.assertEqual(code, 0)
            self.assertIn("HOST_IDENTITY=PINNED", output.getvalue())
            (folder / "droplet_id.txt").write_text("000000000")
            with redirect_stdout(output):
                code = preflight.main(["--check-host", "--evidence-dir", temp],
                                      env=self.env)
            self.assertEqual(code, 79)

    def test_workflow_is_manual_trusted_main_readonly_fixed_root_allowlist(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", source)
        self.assertIn("github.ref == 'refs/heads/main'", source)
        self.assertIn("group: rc6-unified-paper-deploy", source)
        self.assertIn("cancel-in-progress: false", source)
        self.assertIn("contents: read", source)
        self.assertIn("- BOOTSTRAP", source)
        self.assertIn("- PREBIRTH", source)
        self.assertIn("- POSTSEAL", source)
        self.assertNotIn("inputs.helper_sha256", source)
        self.assertNotIn("contents: write", source)
        self.assertNotIn("id-token: write", source)
        self.assertNotIn("schedule:", source)
        self.assertNotIn("push:\n", source)
        self.assertNotIn("docker ps", source)
        self.assertNotIn("sudo -n docker", source)
        self.assertNotIn("systemctl", source)
        self.assertNotIn("PPI_WATCH_", source)
        self.assertNotIn("host_control_verified=true", source)
        fixed = "sudo -n /usr/local/sbin/porota-rc6-oci-custody "
        self.assertIn(fixed + "quiescence verify-prebirth", source)
        self.assertIn(fixed + "verify-current", source)
        self.assertNotIn("sudo -n python", source)
        self.assertNotIn("sudo -n bash", source)
        privileged = [line for line in source.splitlines() if "sudo -n" in line]
        self.assertEqual(len(privileged), 2)
        self.assertTrue(all(fixed in line for line in privileged))
        self.assertLess(source.index('--check-host --evidence-dir'),
                        source.index("capture prebirth.json"))
        self.assertLess(source.index("--check-helper --evidence-dir"),
                        source.index("capture prebirth.json"))
        self.assertLess(source.index("BOOTSTRAP)"), source.index("PREBIRTH)"))
        self.assertLess(source.index("PREBIRTH)"), source.index("POSTSEAL)"))
        self.assertNotIn("RC6_EXPECTED_HELPER_SHA256:", source)

    def test_droplet_identity_pins_must_come_from_protected_actions_secrets(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        python_source = (ROOT / "scripts/rc6_host_preflight_readonly.py").read_text()
        self.assertIn("RC6_EXPECTED_DO_ID: ${{ secrets.RC6_EXPECTED_DO_ID }}", source)
        self.assertIn("RC6_EXPECTED_DO_REGION: ${{ secrets.RC6_EXPECTED_DO_REGION }}", source)
        self.assertNotIn("inputs.expected_do_id", source)
        self.assertNotIn("inputs.expected_do_region", source)
        self.assertNotIn("EXPECTED_DROPLET_ID =", python_source)
        self.assertNotIn("EXPECTED_REGION =", python_source)
        self.assertNotIn("RC6_EXPECTED_DO_ID: ${{ vars.", source)
        self.assertNotIn("RC6_EXPECTED_DO_REGION: ${{ vars.", source)
        self.assertNotIn("echo \"$RC6_EXPECTED_DO", source)

    def test_missing_malformed_or_extra_host_identity_pins_hold_before_ssh(self):
        for key, bad_value in (
            ("RC6_EXPECTED_DO_ID", None),
            ("RC6_EXPECTED_DO_ID", ""),
            ("RC6_EXPECTED_DO_ID", "000000000"),
            ("RC6_EXPECTED_DO_ID", "987654321;id"),
            ("RC6_EXPECTED_DO_REGION", None),
            ("RC6_EXPECTED_DO_REGION", ""),
            ("RC6_EXPECTED_DO_REGION", "lab9;id"),
            ("RC6_EXPECTED_DO_REGION", "LAB9"),
        ):
            for phase in ("BOOTSTRAP", "PREBIRTH", "POSTSEAL"):
                with self.subTest(key=key, phase=phase):
                    env = expected_env(phase)
                    if bad_value is None:
                        env.pop(key)
                    else:
                        env[key] = bad_value
                    with self.assertRaises(preflight.PreflightHold) as caught:
                        preflight.validate_expected(env)
                    self.assertIn(caught.exception.reason,
                                  {"DROPLET_TRUSTED_PIN_UNVERIFIED",
                                   "REGION_TRUSTED_PIN_UNVERIFIED"})

    def test_mismatched_secret_pins_never_leak_identity_in_hold_output(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            write_receipts(folder, phase="BOOTSTRAP")
            for key, value in (("RC6_EXPECTED_DO_ID", "987654322"),
                               ("RC6_EXPECTED_DO_REGION", "lab8")):
                with self.subTest(key=key):
                    output = io.StringIO()
                    env = dict(expected_env("BOOTSTRAP"), **{key: value})
                    with redirect_stdout(output):
                        status = preflight.main(["--check-host", "--evidence-dir", temp],
                                                env=env)
                    self.assertEqual(status, 79)
                    self.assertIn("DROPLET_IDENTITY_UNVERIFIED", output.getvalue())
                    self.assertNotIn(SYNTHETIC_DO_ID, output.getvalue())
                    self.assertNotIn(SYNTHETIC_DO_REGION, output.getvalue())

    def test_public_receipt_digest_never_includes_low_entropy_physical_identity(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            folder_a, folder_b = Path(first), Path(second)
            write_receipts(folder_a, phase="BOOTSTRAP")
            write_receipts(folder_b, phase="BOOTSTRAP")
            (folder_b / "droplet_id.txt").write_text("987654322\n")
            (folder_b / "region.txt").write_text("lab8\n")
            expected_a = preflight.validate_expected(expected_env("BOOTSTRAP"))
            expected_b = preflight.validate_expected(dict(
                expected_env("BOOTSTRAP"),
                RC6_EXPECTED_DO_ID="987654322", RC6_EXPECTED_DO_REGION="lab8"))
            report_a = preflight.validate_evidence(folder_a, expected_a)
            report_b = preflight.validate_evidence(folder_b, expected_b)
            # Public hash may bind approved helper bytes/phase receipt, NEVER
            # a guessable DO ID/region checked only in ephemeral runner memory.
            self.assertNotIn("source_receipts_sha256", report_a)
            self.assertNotIn("source_receipts_sha256", report_b)
            self.assertEqual(report_a["helper_and_phase_receipts_sha256"],
                             report_b["helper_and_phase_receipts_sha256"])
            self.assertNotIn(SYNTHETIC_DO_ID, json.dumps(report_a))
            self.assertNotIn(SYNTHETIC_DO_REGION, json.dumps(report_a))
            self.assertNotIn("987654322", json.dumps(report_b))
            self.assertNotIn("lab8", json.dumps(report_b))


if __name__ == "__main__":
    unittest.main()
