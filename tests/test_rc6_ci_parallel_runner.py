"""Mocked pytest replay: global collection and truly concurrent exact shards."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

import yaml

from scripts import rc6_ci_parallel_runner as runner
from scripts import rc6_ci_pytest_identity as identity
from scripts import rc6_ci_shard_quorum as shards


SHA = "a" * 40
TREE = "b" * 40
NODES = [f"tests/test_ci_stub_{i // 6}.py::test_case[{i}]" for i in range(12)]
REPO = Path(__file__).resolve().parents[1]


def _write_forged_progress(path, manifest, events):
    """Test-only untrusted JSONL input; no reliance on producer acceptance."""
    lines = []
    for index, (node, phase, outcome) in enumerate(events):
        lines.append(json.dumps({
            "source_sha": manifest["source_sha"],
            "source_tree_sha": manifest["source_tree_sha"],
            "segment_sha256": manifest["collection_sha256"],
            "sequence": index,
            "nodeid_sha256": identity.nodeid_digest(node),
            "phase": phase, "outcome": outcome, "elapsed_ms": 0,
        }, sort_keys=True, separators=(",", ":")))
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


class FakePytest:
    """Writes precise private manifests and JUnit as a stand-in for pytest."""

    def __init__(self, *, missing=False, skipped=False, fail_status=False,
                 mismatched_tree=False, extra_test=False, duplicate=False,
                 collection_rc=0, dirty_tree=False, require_parallel=True,
                 failed_nodeids=None, timeout_nodeids=None):
        self.missing = missing
        self.skipped = skipped
        self.fail_status = fail_status
        self.mismatched_tree = mismatched_tree
        self.extra_test = extra_test
        self.duplicate = duplicate
        self.collection_rc = collection_rc
        self.dirty_tree = dirty_tree
        self.failed_nodeids = set(failed_nodeids or ())
        self.timeout_nodeids = set(timeout_nodeids or ())
        self.calls = []
        self.timeouts = []
        self.barrier = threading.Barrier(2) if require_parallel else None

    def __call__(self, argv, *, cwd, env, timeout=3600):
        self.calls.append(argv)
        self.timeouts.append((argv, timeout))
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
            assert argv[-1].startswith("--ignore=")
            root = Path(env["RC6_CI_MANIFEST_PATH"])
            if self.collection_rc == 0:
                tree = "c" * 40 if self.mismatched_tree else TREE
                root.write_text(json.dumps(shards.manifest(SHA, tree, NODES)), encoding="utf-8")
            return subprocess.CompletedProcess(argv, self.collection_rc, "not public", "")
        if "--" not in argv:
            raise AssertionError("Pytest shard must use -- end-of-options marker")
        cases = argv[argv.index("--") + 1:]
        if self.barrier:
            self.barrier.wait(timeout=5)
        if self.timeout_nodeids.intersection(cases):
            raise runner.ParallelRunnerRed(diagnostic={"faulthandler_seen": False,
                                                        "frames": []})
        is_one = NODES[0] in cases
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
            if nodeid in self.failed_nodeids:
                fail = ET.SubElement(case, "failure", message="PRIVATE_PROVIDER_TOKEN")
                fail.text = "PRIVATE_PROVIDER_ACCOUNT=123 SECRET_STACK_PATH=/tmp/private"
        if self.extra_test and is_one:
            ET.SubElement(xml, "testcase", name="extra")
        xml_path.write_bytes(ET.tostring(xml, encoding="utf-8"))
        return subprocess.CompletedProcess(argv, 1 if (self.fail_status and is_one
                                or self.failed_nodeids.intersection(cases)) else 0,
                                           "sensitive-log-not-public", "")


class TestParallelProofRunner(unittest.TestCase):
    def test_child_pytest_bootstrap_arms_faulthandler_without_leaking_payload(self):
        self.assertTrue(hasattr(runner, "_pytest_argv"))
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            # The executor may not have pytest. A local pytest stub exercises
            # the actual interpreter/bootstrap/trace path without network pip.
            (work / "pytest.py").write_text(
                "def main(args):\n    import time\n    time.sleep(0.6)\n    return 0\n",
                encoding="utf-8",
            )
            command = runner._pytest_argv(sys.executable, ["-q"], trace_after=0.1)
            env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                   "PYTHONPATH": str(work)}
            outcome = runner._invoke(command, cwd=work, env=env, timeout=5)
            self.assertEqual(outcome.returncode, 0, outcome.stderr[-400:])
            diag = runner._sanitized_timeout_trace(outcome.stderr)
            self.assertTrue(diag["faulthandler_seen"])
            self.assertNotIn(str(work), str(diag))

    def test_timeout_with_nonempty_stderr_bytes_is_fail_closed(self):
        script = "import sys,time; print('PRIVATE_SECRET_MARKER', file=sys.stderr, flush=True); time.sleep(5)"
        with self.assertRaises(runner.ParallelRunnerRed) as failure:
            runner._invoke([sys.executable, "-c", script], cwd=REPO,
                           env=os.environ.copy(), timeout=0.15)
        self.assertNotIn("PRIVATE_SECRET_MARKER", str(failure.exception))
        self.assertIsInstance(failure.exception.diagnostic, dict)

    def test_timeout_classifies_parent_exit_vs_descendant_pipe_hold_without_raw_logs(self):
        """Locate historic 45m teardown as parent/test vs descendant pipe."""
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            command = [sys.executable, "-c", (
                "import subprocess,sys;"
                "subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)'],"
                "stdout=sys.stdout,stderr=sys.stderr);"
                "sys.exit(0)"
            )]
            with self.assertRaises(runner.ParallelRunnerRed) as failure:
                runner._invoke(command, cwd=work, env=os.environ.copy(), timeout=0.3)
            self.assertEqual(failure.exception.diagnostic["parent_exited_before_timeout"],
                             True)
            self.assertNotIn(str(work), str(failure.exception.diagnostic))
            with self.assertRaises(runner.ParallelRunnerRed) as live_failure:
                runner._invoke([sys.executable, "-c", "import time;time.sleep(10)"],
                               cwd=work, env=os.environ.copy(), timeout=0.3)
            self.assertIs(live_failure.exception.diagnostic[
                "parent_exited_before_timeout"], False)

    def test_large_stdout_stderr_streams_remain_bounded_without_deadlock(self):
        script = (
            "import sys\n"
            "sys.stdout.write('X' * 4_000_000 + 'STDOUT_END')\n"
            "sys.stderr.write('Y' * 4_000_000 + 'STDERR_END')\n"
            "sys.stdout.flush(); sys.stderr.flush()\n"
        )
        outcome = runner._invoke([sys.executable, "-c", script], cwd=REPO,
                                 env=os.environ.copy(), timeout=8)
        self.assertEqual(outcome.returncode, 0)
        self.assertLessEqual(len(outcome.stdout.encode()), runner.OUTPUT_TAIL_LIMIT)
        self.assertLessEqual(len(outcome.stderr.encode()), runner.OUTPUT_TAIL_LIMIT)
        self.assertTrue(outcome.stdout.endswith("STDOUT_END"))
        self.assertTrue(outcome.stderr.endswith("STDERR_END"))

    def test_timeout_flood_keeps_trace_input_bounded(self):
        script = "import sys,time; sys.stderr.write('SECRET'*700_000); sys.stderr.flush(); time.sleep(60)"
        samples = []
        original_sanitize = runner._sanitized_timeout_trace
        def inspect_tail(raw):
            samples.append(len(raw if isinstance(raw, bytes) else raw.encode()))
            return original_sanitize(raw)
        with patch.object(runner, "_sanitized_timeout_trace", side_effect=inspect_tail):
            with self.assertRaises(runner.ParallelRunnerRed):
                runner._invoke([sys.executable, "-c", script], cwd=REPO,
                               env=os.environ.copy(), timeout=0.4)
        self.assertEqual(len(samples), 1)
        self.assertLessEqual(samples[0], runner.OUTPUT_TAIL_LIMIT)

    def test_cli_receipt_sanitizes_faulthandler_frames(self):
        from contextlib import redirect_stderr
        import io

        raw_path = "/home/runner/private-SECRETS/tests/test_auth_cookie.py"
        diag = {"faulthandler_seen": True,
                "frames": [{"file": "test_auth_cookie.py", "line": 91,
                            "function": "test_session"}]}
        with patch.object(runner, "run", side_effect=runner.ParallelRunnerRed(
                diagnostic=diag)):
            output = io.StringIO()
            with redirect_stderr(output):
                rc = runner.main(["--source-sha", SHA, "--source-tree-sha", TREE,
                                  "--repo", str(REPO), "--shards", "2",
                                  "--output-junit", "offline.xml"])
        self.assertEqual(rc, 2)
        self.assertIn("RC6_PARALLEL_PILOT=RED|automatic_go=NO", output.getvalue())
        self.assertIn("RC6_PARALLEL_TIMEOUT_DIAG=", output.getvalue())
        self.assertIn("test_auth_cookie.py", output.getvalue())
        self.assertNotIn(raw_path, output.getvalue())

    def test_timeout_terminates_descendant_process_group_not_only_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            child_pid = work / "child.pid"
            heartbeat = work / "heartbeat.txt"
            child = (
                "import os,signal,sys,time\n"
                "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
                "open(sys.argv[1],'w').write(str(os.getpid()))\n"
                "while True:\n"
                "  with open(sys.argv[2],'a') as out: out.write('x')\n"
                "  time.sleep(0.03)\n"
            )
            parent = (
                "import subprocess,sys,time\n"
                f"subprocess.Popen([sys.executable,'-c',{child!r},sys.argv[1],sys.argv[2]])\n"
                "print('PRIVATE_SECRET_MARKER',flush=True)\n"
                "time.sleep(120)\n"
            )
            try:
                with self.assertRaises(runner.ParallelRunnerRed) as failure:
                    runner._invoke([sys.executable, "-c", parent,
                                    str(child_pid), str(heartbeat)],
                                   cwd=work, env=os.environ.copy(), timeout=1)
                self.assertTrue(child_pid.exists(), "descendant must actually start")
                self.assertTrue(heartbeat.exists(), "descendant must actually run")
                after_timeout = heartbeat.stat().st_size
                time.sleep(0.25)
                self.assertEqual(heartbeat.stat().st_size, after_timeout,
                                 "timed-out descendant must stop writing")
                self.assertNotIn("PRIVATE_SECRET_MARKER", str(failure.exception))
            finally:
                if child_pid.exists():
                    try:
                        os.kill(int(child_pid.read_text()), signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_sanitized_timeout_trace_keeps_module_not_private_path(self):
        self.assertTrue(hasattr(runner, "_sanitized_timeout_trace"))
        raw = (
            "PRIVATE_SECRET_MARKER\nTimeout (0:07:00)!\n"
            "  File \"/home/runner/PRIVATE_SECRET_MARKER/tests/test_dashboard_session.py\", "
            "line 82 in test_auth_cookie\n"
        )
        result = runner._sanitized_timeout_trace(raw)
        self.assertEqual(result, {"faulthandler_seen": True,
                                  "frames": [{"file": "test_dashboard_session.py",
                                              "line": 82, "function": "test_auth_cookie"}]})
        self.assertNotIn("PRIVATE_SECRET_MARKER", str(result))
        self.assertEqual(runner._sanitized_timeout_trace("PRIVATE_SECRET_MARKER"),
                         {"faulthandler_seen": False, "frames": []})

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
            collected = [timeout for argv, timeout in fake.timeouts if "--collect-only" in argv]
            shards_only = [timeout for argv, timeout in fake.timeouts if "--junitxml=" in " ".join(argv)]
            self.assertEqual(collected, [180])
            self.assertEqual(shards_only, [720, 720])
            self.assertTrue(all("--durations=20" in argv for argv, _ in fake.timeouts
                                if "--junitxml=" in " ".join(argv)))

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

    def test_diagnostic_captures_test_failures_without_raw_identifiers_or_false_green(self):
        failed = {NODES[0], NODES[6]}
        fake = FakePytest(failed_nodeids=failed)
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            with patch.object(runner, "_invoke", fake):
                receipt = runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                     output_junit=work / "ignored-normal.xml",
                                     diagnostics_dir=work / "sanitized")
            self.assertEqual(receipt["status"], "RED_DIAGNOSTIC_OFFLINE_ONLY")
            self.assertEqual((receipt["discovered"], receipt["executed"],
                              receipt["failures"]), (12, 12, 2))
            self.assertFalse(receipt["automatic_go"])
            self.assertFalse(receipt["runtime_authority"])
            self.assertFalse(receipt["release_gate_activated"])
            self.assertNotIn("ci_activated", receipt)
            self.assertEqual(set(receipt["phase_elapsed_ms"]),
                             {"collect", "parallel_test", "sanitize"})
            self.assertEqual(len(receipt["shard_elapsed_ms"]), 2)
            self.assertTrue(all(type(ms) is int and ms >= 0
                                for ms in receipt["phase_elapsed_ms"].values()))
            self.assertTrue(all(type(ms) is int and ms >= 0
                                for ms in receipt["shard_elapsed_ms"]))
            junit = work / "sanitized" / "junit-sanitized.xml"
            summary = work / "sanitized" / "receipt.json"
            self.assertTrue(junit.is_file() and summary.is_file())
            xml = ET.parse(junit)
            self.assertEqual(len(list(xml.iter("testcase"))), 12)
            self.assertEqual(len(list(xml.iter("failure"))), 2)
            self.assertEqual(len(list(xml.iter("error"))), 0)
            published = junit.read_text() + summary.read_text()
            self.assertNotIn("PRIVATE_PROVIDER", published)
            self.assertNotIn("SECRET_STACK_PATH", published)
            self.assertNotIn("test_ci_stub_", published)
            self.assertNotIn("sensitive-log-not-public", published)
            self.assertFalse((work / "ignored-normal.xml").exists())

    def test_diagnostic_rejects_contradictory_exit_missing_junit_and_skips(self):
        scenarios = [dict(fail_status=True), dict(missing=True), dict(extra_test=True),
                     dict(duplicate=True), dict(collection_rc=2)]
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                with patch.object(runner, "_invoke", FakePytest(**scenario)):
                    with self.assertRaises(runner.ParallelRunnerRed):
                        runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                   output_junit=work / "normal.xml",
                                   diagnostics_dir=work / "sanitized")
                self.assertFalse((work / "sanitized" / "junit-sanitized.xml").exists())
                self.assertFalse((work / "sanitized" / "receipt.json").exists())

    def test_diagnostic_retains_skips_and_xfails_as_red_not_discarded_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fake = FakePytest(skipped=True, failed_nodeids={NODES[6]})
            with patch.object(runner, "_invoke", fake):
                receipt = runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                     output_junit=work / "normal.xml",
                                     diagnostics_dir=work / "sanitized")
            self.assertEqual(receipt["status"], "RED_DIAGNOSTIC_OFFLINE_ONLY")
            self.assertEqual(receipt["failures"], 1)
            self.assertEqual(receipt["skipped"], 6)
            self.assertEqual(receipt["xfail"], 6)
            self.assertEqual(len(list(ET.parse(work / "sanitized/junit-sanitized.xml")
                                      .iter("skipped"))), 6)
            self.assertFalse(receipt["automatic_go"])

    def test_timeout_shard_keeps_only_verified_sibling_junit_as_incomplete_red(self):
        """The historical 720s timeout must not discard healthy-shard evidence."""
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            fake = FakePytest(timeout_nodeids={NODES[0]},
                              failed_nodeids={NODES[6]})
            with patch.object(runner, "_invoke", fake):
                receipt = runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                     output_junit=work / "normal.xml",
                                     diagnostics_dir=work / "sanitized")
            self.assertEqual(receipt["status"], "RED_INCOMPLETE_DIAGNOSTIC")
            self.assertEqual((receipt["discovered"], receipt["executed"],
                              receipt["missing"], receipt["failures"]),
                             (12, 6, 6, 1))
            self.assertEqual(receipt["timed_out_shards"], 1)
            self.assertFalse(receipt["automatic_go"])
            self.assertFalse(receipt["release_gate_activated"])
            timeout = receipt["timeouts"][0]
            self.assertEqual(timeout["shard_index"], 0)
            self.assertEqual(timeout["assigned_count"], 6)
            self.assertEqual(timeout["reason"], "PYTEST_TIMEOUT")
            self.assertRegex(timeout["assigned_nodeids_sha256"], r"^[a-f0-9]{64}$")
            self.assertEqual(len(list(ET.parse(work / "sanitized/junit-sanitized.xml")
                                      .iter("testcase"))), 6)
            published = (work / "sanitized/junit-sanitized.xml").read_text() + (
                work / "sanitized/receipt.json").read_text()
            for secret in ("PRIVATE_PROVIDER", "SECRET_STACK_PATH", "test_ci_stub_",
                           "sensitive-log-not-public"):
                self.assertNotIn(secret, published)
            self.assertFalse((work / "normal.xml").exists())

    def test_every_shard_timeout_still_publishes_sha_bound_empty_partial_red(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            with patch.object(runner, "_invoke", FakePytest(
                    timeout_nodeids=set(NODES))):
                receipt = runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                     output_junit=work / "normal.xml",
                                     diagnostics_dir=work / "sanitized")
            self.assertEqual(receipt["status"], "RED_INCOMPLETE_DIAGNOSTIC")
            self.assertEqual((receipt["discovered"], receipt["executed"],
                              receipt["missing"], receipt["timed_out_shards"]),
                             (12, 0, 12, 2))
            self.assertFalse(receipt["automatic_go"])
            self.assertEqual(len(list(ET.parse(work / "sanitized/junit-sanitized.xml")
                                      .iter("testcase"))), 0)


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


class TestBoundedDiagnostic(unittest.TestCase):
    def test_bounded_receipt_records_measured_collection_and_batch_time(self):
        fake = self._bounded_fake()

        def invoke(argv, *, cwd, env, timeout):
            if argv[0] != "git":
                time.sleep(0.02)
            return fake(argv, cwd=cwd, env=env, timeout=timeout)

        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "_invoke", invoke):
            output = Path(directory) / "diagnostic"
            runner.run_bounded(SHA, TREE, repo=REPO, shard_count=2,
                shard_index=1, diagnostics_dir=output)
            receipt = json.loads((output / "batch-0/receipt.json").read_text())
            self.assertGreaterEqual(receipt["phase_elapsed_ms"]["collect"], 15)
            self.assertGreaterEqual(receipt["phase_elapsed_ms"]["parallel_test"], 15)
            self.assertGreaterEqual(receipt["shard_elapsed_ms"][0], 15)

    def test_review_regular_evidence_reader_limits_bytes_and_rejects_path_swap(self):
        self.assertTrue(hasattr(runner, "_read_regular"), "bounded fd reader absent")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "evidence"
            target.write_bytes(b"123456")
            self.assertEqual(runner._read_regular(target, 6), b"123456")
            with self.assertRaises(runner.ParallelRunnerRed):
                runner._read_regular(target, 5)
            alias = root / "alias"
            os.link(target, alias)
            with self.assertRaises(runner.ParallelRunnerRed):
                runner._read_regular(target, 6)
            alias.unlink()
            real_read = os.read

            def swap(fd, limit):
                target.rename(root / "old")
                target.write_bytes(b"FOREIGN")
                return real_read(fd, limit)

            with patch.object(runner.os, "read", swap), self.assertRaises(runner.ParallelRunnerRed):
                runner._read_regular(target, 6)

    def test_review_progress_fifo_rejected_without_unbounded_parent_read(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "fifo"
            os.mkfifo(target)
            manifest = shards.manifest(SHA, TREE, NODES[:1])
            script = ("from pathlib import Path;from scripts import rc6_ci_parallel_runner as r;"
                      f"p=Path({str(target)!r});m={manifest!r}\n"
                      "try:r._read_progress(p,m)\n"
                      "except r.ParallelRunnerRed:raise SystemExit(0)\n"
                      "raise SystemExit(1)")
            try:
                result = runner._invoke([sys.executable, "-c", script], cwd=REPO,
                    env={**os.environ, "PYTHONPATH": str(REPO)}, timeout=0.5)
            except runner.ParallelRunnerRed:
                self.fail("FIFO evidence blocked parent read outside pytest deadline")
            self.assertEqual(result.returncode, 0)

    def test_review_writer_rejects_replacement_hardlink_without_modifying_victim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target, victim = root / "progress", root / "victim"
            writer = identity._ProgressWriter(target, SHA, TREE, "c" * 64)
            victim.write_text("PRIVATE_STABLE")
            target.unlink()
            os.link(victim, target)
            with self.assertRaises((RuntimeError, OSError)):
                writer.append(NODES[0], "start", "none")
            self.assertEqual(victim.read_text(), "PRIVATE_STABLE")

    def test_review_normal_diagnostic_rechecks_checkout_before_publication(self):
        fake = FakePytest(require_parallel=False, failed_nodeids={NODES[0]})

        def invoke(argv, *, cwd, env, timeout):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if argv[0] != "git" and "--collect-only" not in argv:
                fake.dirty_tree = True
            return result

        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "_invoke", invoke):
            output = Path(directory) / "diagnostic"
            with self.assertRaises(runner.ParallelRunnerRed):
                runner.run(SHA, TREE, repo=REPO, shard_count=2,
                    output_junit=Path(directory) / "unused.xml", diagnostics_dir=output)
            self.assertFalse(output.exists())

    def test_review_git_environment_cannot_attest_a_different_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clean, actual = root / "clean", root / "actual"
            clean.mkdir()
            actual.mkdir()
            (clean / "tracked.py").write_text("value=1\n")
            for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"]):
                subprocess.run(["git", *args], cwd=clean, check=True, capture_output=True, timeout=10)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=clean, text=True).strip()
            tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=clean, text=True).strip()
            with patch.dict(os.environ, {"GIT_DIR": str(clean / ".git"),
                    "GIT_WORK_TREE": str(clean), "GIT_INDEX_FILE": str(clean / ".git/index"),
                    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.bare",
                    "GIT_CONFIG_VALUE_0": "false"}):
                with self.assertRaises(runner.ParallelRunnerRed):
                    runner._verify_checkout(actual, sha, tree)
                runner._verify_checkout(clean, sha, tree)

    def _bounded_fake(self, *, timeout=False, corrupt=False, missing=False):
        fake = FakePytest(require_parallel=False, missing=missing)

        def invoke(argv, *, cwd, env, timeout=3600):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if "RC6_CI_PROGRESS_PATH" not in env:
                return result
            self.assertNotIn("PRIVATE_PROVIDER_TOKEN", env)
            with patch.dict(os.environ, {**env, "RC6_CI_PROGRESS_PID": str(os.getpid())}, clear=True), patch.object(identity, "_progress", None):
                identity.pytest_sessionstart(None)
                for node in argv[argv.index("--") + 1:]:
                    identity.pytest_runtest_logstart(node, None)
                    if should_timeout:
                        raise runner.ParallelRunnerRed(diagnostic={
                            "faulthandler_seen": False, "parent_exited_before_timeout": False})
                    for phase in ("setup", "call", "teardown"):
                        identity.pytest_runtest_logreport(SimpleNamespace(
                            nodeid=node, when=phase, outcome="passed"))
                    identity.pytest_runtest_logfinish(node, None)
            if corrupt:
                path = Path(env["RC6_CI_PROGRESS_PATH"])
                path.write_text(path.read_text().replace('"sequence":0', '"sequence":1'))
            return result

        should_timeout = timeout
        return invoke

    def test_plan_preserves_exact_shard_and_deterministic_binary_slices(self):
        self.assertTrue(hasattr(runner, "bounded_segments"), "bounded planner absent")
        manifest = shards.manifest(SHA, TREE, NODES)
        original = shards.partition(manifest, 2)[1]
        whole = runner.bounded_segments(manifest, 2, 1, "")
        self.assertEqual([node for segment in whole for node in segment], original)
        halves = [runner.bounded_segments(manifest, 2, 1, bit) for bit in "01"]
        self.assertEqual([n for side in halves for part in side for n in part], original)
        self.assertEqual(len(halves[0][0]), 3)
        for index, path in ((-1, ""), (2, ""), (True, ""), (1, "2"), (1, "0000")):
            with self.subTest(index=index, path=path), self.assertRaises(runner.ParallelRunnerRed):
                runner.bounded_segments(manifest, 2, index, path)

    def test_progress_is_durable_hash_only_and_rejects_duplicate_or_wrong_binding(self):
        self.assertTrue(hasattr(identity, "pytest_runtest_logstart"), "progress hooks absent")
        manifest = shards.manifest(SHA, TREE, NODES[:2])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            env = {"RC6_CI_IDENTITY_PROOF": "1", "RC6_CI_PROGRESS_PATH": str(path),
                   "RC6_CI_PROGRESS_PID": str(os.getpid()),
                   "RC6_CI_SOURCE_SHA": SHA, "RC6_CI_SOURCE_TREE_SHA": TREE,
                   "RC6_CI_SEGMENT_SHA256": manifest["collection_sha256"]}
            with patch.dict(os.environ, env, clear=True), patch.object(identity, "_progress", None):
                identity.pytest_sessionstart(None)
                identity.pytest_runtest_logstart(NODES[0], ("PRIVATE", 0, "PRIVATE"))
                self.assertEqual(len(path.read_text().splitlines()), 1)
                for phase in ("setup", "call", "teardown"):
                    identity.pytest_runtest_logreport(SimpleNamespace(
                        nodeid=NODES[0], when=phase, outcome="passed", longrepr="PRIVATE_TOKEN"))
                identity.pytest_runtest_logfinish(NODES[0], None)
                progress = runner._read_progress(path, manifest)
                self.assertEqual((progress["started"], progress["finished"]), (1, 1))
                self.assertNotIn("tests/", path.read_text())
                self.assertNotIn("PRIVATE", path.read_text())
                with self.assertRaises(runner.ParallelRunnerRed):
                    runner._read_progress(path, shards.manifest("c" * 40, TREE, NODES[:2]))
                identity.pytest_runtest_logstart(NODES[0], None)
                with self.assertRaises(runner.ParallelRunnerRed):
                    runner._read_progress(path, manifest)

    def test_progress_clock_is_not_affected_by_a_test_monkeypatching_global_monotonic(self):
        """A915: another test must not rewind the diagnostic event clock."""
        manifest = shards.manifest(SHA, TREE, [NODES[0]])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            writer = identity._ProgressWriter(path, SHA, TREE, manifest["collection_sha256"])
            try:
                writer.append(NODES[0], "start", "none")
                writer.append(NODES[0], "setup", "passed")
                # The real issue465 Harness patches scalping.time.monotonic,
                # which is the shared Python time module also used by the hook.
                with patch.object(identity.time, "monotonic", return_value=86_400_000.0):
                    writer.append(NODES[0], "call", "passed")
                writer.append(NODES[0], "teardown", "passed")
                writer.append(NODES[0], "finish", "none")
            finally:
                writer.close()
            evidence = runner._read_progress(path, manifest)
            self.assertEqual((evidence["started"], evidence["finished"]), (1, 1))
            elapsed = [json.loads(line)["elapsed_ms"] for line in path.read_text().splitlines()]
            self.assertEqual(elapsed, sorted(elapsed))

    def test_unittest_subtests_keep_one_execution_credit_with_repeated_call_reports(self):
        """A915: genuine unittest subTest reports one node with many call events."""
        # A module-local batch may have other assigned cases; the bounded
        # per-node byte allowance still applies to the complete progress file.
        manifest = shards.manifest(SHA, TREE, NODES[:2])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            writer = identity._ProgressWriter(path, SHA, TREE, manifest["collection_sha256"])
            try:
                writer.append(NODES[0], "start", "none")
                writer.append(NODES[0], "setup", "passed")
                for _ in range(13):  # 12 unittest.subTest reports plus final call
                    writer.append(NODES[0], "call", "passed")
                writer.append(NODES[0], "teardown", "passed")
                writer.append(NODES[0], "finish", "none")
            finally:
                writer.close()
            evidence = runner._read_progress(path, manifest)
            self.assertEqual((evidence["started"], evidence["finished"]), (1, 1))
            self.assertEqual(evidence["last_phase"], "finish")

    def test_repeated_calls_cannot_change_node_or_skip_final_teardown(self):
        """A915: duplicate subTest events never authorize cross-node credit."""
        manifest = shards.manifest(SHA, TREE, NODES[:2])
        sequences = (
            [(NODES[0], "start", "none"), (NODES[0], "setup", "passed"),
             (NODES[0], "call", "passed"), (NODES[1], "call", "passed")],
            [(NODES[0], "start", "none"), (NODES[0], "setup", "passed"),
             (NODES[0], "call", "passed"), (NODES[0], "finish", "none")],
            [(NODES[0], "start", "none"), (NODES[0], "setup", "passed"),
             (NODES[0], "call", "passed"), (NODES[0], "teardown", "passed"),
             (NODES[0], "call", "passed")],
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, events in enumerate(sequences):
                with self.subTest(case=index):
                    path = Path(directory) / f"progress-{index}.jsonl"
                    _write_forged_progress(path, manifest, events)
                    with self.assertRaises(runner.ParallelRunnerRed):
                        runner._read_progress(path, manifest)

    def test_unittest_subtest_call_report_count_is_bounded_not_unlimited(self):
        """A915: duplicate reports are never additional test credit or unlimited."""
        manifest = shards.manifest(SHA, TREE, NODES[:8])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            events = [(NODES[0], "start", "none"), (NODES[0], "setup", "passed")]
            events.extend([(NODES[0], "call", "passed")] * 65)
            events.extend([(NODES[0], "teardown", "passed"),
                           (NODES[0], "finish", "none")])
            _write_forged_progress(path, manifest, events)
            with self.assertRaises(runner.ParallelRunnerRed):
                runner._read_progress(path, manifest)

    def test_progress_writer_rejects_subtest_amplification_before_disk_write(self):
        """A915: reject the 65th call report at producer, not only reader."""
        manifest = shards.manifest(SHA, TREE, [NODES[0]])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            writer = identity._ProgressWriter(path, SHA, TREE, manifest["collection_sha256"])
            try:
                writer.append(NODES[0], "start", "none")
                writer.append(NODES[0], "setup", "passed")
                for _ in range(64):
                    writer.append(NODES[0], "call", "passed")
                size_before = path.stat().st_size
                with self.assertRaises(RuntimeError):
                    writer.append(NODES[0], "call", "passed")
                self.assertEqual(path.stat().st_size, size_before)
            finally:
                writer.close()

    def test_progress_writer_rejects_size_quota_before_disk_write(self):
        """A915: a malicious event cannot grow progress beyond hard 8MiB."""
        manifest = shards.manifest(SHA, TREE, [NODES[0]])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            with patch.object(identity, "MAX_PROGRESS_BYTES", 64, create=True):
                writer = identity._ProgressWriter(path, SHA, TREE, manifest["collection_sha256"])
                try:
                    with self.assertRaises(RuntimeError):
                        writer.append(NODES[0], "start", "none")
                    self.assertEqual(path.stat().st_size, 0)
                finally:
                    writer.close()

    def test_progress_symlink_and_bad_phase_are_rejected(self):
        self.assertTrue(hasattr(identity, "pytest_sessionstart"), "progress init absent")
        with tempfile.TemporaryDirectory() as directory:
            victim = Path(directory) / "victim"
            victim.write_text("STABLE")
            target = Path(directory) / "progress"
            target.symlink_to(victim)
            with patch.dict(os.environ, {"RC6_CI_IDENTITY_PROOF": "1",
                    "RC6_CI_PROGRESS_PATH": str(target), "RC6_CI_SOURCE_SHA": SHA,
                    "RC6_CI_PROGRESS_PID": str(os.getpid()),
                    "RC6_CI_SOURCE_TREE_SHA": TREE, "RC6_CI_SEGMENT_SHA256": "c" * 64}, clear=True):
                with self.assertRaises((RuntimeError, OSError)):
                    identity.pytest_sessionstart(None)
            self.assertEqual(victim.read_text(), "STABLE")

    def test_nested_pytest_cannot_reopen_owned_progress_and_normal_runner_strips_it(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "progress.jsonl"
            target.write_text("STABLE")
            env = {**os.environ, "PYTHONPATH": str(REPO), "RC6_CI_IDENTITY_PROOF": "1",
                   "RC6_CI_PROGRESS_PATH": str(target), "RC6_CI_PROGRESS_PID": str(os.getpid()),
                   "RC6_CI_SOURCE_SHA": SHA, "RC6_CI_SOURCE_TREE_SHA": TREE,
                   "RC6_CI_SEGMENT_SHA256": "c" * 64}
            code = "from scripts import rc6_ci_pytest_identity as p;p.pytest_sessionstart(None)"
            result = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                                    capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, "nested interpreter inherited progress writer")
            self.assertEqual(target.read_text(), "STABLE")
            fake = FakePytest(require_parallel=False)

            def invoke(argv, *, cwd, env, timeout):
                if argv[0] != "git":
                    self.assertFalse(any(k.startswith("RC6_CI_PROGRESS_") for k in env))
                    self.assertNotIn("RC6_CI_SEGMENT_SHA256", env)
                return fake(argv, cwd=cwd, env=env, timeout=timeout)

            with patch.dict(os.environ, env, clear=True), patch.object(runner, "_invoke", invoke):
                runner.run(SHA, TREE, repo=REPO, shard_count=2,
                           output_junit=Path(directory) / "combined.xml")

    def test_bounded_runner_is_always_red_and_never_runs_unselected_batches(self):
        self.assertTrue(hasattr(runner, "run_bounded"), "bounded runner absent")
        fake = FakePytest(require_parallel=False)

        def invoke(argv, *, cwd, env, timeout):
            result = fake(argv, cwd=cwd, env=env, timeout=timeout)
            if "RC6_CI_PROGRESS_PATH" in env:
                with patch.dict(os.environ, {**env, "RC6_CI_PROGRESS_PID": str(os.getpid())}, clear=True), patch.object(identity, "_progress", None):
                    identity.pytest_sessionstart(None)
                    for node in argv[argv.index("--") + 1:]:
                        identity.pytest_runtest_logstart(node, None)
                        for phase in ("setup", "call", "teardown"):
                            identity.pytest_runtest_logreport(SimpleNamespace(
                                nodeid=node, when=phase, outcome="passed"))
                        identity.pytest_runtest_logfinish(node, None)
            return result

        with tempfile.TemporaryDirectory() as directory, patch.object(runner, "_invoke", invoke):
            output = Path(directory) / "diagnostic"
            receipt = runner.run_bounded(SHA, TREE, repo=REPO, shard_count=1,
                shard_index=0, diagnostics_dir=output, batch_limit=1, timeout_seconds=60)
            self.assertEqual(receipt["status"], "RED_BOUNDED_DIAGNOSTIC")
            self.assertFalse(receipt["automatic_go"])
            self.assertEqual(receipt["planned_batches"], 2)
            self.assertEqual(receipt["completed_batches"], 1)
            self.assertEqual(receipt["unselected_count"], 6)
            self.assertEqual(receipt["batches"][0]["progress"]["finished"], 6)
            self.assertEqual(json.loads((output / "receipt.json").read_text()), receipt)
            self.assertEqual([t for a, t in fake.timeouts if "--" in a and a[0] != "git"], [60])
            public = "".join(p.read_text() for p in output.rglob("*") if p.is_file())
            self.assertNotIn("tests/", public)
            self.assertNotIn("sensitive-log", public)

    def test_bounds_fail_before_any_process_or_output(self):
        self.assertTrue(hasattr(runner, "run_bounded"), "bounded runner absent")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "diagnostic"
            for options in ({"timeout_seconds": 59}, {"timeout_seconds": 121},
                            {"timeout_seconds": True}, {"batch_limit": 0},
                            {"batch_limit": 9}, {"batch_offset": -1},
                            {"split_path": "PRIVATE"}):
                with self.subTest(options=options), patch.object(runner, "_invoke") as invoke:
                    with self.assertRaises(runner.ParallelRunnerRed):
                        runner.run_bounded(SHA, TREE, repo=REPO, shard_count=2,
                            shard_index=1, diagnostics_dir=target, **options)
                    self.assertEqual(invoke.call_count, 0)
                    self.assertFalse(target.exists())

    def test_bounded_timeout_exposes_progress_but_ignores_even_complete_junit(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(runner, "_invoke", self._bounded_fake(timeout=True)), \
                patch.dict(os.environ, {"PRIVATE_PROVIDER_TOKEN": "DO_NOT_INHERIT"}):
            output = Path(directory) / "diagnostic"
            receipt = runner.run_bounded(SHA, TREE, repo=REPO, shard_count=2,
                shard_index=1, diagnostics_dir=output, timeout_seconds=120)
            batch = receipt["batches"][0]
            self.assertEqual((batch["executed"], batch["missing"]), (0, 6))
            self.assertEqual((batch["progress"]["started"], batch["progress"]["finished"]), (1, 0))
            self.assertEqual(batch["status"], "RED_INCOMPLETE_DIAGNOSTIC")
            self.assertEqual(len(list(ET.parse(output / "batch-0/junit-sanitized.xml").iter("testcase"))), 0)

    def test_bounded_missing_or_tampered_evidence_checkpoints_incomplete(self):
        for options in ({"corrupt": True}, {"missing": True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory, \
                    patch.object(runner, "_invoke", self._bounded_fake(**options)):
                output = Path(directory) / "diagnostic"
                with self.assertRaises(runner.ParallelRunnerRed):
                    runner.run_bounded(SHA, TREE, repo=REPO, shard_count=1,
                        shard_index=0, diagnostics_dir=output)
                proof = json.loads((output / "receipt.json").read_text())
                self.assertEqual(proof["phase"], "INCOMPLETE")
                self.assertFalse(proof["automatic_go"])
                self.assertEqual(proof["completed_batches"], 0)

    def test_bounded_cli_never_calls_normal_runner_or_returns_green(self):
        with tempfile.TemporaryDirectory() as directory:
            args = ["--source-sha", SHA, "--source-tree-sha", TREE, "--repo", str(REPO),
                    "--shards", "2", "--bounded-shard-index", "1", "--diagnostics-dir", directory]
            with patch.object(runner, "run_bounded", return_value={"automatic_go": False}), \
                    patch.object(runner, "run") as ordinary, redirect_stdout(io.StringIO()):
                try:
                    result = runner.main(args)
                except SystemExit:
                    self.fail("bounded CLI must be independently supported")
                self.assertEqual(result, 2)
                ordinary.assert_not_called()

    def test_timeout_progress_survives_kill_without_junit_credit(self):
        manifest = shards.manifest(SHA, TREE, [NODES[0]])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            env = {**os.environ, "PYTHONPATH": str(REPO), "RC6_CI_IDENTITY_PROOF": "1",
                   "RC6_CI_PROGRESS_PATH": str(path), "RC6_CI_SOURCE_SHA": SHA,
                   "RC6_CI_SOURCE_TREE_SHA": TREE, "RC6_CI_SEGMENT_SHA256": manifest["collection_sha256"]}
            script = ("from scripts import rc6_ci_pytest_identity as p;import time,os;"
                      "os.environ['RC6_CI_PROGRESS_PID']=str(os.getpid());"
                      "p.pytest_sessionstart(None);"
                      f"p.pytest_runtest_logstart({NODES[0]!r},None);time.sleep(10)")
            with self.assertRaises(runner.ParallelRunnerRed):
                runner._invoke([sys.executable, "-c", script], cwd=REPO, env=env, timeout=0.3)
            evidence = runner._read_progress(path, manifest)
            self.assertEqual((evidence["started"], evidence["finished"]), (1, 0))
            self.assertEqual(evidence["last_phase"], "start")

    def test_progress_rejects_partial_line_foreign_node_or_wrong_phase(self):
        manifest = shards.manifest(SHA, TREE, NODES[:2])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.jsonl"
            writer = identity._ProgressWriter(path, SHA, TREE, manifest["collection_sha256"])
            writer.append(NODES[0], "start", "none")
            original = path.read_text()
            invalids = [original[:-2], original + original,
                        original.replace('"start"', '"finish"'),
                        original.replace(identity.nodeid_digest(NODES[0]), "f" * 64)]
            for bad in invalids:
                path.write_text(bad)
                with self.assertRaises(runner.ParallelRunnerRed):
                    runner._read_progress(path, manifest)

    def test_real_pytest_bounded_roundtrip_binds_collection_progress_and_junit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            (repo / "scripts").mkdir(parents=True)
            (repo / "scripts/__init__.py").write_text("")
            for name in ("rc6_ci_pytest_identity.py", "rc6_ci_shard_quorum.py"):
                shutil.copyfile(REPO / "scripts" / name, repo / "scripts" / name)
            policy = repo / "ops/policy"
            policy.mkdir(parents=True)
            (policy / "test-policy.yaml").write_text(
                "test_governance:\n  automatic_discovery_required: true\n"
                "  manual_test_file_allowlist_as_primary_ci: false\n"
                "productive_scope:\n  roots: ['.']\n  runner: pytest\n  exclusions: []\n")
            (repo / "test_example.py").write_text(
                "import os\ndef test_ok():\n"
                "    assert 'PRIVATE_PROVIDER_TOKEN' not in os.environ\n"
                "def test_fail():\n    assert False, 'PRIVATE_TEST_FAILURE'\n")
            for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Fixture",
                    "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"]):
                subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, timeout=10)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
            tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
            with patch.dict(os.environ, {"PRIVATE_PROVIDER_TOKEN": "PRIVATE_SECRET"}):
                receipt = runner.run_bounded(sha, tree, repo=repo, shard_count=1,
                    shard_index=0, diagnostics_dir=root / "diagnostic", timeout_seconds=60)
            self.assertEqual(receipt["phase"], "FINISHED")
            self.assertFalse(receipt["automatic_go"])
            batch = receipt["batches"][0]
            self.assertEqual((batch["executed"], batch["failures"], batch["missing"]), (2, 1, 0))
            self.assertEqual(batch["progress"]["finished"], 2)
            public = "".join(p.read_text() for p in (root / "diagnostic").rglob("*") if p.is_file())
            self.assertNotIn("PRIVATE", public)
            self.assertNotIn("test_example", public)

    def test_real_unittest_subtests_generate_one_bounded_junit_credit(self):
        """A915: verify real pytest/JUnit, not a mocked subTest assumption."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            (repo / "scripts").mkdir(parents=True)
            (repo / "scripts/__init__.py").write_text("")
            for name in ("rc6_ci_pytest_identity.py", "rc6_ci_shard_quorum.py"):
                shutil.copyfile(REPO / "scripts" / name, repo / "scripts" / name)
            (repo / "ops/policy").mkdir(parents=True)
            (repo / "ops/policy/test-policy.yaml").write_text(
                "test_governance:\n  automatic_discovery_required: true\n"
                "  manual_test_file_allowlist_as_primary_ci: false\n"
                "productive_scope:\n  roots: ['.']\n  runner: pytest\n  exclusions: []\n")
            (repo / "test_subtests.py").write_text(
                "import unittest\n"
                "class TestSubtests(unittest.TestCase):\n"
                "    def test_scenarios(self):\n"
                "        for index in range(12):\n"
                "            with self.subTest(index=index):\n"
                "                self.assertGreaterEqual(index, 0)\n")
            for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Fixture",
                         "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"]):
                subprocess.run(["git", *args], cwd=repo, check=True,
                               capture_output=True, timeout=10)
            sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo,
                                          text=True).strip()
            tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo,
                                           text=True).strip()
            receipt = runner.run_bounded(sha, tree, repo=repo, shard_count=1,
                shard_index=0, diagnostics_dir=root / "diagnostic", timeout_seconds=60)
            self.assertEqual(receipt["phase"], "FINISHED")
            self.assertEqual((receipt["completed_batches"], receipt["batches"][0]["executed"],
                              receipt["batches"][0]["failures"]), (1, 1, 0))
            self.assertEqual(receipt["batches"][0]["progress"]["finished"], 1)


if __name__ == "__main__":
    unittest.main()
