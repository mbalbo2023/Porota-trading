"""Fail-closed, opt-in offline proof runner for future lossless pytest shards.

Enabled only as a non-authoritative Predeploy V2 pilot for the exact selected
candidate or explicit repository opt-in. It never contacts GitHub, Docker, a
host, market or deploy plane; it runs only local pytest tests. The existing
single-suite job remains authoritative, even if pilot quorum is GREEN.
No time-of-day, weekday or market calendar restriction applies.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

import yaml

from scripts import rc6_ci_shard_quorum as shards
from scripts.rc6_ci_junit_quorum import JunitQuorumRed, verify_parallel


class ParallelRunnerRed(ValueError):
    """Missing, inconsistent or unsuccessful offline pytest evidence."""

    def __init__(self, message: str = "RC6_PARALLEL_RUNNER_RED",
                 diagnostic: dict | None = None):
        super().__init__(message)
        self.diagnostic = diagnostic


def _red() -> None:
    raise ParallelRunnerRed("RC6_PARALLEL_RUNNER_RED")


def _sanitized_timeout_trace(raw: str | bytes | None) -> dict:
    """Retain bounded source frame labels, never runner paths or test output."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        raw = ""
    frames = []
    for match in re.finditer(r'File "([^"\n]+)", line ([0-9]{1,7}) in ([A-Za-z_<>][A-Za-z0-9_<>]{0,79})',
                             raw):
        path = match.group(1).replace("\\", "/")
        filename = path.rsplit("/", 1)[-1]
        if (not any(segment in path for segment in ("/tests/", "/scripts/")) or
                re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,79}[.]py", filename) is None):
            filename = "external.py"
        frames.append({"file": filename, "line": int(match.group(2)),
                       "function": match.group(3)})
        if len(frames) >= 16:
            break
    return {"faulthandler_seen": "Timeout (" in raw, "frames": frames}


def _pytest_argv(python: str, args: list[str], *,
                 trace_after: int | float = 420) -> list[str]:
    """Run pytest via its normal main() with a bounded stack-only timer."""
    if (not isinstance(trace_after, (int, float)) or
            not 0 < trace_after <= 3600):
        _red()
    bootstrap = (
        "import faulthandler,sys;"
        f"faulthandler.dump_traceback_later({trace_after!r},repeat=False,file=sys.stderr);"
        "import pytest;"
        "raise SystemExit(pytest.main(sys.argv[1:]))"
    )
    return [python, "-c", bootstrap, *args]


def _invoke(argv: list[str], *, cwd: Path, env: dict,
            timeout: int = 3600) -> subprocess.CompletedProcess:
    # Captured stdout/stderr are never printed or returned: private test
    # failures may accidentally contain identifiers or host-sensitive values.
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
    except OSError:
        _red()
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired as expired:
        # TimeoutExpired may contain bytes even when Popen(text=True), and
        # the resumed communicate() result already includes earlier output.
        raw_stderr = expired.stderr
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            _, remaining = process.communicate(timeout=1)
            if remaining is not None:
                raw_stderr = remaining
        except subprocess.TimeoutExpired as later:
            if later.stderr is not None:
                raw_stderr = later.stderr
        finally:
            # A descendant may have closed both pipes or ignored TERM even
            # after its parent exits. Always terminate the owned process group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                _, remaining = process.communicate(timeout=2)
                if remaining is not None:
                    raw_stderr = remaining
            except subprocess.TimeoutExpired:
                _red()
        raise ParallelRunnerRed(diagnostic=_sanitized_timeout_trace(raw_stderr))


def _verify_checkout(repo: Path, candidate_sha: str, source_tree_sha: str) -> None:
    try:
        shards._sha(candidate_sha)
        shards._sha(source_tree_sha)
        for command, expected in ((["git", "rev-parse", "HEAD"], candidate_sha),
                                  (["git", "rev-parse", "HEAD^{tree}"], source_tree_sha)):
            response = _invoke(command, cwd=repo, env=os.environ.copy(), timeout=30)
            if response.returncode != 0 or response.stdout.strip() != expected:
                _red()
        response = _invoke(["git", "diff", "--quiet", "HEAD", "--"],
                           cwd=repo, env=os.environ.copy(), timeout=30)
        if response.returncode != 0:
            _red()
        response = _invoke(["git", "status", "--porcelain=v1",
                            "--untracked-files=normal"],
                           cwd=repo, env=os.environ.copy(), timeout=30)
        if response.returncode != 0 or response.stdout.strip():
            _red()  # Untracked source/tests are not represented by SHA/tree.
    except shards.ShardQuorumRed:
        _red()


def _test_args(repo: Path) -> list[str]:
    try:
        policy = yaml.safe_load((repo / "ops/policy/test-policy.yaml").read_text(encoding="utf-8"))
        governance = policy["test_governance"]
        scope = policy["productive_scope"]
        if (governance["automatic_discovery_required"] is not True or
                governance["manual_test_file_allowlist_as_primary_ci"] is not False or
                scope["roots"] != ["."] or scope["runner"] != "pytest"):
            _red()
        args = ["."]
        for entry in scope["exclusions"]:
            path, successor = Path(entry["path"]), Path(entry["successor"])
            for candidate in (path, successor):
                if (candidate.is_absolute() or any(part in ("", ".", "..")
                        for part in candidate.parts) or
                        not (repo / candidate).is_file() or
                        any((repo / Path(*candidate.parts[:i])).is_symlink()
                            for i in range(1, len(candidate.parts) + 1))):
                    _red()
            if not all(entry.get(key) for key in ("classification", "reason", "owner")):
                _red()
            args.append(f"--ignore={path.as_posix()}")
        return args
    except (OSError, KeyError, TypeError, AttributeError, yaml.YAMLError):
        _red()


def run(candidate_sha: str, source_tree_sha: str, *, repo: Path,
        shard_count: int, output_junit: Path) -> dict:
    """Discover globally, execute exact IDs concurrently, attest actual XML."""
    repo = Path(repo).resolve(strict=True)
    output_junit = Path(output_junit)
    _verify_checkout(repo, candidate_sha, source_tree_sha)
    args = _test_args(repo)
    env_base = os.environ.copy()
    env_base.pop("PYTEST_ADDOPTS", None)
    env_base.pop("PYTEST_PLUGINS", None)
    env_base["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env_base["RC6_CI_IDENTITY_PROOF"] = "1"
    env_base["RC6_CI_SOURCE_SHA"] = candidate_sha
    env_base["RC6_CI_SOURCE_TREE_SHA"] = source_tree_sha
    python = sys.executable
    plugin = ["-p", "scripts.rc6_ci_pytest_identity"]
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="rc6-ci-parallel-") as temp:
        temp_dir = Path(temp)
        collected = temp_dir / "collected.json"
        collect_env = {**env_base, "RC6_CI_MANIFEST_PATH": str(collected)}
        collect = _invoke(_pytest_argv(python, [*plugin, "--collect-only", "-q", *args]),
                          cwd=repo, env=collect_env, timeout=180)
        if collect.returncode != 0 or not collected.is_file() or collected.is_symlink():
            _red()
        try:
            manifest = json.loads(collected.read_text(encoding="utf-8"))
            parsed = shards._validate_manifest(manifest)
            if (parsed["source_sha"] != candidate_sha or
                    parsed["source_tree_sha"] != source_tree_sha):
                _red()
            groups = shards.partition(parsed, shard_count)
        except (ValueError, OSError, TypeError, shards.ShardQuorumRed):
            _red()
        collection_ms = round((time.monotonic() - start) * 1000)

        def run_one(i: int) -> dict:
            runner_dir = temp_dir / f"shard-{i}"
            runner_dir.mkdir(mode=0o700)
            executed = runner_dir / "executed.json"
            xml_path = runner_dir / "junit.xml"
            isolated_base = runner_dir / "basetemp"
            env = {**env_base, "RC6_CI_MANIFEST_PATH": str(executed)}
            result = _invoke(_pytest_argv(python, [*plugin, "-q", "--durations=20",
                              "-o", "xfail_strict=true", f"--junitxml={xml_path}",
                              f"--basetemp={isolated_base}", "--", *groups[i]]),
                             cwd=repo, env=env, timeout=720)
            if not executed.is_file() or executed.is_symlink():
                _red()
            try:
                actual = json.loads(executed.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                _red()
            return {"shard_index": i, "manifest": actual, "junit_file": xml_path,
                    "pytest_exit_code": result.returncode}

        started_parallel = time.monotonic()
        receipts = []
        with ThreadPoolExecutor(max_workers=shard_count) as executor:
            futures = [executor.submit(run_one, i) for i in range(shard_count)]
            # Consume every result; a failed shard cannot silently disappear.
            for future in as_completed(futures):
                receipts.append(future.result())
        test_ms = round((time.monotonic() - started_parallel) * 1000)
        started_quorum = time.monotonic()
        try:
            receipt = verify_parallel(parsed, receipts, shard_count, output_junit)
        except (ValueError, OSError, JunitQuorumRed, shards.ShardQuorumRed):
            _red()
        receipt["phase_elapsed_ms"] = {"collect": collection_ms,
                                       "parallel_test": test_ms,
                                       "quorum": round((time.monotonic() - started_quorum) * 1000)}
        receipt["ci_activated"] = False  # Pilot cannot certify any existing workflow.
        return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree-sha", required=True)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--shards", required=True, type=int)
    parser.add_argument("--output-junit", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        proof = run(args.source_sha, args.source_tree_sha, repo=args.repo,
                    shard_count=args.shards, output_junit=args.output_junit)
    except ParallelRunnerRed as exc:
        if exc.diagnostic is not None:
            print("RC6_PARALLEL_TIMEOUT_DIAG=" + json.dumps(
                exc.diagnostic, separators=(",", ":"), sort_keys=True), file=sys.stderr)
        print("RC6_PARALLEL_PILOT=RED|automatic_go=NO", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("RC6_PARALLEL_PILOT=RED|automatic_go=NO", file=sys.stderr)
        return 2
    print("RC6_PARALLEL_PILOT=" + json.dumps(proof, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
