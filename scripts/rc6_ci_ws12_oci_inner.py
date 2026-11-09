"""Trusted-main OCI test harness, never a publisher or release authority.

Run only INSIDE an independently constrained, secretless, nonroot QA container.
The candidate tree is read-only at /src and this trusted file is read-only at
/control; private collection/JUnit/pytest output remains under /work until an
outer trusted publisher extracts and verifies it.  No network or host Docker
access belongs in this module. Even a zero exit is NOT a certificate.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

import yaml


HOLD = 79
SUBSET_ARGS = ["tests/"]
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")


class InnerHold(ValueError):
    """Test source, subprocess or policy is not independently established."""


@dataclass(frozen=True)
class Step:
    phase: str
    argv: tuple[str, ...]
    manifest: str
    timeout: int


def productive_args(repo: Path) -> list[str]:
    """Read the existing Predeploy test-policy, never a second test allowlist."""
    repo = Path(repo)
    policy_path = repo / "ops/policy/test-policy.yaml"
    if policy_path.is_symlink() or not policy_path.is_file():
        raise InnerHold("POLICY_MISSING")
    try:
        raw = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
        governance = raw["test_governance"]
        scope = raw["productive_scope"]
        if (governance.get("automatic_discovery_required") is not True or
                governance.get("manual_test_file_allowlist_as_primary_ci") is not False or
                scope.get("runner") != "pytest" or scope.get("roots") != ["."]):
            raise InnerHold("POLICY_DRIFT")
        exclusions = scope.get("exclusions", [])
        if not isinstance(exclusions, list) or len(exclusions) > 16:
            raise InnerHold("POLICY_EXCLUSIONS")
        result = ["."]
        seen = set()
        for item in exclusions:
            if not isinstance(item, dict):
                raise InnerHold("POLICY_EXCLUSION_ITEM")
            path = item.get("path")
            successor = item.get("successor")
            if (not isinstance(path, str) or not isinstance(successor, str) or
                    not all(isinstance(item.get(k), str) and item[k].strip()
                            for k in ("classification", "reason", "owner")) or
                    any("\n" in value or "\r" in value for value in (path, successor))):
                raise InnerHold("POLICY_EXCLUSION_IDENTITY")
            relative = Path(path)
            replacement = Path(successor)
            if (relative.is_absolute() or replacement.is_absolute() or
                    ".." in relative.parts or ".." in replacement.parts or
                    path in seen or not (repo / relative).is_file() or
                    not (repo / replacement).is_file() or
                    (repo / relative).is_symlink() or (repo / replacement).is_symlink()):
                raise InnerHold("POLICY_EXCLUSION_PATH")
            seen.add(path)
            result.append("--ignore=" + path)
        return result
    except (KeyError, TypeError, ValueError, yaml.YAMLError, OSError) as exc:
        raise InnerHold("POLICY_UNVERIFIED") from None


def pytest_env(source_sha: str, tree: str, manifest: str,
               inherited: dict[str, str] | None = None, *,
               evidence_root: Path = Path("/work/evidence")) -> dict[str, str]:
    """Explicit safe allowlist; no Actions tokens, candidate env or addons."""
    if any(_HEX40.fullmatch(value) is None for value in (source_sha, tree)):
        raise InnerHold("SOURCE_IDENTITY")
    if Path(manifest).parent != evidence_root or ".." in Path(manifest).parts:
        raise InnerHold("OUTPUT_IDENTITY")
    return {"PATH": "/usr/local/bin:/usr/bin:/bin", "LC_ALL": "C.UTF-8",
            "HOME": "/work", "PYTHONPATH": "/src", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "RC6_CI_IDENTITY_PROOF": "1",
            "RC6_CI_SOURCE_SHA": source_sha, "RC6_CI_SOURCE_TREE_SHA": tree,
            "RC6_CI_MANIFEST_PATH": manifest}


def plan(source_sha: str, tree: str, repo: Path, evidence: Path,
         python: str = "python3") -> tuple[Step, ...]:
    """Three independent evidence steps, exact same root scope as Predeploy."""
    args = productive_args(repo)
    if any(_HEX40.fullmatch(value) is None for value in (source_sha, tree)):
        raise InnerHold("SOURCE_IDENTITY")
    prefix = (python, "-B", "-m", "pytest", "-p", "scripts.rc6_ci_pytest_identity",
              "-q", "-o", "xfail_strict=true")
    return (
        Step("collect_full", (*prefix, "--collect-only", *args),
             str(evidence / "full-collected.json"), 180),
        Step("collect_subset", (*prefix, "--collect-only", *SUBSET_ARGS),
             str(evidence / "subset-collected.json"), 180),
        Step("execute_full", (*prefix, "--junitxml=" + str(evidence / "junit.xml"),
                              *args), str(evidence / "full-executed.json"), 2400),
    )


def _invoke(step: Step, cwd: Path, env: dict[str, str], timeout: int) -> int:
    """Redirect child output entirely into container-private tmpfs, bounded."""
    path = Path(step.manifest).with_suffix(".raw.log")
    with path.open("xb") as private:
        process = subprocess.Popen(step.argv, cwd=cwd, env=env,
                                   stdin=subprocess.DEVNULL, stdout=private,
                                   stderr=private, start_new_session=True)
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=10)
            return HOLD


def run(repo: Path, evidence: Path, source_sha: str, tree: str, *,
        python: str = "python3", invoke=None) -> int:
    """Complete physical test execution plan; outer verifier decides quorum."""
    invoke = invoke or _invoke
    repo, evidence = Path(repo), Path(evidence)
    try:
        steps = plan(source_sha, tree, repo, evidence, python)
        if evidence.exists() or evidence.is_symlink():
            raise InnerHold("OUTPUT_ALREADY_EXISTS")
        evidence.mkdir(mode=0o700, parents=False)
        if evidence.is_symlink() or not evidence.is_dir():
            raise InnerHold("OUTPUT_INVALID")
        for step in steps:
            env = pytest_env(source_sha, tree, step.manifest,
                             evidence_root=evidence)
            rc = invoke(step, repo, env, step.timeout)
            if type(rc) is not int or rc != 0:
                return HOLD
        return 0
    except (OSError, ValueError, TypeError):
        return HOLD


def main() -> int:
    # CLI is a fixed container entry, not an operator terminal command.
    source_sha = os.environ.get("RC6_CANDIDATE_SHA", "")
    source_tree = os.environ.get("RC6_CANDIDATE_TREE_SHA", "")
    code = run(Path("/src"), Path("/work/evidence"), source_sha, source_tree)
    print("RC6_OCI_INNER=" + ("EXECUTED_PRIVATE" if code == 0 else "HOLD") +
          "|qa_certified=false|automatic_go=false")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
