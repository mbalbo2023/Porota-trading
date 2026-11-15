"""Release bytes, source identity and PAPER receipts; no Docker daemon required."""
from __future__ import annotations

import gzip
import hashlib
import importlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from scripts.porota_build_deploy_bundle_v2 import build_bundle


def _tar(path, members):
    with gzip.open(path, "wb") as raw:
        with tarfile.open(fileobj=raw, mode="w") as archive:
            for name, data in members.items():
                item = tarfile.TarInfo(name)
                item.size = len(data)
                archive.addfile(item, io.BytesIO(data))


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.output = self.root / "release"
        self.output.mkdir()
        for name, data in {"Dockerfile": "FROM scratch\n", "app.py": "answer = 42\n"}.items():
            (self.repo / name).write_text(data)
        self.git("init", "-q")
        self.git("-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "add", ".")
        self.git("-c", "user.name=CI", "-c", "user.email=ci@example.invalid", "commit", "-qm", "fixture")
        self.sha = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")
        config = json.dumps({"config": {"User": "botuser", "Env": [
            "POROTA_BUILD_SHA=" + self.sha, "POROTA_CANDIDATE_TREE_SHA=" + self.tree,
        ]}}, sort_keys=True).encode()
        digest = hashlib.sha256(config).hexdigest()
        self.image_id = "sha256:" + digest
        self.image_members = {
            "manifest.json": json.dumps([{"Config": digest + ".json", "RepoTags": ["fixture:ci"], "Layers": []}]).encode(),
            digest + ".json": config,
        }
        _tar(self.output / "porota-predeploy-image.tar.gz", self.image_members)
        build_bundle(self.repo, self.output / "porota-deploy-bundle-v2.tgz",
                     self.output / "porota-deploy-bundle-v2-manifest.json")
        identity = {"status": "PASS", "source_sha": self.sha, "source_tree": self.tree, "image_id": self.image_id}
        self.write("code-tests.json", {**identity, "schema": "porota.code-tests.v1", "collected": 2,
                   "executed": 2, "failed": 0, "errors": 0, "skipped": 0, "retired": []})
        self.write("functional.json", {**identity, "schema": "porota.functional.v1", "http": {
            "/health": 200, "/api/dashboard/truth": 200}, "paper_mode": "PRODUCTION_PAPER",
            "real_orders_sent": 0, "order_routes_blocked": True, "sqlite_integrity": "ok"})

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args], text=True).strip()

    def write(self, name, value):
        (self.output / name).write_text(json.dumps(value, sort_keys=True) + "\n")

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("scripts.porota_release"),
                             "Release verification implementation is missing")
        return importlib.import_module("scripts.porota_release")

    def create(self):
        return self.api().create_release(self.output, self.repo, image_id=self.image_id,
                                         image_size_bytes=123456, run_id=71, run_attempt=1)

    def verify(self, **changes):
        return self.api().verify_release(self.output, expected_sha=self.sha, expected_tree=self.tree,
                                         repository=self.repo, **changes)

    def test_exact_bytes_and_real_checkout_identity_are_accepted(self):
        created = self.create()
        result = self.verify(expected_image_id=self.image_id)
        self.assertEqual(result, created)
        self.assertEqual(result["source_sha"], self.sha)
        self.assertEqual(result["source_tree"], self.tree)
        self.assertEqual(result["build_run_id"], 71)
        self.assertEqual(set(result["members"]), {
            "porota-predeploy-image.tar.gz", "porota-deploy-bundle-v2.tgz",
            "porota-deploy-bundle-v2-manifest.json", "code-tests.json", "functional.json"})

    def test_corrupt_image_bytes_are_rejected_before_load(self):
        self.create()
        with (self.output / "porota-predeploy-image.tar.gz").open("ab") as stream:
            stream.write(b"corruption")
        with self.assertRaisesRegex(ValueError, "MEMBER_DIGEST"):
            self.verify()

    def test_wrong_image_id_is_rejected_even_with_valid_archive_hash(self):
        self.create()
        with self.assertRaisesRegex(ValueError, "IMAGE_ID"):
            self.verify(expected_image_id="sha256:" + "f" * 64)

    def test_missing_or_extra_file_is_rejected(self):
        self.create()
        (self.output / "unexpected.txt").write_text("not part of the release")
        with self.assertRaisesRegex(ValueError, "MEMBERS"):
            self.verify()

    def test_symlink_member_is_rejected(self):
        self.create()
        path = self.output / "code-tests.json"
        backup = self.root / "test-result.json"
        path.rename(backup)
        path.symlink_to(backup)
        with self.assertRaisesRegex(ValueError, "REGULAR_FILE"):
            self.verify()

    def test_manifest_symlink_cannot_overwrite_another_file(self):
        target = self.root / "do-not-change.txt"
        target.write_text("unchanged")
        (self.output / "release.json").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "REGULAR_FILE"):
            self.create()
        self.assertEqual(target.read_text(), "unchanged")

    def test_test_failure_or_nonzero_counter_cannot_publish(self):
        receipt = json.loads((self.output / "functional.json").read_text())
        for value in (1, True, "0"):
            with self.subTest(value=value):
                self.write("functional.json", {**receipt, "real_orders_sent": value})
                with self.assertRaisesRegex(ValueError, "FUNCTIONAL"):
                    self.create()

    def test_empty_code_suite_cannot_publish(self):
        receipt = json.loads((self.output / "code-tests.json").read_text())
        self.write("code-tests.json", {**receipt, "collected": 0, "executed": 0})
        with self.assertRaisesRegex(ValueError, "CODE_TESTS"):
            self.create()

    def test_dirty_checkout_cannot_claim_its_head(self):
        (self.repo / "app.py").write_text("answer = 99\n")
        with self.assertRaisesRegex(ValueError, "CHECKOUT_DIRTY"):
            self.create()

    def test_bundle_with_rewritten_manifest_still_must_match_git(self):
        (self.repo / "app.py").write_text("answer = 99\n")
        build_bundle(self.repo, self.output / "porota-deploy-bundle-v2.tgz",
                     self.output / "porota-deploy-bundle-v2-manifest.json")
        self.git("checkout", "--", "app.py")
        with self.assertRaisesRegex(ValueError, "SOURCE_BYTES"):
            self.create()

    def test_image_environment_cannot_claim_another_source(self):
        config = json.dumps({"config": {"Env": ["POROTA_BUILD_SHA=" + "f" * 40,
                                               "POROTA_CANDIDATE_TREE_SHA=" + self.tree]}}).encode()
        digest = hashlib.sha256(config).hexdigest()
        self.image_id = "sha256:" + digest
        _tar(self.output / "porota-predeploy-image.tar.gz", {
            "manifest.json": json.dumps([{"Config": digest + ".json", "Layers": []}]).encode(),
            digest + ".json": config})
        with self.assertRaisesRegex(ValueError, "IMAGE_SOURCE"):
            self.create()


if __name__ == "__main__":
    unittest.main()
