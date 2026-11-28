"""Mocked pytest replay: global collection and truly concurrent exact shards."""

from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from rc6_audit_evidence import sqlite_scratch as scratch
from scripts import rc6_ci_parallel_runner as runner
from scripts import rc6_ci_pytest_identity as identity
from scripts import rc6_ci_shard_quorum as shards


SHA = "a" * 40
TREE = "b" * 40
NODES = [f"tests/test_ci_stub.py::test_case[{i}]" for i in range(12)]
REPO = Path(__file__).resolve().parents[1]


class FakePytest:
    """Writes precise private manifests and JUnit as a stand-in for pytest."""

    def __init__(self, *, missing=False, skipped=False, fail_status=False,
                 mismatched_tree=False, extra_test=False, duplicate=False,
                 collection_rc=0, dirty_tree=False, require_parallel=True,
                 nodes=None):
        self.nodes = list(NODES if nodes is None else nodes)
        self.missing = missing
        self.skipped = skipped
        self.fail_status = fail_status
        self.mismatched_tree = mismatched_tree
        self.extra_test = extra_test
        self.duplicate = duplicate
        self.collection_rc = collection_rc
        self.dirty_tree = dirty_tree
        self.calls = []
        self.barrier = threading.Barrier(2) if require_parallel else None

    def __call__(self, argv, *, cwd, env, timeout=3600):
        self.calls.append(argv)
        if argv[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(argv, 0, SHA + "\n", "")
        if argv[:3] == ["git", "rev-parse", "HEAD^{tree}"]:
            return subprocess.CompletedProcess(argv, 0, TREE + "\n", "")
        if argv[:3] == ["git", "diff", "--quiet"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["git", "status", "--porcelain=v1"]:
            return subprocess.CompletedProcess(argv, 0,
                                               "?? untracked.py\n" if self.dirty_tree else "", "")
        if "--collect-only" in argv:
            root_arg, *optional_exclusions = argv[argv.index("-q") + 1:]
            assert root_arg == "." and all(value.startswith("--ignore=") for value in optional_exclusions)
            root = Path(env["RC6_CI_MANIFEST_PATH"])
            if self.collection_rc == 0:
                tree = "c" * 40 if self.mismatched_tree else TREE
                root.write_text(json.dumps(shards.manifest(SHA, tree, self.nodes)), encoding="utf-8")
            return subprocess.CompletedProcess(argv, self.collection_rc, "not public", "")
        if "--" not in argv:
            raise AssertionError("Pytest shard must use -- end-of-options marker")
        cases = argv[argv.index("--") + 1:]
        if self.barrier:
            self.barrier.wait(timeout=5)
        is_one = self.nodes[0] in cases
        actual = cases[:-1] if self.missing and is_one else cases
        if self.duplicate and is_one:
            actual = [*actual, actual[0]]
        Path(env["RC6_CI_MANIFEST_PATH"]).write_text(
            json.dumps(shards.manifest(SHA, TREE, actual)) if len(actual) == len(set(actual))
            else json.dumps({"invalid": "duplicate"}), encoding="utf-8")
        xml_path = Path(next(value.split("=", 1)[1]
                             for value in argv if value.startswith("--junitxml=")))
        xml = ET.Element("testsuite", tests=str(len(actual)))
        for nodeid in actual:
            case = ET.SubElement(xml, "testcase", name="case")
            props = ET.SubElement(case, "properties")
            ET.SubElement(props, "property", name=identity.PROPERTY,
                          value=identity.nodeid_digest(nodeid))
            if self.skipped and is_one:
                ET.SubElement(case, "skipped", type="pytest.xfail")
        if self.extra_test and is_one:
            ET.SubElement(xml, "testcase", name="extra")
        xml_path.write_bytes(ET.tostring(xml, encoding="utf-8"))
        return subprocess.CompletedProcess(argv, 1 if self.fail_status and is_one else 0,
                                           "sensitive-log-not-public", "")


class TestParallelProofRunner(unittest.TestCase):
    def setUp(self):
        # The immutable image is readable by botuser, but its parent is '/'.
        # Every replay owns writable scratch and exercises Actions' explicit
        # RUNNER_TEMP branch instead of relying on the checkout's permissions.
        temporary = tempfile.TemporaryDirectory(
            prefix="rc6-runner-test-", dir=os.environ.get("RUNNER_TEMP") or None)
        self.addCleanup(temporary.cleanup)
        self.temporary_root = Path(temporary.name)
        environment = patch.dict(os.environ, {
            "GITHUB_ACTIONS": "true", "RUNNER_TEMP": temporary.name,
        })
        environment.start()
        self.addCleanup(environment.stop)

    def test_pilot_shards_use_disk_backed_basetemp_despite_ambient_tmpfs(self):
        """Pytest tmp_path must not silently inherit the runner's /tmp mount."""
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            disk_root = Path(directory)
            output = disk_root / "combined.xml"
            fake = FakePytest()
            with patch.dict(os.environ, {"RUNNER_TEMP": directory, "TMPDIR": "/tmp"}), \
                    patch.object(runner, "_invoke", fake):
                receipt = runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                     output_junit=output)
            self.assertEqual(receipt["status"], "GREEN_QUORUM_OFFLINE_ONLY")
            shard_commands = [cmd for cmd in fake.calls if "--junitxml=" in " ".join(cmd)]
            self.assertEqual(len(shard_commands), 2)
            for command in shard_commands:
                basetemp = Path(next(arg.split("=", 1)[1] for arg in command
                                     if arg.startswith("--basetemp=")))
                self.assertTrue(basetemp.is_relative_to(disk_root))
                self.assertNotEqual(scratch._storage_type(basetemp), "tmpfs")

    def test_tmpfs_runner_temp_fails_closed_before_collecting_pytest(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            fake = FakePytest()
            with patch.dict(os.environ, {"RUNNER_TEMP": directory}), \
                    patch.object(scratch, "_storage_type", side_effect=scratch.SnapshotError(
                        "SCRATCH_DISK_BACKED_REQUIRED")), \
                    patch.object(runner, "_invoke", fake):
                with self.assertRaises(runner.ParallelRunnerRed):
                    runner.run(SHA, TREE, repo=REPO, shard_count=2,
                               output_junit=Path(directory) / "combined.xml")
            self.assertFalse(any("pytest" in " ".join(cmd) for cmd in fake.calls))

    def test_unsafe_runner_temp_cannot_write_into_candidate_or_follow_symlink(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            link = Path(directory) / "link"
            link.symlink_to(Path(directory))
            for unsafe in (REPO, link, Path(directory) / "missing"):
                with self.subTest(unsafe=str(unsafe)):
                    fake = FakePytest()
                    with patch.dict(os.environ, {"RUNNER_TEMP": str(unsafe)}), \
                            patch.object(runner, "_invoke", fake):
                        with self.assertRaises(runner.ParallelRunnerRed):
                            runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                       output_junit=Path(directory) / "combined.xml")
                    self.assertFalse(any("pytest" in " ".join(cmd) for cmd in fake.calls))

    def test_self_referential_runner_temp_symlink_emits_normalized_red(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            loop = Path(directory) / "loop"
            loop.symlink_to("loop")
            fake = FakePytest()
            stderr = io.StringIO()
            with patch.dict(os.environ, {"RUNNER_TEMP": str(loop)}), \
                    patch.object(runner, "_invoke", fake), redirect_stderr(stderr):
                result = runner.main([
                    "--source-sha", SHA, "--source-tree-sha", TREE,
                    "--repo", str(REPO), "--shards", "2",
                    "--output-junit", str(Path(directory) / "combined.xml"),
                ])
            self.assertEqual(result, 2)
            self.assertEqual(stderr.getvalue(),
                             "RC6_PARALLEL_PILOT=RED|automatic_go=NO\n")
            self.assertFalse(any("pytest" in " ".join(cmd) for cmd in fake.calls))

    def test_github_runner_missing_temp_root_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            fake = FakePytest()
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "true", "RUNNER_TEMP": ""}), \
                    patch.object(runner, "_invoke", fake):
                with self.assertRaises(runner.ParallelRunnerRed):
                    runner.run(SHA, TREE, repo=REPO, shard_count=2,
                               output_junit=Path(directory) / "combined.xml")
            self.assertFalse(any("pytest" in " ".join(cmd) for cmd in fake.calls))

    def test_automatic_global_discovery_then_exact_parallel_shards(self):
        fake = FakePytest()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "combined.xml"
            with patch.object(runner, "_invoke", fake):
                receipt = runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                     output_junit=output)
            self.assertEqual(receipt["status"], "GREEN_QUORUM_OFFLINE_ONLY")
            self.assertEqual((receipt["discovered"], receipt["executed"]), (12, 12))
            self.assertEqual(len(list(ET.parse(output).iter("testcase"))), 12)
            self.assertFalse(receipt["automatic_go"])
            self.assertFalse(receipt["ci_activated"])
            self.assertEqual(set(receipt["phase_elapsed_ms"]),
                             {"collect", "parallel_test", "quorum"})
            self.assertEqual(len([c for c in fake.calls if "--collect-only" in c]), 1)
            self.assertEqual(len([c for c in fake.calls if "--junitxml=" in " ".join(c)]), 2)
            self.assertEqual(len([c for c in fake.calls if c[:2] == ["git", "rev-parse"]]), 2)
            self.assertNotIn("sensitive-log-not-public", str(receipt))

    def test_progress_callback_reports_only_fixed_phases_and_shard_counters(self):
        fake = FakePytest(require_parallel=False)
        progress = []
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner, "_invoke", fake):
                proof = runner.run(
                    SHA, TREE, repo=REPO, shard_count=2, max_workers=1,
                    output_junit=Path(directory) / "combined.xml",
                    progress=lambda phase, index, completed:
                        progress.append((phase, index, completed)))
        self.assertEqual(proof["status"], "GREEN_QUORUM_OFFLINE_ONLY")
        self.assertEqual(progress, [
            ("SOURCE_CHECKOUT", -1, 0),
            ("COLLECTION", -1, 0),
            ("COLLECTION_COMPLETE", -1, 0),
            ("SHARD_RUNNING", 0, 0),
            ("SHARD_COMPLETE", 0, 1),
            ("SHARD_RUNNING", 1, 1),
            ("SHARD_COMPLETE", 1, 2),
            ("JUNIT_QUORUM", -1, 2),
        ])
        self.assertNotIn("test_example", str(progress))

    def test_governed_serial_missing_manifest_stops_before_next_shard(self):
        # Regress the native failure: a missing manifest must NOT trigger
        # queued tests 2..16 while an executor waits on unfinished futures.
        fake = FakePytest(require_parallel=False)
        progress = []

        def omit_first_manifest(argv, *, cwd, env, timeout=3600):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if any(arg.startswith("--junitxml=") for arg in argv):
                Path(env["RC6_CI_MANIFEST_PATH"]).unlink(missing_ok=True)
            return result

        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            output = Path(directory) / "combined.xml"
            with patch.object(runner, "_invoke", omit_first_manifest):
                with self.assertRaises(runner.ParallelRunnerRed) as caught:
                    runner.run(
                        SHA, TREE, repo=REPO, shard_count=2, max_workers=1,
                        output_junit=output,
                        progress=lambda phase, index, completed:
                            progress.append((phase, index, completed)))
            self.assertEqual(caught.exception.reason, "SHARD_REPORT_INVALID")
            self.assertFalse(output.exists())
        invoked = [cmd for cmd in fake.calls
                   if any(arg.startswith("--junitxml=") for arg in cmd)]
        self.assertEqual(len(invoked), 1)
        self.assertEqual(progress[-1], ("SHARD_RUNNING", 0, 0))
        self.assertNotIn("SHARD_COMPLETE", [phase for phase, _, _ in progress])

    def test_missing_shard_manifest_must_not_hide_sigkill_or_last_module(self):
        fake = FakePytest(require_parallel=False)

        def missing_manifest_after_signal(argv, *, cwd, env, timeout=3600):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if any(arg.startswith("--junitxml=") for arg in argv):
                Path(env["RC6_CI_MANIFEST_PATH"]).unlink(missing_ok=True)
                return subprocess.CompletedProcess(
                    argv, -9,
                    "\nRC6_CI_ACTIVE_MODULE:tests/test_rc6_ci_parallel_runner.py\n",
                    "PRIVATE_STDERR")
            return result

        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            output = Path(directory) / "combined.xml"
            with patch.object(runner, "_invoke", missing_manifest_after_signal):
                with self.assertRaises(runner.ParallelRunnerRed) as caught:
                    runner.run(SHA, TREE, repo=REPO, shard_count=2,
                               max_workers=1, output_junit=output)
            self.assertFalse(output.exists())
        error = caught.exception
        self.assertEqual(error.reason, "SHARD_PROCESS_SIGNAL")
        self.assertEqual(error.process_exit_code, -9)
        self.assertEqual(error.last_active_module,
                         "tests/test_rc6_ci_parallel_runner.py")
        self.assertEqual(len([cmd for cmd in fake.calls
                              if any(a.startswith("--junitxml=") for a in cmd)]), 1)
        self.assertNotIn("PRIVATE_STDERR", str(error))

    def test_governed_serial_nonzero_pytest_stops_before_next_shard(self):
        fake = FakePytest(require_parallel=False)

        def first_pytest_error(argv, *, cwd, env, timeout=3600):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if any(arg.startswith("--junitxml=") for arg in argv):
                return subprocess.CompletedProcess(argv, 1, "PRIVATE_PYTEST", "")
            return result

        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            with patch.object(runner, "_invoke", first_pytest_error):
                with self.assertRaises(runner.ParallelRunnerRed) as caught:
                    runner.run(
                        SHA, TREE, repo=REPO, shard_count=2, max_workers=1,
                        output_junit=Path(directory) / "combined.xml")
            self.assertEqual(caught.exception.reason, "SHARD_PYTEST_NONZERO")
        invoked = [cmd for cmd in fake.calls
                   if any(arg.startswith("--junitxml=") for arg in cmd)]
        self.assertEqual(len(invoked), 1)
        self.assertNotIn("PRIVATE_PYTEST", str(caught.exception))


    def test_failed_junit_reports_only_safe_existing_module(self):
        # A private parameter/failure body never reaches the public receipt.
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            xml = Path(directory) / "failure.xml"
            xml.write_text(
                '<testsuite><testcase file="tests/test_rc6_ci_parallel_runner.py"'
                ' name="case[PRIVATE_PARAMETER]"><failure>PRIVATE_MESSAGE'
                '</failure></testcase></testsuite>', encoding="utf-8")
            self.assertEqual(runner._failed_module_from_junit(xml, REPO),
                             "tests/test_rc6_ci_parallel_runner.py")
            self.assertNotIn("PRIVATE_", str(runner.ParallelRunnerRed(
                "SHARD_PYTEST_NONZERO",
                failure_module=runner._failed_module_from_junit(xml, REPO))))
            xml.write_text(
                '<testsuite><testcase file="../PRIVATE_SECRET.py">'
                '<error>PRIVATE_MESSAGE</error></testcase></testsuite>',
                encoding="utf-8")
            self.assertIsNone(runner._failed_module_from_junit(xml, REPO))
            self.assertIsNone(runner.ParallelRunnerRed(
                "SHARD_PYTEST_NONZERO",
                failure_module="../PRIVATE_SECRET.py").failure_module)
            xml.unlink()
            xml.symlink_to(Path(directory) / "nonexistent.xml")
            self.assertIsNone(runner._failed_module_from_junit(xml, REPO))

    def test_junit_xunit2_classname_reports_module_without_parameters(self):
        # Real pytest xunit2 XML has classname+name but may have no file attr.
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            xml = Path(directory) / "xunit2.xml"
            xml.write_text(
                '<testsuites><testsuite>'
                '<testcase classname="tests.test_rc6_ci_parallel_runner'
                '.TestParallelProofRunner" name="test_foo[PRIVATE_PARAMETER]">'
                '<failure>PRIVATE_TRACEBACK</failure></testcase>'
                '</testsuite></testsuites>', encoding="utf-8")
            result = runner._failed_module_from_junit(xml, REPO)
            self.assertEqual(result, "tests/test_rc6_ci_parallel_runner.py")
            self.assertNotIn("PRIVATE", result)
            xml.write_text(
                '<testsuite><testcase classname="tests...PRIVATE"'
                ' name="anything[SECRET]"><failure>SECRET</failure>'
                '</testcase></testsuite>', encoding="utf-8")
            self.assertIsNone(runner._failed_module_from_junit(xml, REPO))

    def test_pytest_short_failure_summary_fallback_is_path_only(self):
        from types import SimpleNamespace
        source = (
            "PRIVATE_LOGS\n"
            "================ short test summary info =================\n"
            "FAILED tests/test_rc6_ci_parallel_runner.py::test_foo"
            "[PRIVATE_PARAMETER] - PRIVATE_TRACEBACK\n"
            "1 failed in 2.00s\n")
        module = runner._failed_module_from_pytest_summary(
            SimpleNamespace(stdout=source), REPO)
        self.assertEqual(module, "tests/test_rc6_ci_parallel_runner.py")
        self.assertNotIn("PRIVATE", module)
        self.assertIsNone(runner._failed_module_from_pytest_summary(
            SimpleNamespace(stdout="FAILED tests/test_rc6_ci_parallel_runner.py"
                            "::test_foo[PRIVATE]"), REPO))
        self.assertIsNone(runner._failed_module_from_pytest_summary(
            SimpleNamespace(stdout="short test summary info\n"
                            "FAILED ../../PRIVATE_SECRET.py::test\n"), REPO))

    def test_opt_in_pytest_module_trace_never_exposes_nodeid_parameters(self):
        from scripts import rc6_ci_pytest_identity as plugin
        output = io.StringIO()
        plugin._LAST_ACTIVE_MODULE = None
        with patch.dict(os.environ, {
                "RC6_CI_IDENTITY_PROOF": "1", "RC6_CI_MODULE_TRACE": "1"}), \
             patch.object(plugin.sys, "__stdout__", output):
            for _ in range(2):
                plugin.pytest_runtest_logstart(
                    "tests/test_rc6_ci_parallel_runner.py::test[PRIVATE_PARAMETER]",
                    ("tests/test_rc6_ci_parallel_runner.py", 7, "PRIVATE_NAME"))
            plugin.pytest_runtest_logstart(
                "../PRIVATE_SECRET.py::test",
                ("../PRIVATE_SECRET.py", 7, "PRIVATE_NAME"))
        self.assertEqual(output.getvalue(),
                         "\nRC6_CI_ACTIVE_MODULE:tests/test_rc6_ci_parallel_runner.py\n")
        plugin._LAST_ACTIVE_MODULE = None

    def test_pytest_progress_prefixed_marker_prefers_newest_safe_module(self):
        from types import SimpleNamespace
        # pytest -q can place its progress '.' directly before the hook
        # marker, which used to hide the final active test module on SIGKILL.
        output = (
            "RC6_CI_ACTIVE_MODULE:tests/test_porota_ci.py\n"
            ".RC6_CI_ACTIVE_MODULE:tests/test_rc6_ci_parallel_runner.py\n")
        self.assertEqual(
            runner._last_active_module_from_output(
                SimpleNamespace(stdout=output), REPO),
            "tests/test_rc6_ci_parallel_runner.py")
        self.assertIsNone(
            runner._last_active_module_from_output(
                SimpleNamespace(stdout="PRIVATE_OUTPUT:"
                                "RC6_CI_ACTIVE_MODULE:"
                                "tests/test_rc6_ci_parallel_runner.py\n"),
                REPO))

    def test_serial_signal_carries_last_source_and_exit_without_next_shard(self):
        fake = FakePytest(require_parallel=False)

        def synthetic_signal(argv, *, cwd, env, timeout=3600):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if any(arg.startswith("--junitxml=") for arg in argv):
                self.assertEqual(env.get("RC6_CI_MODULE_TRACE"), "1")
                return subprocess.CompletedProcess(
                    argv, -9,
                    ".RC6_CI_ACTIVE_MODULE:tests/test_rc6_ci_parallel_runner.py\n",
                    "")
            return result

        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            with patch.object(runner, "_invoke", synthetic_signal):
                with self.assertRaises(runner.ParallelRunnerRed) as caught:
                    runner.run(
                        SHA, TREE, repo=REPO, shard_count=2, max_workers=1,
                        output_junit=Path(directory) / "combined.xml")
        error = caught.exception
        self.assertEqual(error.reason, "SHARD_PROCESS_SIGNAL")
        self.assertEqual(error.process_exit_code, -9)
        self.assertEqual(error.last_active_module,
                         "tests/test_rc6_ci_parallel_runner.py")
        self.assertEqual(len([
            cmd for cmd in fake.calls
            if any(arg.startswith("--junitxml=") for arg in cmd)]), 1)

    def test_synthetic_failures_or_missing_evidence_cannot_be_green(self):
        scenarios = [dict(missing=True), dict(skipped=True), dict(fail_status=True),
                     dict(mismatched_tree=True), dict(extra_test=True),
                     dict(duplicate=True), dict(collection_rc=2), dict(dirty_tree=True)]
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "combined.xml"
                with patch.object(runner, "_invoke", FakePytest(**scenario)):
                    with self.assertRaises(runner.ParallelRunnerRed):
                        runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                   output_junit=output)
                self.assertFalse(output.exists())

    def test_collection_phase_reports_specific_fixed_codes_without_leaking_output(self):
        from types import SimpleNamespace
        self.assertEqual(runner._collection_failure_reason(
            SimpleNamespace(returncode=2,
                            stderr="RC6_SHARD_QUORUM_RED:NODEID_UTF8_BYTES secret-param",
                            stdout="")), "COLLECTION_NODEID_UTF8_BYTES")
        self.assertEqual(runner._collection_failure_reason(
            SimpleNamespace(returncode=2,
                            stderr="RC6_SHARD_QUORUM_RED:NODEID_TOTAL_BYTES_UNBOUNDED",
                            stdout="")), "COLLECTION_NODEID_TOTAL_BYTES_UNBOUNDED")
        self.assertEqual(runner._collection_failure_reason(
            SimpleNamespace(returncode=2,
                            stderr="ModuleNotFoundError: secret_value",
                            stdout="")), "COLLECTION_IMPORT_ERROR")
        self.assertEqual(runner._collection_failure_reason(
            SimpleNamespace(returncode=1,
                            stderr="RC6_SHARD_QUORUM_RED\nprivate nodeid",
                            stdout="")), "COLLECTION_PLUGIN_IDENTITY_RED")
        self.assertEqual(runner._collection_failure_reason(
            SimpleNamespace(returncode=2, stderr="ERROR collecting tests",
                            stdout="")), "COLLECTION_PYTEST_COLLECT_ERROR")
        self.assertEqual(runner._collection_failure_reason(
            SimpleNamespace(returncode=5, stderr="private-path",
                            stdout="")), "COLLECTION_PYTEST_NONZERO")
        self.assertEqual(runner._collection_failure_reason(
            SimpleNamespace(returncode=0, stderr="", stdout="")), "UNVERIFIED")

    def test_source_checkout_dirty_has_fixed_failure_phase(self):
        fake = FakePytest(dirty_tree=True)
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            with patch.object(runner, "_invoke", fake):
                with self.assertRaises(runner.ParallelRunnerRed) as caught:
                    runner.run(SHA, TREE, repo=REPO, shard_count=2,
                               output_junit=Path(directory) / "denied.xml")
            self.assertEqual(caught.exception.reason, "SOURCE_CHECKOUT_DIRTY")

    def test_generated_native_elf_is_only_allowed_untracked_image_file(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            root = Path(directory)
            native = root / "rc6_shadow_runtime" / "_rc6_json_bound.so"
            native.parent.mkdir()
            native.write_bytes(b"\x7fELF" + b"native-synthetic" * 5)
            native.chmod(0o600)
            exact = "?? rc6_shadow_runtime/_rc6_json_bound.so\n"
            self.assertTrue(runner._allow_image_generated_native_status(root, exact))
            self.assertTrue(runner._allow_image_generated_native_status(root, ""))
            self.assertFalse(runner._allow_image_generated_native_status(
                root, exact + "?? tests/test_new.py\n"))
            self.assertFalse(runner._allow_image_generated_native_status(
                root, " M scripts/porota_ci.py\n" + exact))
            native.write_bytes(b"NOT_A_NATIVE_ELF" + b"x" * 50)
            self.assertFalse(runner._allow_image_generated_native_status(root, exact))
            native.write_bytes(b"\x7fELF" + b"native-synthetic" * 5)
            other = root / "extra-hardlink"
            other.hardlink_to(native)
            self.assertFalse(runner._allow_image_generated_native_status(root, exact))
            other.unlink()
            native.unlink()
            native.symlink_to(root / "foreign")
            self.assertFalse(runner._allow_image_generated_native_status(root, exact))

    def test_serial_existing_quorum_checks_all_retirements_exactly(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            path = Path(directory) / "combined.xml"
            nodes = (["tests/test_ci_stub.py::test_retired",
                      "tests/test_ci_stub.py::test_param[0]",
                      "tests/test_ci_stub.py::test_param[1]"]
                     + [f"tests/test_ci_stub.py::test_keep_{i}" for i in range(9)])
            fake = FakePytest(require_parallel=False, nodes=nodes)
            retired = ["tests/test_ci_stub.py::test_retired",
                       "tests/test_ci_stub.py::test_param"]
            with patch.dict(os.environ, {"RUNNER_TEMP": directory}), \
                 patch.object(runner, "_invoke", fake):
                receipt = runner.run(
                    SHA, TREE, repo=REPO, shard_count=2, max_workers=1,
                    retired_nodeids=retired, output_junit=path)
            self.assertEqual(receipt["status"], "GREEN_QUORUM_OFFLINE_ONLY")
            self.assertEqual((receipt["globally_discovered"], receipt["discovered"],
                              receipt["executed"], receipt["retired_testcases"]),
                             (12, 9, 9, 3))
            self.assertEqual(receipt["retired"], sorted(retired))
            self.assertEqual(receipt["execution_workers"], 1)
            self.assertFalse(receipt["ci_activated"])
            self.assertTrue(path.is_file())
            bad = FakePytest(require_parallel=False, nodes=nodes)
            with patch.dict(os.environ, {"RUNNER_TEMP": directory}), \
                 patch.object(runner, "_invoke", bad):
                with self.assertRaises(runner.ParallelRunnerRed):
                    runner.run(
                        SHA, TREE, repo=REPO, shard_count=2, max_workers=1,
                        retired_nodeids=["test_missing.py::test_uncollected"],
                        output_junit=Path(directory) / "reject.xml")

    def test_wrong_sha_tree_shard_count_policy_or_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "combined.xml"
            with patch.object(runner, "_invoke", FakePytest(require_parallel=False)):
                for kwargs in (dict(candidate="f" * 40, tree=TREE, count=2),
                               dict(candidate=SHA, tree="f" * 40, count=2),
                               dict(candidate=SHA, tree=TREE, count=0),
                               dict(candidate=SHA, tree=TREE, count=13)):
                    with self.subTest(kwargs=kwargs):
                        with self.assertRaises(runner.ParallelRunnerRed):
                            runner.run(kwargs["candidate"], kwargs["tree"], repo=REPO,
                                       shard_count=kwargs["count"], output_junit=output)
            self.assertFalse(output.exists())


    def test_pytest_internalerror_hook_writes_private_typed_receipt(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            target = Path(directory) / "internalerror.json"
            with patch.dict(os.environ, {
                    "RC6_CI_IDENTITY_PROOF": "1",
                    "RC6_CI_INTERNAL_ERROR_PATH": str(target)}):
                try:
                    raise RuntimeError("PRIVATE_CREDENTIAL_TOKEN")
                except RuntimeError as error:
                    identity.pytest_internalerror(
                        None, SimpleNamespace(type=type(error), value=error))
            receipt = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(receipt["schema"], "rc6.pytest.internalerror.v1")
            self.assertEqual(receipt["exception_type"], "RuntimeError")
            self.assertEqual(receipt["source_file"],
                             "tests/test_rc6_ci_parallel_runner.py")
            self.assertGreater(receipt["source_line"], 0)
            self.assertEqual(target.stat().st_mode & 0o077, 0)
            self.assertNotIn("PRIVATE_CREDENTIAL_TOKEN", target.read_text())
            self.assertEqual(runner._verified_internalerror_sidecar(target, REPO), {
                "exception_type": "RuntimeError",
                "source_file": "tests/test_rc6_ci_parallel_runner.py",
                "source_line": receipt["source_line"]})

    def test_pytest_internalerror_exit3_stops_before_next_shard(self):
        fake = FakePytest(require_parallel=False)

        def internalerror(argv, *, cwd, env, timeout=3600):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if any(arg.startswith("--junitxml=") for arg in argv):
                path = Path(env["RC6_CI_INTERNAL_ERROR_PATH"])
                path.write_text(json.dumps({
                    "schema": "rc6.pytest.internalerror.v1",
                    "exception_type": "ValueError",
                    "source_file": "tests/test_rc6_ci_parallel_runner.py",
                    "source_line": 300}))
                path.chmod(0o600)
                return subprocess.CompletedProcess(
                    argv, 3, "PRIVATE_PARAMETER_SECRET", "PRIVATE_TRACE")
            return result

        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            with patch.object(runner, "_invoke", internalerror):
                with self.assertRaises(runner.ParallelRunnerRed) as caught:
                    runner.run(SHA, TREE, repo=REPO, shard_count=2,
                               max_workers=1,
                               output_junit=Path(directory) / "combined.xml")
        error = caught.exception
        self.assertEqual(error.reason, "SHARD_PYTEST_INTERNAL_ERROR")
        self.assertEqual(error.process_exit_code, 3)
        self.assertEqual(error.internal_error, {
            "exception_type": "ValueError",
            "source_file": "tests/test_rc6_ci_parallel_runner.py",
            "source_line": 300})
        self.assertNotIn("PRIVATE", str(error))
        self.assertEqual(len([cmd for cmd in fake.calls
                              if any(arg.startswith("--junitxml=") for arg in cmd)]), 1)

    def test_internalerror_sidecar_disallows_private_forgery_and_symlink(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            path = Path(directory) / "internalerror.json"
            invalid = (
                '{"schema":"rc6.pytest.internalerror.v1",'
                '"exception_type":"PRIVATE SECRET"}',
                '{"schema":"rc6.pytest.internalerror.v1",'
                '"exception_type":"NameError","source_file":"../secret.py",'
                '"source_line":7}',
                '{"schema":"rc6.pytest.internalerror.v1",'
                '"exception_type":"NameError","extra":"PRIVATE"}',
            )
            for document in invalid:
                path.write_text(document, encoding="utf-8")
                path.chmod(0o600)
                parsed = runner._verified_internalerror_sidecar(path, REPO)
                self.assertTrue(parsed is None or "source_file" not in parsed)
                self.assertNotIn("PRIVATE", str(parsed))
            path.unlink()
            path.symlink_to(Path(directory) / "missing")
            self.assertIsNone(
                runner._verified_internalerror_sidecar(path, REPO))


    def test_xunit2_exact_failure_name_without_params(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            path = Path(directory) / "junit.xml"
            path.write_text(
                '<testsuite><testcase classname="tests.test_rc6_ci_parallel_runner.'
                'TestParallelProofRunner" name="test_synthetic_case[PRIVATE_ACCOUNT]">'
                '<failure>PRIVATE_TRACEBACK</failure></testcase></testsuite>',
                encoding="utf-8")
            self.assertEqual(
                runner._failed_test_base_from_junit(path, REPO),
                "tests/test_rc6_ci_parallel_runner.py::test_synthetic_case")
            self.assertNotIn(
                "PRIVATE", runner._failed_test_base_from_junit(path, REPO))
            path.write_text(
                '<testsuite><testcase classname="tests...PRIVATE"'
                ' name="test_secret[PRIVATE]"><error>PRIVATE</error>'
                '</testcase></testsuite>', encoding="utf-8")
            self.assertIsNone(runner._failed_test_base_from_junit(path, REPO))
            path.write_text(
                '<testsuite><testcase file="../../private.py"'
                ' name="test_secret"><failure>PRIVATE</failure></testcase>'
                '</testsuite>', encoding="utf-8")
            self.assertIsNone(runner._failed_test_base_from_junit(path, REPO))

    def test_nonzero_shard_propagates_only_sanitized_static_test_base(self):
        fake = FakePytest(require_parallel=False)

        def failing_case(argv, *, cwd, env, timeout=3600):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if any(arg.startswith("--junitxml=") for arg in argv):
                path = Path(next(arg.split("=", 1)[1] for arg in argv
                                 if arg.startswith("--junitxml=")))
                path.write_text(
                    '<testsuite><testcase classname="tests.test_rc6_ci_parallel_runner"'
                    ' name="test_known_issue[PRIVATE_ACCOUNT_NUMBER]">'
                    '<failure>PRIVATE_ASSERTION</failure></testcase></testsuite>',
                    encoding="utf-8")
                return subprocess.CompletedProcess(
                    argv, 1, "PRIVATE_OUTPUT", "PRIVATE_STDERR")
            return result

        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            with patch.object(runner, "_invoke", failing_case):
                with self.assertRaises(runner.ParallelRunnerRed) as caught:
                    runner.run(SHA, TREE, repo=REPO, shard_count=2,
                               max_workers=1,
                               output_junit=Path(directory) / "combined.xml")
        self.assertEqual(caught.exception.reason, "SHARD_PYTEST_NONZERO")
        self.assertEqual(
            caught.exception.failed_test_base,
            "tests/test_rc6_ci_parallel_runner.py::test_known_issue")
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(len([x for x in fake.calls
                              if any(a.startswith("--junitxml=") for a in x)]), 1)

    def test_pytest_internalerror_quorum_reason_is_allowlisted(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            path = Path(directory) / "internalerror.json"
            with patch.dict(os.environ, {
                    "RC6_CI_IDENTITY_PROOF": "1",
                    "RC6_CI_INTERNAL_ERROR_PATH": str(path)}):
                try:
                    shards._red("NODEID_DUPLICATE")
                except shards.ShardQuorumRed as error:
                    identity.pytest_internalerror(
                        None, SimpleNamespace(type=type(error), value=error))
            receipt = json.loads(path.read_text())
            self.assertEqual(receipt["exception_type"], "ShardQuorumRed")
            self.assertEqual(receipt["quorum_reason"], "NODEID_DUPLICATE")
            self.assertNotIn("PRIVATE", path.read_text())
            parsed = runner._verified_internalerror_sidecar(path, REPO)
            self.assertEqual(parsed["quorum_reason"], "NODEID_DUPLICATE")
            self.assertEqual(parsed["exception_type"], "ShardQuorumRed")

    def test_internalerror_unknown_quorum_code_is_not_trusted(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            path = Path(directory) / "internalerror.json"
            for invalid in [
                    {"schema": "rc6.pytest.internalerror.v1",
                     "exception_type": "ShardQuorumRed",
                     "quorum_reason": "PRIVATE_NODEID"},
                    {"schema": "rc6.pytest.internalerror.v1",
                     "exception_type": "RuntimeError",
                     "quorum_reason": "NODEID_DUPLICATE"}]:
                path.write_text(json.dumps(invalid), encoding="utf-8")
                path.chmod(0o600)
                self.assertIsNone(runner._verified_internalerror_sidecar(path, REPO))

    def test_new_diagnostic_regressions_are_real_unittest_members(self):
        required = ("test_pytest_internalerror_hook_writes_private_typed_receipt",
                    "test_pytest_internalerror_exit3_stops_before_next_shard",
                    "test_internalerror_sidecar_disallows_private_forgery_and_symlink",
                    "test_xunit2_exact_failure_name_without_params",
                    "test_nonzero_shard_propagates_only_sanitized_static_test_base",
                    "test_pytest_internalerror_quorum_reason_is_allowlisted",
                    "test_internalerror_unknown_quorum_code_is_not_trusted",
                    "test_pytest_failure_hook_records_phase_type_and_tracked_line_only",
                    "test_failed_pytest_shard_retains_sanitized_first_exception",
                    "test_failure_sidecar_rejects_forged_location_and_private_type")
        for name in required:
            self.assertTrue(callable(getattr(TestParallelProofRunner, name, None)),
                            name + "_NOT_COLLECTED")


    def test_pytest_failure_hook_records_phase_type_and_tracked_line_only(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            target = Path(directory) / "first-failure.json"
            with patch.dict(os.environ, {
                    "RC6_CI_IDENTITY_PROOF": "1",
                    "RC6_CI_FAILURE_PATH": str(target)}):
                try:
                    raise AssertionError("PRIVATE_TICKER_ACCOUNT_TOKEN")
                except AssertionError as error:
                    call = SimpleNamespace(
                        excinfo=SimpleNamespace(type=type(error), value=error))
                    identity.pytest_exception_interact(
                        None, call, SimpleNamespace(failed=True, when="call"))
                    # Second failure must not overwrite first evidence.
                    identity.pytest_exception_interact(
                        None, call, SimpleNamespace(failed=True, when="teardown"))
            record = json.loads(target.read_text())
            self.assertEqual(record["schema"], "rc6.pytest.failure.v1")
            self.assertEqual(record["exception_type"], "AssertionError")
            self.assertEqual(record["phase"], "call")
            self.assertEqual(record["source_file"],
                             "tests/test_rc6_ci_parallel_runner.py")
            self.assertGreater(record["source_line"], 0)
            self.assertEqual(target.stat().st_mode & 0o077, 0)
            self.assertNotIn("PRIVATE_", target.read_text())
            self.assertEqual(
                runner._verified_test_failure_sidecar(target, REPO),
                {"exception_type": "AssertionError", "phase": "call",
                 "source_file": "tests/test_rc6_ci_parallel_runner.py",
                 "source_line": record["source_line"]})
            target.unlink()
            with patch.dict(os.environ, {
                    "RC6_CI_IDENTITY_PROOF": "1",
                    "RC6_CI_FAILURE_PATH": str(target)}):
                try:
                    shards._red("NODEID_DUPLICATE")
                except shards.ShardQuorumRed as error:
                    call = SimpleNamespace(
                        excinfo=SimpleNamespace(type=type(error), value=error))
                    identity.pytest_exception_interact(
                        None, call, SimpleNamespace(failed=True, when="call"))
            tracked = json.loads(target.read_text())
            self.assertEqual(tracked["exception_type"], "ShardQuorumRed")
            self.assertEqual(tracked["source_file"],
                             "tests/test_rc6_ci_parallel_runner.py")
            self.assertEqual(tracked["origin_file"],
                             "scripts/rc6_ci_shard_quorum.py")
            self.assertGreater(tracked["origin_line"], 0)
            self.assertEqual(
                runner._verified_test_failure_sidecar(target, REPO)["origin_file"],
                "scripts/rc6_ci_shard_quorum.py")
            # Cause of a wrapped exception gets a separate sanitized source,
            # never the private message used to instantiate ValueError.
            target.unlink()
            with patch.dict(os.environ, {
                    "RC6_CI_IDENTITY_PROOF": "1",
                    "RC6_CI_FAILURE_PATH": str(target)}):
                try:
                    try:
                        raise ValueError("PRIVATE_PROVIDER_ACCOUNT_INFORMATION")
                    except ValueError as private:
                        raise RuntimeError("PRIVATE_WRAPPER") from private
                except RuntimeError as error:
                    call = SimpleNamespace(
                        excinfo=SimpleNamespace(type=type(error), value=error))
                    identity.pytest_exception_interact(
                        None, call, SimpleNamespace(failed=True, when="call"))
            clean = runner._verified_test_failure_sidecar(target, REPO)
            self.assertEqual(clean["cause_exception_type"], "ValueError")
            self.assertEqual(clean["cause_origin_file"],
                             "tests/test_rc6_ci_parallel_runner.py")
            self.assertGreater(clean["cause_origin_line"], 0)
            self.assertNotIn("PRIVATE", target.read_text(encoding="utf-8"))

    def test_failed_pytest_shard_retains_sanitized_first_exception(self):
        fake = FakePytest(require_parallel=False)

        def fail(argv, *, cwd, env, timeout=3600):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if any(arg.startswith("--junitxml=") for arg in argv):
                target = Path(env["RC6_CI_FAILURE_PATH"])
                target.write_text(json.dumps({
                    "schema": "rc6.pytest.failure.v1",
                    "exception_type": "AssertionError",
                    "phase": "call",
                    "source_file": "tests/test_rc6_ci_parallel_runner.py",
                    "source_line": 100}))
                target.chmod(0o600)
                return subprocess.CompletedProcess(
                    argv, 1, "PRIVATE_PARAMETERS", "PRIVATE_EXCEPTIONS")
            return result

        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            with patch.object(runner, "_invoke", fail):
                with self.assertRaises(runner.ParallelRunnerRed) as caught:
                    runner.run(SHA, TREE, repo=REPO, shard_count=2,
                               max_workers=1,
                               output_junit=Path(directory) / "combined.xml")
        self.assertEqual(caught.exception.reason, "SHARD_PYTEST_NONZERO")
        self.assertEqual(caught.exception.test_failure, {
            "exception_type": "AssertionError", "phase": "call",
            "source_file": "tests/test_rc6_ci_parallel_runner.py",
            "source_line": 100})
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(len([
            x for x in fake.calls
            if any(a.startswith("--junitxml=") for a in x)]), 1)

    def test_failure_sidecar_rejects_forged_location_and_private_type(self):
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            path = Path(directory) / "failure.json"
            cases = (
                {"schema": "rc6.pytest.failure.v1",
                 "exception_type": "PRIVATE:TOKEN", "phase": "call"},
                {"schema": "rc6.pytest.failure.v1",
                 "exception_type": "AssertionError", "phase": "call",
                 "source_file": "../PRIVATE.py", "source_line": 5},
                {"schema": "rc6.pytest.failure.v1",
                 "exception_type": "AssertionError", "phase": "outside"},
                {"schema": "rc6.pytest.failure.v1",
                 "exception_type": "AssertionError", "phase": "call",
                 "origin_file": "../PRIVATE_TOKEN.py", "origin_line": 7},
                {"schema": "rc6.pytest.failure.v1",
                 "exception_type": "AssertionError", "phase": "call",
                 "cause_exception_type": "PRIVATE TOKEN",
                 "cause_origin_file": "tests/test_rc6_ci_parallel_runner.py",
                 "cause_origin_line": 7},
            )
            for record in cases:
                path.write_text(json.dumps(record))
                path.chmod(0o600)
                parsed = runner._verified_test_failure_sidecar(path, REPO)
                self.assertTrue(parsed is None or "source_file" not in parsed)
                self.assertNotIn("PRIVATE", str(parsed))


    def test_empty_shard_selection_does_not_hide_real_pytest_exit4(self):
        # With one wrong explicit -k/nodeid selector, actual pytest reports
        # "ERROR: not found" and zero selected. A collection hook must NOT
        # turn this into ShardQuorumRed/exit3 before pytest emits its error.
        from types import SimpleNamespace
        from scripts import rc6_ci_pytest_identity as plugin
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            manifest_path = Path(directory) / "empty.json"
            with patch.dict(os.environ, {
                    "RC6_CI_IDENTITY_PROOF": "1",
                    "RC6_CI_MANIFEST_PATH": str(manifest_path)}):
                plugin.pytest_collection_finish(SimpleNamespace(items=[]))
            self.assertFalse(manifest_path.exists())
        self.assertEqual(
            runner._missing_selected_nodeid_module(
                SimpleNamespace(stderr="ERROR: not found: " +
                    str(REPO / "tests/test_rc6_ci_parallel_runner.py") +
                    "::test_not_collected[PRIVATE_ID]\n"), REPO),
            "tests/test_rc6_ci_parallel_runner.py")
        self.assertIsNone(runner._missing_selected_nodeid_module(
            SimpleNamespace(stderr="ERROR: not found: /etc/PRIVATE.py"
                            "::test_foo[SECRET]\n"), REPO))

    def test_missing_exact_nodeid_is_fail_closed_without_next_shard(self):
        fake = FakePytest(require_parallel=False)
        def missing(argv, *, cwd, env, timeout=3600):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if any(a.startswith("--junitxml=") for a in argv):
                return subprocess.CompletedProcess(argv, 4, "",
                    "ERROR: not found: " +
                    str(REPO / "tests/test_rc6_ci_parallel_runner.py") +
                    "::test_private[PRIVATE_SECRET]\n")
            return result
        with tempfile.TemporaryDirectory(dir=self.temporary_root) as directory:
            with patch.object(runner, "_invoke", missing):
                with self.assertRaises(runner.ParallelRunnerRed) as caught:
                    runner.run(SHA, TREE, repo=REPO, shard_count=2,
                               max_workers=1,
                               output_junit=Path(directory) / "combined.xml")
        self.assertEqual(caught.exception.reason,
                         "SHARD_PYTEST_NODEID_NOT_FOUND")
        self.assertEqual(caught.exception.failure_module,
                         "tests/test_rc6_ci_parallel_runner.py")
        self.assertEqual(caught.exception.process_exit_code, 4)
        self.assertNotIn("PRIVATE", str(caught.exception))
        self.assertEqual(len([cmd for cmd in fake.calls
                              if any(a.startswith("--junitxml=") for a in cmd)]), 1)


if __name__ == "__main__":
    unittest.main()
