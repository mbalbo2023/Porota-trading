import copy
import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path


REPO = "mbalbo2023/Porota-trading"
SHA = "a" * 40


class ActionsReleaseTests(unittest.TestCase):
    def setUp(self):
        path = Path(__file__).resolve().parents[1] / "scripts/porota_actions_release.py"
        self.assertTrue(path.is_file(), "missing executable Actions release consumer")
        spec = importlib.util.spec_from_file_location("actions_release", path)
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self.run = {"id": 123, "run_attempt": 1, "status": "completed", "conclusion": "success",
                    "event": "pull_request", "head_sha": SHA,
                    "head_repository": {"full_name": REPO},
                    "path": ".github/workflows/porota-predeploy-v2.yml"}
        self.artifact = {"id": 456, "name": "porota-predeploy-v2-" + SHA, "expired": False,
                         "size_in_bytes": 1234, "digest": "sha256:" + "b" * 64,
                         "workflow_run": {"id": 123, "head_sha": SHA}}

    def test_exact_completed_candidate_is_accepted(self):
        self.mod.validate_run(self.run, SHA)
        self.mod.validate_artifact(self.artifact, self.run, SHA)

    def test_failed_foreign_or_different_source_run_rejected(self):
        for key, value in [("head_sha", "c" * 40), ("conclusion", "failure"),
                           ("status", "in_progress"), ("event", "pull_request_target"),
                           ("path", ".github/workflows/ci.yml"), ("run_attempt", True),
                           ("head_repository", {"full_name": "foreign/repo"})]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                run = copy.deepcopy(self.run)
                run[key] = value
                self.mod.validate_run(run, SHA)

    def test_artifact_must_belong_to_exact_source_and_run(self):
        for key, value in [("name", "different"), ("expired", True), ("size_in_bytes", 0),
                           ("id", True), ("digest", "not-a-digest"),
                           ("workflow_run", {"id": 124, "head_sha": SHA})]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                artifact = copy.deepcopy(self.artifact)
                artifact[key] = value
                self.mod.validate_artifact(artifact, self.run, SHA)

    def test_archive_digest_checked_before_extract(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "artifact.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("release.json", "{}")
            with self.assertRaises(ValueError):
                self.mod.extract_artifact(archive, Path(directory) / "out", "sha256:" + "0" * 64)
            self.assertFalse((Path(directory) / "out").exists())

    def test_archive_path_traversal_and_extra_member_rejected(self):
        for name in ("../escape", "/escape", "unexpected.json"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / "artifact.zip"
                with zipfile.ZipFile(archive, "w") as handle:
                    handle.writestr(name, "unsafe")
                digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
                with self.assertRaises(ValueError):
                    self.mod.extract_artifact(archive, Path(directory) / "out", digest)

    def test_archive_requires_all_six_members_and_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "artifact.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                for name in self.mod.MEMBERS:
                    handle.writestr(name, "{}")
            digest = "sha256:" + hashlib.sha256(archive.read_bytes()).hexdigest()
            out = Path(directory) / "out"
            self.mod.extract_artifact(archive, out, digest)
            self.assertEqual({p.name for p in out.iterdir()}, set(self.mod.MEMBERS))

    def test_receipt_run_attempt_binding_is_not_self_asserted(self):
        receipt = {"source_sha": SHA, "build_run_id": 123, "build_run_attempt": 1}
        self.mod.validate_release_run(receipt, self.run)
        receipt["build_run_attempt"] = 2
        with self.assertRaises(ValueError):
            self.mod.validate_release_run(receipt, self.run)

    def test_verified_bundle_extracts_without_changing_release_members(self):
        from tests.test_porota_release import ReleaseTests
        fixture = ReleaseTests('test_exact_bytes_and_real_checkout_identity_are_accepted')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.create()
        fixture.verify()
        source = self.mod.extract_source_bundle(fixture.output)
        self.assertEqual(source, fixture.output.parent/'source')
        self.assertEqual((source/'app.py').read_bytes(), (fixture.repo/'app.py').read_bytes())
        fixture.verify()


if __name__ == "__main__":
    unittest.main()
