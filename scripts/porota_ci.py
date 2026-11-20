#!/usr/bin/env python3
"""Run automatic code discovery once and emit a compact, truthful CI receipt.

Retired workflow assertions are selected by exact test identity in the reviewed
policy. Neighboring application tests remain automatically collected. Raw test
output and JUnit are not release artifacts.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time


def _policy(root):
    import yaml
    policy = yaml.safe_load((root / "ops/policy/test-policy.yaml").read_text())
    governance, scope = policy["test_governance"], policy["productive_scope"]
    if governance["automatic_discovery_required"] is not True or governance["manual_test_file_allowlist_as_primary_ci"] is not False or scope["roots"] != ["."]:
        raise ValueError("AUTOMATIC_DISCOVERY_REQUIRED")
    arguments = []
    for item in scope.get("exclusions", []):
        if not all(item.get(key) for key in ("path", "classification", "reason", "successor", "owner")):
            raise ValueError("EXCLUSION_METADATA_REQUIRED")
        if not (root / item["path"]).is_file() or not (root / item["successor"]).is_file():
            raise ValueError("EXCLUSION_SOURCE_REQUIRED")
        arguments.append("--ignore=" + item["path"])
    retired = json.loads((root / scope["retired_contracts"]).read_text()) if scope.get("retired_contracts") else []
    entries = {}
    for item in retired:
        if set(item) != {"nodeid", "reason", "successor", "owner"} or not all(isinstance(value, str) and value for value in item.values()):
            raise ValueError("RETIREMENT_METADATA_REQUIRED")
        nodeid = item["nodeid"]
        if "::" not in nodeid or "[" in nodeid or nodeid in entries or not (root / item["successor"]).is_file():
            raise ValueError("RETIREMENT_IDENTITY_INVALID")
        entries[nodeid] = item
    return arguments, entries


def _python_path(root, path):
    try:
        path = Path(path)
        path = (path if path.is_absolute() else root / path).resolve(strict=True)
        relative = path.relative_to(root.resolve()).as_posix()
        if path.is_file() and re.fullmatch(r'[A-Za-z0-9_./-]{1,500}\.py', relative):
            return relative
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return None


def _failure_location(root, nodeid, phase, path, lineno, exception_type=None):
    """Only source coordinates cross the receipt boundary, never error text."""
    relative = _python_path(root, path)
    if (relative is None or phase not in {'collect', 'setup', 'call', 'teardown'}
            or type(lineno) is not int or not 1 <= lineno <= 10_000_000):
        return None
    parts = nodeid.split('[', 1)[0].split('::') if isinstance(nodeid, str) else []
    source = _python_path(root, parts[0]) if parts else None
    identity = (source + ''.join('::' + part for part in parts[1:])
                if source and all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', part)
                                  for part in parts[1:]) else relative)
    result = {'nodeid': identity, 'phase': phase, 'path': relative, 'lineno': lineno}
    if isinstance(exception_type, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', exception_type):
        result['exception_type'] = exception_type
    return result


class _Receipt:
    def __init__(self, root, output, identity):
        self.root, self.output, self.identity = root, output, identity
        _, self.retirement = _policy(root)
        self.collected = 0
        self.executed = set()
        self.retired = []
        self.failure_ids = set()
        self.failure_locations = {}
        self.failed = self.errors = self.skipped = 0
        self._last_progress_module = None
        self._last_progress_at = 0.0

    def _report_location(self, report, phase):
        location = getattr(report, 'location', None)
        if isinstance(location, (tuple, list)) and len(location) >= 2 and type(location[1]) is int:
            value = _failure_location(self.root, report.nodeid, phase, location[0], location[1] + 1)
            if value:
                self.failure_locations.setdefault((report.nodeid, phase), value)

    def pytest_exception_interact(self, node, call, report):
        if not report.failed or call.excinfo is None:
            return
        # Follow structured causes for wrapped import/collection errors. Never
        # stringify exceptions, locals, assertion values or pytest longrepr.
        error, seen = call.excinfo.value, set()
        while error is not None and id(error) not in seen and len(seen) < 8:
            seen.add(id(error))
            trace = error.__traceback__
            while trace is not None:
                value = _failure_location(self.root, report.nodeid, call.when,
                                          trace.tb_frame.f_code.co_filename, trace.tb_lineno,
                                          type(error).__name__)
                if value:
                    self.failure_locations[(report.nodeid, call.when)] = value
                trace = trace.tb_next
            if isinstance(error, SyntaxError):
                value = _failure_location(self.root, report.nodeid, call.when,
                                          error.filename, error.lineno, type(error).__name__)
                if value:
                    self.failure_locations[(report.nodeid, call.when)] = value
            cause = error.__cause__
            error = cause if cause is not None else (None if error.__suppress_context__ else error.__context__)

    def pytest_collection_modifyitems(self, session, config, items):
        import pytest
        kept, retired = [], []
        matched = set()
        for item in items:
            identity = item.nodeid.split("[", 1)[0]
            if identity in self.retirement:
                retired.append(item)
                matched.add(identity)
            else:
                kept.append(item)
        missing = set(self.retirement) - matched
        if missing:
            raise pytest.UsageError("RETIRED_TEST_NOT_FOUND:" + ",".join(sorted(missing)))
        self.collected = len(kept)
        self.retired = sorted(matched)
        if retired:
            config.hook.pytest_deselected(items=retired)
        items[:] = kept
        # Fail-closed progress snapshot survives SIGKILL before sessionfinish.
        self._checkpoint_progress(None)

    def _checkpoint_progress(self, module):
        receipt = _failclosed_start_receipt(
            self.output, self.identity, "PYTEST_IN_PROGRESS")
        receipt["collected"] = self.collected
        receipt["executed"] = len(self.executed)
        if module is not None:
            receipt["active_test_module"] = module
        self.output.write_text(json.dumps(receipt, sort_keys=True) + "\n")
        self._last_progress_at = time.monotonic()
        self._last_progress_module = module

    def pytest_runtest_logstart(self, nodeid, location):
        # File-only allowlisted identity, never parameter values/assertions.
        module = _python_path(self.root, nodeid.split("::", 1)[0])
        now = time.monotonic()
        if module is not None and (
                module != self._last_progress_module or
                now - self._last_progress_at >= 20):
            self._checkpoint_progress(module)

    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            self.executed.add(report.nodeid)
        if report.failed:
            self.failure_ids.add(report.nodeid.split("[", 1)[0])
            self._report_location(report, report.when)
            if report.when == "call":
                self.failed += 1
            else:
                self.errors += 1
        if report.skipped:
            self.skipped += 1

    def pytest_collectreport(self, report):
        if report.failed:
            self.errors += 1
            self.failure_ids.add(report.nodeid.split("[", 1)[0])
            self._report_location(report, 'collect')

    def pytest_sessionfinish(self, session, exitstatus):
        passed = (exitstatus == 0 and self.collected > 0 and len(self.executed) == self.collected
                  and self.failed == self.errors == self.skipped == 0)
        locations = {json.dumps(value, sort_keys=True): value for value in self.failure_locations.values()}
        receipt = {"schema": "porota.code-tests.v1", **self.identity,
                   "status": "PASS" if passed else "FAIL", "collected": self.collected,
                   "executed": len(self.executed), "failed": self.failed, "errors": self.errors,
                   "skipped": self.skipped, "retired": self.retired,
                   "failure_ids": sorted(self.failure_ids),
                   "failure_locations": [locations[key] for key in sorted(locations)]}
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(receipt, sort_keys=True) + "\n")


def pytest_configure(config):
    """Loaded explicitly with -p; normal repository test runs do not enable it."""
    if os.getenv("POROTA_CI_RECEIPT_PATH"):
        config.pluginmanager.register(_Receipt(
            Path.cwd(), Path(os.environ["POROTA_CI_RECEIPT_PATH"]),
            json.loads(os.environ["POROTA_CI_IDENTITY"])), "porota-receipt")


def _failclosed_start_receipt(output, identity, phase, *, process_exit=None, progress=None):
    """Leave a small public FAIL receipt if source preflight or pytest dies early.

    Never interpret absence of pytest_sessionfinish as success, never leak the
    suppressed pytest output, environment, assertion values or private paths.
    Completed pytest still atomically supplies its normal authoritative report.
    """
    if phase not in {"SOURCE_PRECHECK_PENDING", "PYTEST_PRECHECK_PENDING",
                     "PYTEST_FINAL_RECEIPT_ABSENT", "PYTEST_IN_PROGRESS",
                     "GOVERNED_SHARD_QUORUM_REJECTED",
                     "GOVERNED_SHARD_IN_PROGRESS"}:
        raise ValueError("CI_RECEIPT_PHASE_INVALID")
    safe = {}
    for key, pattern in (("source_sha", r"[0-9a-f]{40}"),
                         ("source_tree", r"[0-9a-f]{40}"),
                         ("image_id", r"sha256:[0-9a-f]{64}")):
        value = identity.get(key)
        safe[key] = value if isinstance(value, str) and re.fullmatch(pattern, value) else "NO_VERIFICADO"
    receipt = {
        "schema": "porota.code-tests.v1", **safe, "status": "FAIL",
        "collected": 0, "executed": 0, "failed": 0, "errors": 1,
        "skipped": 0, "retired": [],
        "failure_ids": ["CI_RUNNER_NO_COMPLETION_RECEIPT"],
        "failure_locations": [], "runner_phase": phase,
    }
    if type(process_exit) is int:
        receipt["process_exit_class"] = (
            "SIGNAL" if process_exit < 0
            else "NO_REPORT_ZERO_EXIT" if process_exit == 0
            else "NO_REPORT_NONZERO_EXIT")
        if -128 <= process_exit <= 255:
            receipt["process_exit_code"] = process_exit
    if progress is not None:
        if (type(progress) is not dict or set(progress) != {"stage", "index", "completed"}
                or progress["stage"] not in {
                    "SOURCE_CHECKOUT", "COLLECTION", "COLLECTION_COMPLETE",
                    "SHARD_RUNNING", "SHARD_COMPLETE", "JUNIT_QUORUM"}
                or type(progress["index"]) is not int
                or type(progress["completed"]) is not int
                or not -1 <= progress["index"] < 16
                or not 0 <= progress["completed"] <= 16
                or (progress["stage"] in {"SHARD_RUNNING", "SHARD_COMPLETE"}
                    and progress["index"] < 0)
                or (progress["stage"] not in {"SHARD_RUNNING", "SHARD_COMPLETE"}
                    and progress["index"] != -1)):
            raise ValueError("CI_PROGRESS_METADATA_INVALID")
        receipt["progress_stage"] = progress["stage"]
        receipt["progress_shard_index"] = progress["index"]
        receipt["progress_completed_shards"] = progress["completed"]
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replacement retains the previous valid FAIL envelope if the
    # outer Docker/Actions deadline kills a writer mid-checkpoint.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=str(output.parent),
                prefix=".porota-ci-receipt-", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(receipt, sort_keys=True) + "\n")
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return receipt


def _cgroup_oom_kills():
    """Read only the cgroup-v2 oom_kill counter; no paths or raw logs exported."""
    try:
        with open("/sys/fs/cgroup/memory.events", encoding="ascii") as handle:
            raw = handle.read(4097)
        if len(raw) > 4096:
            return None
        fields = [line.split() for line in raw.splitlines()]
        matches = [value for field, value in fields if field == "oom_kill"]
        if len(matches) != 1 or not matches[0].isdigit():
            return None
        count = int(matches[0])
        return count if 0 <= count <= 2**50 else None
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _governed_serial_shard_tests(root, output, identity, retirement):
    """Elevate an EXACT image JUnit quorum, never a pilot label by itself.

    Existing runner auto-collects all nodeids, verifies each retirement against
    global collection, then runs disjoint shards with only one pytest child at
    a time. No second production test suite, no missing/xfail/skip exemption.
    """
    from scripts import rc6_ci_parallel_runner as shard_runner

    previous_temp = os.environ.get("RUNNER_TEMP")
    # /var/tmp is disk-backed only if the existing scratch guard verifies it;
    # fail closed otherwise. Docker image checkout /app must remain untouched.
    os.environ["RUNNER_TEMP"] = "/var/tmp"
    before = _cgroup_oom_kills()
    latest_progress = None

    def on_progress(stage, index, completed):
        nonlocal latest_progress
        latest_progress = {"stage": stage, "index": index, "completed": completed}
        _failclosed_start_receipt(
            output, identity, "GOVERNED_SHARD_IN_PROGRESS",
            progress=latest_progress)

    try:
        with tempfile.TemporaryDirectory(prefix="porota-ci-quorum-", dir="/var/tmp") as temp:
            try:
                proof = shard_runner.run(
                    identity["source_sha"], identity["source_tree"], repo=root,
                    shard_count=16, max_workers=1,
                    retired_nodeids=sorted(retirement),
                    output_junit=Path(temp) / "governed-tests.xml",
                    progress=on_progress)
            except (shard_runner.ParallelRunnerRed, OSError, ValueError,
                    subprocess.TimeoutExpired):
                receipt = _failclosed_start_receipt(
                    output, identity, "GOVERNED_SHARD_QUORUM_REJECTED",
                    progress=latest_progress)
                failure = sys.exc_info()[1]
                receipt["validation_failure_phase"] = (
                    failure.reason if isinstance(
                        failure, shard_runner.ParallelRunnerRed)
                    else "UNVERIFIED")
                after = _cgroup_oom_kills()
                if type(before) is int and type(after) is int and after >= before:
                    receipt["cgroup_oom_kill_delta"] = min(after - before, 100_000)
                    receipt["cgroup_oom_status"] = (
                        "OOM_KILL_EVENT_OBSERVED" if after > before
                        else "NO_OOM_KILL_EVENT_OBSERVED")
                else:
                    receipt["cgroup_oom_status"] = "NO_VERIFICADO"
                output.write_text(json.dumps(receipt, sort_keys=True) + "\n")
                return receipt
    finally:
        if previous_temp is None:
            os.environ.pop("RUNNER_TEMP", None)
        else:
            os.environ["RUNNER_TEMP"] = previous_temp

    valid = (
        proof.get("status") == "GREEN_QUORUM_OFFLINE_ONLY"
        and proof.get("source_sha") == identity["source_sha"]
        and proof.get("source_tree_sha") == identity["source_tree"]
        and proof.get("shards") == 16 and proof.get("execution_workers") == 1
        and proof.get("ci_activated") is False
        and proof.get("automatic_go") is False
        and proof.get("runtime_authority") is False
        and type(proof.get("discovered")) is int
        and proof["discovered"] >= 16
        and proof.get("executed") == proof["discovered"]
        and type(proof.get("retired_testcases")) is int
        and proof["retired_testcases"] >= len(retirement)
        and proof.get("globally_discovered") ==
            proof["discovered"] + proof["retired_testcases"]
        and proof.get("retired") == sorted(retirement)
    )
    if not valid:
        return _failclosed_start_receipt(
            output, identity, "GOVERNED_SHARD_QUORUM_REJECTED",
            progress=latest_progress)
    # This receipt is issued by the governing Predeploy runner only AFTER
    # exact JUnit/nodeid/exit quorum. It is not deploy or PAPER entry authority.
    receipt = {
        "schema": "porota.code-tests.v1", **identity,
        "status": "PASS", "collected": proof["discovered"],
        "executed": proof["executed"], "failed": 0, "errors": 0,
        "skipped": 0, "retired": sorted(retirement),
        "failure_ids": [], "failure_locations": [],
        "runner_mode": "GOVERNED_EXACT_SERIAL_SHARDS",
        "collection_sha256": proof["collection_sha256"],
        "shards": 16, "execution_workers": 1,
        "globally_discovered": proof["globally_discovered"],
        "retired_testcases": proof["retired_testcases"],
        "automatic_deploy": False,
    }
    output.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    return receipt


def run_code_tests(root, output, identity):
    root, output = Path(root).resolve(), Path(output).resolve()
    _failclosed_start_receipt(output, identity, "PYTEST_PRECHECK_PENDING")
    arguments, retirement = _policy(root)
    # The source identity was already verified by main(). Miniature fixture
    # roots still use the historical single-process test harness unchanged.
    if (root == Path(__file__).resolve().parents[1]
            and os.environ.get("POROTA_BUILD_SHA") == identity.get("source_sha")
            and os.environ.get("POROTA_CANDIDATE_TREE_SHA") ==
                identity.get("source_tree")):
        return _governed_serial_shard_tests(root, output, identity, retirement)
    environment = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                       POROTA_CI_RECEIPT_PATH=str(output), POROTA_CI_IDENTITY=json.dumps(identity),
                       PYTHONDONTWRITEBYTECODE="1")
    # Explicit plugin source is the tested image; .git/docs mounts are metadata.
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    oom_kills_before = _cgroup_oom_kills()
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             "-p", "scripts.porota_ci", "-o", "xfail_strict=true", ".", *arguments],
                            cwd=root, env=environment, timeout=2400, check=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not output.is_file():
        raise ValueError("CODE_TEST_RECEIPT_MISSING")
    receipt = json.loads(output.read_text())
    if receipt.get("runner_phase") in {"PYTEST_PRECHECK_PENDING", "PYTEST_IN_PROGRESS"}:
        prior = receipt
        receipt = _failclosed_start_receipt(
            output, identity, "PYTEST_FINAL_RECEIPT_ABSENT",
            process_exit=result.returncode)
        # These are progress counters only, not a proof of completed tests.
        for field in ("collected", "executed"):
            value = prior.get(field)
            if type(value) is int and 0 <= value <= 100_000:
                receipt[field] = value
        module = prior.get("active_test_module")
        if isinstance(module, str) and _python_path(root, module) == module:
            receipt["active_test_module_before_interrupt"] = module
        after = _cgroup_oom_kills()
        if type(oom_kills_before) is int and type(after) is int and after >= oom_kills_before:
            delta = after - oom_kills_before
            receipt["cgroup_oom_kill_delta"] = min(delta, 100_000)
            receipt["cgroup_oom_status"] = (
                "OOM_KILL_EVENT_OBSERVED" if delta > 0 else "NO_OOM_KILL_EVENT_OBSERVED")
        else:
            receipt["cgroup_oom_status"] = "NO_VERIFICADO"
        output.write_text(json.dumps(receipt, sort_keys=True) + "\n")
    if result.returncode and receipt.get("status") == "PASS":
        raise ValueError("CODE_TEST_EXIT_MISMATCH")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    identity = {"source_sha": args.source_sha, "source_tree": args.source_tree,
                "image_id": args.image_id}
    _failclosed_start_receipt(args.output, identity, "SOURCE_PRECHECK_PENDING")
    from scripts.porota_image_source_identity import verify_source_identity
    verify_source_identity(args.source_sha, args.source_tree)
    receipt = run_code_tests(root, args.output, identity)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
