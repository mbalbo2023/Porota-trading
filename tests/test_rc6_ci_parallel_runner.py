"""Mocked pytest replay: global collection and truly concurrent exact shards."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import signal
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

    def test_workflow_verifier_accepts_only_honest_partial_red_and_complete_red(self):
        """Execute the actual YAML verifier offhost, including adversarial receipt edits."""
        workflow = yaml.load((REPO / ".github/workflows/rc6-ws-tests-12-current-contract.yml")
                             .read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        step = next(s for s in workflow["jobs"]["diagnostic-shards"]["steps"]
                    if s.get("id") == "receipt")
        snippet = step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]

        def assert_verifier(work, *, expected):
            captured = io.StringIO()
            with (patch.dict(os.environ, {"RUNNER_TEMP": str(work), "GITHUB_SHA": SHA}),
                  patch.object(subprocess, "check_output", return_value=TREE + "\n"),
                  redirect_stdout(captured)):
                exec(compile(snippet, "<a820-workflow-verifier>", "exec"),
                     {"__name__": "__main__"})
            self.assertIn(expected, captured.getvalue())

        scenarios = [
            (FakePytest(timeout_nodeids={NODES[0]}, failed_nodeids={NODES[6]}),
             "INCOMPLETE_RED"),
            (FakePytest(failed_nodeids={NODES[6]}), "COMPLETE_RED"),
            (FakePytest(timeout_nodeids=set(NODES)), "INCOMPLETE_RED"),
        ]
        for fake, expected in scenarios:
            with self.subTest(expected=expected, timeout=bool(fake.timeout_nodeids)):
                with tempfile.TemporaryDirectory() as directory:
                    work = Path(directory)
                    with patch.object(runner, "_invoke", fake):
                        runner.run(SHA, TREE, repo=REPO, shard_count=2,
                                   output_junit=work / "normal.xml",
                                   diagnostics_dir=work / "rc6-a820-sanitized")
                    assert_verifier(work, expected=expected)
                    receipt_path = work / "rc6-a820-sanitized/receipt.json"
                    original = json.loads(receipt_path.read_text())
                    for key, value in (("automatic_go", True), ("missing", 999),
                                       ("sanitized_junit_sha256", "f" * 64)):
                        tampered = {**original, key: value}
                        receipt_path.write_text(json.dumps(tampered), encoding="utf-8")
                        with self.assertRaises(AssertionError):
                            assert_verifier(work, expected=expected)
                    receipt_path.write_text(json.dumps(original), encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
