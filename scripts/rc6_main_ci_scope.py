"""Fail-closed classification and test proof for main's repository CI.

The main control-plane tree is not the RC6 product package. Passing its own
tests must not be reported as a product test, build, runtime, or READY result.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


class ScopeError(ValueError):
    pass


_CONTROL_DEPENDENCIES = (
    "pytest==9.1.1",
    "PyYAML==6.0.3",
    "cryptography==50.0.2",
)


def validate_control_deps(root: Path) -> int:
    """Enforce the exact preexisting CI pins before enabling pip caching."""
    lock = Path(root) / ".github/requirements-control-ci.txt"
    if not lock.is_file():
        raise ScopeError("CI_CONTROL_DEPS_MISSING")
    if lock.is_symlink():
        raise ScopeError("CI_CONTROL_DEPS_MISMATCH")
    try:
        if lock.stat().st_size > 512 or lock.read_text(encoding="utf-8") != (
                "\n".join(_CONTROL_DEPENDENCIES) + "\n"):
            raise ScopeError("CI_CONTROL_DEPS_MISMATCH")
    except (OSError, UnicodeError):
        raise ScopeError("CI_CONTROL_DEPS_MISMATCH") from None
    return len(_CONTROL_DEPENDENCIES)


_REPOSITORY = "mbalbo2023/Porota-trading"
_SHA40 = re.compile(r"[0-9a-fA-F]{40}\Z")
_DIFF_SIZE_MAX = 65536


def _valid_sha(value: object) -> bool:
    return (isinstance(value, str) and _SHA40.fullmatch(value) is not None
            and value != "0" * 40)


def _git_name_status(root: Path, before: str, after: str) -> list[tuple[str, str]] | None:
    """Read authenticated git objects as bounded NUL records, never PR file lists."""
    try:
        for sha in (before, after):
            kind = subprocess.run(
                ["git", "cat-file", "-t", sha], cwd=root, timeout=10,
                check=True, capture_output=True, text=True,
            )
            if kind.stdout.strip() != "commit":
                return None
        result = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--no-textconv",
             "--name-status", "-z", "--find-renames", before, after, "--"],
            cwd=root, timeout=10, check=True, capture_output=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        return None
    if len(result.stdout) > _DIFF_SIZE_MAX or not result.stdout.endswith(b"\0"):
        return None
    tokens = result.stdout[:-1].split(b"\0")
    if len(tokens) % 2 or len(tokens) > 1024:
        return None
    changes = []
    try:
        for i in range(0, len(tokens), 2):
            status, path = tokens[i].decode("ascii"), tokens[i + 1].decode("ascii")
            # Rename/copy output has an extra path; it can never grant a fast path.
            if status != "M":
                return None
            changes.append((status, path))
    except UnicodeDecodeError:
        return None
    return changes


def classify_event_diff(
        root: Path, event: dict, *, event_name: str, repository: str,
        github_sha: str) -> str:
    """Advisory path scope from trusted GitHub event SHAs and local git trees.

    A changed-path result never certifies release, PAPER safety, or runtime.
    Any unknown source, insufficient git history, or ambiguous diff runs full.
    """
    if (not isinstance(event, dict) or repository != _REPOSITORY or
            not _valid_sha(github_sha) or
            not isinstance(event.get("repository"), dict) or
            event["repository"].get("full_name") != repository):
        return "FULL_REQUIRED"
    if event_name == "pull_request":
        pr = event.get("pull_request")
        if not isinstance(pr, dict) or type(pr.get("number")) is not int or pr["number"] <= 0:
            return "FULL_REQUIRED"
        base, head = pr.get("base"), pr.get("head")
        if not isinstance(base, dict) or not isinstance(head, dict):
            return "FULL_REQUIRED"
        if base.get("ref") not in {"main", "develop", "testing"}:
            return "FULL_REQUIRED"
        if (not isinstance(base.get("repo"), dict) or
                base["repo"].get("full_name") != repository or
                not isinstance(head.get("repo"), dict) or
                head["repo"].get("full_name") != repository):
            # Fork PRs never get a reduced path; still run security checks.
            return "FULL_REQUIRED"
        before, after = base.get("sha"), head.get("sha")
    elif event_name == "push":
        if (event.get("ref") not in {
                "refs/heads/main", "refs/heads/develop", "refs/heads/testing"
        } or event.get("deleted") or event.get("forced")):
            return "FULL_REQUIRED"
        before, after = event.get("before"), event.get("after")
        if after != github_sha:
            return "FULL_REQUIRED"
    else:
        # workflow_dispatch and all other events are not attestable path diffs.
        return "FULL_REQUIRED"
    if not _valid_sha(before) or not _valid_sha(after) or before == after:
        return "FULL_REQUIRED"
    changes = _git_name_status(Path(root), before, after)
    if changes is None:
        return "FULL_REQUIRED"
    return classify_changed_paths(changes)


def classify_github_diff() -> str:
    """Output a bounded advisory plan; malformed or absent event stays FULL."""
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not event_path:
        return "FULL_REQUIRED"
    try:
        source = Path(event_path)
        if not source.is_file() or source.stat().st_size > 1024 * 1024:
            return "FULL_REQUIRED"
        event = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return "FULL_REQUIRED"
    # The runner must be checked out at the *same* GitHub SHA as the event.
    # For PRs, the synthetic merge's two exact parents bind base and head;
    # otherwise a stale checkout/event could mislabel a different code tree.
    try:
        graph = subprocess.run(
            ["git", "rev-list", "--parents", "-n", "1", "HEAD"],
            cwd=Path.cwd(), capture_output=True, text=True,
            check=True, timeout=10,
        ).stdout.strip().split()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "FULL_REQUIRED"
    github_sha = os.environ.get("GITHUB_SHA", "")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if not graph or graph[0] != github_sha:
        return "FULL_REQUIRED"
    if event_name == "pull_request":
        pr = event.get("pull_request") if isinstance(event, dict) else None
        if not isinstance(pr, dict):
            return "FULL_REQUIRED"
        base, head = pr.get("base"), pr.get("head")
        if (len(graph) != 3 or not isinstance(base, dict) or
                not isinstance(head, dict) or
                graph[1] != base.get("sha") or graph[2] != head.get("sha")):
            return "FULL_REQUIRED"
    return classify_event_diff(
        Path.cwd(), event, event_name=event_name,
        repository=os.environ.get("GITHUB_REPOSITORY", ""),
        github_sha=github_sha,
    )


def classify_changed_paths(changes: list[tuple[str, str]]) -> str:
    """Conservative advisory plan; never grants release or skips PAPER gates."""
    if not 0 < len(changes) <= 512:
        return "FULL_REQUIRED"
    classes = set()
    for status, path in changes:
        if status != "M" or not isinstance(path, str):
            return "FULL_REQUIRED"
        if (len(path) > 240 or
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", path) is None or
                any(not part or part.startswith(".") for part in path.split("/"))):
            return "FULL_REQUIRED"
        if (path == "README.md" or
                (path.startswith("docs/") and path.endswith(".md") and
                 not path.startswith(("docs/governance/", "docs/policy/",
                                          "docs/security/", "docs/ci/",
                                          "docs/runbooks/", "docs/ops/")))):
            classes.add("DOCS_ONLY")
        elif path in {"da_dashboard_ux_hf6.py",
                      "rc6_trader_dashboard/views_analytics.py"}:
            classes.add("DASHBOARD_ONLY")
        else:
            return "FULL_REQUIRED"
    return next(iter(classes)) if len(classes) == 1 else "FULL_REQUIRED"


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
    if argv == ["validate-control-deps"]:
        count = validate_control_deps(Path.cwd())
        print(f"CONTROL_CI_DEPENDENCIES=PINNED|count={count}|cache=pip")
        return 0
    if argv == ["classify-diff"]:
        print(classify_github_diff())
        return 0
    raise ScopeError("CI_COMMAND_INVALID")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ScopeError as error:
        print(f"::error::{error}", file=sys.stderr)
        raise SystemExit(2) from None
