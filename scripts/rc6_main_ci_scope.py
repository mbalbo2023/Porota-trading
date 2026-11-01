"""Fail-closed classification and test proof for main's repository CI.

The main control-plane tree is not the RC6 product package. Passing its own
tests must not be reported as a product test, build, runtime, or READY result.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest


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
    suite = unittest.TestLoader().discover(str(tests), pattern="test_*.py")
    count = suite.countTestCases()
    if count < 1:
        raise ScopeError("CI_CONTROL_TESTS_EMPTY")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful() or result.skipped or result.testsRun != count:
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
