"""Real owned child FIN and adversarial namespace custody; no heavy workload."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from scripts import porota_predeploy_cleanup as custody
from scripts import rc6_authenticated_fixture_lifecycle as lifecycle
from tests.test_rc6_heavy_test_preflight import binding


class FixtureLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rc6-lifecycle-cheap-")
        self.parent = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.namespace = lifecycle.create_namespace(self.parent, binding())

    def produce(self, *, exit_code=0):
        code = ("from pathlib import Path; import sys; "
                "Path('raw-control.json').write_text('{\\\"controlled\\\":true}\\n'); "
                "print('CONTROLLED_OWN_PRODUCER_FIN'); sys.exit(" + str(exit_code) + ")")
        kernel, fin = lifecycle.execute_owned(self.namespace, [sys.executable, "-I", "-B", "-c", code],
            cwd=self.namespace.path, environ=dict(os.environ), timeout_seconds=10)
        self.assertTrue(kernel["actual_child_reaped"])
        self.assertEqual(kernel["wait4_reaped_pid"], kernel["pid"])
        self.assertEqual(kernel["returncode"], exit_code)
        return fin

    def capture(self, fin):
        return lifecycle.capture_required_evidence(self.namespace, fin, self.parent / "preserved-capture",
                                                   ["producer-native.log", "raw-control.json"])

    def test_cleanup_requires_actual_fin_and_verified_required_capture(self):
        with self.assertRaisesRegex(custody.CleanupRejected, "ACTUAL_OWNED_FIN_REQUIRED"):
            lifecycle.cleanup_namespace(self.namespace, {"owned_fin_closed": True}, None)
        self.assertTrue(self.namespace.path.is_dir())
        fin = self.produce()
        with self.assertRaisesRegex(custody.CleanupRejected, "CAPTURE_MISSING"):
            lifecycle.cleanup_namespace(self.namespace, fin, {"captured": True})
        capture = self.capture(fin)
        receipt = lifecycle.cleanup_namespace(self.namespace, fin, capture)
        self.assertTrue(receipt["namespace_removed"])
        self.assertLessEqual(receipt["retained_entries_before"], 100000)
        self.assertEqual(receipt["foreign_paths_removed"], 0)
        self.assertFalse(receipt["global_cleanup_claimed"])
        # df describes the shared filesystem, whose other producers may write
        # concurrently. Authenticate removal of OUR inode and keep both actual
        # measurements; a global free-space increase is not causal proof.
        self.assertTrue(receipt["directory_inode_removed"])
        self.assertGreater(receipt["allocated_bytes_before"], 0)
        self.assertEqual(receipt["capacity_after"]["filesystem_device"],
                         receipt["capacity_before"]["filesystem_device"])
        self.assertEqual(receipt["capacity_after"]["mount_id"], receipt["capacity_before"]["mount_id"])
        lifecycle.verify_capture(self.namespace, capture)

    def test_mutating_native_failed_kernel_cannot_manufacture_green(self):
        fin = self.produce(exit_code=1)
        fin.kernel["returncode"] = 0
        with self.assertRaisesRegex(custody.CleanupRejected, "ORIGINAL_FIN_KERNEL_CHANGED"):
            self.capture(fin)
        self.assertTrue(self.namespace.path.is_dir())

    def test_own_symlink_and_fifo_removed_without_following_outside_target(self):
        outside = self.parent / "foreign-preserved.raw"
        outside.write_bytes(b"foreign data must remain byte/stat unchanged")
        before = custody.identity(outside.lstat())
        (self.namespace.path / "outside-alias").symlink_to(outside)
        os.mkfifo(self.namespace.path / "generated-fifo", 0o600)
        payload = self.namespace.path / "own-generated.raw"
        payload.write_bytes(b"discardable own generated payload")
        os.link(payload, self.namespace.path / "own-second-link.raw")
        fin = self.produce()
        capture = self.capture(fin)
        receipt = lifecycle.cleanup_namespace(self.namespace, fin, capture)
        self.assertFalse(receipt["link_targets_followed"])
        self.assertEqual(custody.identity(outside.lstat()), before)
        self.assertEqual(outside.read_bytes(), b"foreign data must remain byte/stat unchanged")
        self.assertFalse(self.namespace.path.exists())

    def test_external_hardlink_blocks_before_first_delete(self):
        outside = self.parent / "foreign-preserved.raw"
        outside.write_bytes(b"must preserve foreign hardlinked inode")
        os.link(outside, self.namespace.path / "external-hardlink")
        fin = self.produce()
        capture = self.capture(fin)
        before = custody.identity(outside.lstat())
        with self.assertRaisesRegex(custody.CleanupRejected, "EXTERNAL_HARDLINK_BLOCKED"):
            lifecycle.cleanup_namespace(self.namespace, fin, capture)
        self.assertTrue((self.namespace.path / lifecycle.MARKER).exists())
        self.assertEqual(custody.identity(outside.lstat()), before)
        self.assertEqual((self.namespace.path / "external-hardlink").stat().st_nlink, 2)

    def test_missing_or_corrupt_capture_cannot_authorize_removal(self):
        fin = self.produce()
        with self.assertRaises(FileNotFoundError):
            lifecycle.capture_required_evidence(self.namespace, fin, self.parent / "missing-capture", ["missing.raw"])
        self.assertTrue(self.namespace.path.exists())
        capture = self.capture(fin)
        payload = capture.path / capture.files[-1]["capture_file"]
        payload.write_bytes(b"unverified replacement")
        with self.assertRaises(custody.CleanupRejected):
            lifecycle.cleanup_namespace(self.namespace, fin, capture)
        self.assertTrue((self.namespace.path / lifecycle.MARKER).exists())

    def test_foreign_uid_or_mount_blocks_before_first_unlink(self):
        fin = self.produce()
        capture = self.capture(fin)
        (self.namespace.path / "controlled-member").write_bytes(b"owned test member")
        original_mount = custody.require_member_mount
        for fault in ("uid", "mount"):
            def fault_member(parent, name, expected):
                if name == "controlled-member":
                    if fault == "mount":
                        raise custody.CleanupRejected("PRIVATE_NAMESPACE_MOUNT")
                    real = original_mount(parent, name, expected)
                    values = list(real)
                    values[4] = real.st_uid + 1
                    return os.stat_result(values)
                return original_mount(parent, name, expected)
            with mock.patch.object(custody, "require_member_mount", side_effect=fault_member):
                with self.assertRaises(custody.CleanupRejected):
                    lifecycle.cleanup_namespace(self.namespace, fin, capture)
            self.assertTrue((self.namespace.path / lifecycle.MARKER).exists())

    def test_root_or_marker_rebinding_blocks_cleanup(self):
        fin = self.produce()
        capture = self.capture(fin)
        original = self.namespace.path
        preserved = original.with_name("preserved-original")
        original.rename(preserved)
        original.mkdir(mode=0o700)
        with self.assertRaisesRegex(custody.CleanupRejected, "NAMESPACE_REBOUND"):
            lifecycle.cleanup_namespace(self.namespace, fin, capture)
        self.assertTrue((preserved / lifecycle.MARKER).exists())

    def test_native_failed_phase_can_be_preserved_cleaned_but_never_turn_green(self):
        fin = self.produce(exit_code=1)
        capture = self.capture(fin)
        receipt = lifecycle.cleanup_namespace(self.namespace, fin, capture)
        self.assertTrue(receipt["actual_owned_fin_closed"])
        self.assertFalse(receipt["phase_green"])
        self.assertTrue(receipt["namespace_removed"])

    def test_new_member_between_inventory_passes_blocks_before_first_unlink(self):
        fin = self.produce()
        capture = self.capture(fin)
        original = lifecycle.inventory
        calls = 0
        def measured_then_race(namespace):
            nonlocal calls
            result = original(namespace)
            calls += 1
            if calls == 1:
                (namespace.path / "late-concurrent-member").write_bytes(b"preserve race evidence")
            return result
        with mock.patch.object(lifecycle, "inventory", side_effect=measured_then_race):
            with self.assertRaisesRegex(custody.CleanupRejected, "CHANGED_BEFORE_FIRST_UNLINK"):
                lifecycle.cleanup_namespace(self.namespace, fin, capture)
        self.assertTrue((self.namespace.path / lifecycle.MARKER).exists())
        self.assertTrue((self.namespace.path / "late-concurrent-member").exists())

    def test_a_copied_or_forged_namespace_record_does_not_grant_deletion_authority(self):
        forged = copy.copy(self.namespace)
        with self.assertRaisesRegex(custody.CleanupRejected, "NOT_CREATED_BY_THIS_OWNER"):
            lifecycle.authenticate(forged)
        claim = lifecycle.namespace_receipt(self.namespace)
        path = lifecycle.validate_consumer_receipt(claim, candidate_sha=binding()["candidate_sha"],
                                                  candidate_tree=binding()["candidate_tree"])
        self.assertEqual(path, self.namespace.path)
        with self.assertRaisesRegex(custody.CleanupRejected, "AUTHENTICATED_NAMESPACE_REQUIRED"):
            lifecycle.authenticate(claim)
        with self.assertRaisesRegex(custody.CleanupRejected, "CANDIDATE_MISMATCH"):
            lifecycle.validate_consumer_receipt(claim, candidate_sha="d" * 40, candidate_tree=binding()["candidate_tree"])


class ReadonlyConstructionTests(unittest.TestCase):
    def tiny_owned_archive(self):
        from tests.rc6_readonly_complete_archive_fixture import ArchiveRegistration, ReadonlyArchive
        temp = tempfile.TemporaryDirectory(prefix="rc6-source-quiescence-cheap-")
        self.addCleanup(temp.cleanup)
        parent = Path(temp.name)
        root = parent / "source"
        root.mkdir(mode=0o700)
        payload = b"VALUE = 1\n"
        (root / "caller.py").write_bytes(payload)
        (root / "caller.py").chmod(0o644)
        (parent / "source.tar").write_bytes(b"controlled constructor archive bytes")
        (parent / "source.commit.raw").write_bytes(b"controlled raw metadata bytes")
        index = parent / "source.index.json"
        index.write_text(json.dumps({"schema": "rc6.complete-archive-source-pin.v1", "source_sha": "a" * 40,
            "source_tree": "b" * 40, "overlay_count": 0, "files": {"caller.py": hashlib.sha256(payload).hexdigest()},
            "modes": {"caller.py": "100644"}, "blob_ids": {"caller.py": "c" * 40}}))
        controls = parent / "controls"
        controls.mkdir(mode=0o700)
        return ReadonlyArchive((root, index, "b" * 40), ArchiveRegistration(), controls)

    def original_owned_archive(self, *, existing_control=False):
        from _pytest.tmpdir import TempPathFactory
        from tests import rc6_readonly_complete_archive_fixture as fixture
        temp = tempfile.TemporaryDirectory(prefix="rc6-original-source-producer-cheap-")
        self.addCleanup(temp.cleanup)
        parent, repository = Path(temp.name), Path(temp.name) / "tiny-original-git-input"
        repository.mkdir(mode=0o700)
        (repository / "caller.py").write_bytes(b"VALUE = 1\n")
        (repository / "caller.py").chmod(0o644)
        for arguments in (("init", "--template=", "-q"), ("config", "user.name", "Original producer unit"),
                          ("config", "user.email", "fixture@example.invalid"), ("add", "caller.py"),
                          ("commit", "-qm", "Explicit tiny original producer input")):
            subprocess.run(["git", "-C", str(repository), *arguments], check=True, capture_output=True,
                timeout=10, env=dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull))
        factory = TempPathFactory(parent / "fresh-factory", 0, "all", lambda *args: None, _ispytest=True)
        registry = fixture.ArchiveRegistration()
        # Only the original producer's input repository changes for this tiny
        # explicit unit. Its body/factory remain actual originals; this is not
        # candidate/product/artifact qualification.
        with mock.patch.dict(fixture._ORIGINAL_PROVIDER.__globals__, {"ROOT": repository}):
            triple, token = registry.construct_original_source(factory)
        controls = factory.mktemp("original-source-controls")
        if existing_control:
            (controls / "prior-source-lease.json").write_bytes(b"existing control must be preserved")
        return fixture.ReadonlyArchive(triple, registry, controls, construction=token), token, factory

    def test_all_owned_constructed_members_synced_before_source10_freeze_without_payload_read(self):
        synced = []
        real = os.fsync
        def observe(fd):
            details = os.fstat(fd)
            synced.append((details.st_dev, details.st_ino, details.st_mode))
            return real(fd)
        with mock.patch.object(os, "fsync", side_effect=observe):
            archive, _token, _factory = self.original_owned_archive()
        self.assertEqual(archive.construction_quiescence["files_fsynced_before_initial_snapshot"], 4)
        self.assertEqual(archive.construction_quiescence["source_payload_bytes_read"], 0)
        self.assertEqual(archive.construction_quiescence["metadata_fields_removed_from_guard"], [])
        self.assertFalse(archive.construction_quiescence["global_sync_performed"])
        self.assertEqual(len(synced), 5)  # four exact files plus own source directory.
        self.assertEqual(sum(stat.S_ISREG(row[2]) for row in synced), 4)
        self.assertEqual(archive._compare(archive.initial, archive._snapshot()), [])

    def test_source_tuple_without_original_producer_has_no_sync_authority(self):
        with mock.patch.object(os, "fsync", side_effect=AssertionError("Source tuple may not authorize sync")):
            archive = self.tiny_owned_archive()
        self.assertEqual(archive.construction_quiescence["status"], "BLOCKED_NO_ORIGINAL_CONSTRUCTION_AUTHORITY")
        self.assertEqual(archive.construction_quiescence["files_fsynced_before_initial_snapshot"], 0)
        self.assertEqual(archive._compare(archive.initial, archive._snapshot()), [])

    def test_original_construction_token_is_single_use_and_requires_empty_control_namespace(self):
        from tests.rc6_readonly_complete_archive_fixture import ReadonlyArchive
        archive, token, factory = self.original_owned_archive()
        controls = factory.mktemp("never-reuse-original-source")
        with self.assertRaisesRegex(RuntimeError, "FRESH_SINGLE_USE_ORIGINAL_CONSTRUCTION_REQUIRED"):
            ReadonlyArchive(archive.triple, archive.registry, controls, construction=token)
        self.assertEqual(archive._compare(archive.initial, archive._snapshot()), [])
        with mock.patch.object(os, "fsync", side_effect=AssertionError("Existing controls cannot authorize sync")):
            with self.assertRaisesRegex(RuntimeError, "FRESH_CONSTRUCTION_DIRECTORY_NOT_EMPTY"):
                self.original_owned_archive(existing_control=True)

    def test_real_post_freeze_mutation_and_block_drift_still_fail_closed(self):
        archive = self.tiny_owned_archive()
        after = archive._snapshot()
        changed = copy.deepcopy(after)
        changed["source.tar"]["st_blocks"] += 8
        with self.assertRaisesRegex(RuntimeError, "SOURCE10_CHANGED"):
            archive._compare(archive.initial, changed)
        (archive.root / "caller.py").write_bytes(b"VALUE = 2\n")
        with self.assertRaisesRegex(RuntimeError, "SOURCE10_CHANGED"):
            archive._compare(archive.initial, archive._snapshot())


class AuthenticatedPhaseNamespaceTests(unittest.TestCase):
    def test_short_tmp_never_escapes_owner_and_sha_rebinding_is_refused(self):
        from scripts import rc6_controlled_governed_runner as governed
        with tempfile.TemporaryDirectory(prefix="r6-", dir="/tmp") as temporary:
            parent = Path(temporary)
            namespace = lifecycle.create_namespace(parent, binding())
            output = namespace.path / "out"
            output.mkdir(mode=0o700)
            source = parent / "literal-source"
            source.mkdir(mode=0o700)
            claim = lifecycle.namespace_receipt(namespace)
            previous_tempdir = tempfile.tempdir
            try:
                with mock.patch.dict(os.environ, clear=False):
                    result = governed.phase_namespace(source, output, "collection", authenticated_binding=claim)
                    self.assertTrue(Path(result["tmpdir"]).is_relative_to(namespace.path))
                    self.assertLessEqual(len(os.fsencode(result["tmpdir"])), 50)
                    self.assertFalse(result["tmpdir_outside_owner_namespace"])
            finally:
                tempfile.tempdir = previous_tempdir
            with self.assertRaisesRegex(ValueError, "AUTHENTICATED_GOVERNED_NAMESPACE_REQUIRED"):
                governed.phase_namespace(source, output, "execution")
            foreign_output = parent / "foreign-output"
            foreign_output.mkdir(mode=0o700)
            with self.assertRaisesRegex(ValueError, "OUTPUT_OUTSIDE_AUTHENTICATED_NAMESPACE"):
                governed.phase_namespace(source, foreign_output, "execution", authenticated_binding=claim)


class FreshNativeConstructionActorTests(unittest.TestCase):
    def owned_control(self):
        temp = tempfile.TemporaryDirectory(prefix="rc6-construction-actor-cheap-")
        self.addCleanup(temp.cleanup)
        parent = Path(temp.name)
        repo, runner = parent / "source", parent / "runner"
        repo.mkdir(mode=0o755)
        runner.mkdir(mode=0o755)
        context = {"repository": custody.REPOSITORY, "workflow_path": custody.WORKFLOW,
            "event": "pull_request", "run_id": "471", "run_attempt": "2",
            "candidate_sha": "a" * 40, "candidate_tree": "b" * 40}

        def no_runtime_docker(*args, timeout):
            self.assertGreater(timeout, 0)
            if args == ("info", "--format", "{{.DockerRootDir}}"):
                return str(parent)
            self.assertEqual(args[:2], ("image", "ls"))
            return ""

        _scope, control = custody.prepare(repo, runner, context, run=no_runtime_docker)
        return control

    def test_native_constructor_red_restores_original_mask_and_propagates_error(self):
        from scripts import porota_predeploy_test_workspace as workspace
        control = self.owned_control()
        caller_mask = os.umask(0o077)

        def unavailable_constructor(_self, path):
            self.assertEqual(Path(path), Path(control["private_root"]) / "venv")
            current = os.umask(0o022)
            self.assertEqual(current, 0o022)
            raise RuntimeError("CONTROLLED_ORIGINAL_ENVBUILDER_RED")

        try:
            with mock.patch.object(workspace.venv.EnvBuilder, "create", unavailable_constructor):
                with self.assertRaisesRegex(RuntimeError, "CONTROLLED_ORIGINAL_ENVBUILDER_RED"):
                    workspace.construct_private_venv(control)
            observed = os.umask(0o077)
            self.assertEqual(observed, 0o077)
            self.assertEqual(stat.S_IMODE((Path(control["private_root"]) / "venv").stat().st_mode), 0o755)
        finally:
            os.umask(caller_mask)

    def test_other_live_thread_blocks_before_fresh_directory_or_global_mask_change(self):
        from scripts import porota_predeploy_test_workspace as workspace
        control = self.owned_control()
        ready, finish = threading.Event(), threading.Event()
        def owned_thread():
            ready.set()
            finish.wait(timeout=5)
        thread = threading.Thread(target=owned_thread)
        thread.start()
        self.assertTrue(ready.wait(timeout=1))
        caller_mask = os.umask(0o077)
        try:
            with self.assertRaisesRegex(custody.CleanupRejected, "SINGLE_THREAD_CONSTRUCTION_REQUIRED"):
                workspace.construct_private_venv(control)
            self.assertFalse((Path(control["private_root"]) / "venv").exists())
            self.assertEqual(os.umask(0o077), 0o077)
        finally:
            os.umask(caller_mask)
            finish.set()
            thread.join(timeout=1)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
