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


def run_code_tests(root, output, identity):
    root, output = Path(root).resolve(), Path(output).resolve()
    arguments, _ = _policy(root)
    output.unlink(missing_ok=True)
    environment = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                       POROTA_CI_RECEIPT_PATH=str(output), POROTA_CI_IDENTITY=json.dumps(identity),
                       PYTHONDONTWRITEBYTECODE="1")
    # Explicit plugin source is the tested image; .git/docs mounts are metadata.
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                             "-p", "scripts.porota_ci", "-o", "xfail_strict=true", ".", *arguments],
                            cwd=root, env=environment, timeout=2400, check=False,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not output.is_file():
        raise ValueError("CODE_TEST_RECEIPT_MISSING")
    receipt = json.loads(output.read_text())
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
    from scripts.porota_image_source_identity import verify_source_identity
    verify_source_identity(args.source_sha, args.source_tree)
    receipt = run_code_tests(root, args.output, {"source_sha": args.source_sha,
                             "source_tree": args.source_tree, "image_id": args.image_id})
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
