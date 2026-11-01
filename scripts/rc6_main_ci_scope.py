"""Fail-closed classification and test proof for main's repository CI.

The main control-plane tree is not the RC6 product package. Passing its own
tests must not be reported as a product test, build, runtime, or READY result.
"""
from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


class ScopeError(ValueError):
    pass


def classify(root: Path) -> str:
    root = Path(root)
    requirements = (root / "requirements.txt").is_file()
    entrypoint = (root / "entrypoint.py").is_file()
    if requirements != entrypoint:
        raise ScopeError("CI_APPLICATION_PACKAGE_PARTIAL")
    if requirements:
        return "APPLICATION"
    # Never infer a control plane from arbitrary missing application files.
    if not (root / "AGENTS.md").is_file() or not (
            root / ".github/workflows/checkpoint-attestation.yml").is_file():
        raise ScopeError("CI_REPOSITORY_SCOPE_UNVERIFIED")
    return "CONTROL_PLANE_ONLY"


def run_control_tests(root: Path) -> int:
    root = Path(root)
    if classify(root) != "CONTROL_PLANE_ONLY":
        raise ScopeError("CI_CONTROL_TESTS_WRONG_SCOPE")
    tests = root / "tests"
    if not tests.is_dir():
        raise ScopeError("CI_CONTROL_TESTS_MISSING")
    # pytest includes unittest.TestCase *and* pytest-style functions. JUnit
    # gives an exact collected/executed denominator even when pytest exits 0
    # after skipping every test. Keep the XML in the runner's temp directory.
    with tempfile.TemporaryDirectory(prefix="rc6-main-ci-") as scratch:
        report = Path(scratch) / "control-tests.xml"
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                 "--junitxml=" + str(report), "tests"],
                cwd=root, timeout=120, check=False)
        except subprocess.TimeoutExpired:
            raise ScopeError("CI_CONTROL_TESTS_TIMEOUT") from None
        if completed.returncode == 5:
            raise ScopeError("CI_CONTROL_TESTS_EMPTY")
        if not report.is_file():
            raise ScopeError("CI_CONTROL_TESTS_REPORT_MISSING")
        try:
            xml = ET.parse(report).getroot()
            suites = [xml] if xml.tag == "testsuite" else xml.findall("testsuite")
            if not suites or xml.tag not in {"testsuite", "testsuites"}:
                raise ValueError("unknown report")
            count = sum(int(s.attrib["tests"]) for s in suites)
            failures = sum(int(s.attrib.get("failures", "0")) for s in suites)
            errors = sum(int(s.attrib.get("errors", "0")) for s in suites)
            skipped = sum(int(s.attrib.get("skipped", "0")) for s in suites)
            cases = sum(len(s.findall("testcase")) for s in suites)
        except (ET.ParseError, KeyError, TypeError, ValueError):
            raise ScopeError("CI_CONTROL_TESTS_REPORT_INVALID") from None
        if count < 1:
            raise ScopeError("CI_CONTROL_TESTS_EMPTY")
        # pytest's JUnit `tests` includes unittest subtests, which share a
        # testcase element; count may exceed elements but never be lower.
        if (completed.returncode != 0 or cases < 1 or count < cases or
                failures or errors or skipped):
            raise ScopeError("CI_CONTROL_TESTS_INCOMPLETE_OR_FAILED")
        return count


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["classify"]:
        print(classify(Path.cwd()))
        return 0
    if argv == ["test-control"]:
        count = run_control_tests(Path.cwd())
        print(f"CONTROL_PLANE_TESTS=GREEN|ran={count}|skipped=0|product_tests=NOT_APPLICABLE")
        return 0
    raise ScopeError("CI_COMMAND_INVALID")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ScopeError as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(2) from None
