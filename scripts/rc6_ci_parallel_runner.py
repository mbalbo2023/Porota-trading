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
import re
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import threading
import time
from xml.etree import ElementTree as ET

import yaml

from rc6_audit_evidence import sqlite_scratch as scratch
from scripts import rc6_ci_shard_quorum as shards
from scripts.rc6_ci_junit_quorum import JunitQuorumRed, verify_parallel


class ParallelRunnerRed(ValueError):
    """Missing, inconsistent or unsuccessful offline pytest evidence."""

    REASONS = frozenset({
        "UNVERIFIED", "SOURCE_CHECKOUT_DIRTY", "SOURCE_REVISION_MISMATCH",
        "TEST_POLICY_INVALID", "SUBPROCESS_INVALID", "COLLECTION_INVALID",
        "COLLECTION_PYTEST_NONZERO", "COLLECTION_PLUGIN_IDENTITY_RED",
        "COLLECTION_NODEID_COLLECTION_INVALID", "COLLECTION_NODEID_EMPTY_OR_UNBOUNDED",
        "COLLECTION_NODEID_NOT_STRING", "COLLECTION_NODEID_EMPTY",
        "COLLECTION_NODEID_CHAR_LENGTH", "COLLECTION_NODEID_UTF8_ENCODING",
        "COLLECTION_NODEID_UTF8_BYTES", "COLLECTION_NODEID_NONPRINTABLE",
        "COLLECTION_NODEID_CONTROL_OR_BIDI", "COLLECTION_NODEID_DUPLICATE",
        "COLLECTION_NODEID_TOTAL_BYTES_UNBOUNDED",
        "COLLECTION_IMPORT_ERROR", "COLLECTION_PYTEST_COLLECT_ERROR",
        "COLLECTION_MANIFEST_MISSING", "COLLECTION_MANIFEST_ALIAS",
        "COLLECTION_MANIFEST_INVALID", "COLLECTION_SOURCE_MISMATCH",
        "COLLECTION_PARTITION_INVALID",
        "RETIRED_TEST_NOT_COLLECTED", "SHARD_REPORT_INVALID",
        "SHARD_PYTEST_NONZERO", "SHARD_PYTEST_INTERNAL_ERROR",
        "SHARD_PROCESS_SIGNAL",
        "JUNIT_QUORUM_INVALID",
        "SCRATCH_ROOT_INVALID",
    })

    def __init__(self, reason="UNVERIFIED", *, failure_module=None,
                 last_active_module=None, process_exit_code=None,
                 internal_error=None, failed_test_base=None):
        self.reason = reason if reason in self.REASONS else "UNVERIFIED"
        # No pytest nodeids/parameters, output, paths outside tracked tests.
        def safe(module):
            return (module if type(module) is str
                    and re.fullmatch(r"tests/[A-Za-z0-9_./-]{1,220}\.py",
                                     module)
                    and ".." not in Path(module).parts else None)
        self.failure_module = safe(failure_module)
        self.last_active_module = safe(last_active_module)
        self.process_exit_code = (
            process_exit_code if type(process_exit_code) is int
            and -255 <= process_exit_code <= 255 else None)
        self.internal_error = internal_error if isinstance(internal_error, dict) else None
        self.failed_test_base = (
            failed_test_base if type(failed_test_base) is str
            and re.fullmatch(r"tests/[A-Za-z0-9_./-]{1,220}\.py::test_[A-Za-z0-9_]{1,120}",
                             failed_test_base)
            and ".." not in Path(failed_test_base.split("::", 1)[0]).parts
            else None)
        super().__init__("RC6_PARALLEL_RUNNER_RED")


def _red(reason="UNVERIFIED", *, failure_module=None,
         last_active_module=None, process_exit_code=None,
         internal_error=None, failed_test_base=None) -> None:
    raise ParallelRunnerRed(
        reason, failure_module=failure_module,
        last_active_module=last_active_module,
        process_exit_code=process_exit_code,
        internal_error=internal_error,
        failed_test_base=failed_test_base)


def _safe_pytest_module(repo: Path, source: str) -> str | None:
    """Only a real tests/*.py file from the pinned checkout can be disclosed."""
    if (type(source) is not str
            or not re.fullmatch(r"tests/[A-Za-z0-9_./-]{1,220}\.py", source)):
        return None
    parts = Path(source).parts
    if (".." in parts or any(
            (repo / Path(*parts[:i])).is_symlink()
            for i in range(1, len(parts) + 1))):
        return None
    return source if (repo / source).is_file() else None



def _verified_internalerror_sidecar(path: Path, repo: Path) -> dict | None:
    """Only typed, bounded exception evidence from a private 0600 file."""
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or not 1 <= info.st_size <= 1024):
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if (type(record) is not dict or
                record.get("schema") != "rc6.pytest.internalerror.v1" or
                set(record) - {"schema", "exception_type", "quorum_reason",
                               "source_file", "source_line"}):
            return None
        kind = record.get("exception_type")
        if (type(kind) is not str or
                not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", kind)):
            return None
        result = {"exception_type": kind}
        subtype = record.get("quorum_reason")
        if subtype is not None:
            if (kind != "ShardQuorumRed" or
                    type(subtype) is not str or
                    subtype not in shards._NODEID_REASON_CODES):
                return None
            result["quorum_reason"] = subtype
        source = record.get("source_file")
        line = record.get("source_line")
        if source is None and line is None:
            return result
        if (type(source) is not str or
                not re.fullmatch(r"[A-Za-z0-9_./-]{1,220}\.py", source) or
                ".." in Path(source).parts or
                type(line) is not int or not 1 <= line <= 10000000):
            return result
        parts = Path(source).parts
        if (repo / source).is_file() and not any(
                (repo / Path(*parts[:i])).is_symlink()
                for i in range(1, len(parts) + 1)):
            result["source_file"] = source
            result["source_line"] = line
        return result
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def _failed_test_base_from_junit(junit_path: Path, repo: Path) -> str | None:
    """Return only a tracked static test function name, NEVER parameter IDs."""
    try:
        info = junit_path.lstat()
        if (not stat.S_ISREG(info.st_mode)
                or not 0 < info.st_size <= 64 * 1024 * 1024):
            return None
        for _, case in ET.iterparse(junit_path, events=("end",)):
            if case.tag != "testcase":
                continue
            if case.find("failure") is None and case.find("error") is None:
                case.clear()
                continue
            test_name = str(case.attrib.get("name", "")).split("[", 1)[0]
            if not re.fullmatch(r"test_[A-Za-z0-9_]{1,120}", test_name):
                case.clear()
                continue
            sources = [case.attrib.get("file", "")]
            classname = case.attrib.get("classname", "")
            if (type(classname) is str and len(classname) <= 260
                    and re.fullmatch(r"tests(?:\.[A-Za-z0-9_]+){1,24}",
                                     classname)):
                parts = classname.split(".")
                for count in range(len(parts), 1, -1):
                    sources.append("/".join(parts[:count]) + ".py")
            for source in sources:
                safe = _safe_pytest_module(repo, source)
                if safe is not None:
                    return safe + "::" + test_name
            case.clear()
    except (OSError, ValueError, TypeError, ET.ParseError):
        return None
    return None

def _failed_module_from_junit(junit_path: Path, repo: Path) -> str | None:
    """Read bounded JUnit locally, emit only safe source path, no test names."""
    try:
        metadata = junit_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= 64 * 1024 * 1024:
            return None
        for _, case in ET.iterparse(junit_path, events=("end",)):
            if case.tag != "testcase":
                continue
            if case.find("failure") is None and case.find("error") is None:
                case.clear()
                continue
            sources = [case.attrib.get("file", "")]
            classname = case.attrib.get("classname", "")
            if (len(classname) <= 260 and re.fullmatch(
                    r"tests(?:\.[A-Za-z0-9_]+){1,24}", classname)):
                parts = classname.split(".")
                for count in range(len(parts), 1, -1):
                    sources.append("/".join(parts[:count]) + ".py")
            for source in sources:
                safe = _safe_pytest_module(repo, source)
                if safe is not None:
                    return safe
            case.clear()
    except (OSError, ValueError, TypeError, ET.ParseError):
        return None
    return None


def _failed_module_from_pytest_summary(result, repo: Path) -> str | None:
    """Parse only pytest's short failure summary, never stdout/parameters."""
    tail = str(result.stdout or "")[-262144:]
    cut = tail.rfind("short test summary info")
    if cut < 0:
        return None
    for line in tail[cut:].splitlines():
        match = re.match(
            r"^(?:FAILED|ERROR)\s+(tests/[A-Za-z0-9_./-]{1,220}\.py)(?:::|\s|$)",
            line)
        if match:
            safe = _safe_pytest_module(repo, match.group(1))
            if safe is not None:
                return safe
    return None


def _last_active_module_from_output(result, repo: Path) -> str | None:
    """Recover the final opt-in source-only heartbeat after a SIGKILL."""
    tail = str(result.stdout or "")[-262144:]
    prefix = "RC6_CI_ACTIVE_MODULE:"
    for line in reversed(tail.splitlines()):
        # pytest -q may write a progress glyph immediately before the hook's
        # module marker, e.g. '.RC6_CI_ACTIVE_MODULE:tests/test_a.py'.
        offset = line.rfind(prefix)
        if offset < 0:
            continue
        preceding = line[:offset]
        if preceding and (len(preceding) > 64
                          or not re.fullmatch(r"[.FsxXE]{1,64}", preceding)):
            continue
        safe = _safe_pytest_module(repo, line[offset + len(prefix):])
        if safe is not None:
            return safe
    return None


def _invoke(argv: list[str], *, cwd: Path, env: dict,
            timeout: int = 3600) -> subprocess.CompletedProcess:
    # Captured stdout/stderr are never printed or returned: private test
    # failures may accidentally contain identifiers or host-sensitive values.
    try:
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        _red("SUBPROCESS_INVALID")


def _collection_failure_reason(result) -> str:
    """Classify only fixed error categories, never export raw pytest output."""
    if result.returncode == 0:
        return "UNVERIFIED"
    # Captured by existing runner; no log content is copied to public receipt.
    detail = (str(result.stderr or "") + str(result.stdout or ""))[:131072]
    for code in (
            "NODEID_COLLECTION_INVALID", "NODEID_EMPTY_OR_UNBOUNDED",
            "NODEID_NOT_STRING", "NODEID_EMPTY", "NODEID_CHAR_LENGTH",
            "NODEID_UTF8_ENCODING", "NODEID_UTF8_BYTES", "NODEID_NONPRINTABLE",
            "NODEID_CONTROL_OR_BIDI", "NODEID_DUPLICATE",
            "NODEID_TOTAL_BYTES_UNBOUNDED"):
        if "RC6_SHARD_QUORUM_RED:" + code in detail:
            return "COLLECTION_" + code
    if "RC6_SHARD_QUORUM_RED" in detail:
        return "COLLECTION_PLUGIN_IDENTITY_RED"
    if "ModuleNotFoundError" in detail or "ImportError" in detail:
        return "COLLECTION_IMPORT_ERROR"
    if "ERROR collecting" in detail:
        return "COLLECTION_PYTEST_COLLECT_ERROR"
    return "COLLECTION_PYTEST_NONZERO"


def _allow_image_generated_native_status(repo: Path, status: str) -> bool:
    """Permit ONLY the locally built native ELF absent from the source tree.

    Git-committed application, tests and policy must remain byte-clean.
    The Dockerfile builds this one extension from a tracked C source and
    copies it to the running image. No other untracked path is allowed.
    """
    if status == "":
        return True
    if status.strip() != "?? rc6_shadow_runtime/_rc6_json_bound.so":
        return False
    native = repo / "rc6_shadow_runtime" / "_rc6_json_bound.so"
    try:
        parent = native.parent
        if parent.is_symlink() or native.is_symlink():
            return False
        metadata = native.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
                or metadata.st_uid != os.geteuid()
                or metadata.st_mode & 0o022
                or not 16 <= metadata.st_size <= 5 * 1024 * 1024):
            return False
        fd = os.open(native, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            pinned = os.fstat(fd)
            if ((pinned.st_dev, pinned.st_ino, pinned.st_size, pinned.st_mode)
                    != (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mode)):
                return False
            return os.read(fd, 4) == b"\x7fELF"
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError):
        return False


def _verify_checkout(repo: Path, candidate_sha: str, source_tree_sha: str) -> None:
    try:
        shards._sha(candidate_sha)
        shards._sha(source_tree_sha)
        for command, expected in ((["git", "rev-parse", "HEAD"], candidate_sha),
                                  (["git", "rev-parse", "HEAD^{tree}"], source_tree_sha)):
            response = _invoke(command, cwd=repo, env=os.environ.copy(), timeout=30)
            if response.returncode != 0 or response.stdout.strip() != expected:
                _red("SOURCE_REVISION_MISMATCH")
        response = _invoke(["git", "diff", "--quiet", "HEAD", "--"],
                           cwd=repo, env=os.environ.copy(), timeout=30)
        if response.returncode != 0:
            _red("SOURCE_CHECKOUT_DIRTY")
        response = _invoke(["git", "status", "--porcelain=v1",
                            "--untracked-files=normal"],
                           cwd=repo, env=os.environ.copy(), timeout=30)
        if response.returncode != 0 or not _allow_image_generated_native_status(
                repo, response.stdout):
            _red("SOURCE_CHECKOUT_DIRTY")  # All other untracked/modified files deny.
    except shards.ShardQuorumRed:
        _red("SOURCE_REVISION_MISMATCH")


def _test_args(repo: Path) -> list[str]:
    try:
        policy = yaml.safe_load((repo / "ops/policy/test-policy.yaml").read_text(encoding="utf-8"))
        governance = policy["test_governance"]
        scope = policy["productive_scope"]
        if (governance["automatic_discovery_required"] is not True or
                governance["manual_test_file_allowlist_as_primary_ci"] is not False or
                scope["roots"] != ["."] or scope["runner"] != "pytest"):
            _red("TEST_POLICY_INVALID")
        args = ["."]
        for entry in scope["exclusions"]:
            path, successor = Path(entry["path"]), Path(entry["successor"])
            for candidate in (path, successor):
                if (candidate.is_absolute() or any(part in ("", ".", "..")
                        for part in candidate.parts) or
                        not (repo / candidate).is_file() or
                        any((repo / Path(*candidate.parts[:i])).is_symlink()
                            for i in range(1, len(candidate.parts) + 1))):
                    _red("TEST_POLICY_INVALID")
            if not all(entry.get(key) for key in ("classification", "reason", "owner")):
                _red("TEST_POLICY_INVALID")
            args.append(f"--ignore={path.as_posix()}")
        return args
    except (OSError, KeyError, TypeError, AttributeError, yaml.YAMLError):
        _red("TEST_POLICY_INVALID")


def _disk_backed_temp_parent(repo: Path) -> Path:
    """Put pytest basetemp outside the checkout, never on an unverified tmpfs."""
    runner_temp = os.environ.get("RUNNER_TEMP", "")
    if not runner_temp and os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        _red("SCRATCH_ROOT_INVALID")
    parent = Path(runner_temp) if runner_temp else repo.parent
    if (not parent.is_absolute() or parent == Path("/") or parent == repo
            or repo in parent.parents):
        _red("SCRATCH_ROOT_INVALID")
    try:
        if (parent.resolve(strict=True) != parent or not parent.is_dir()
                or not os.access(parent, os.W_OK | os.X_OK)):
            _red("SCRATCH_ROOT_INVALID")
        # Reuse the SQLite capture's mount policy; do not weaken its guard.
        scratch._storage_type(parent)
    except (OSError, ValueError, RuntimeError):
        _red("SCRATCH_ROOT_INVALID")
    return parent


def run(candidate_sha: str, source_tree_sha: str, *, repo: Path,
        shard_count: int, output_junit: Path,
        max_workers: int | None = None,
        retired_nodeids: list[str] | tuple[str, ...] = (),
        progress=None) -> dict:
    """Discover globally, execute exact IDs concurrently, attest actual XML."""
    repo = Path(repo).resolve(strict=True)
    output_junit = Path(output_junit)
    if max_workers is None:
        max_workers = shard_count
    if (type(max_workers) is not int or type(shard_count) is not int
            or not 1 <= max_workers <= shard_count <= 16):
        _red()
    if not isinstance(retired_nodeids, (tuple, list)):
        _red()
    retired = list(retired_nodeids)
    if len(set(retired)) != len(retired) or any(
            not isinstance(item, str) or "[" in item
            for item in retired):
        _red()
    if progress is not None and not callable(progress):
        _red("SUBPROCESS_INVALID")

    def notify(stage: str, index: int = -1, completed: int = 0) -> None:
        # Fixed public phase + bounded counters only; never test IDs or logs.
        if progress is not None:
            progress(stage, index, completed)

    notify("SOURCE_CHECKOUT")
    _verify_checkout(repo, candidate_sha, source_tree_sha)
    args = _test_args(repo)
    env_base = os.environ.copy()
    env_base.pop("PYTEST_ADDOPTS", None)
    env_base.pop("PYTEST_PLUGINS", None)
    env_base.pop("RC6_CI_MODULE_TRACE", None)
    env_base["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env_base["RC6_CI_IDENTITY_PROOF"] = "1"
    env_base["RC6_CI_SOURCE_SHA"] = candidate_sha
    env_base["RC6_CI_SOURCE_TREE_SHA"] = source_tree_sha
    python = sys.executable
    plugin = ["-p", "scripts.rc6_ci_pytest_identity",
              "-p", "no:cacheprovider"]
    start = time.monotonic()
    disk_parent = _disk_backed_temp_parent(repo)
    with tempfile.TemporaryDirectory(prefix="rc6-ci-parallel-", dir=disk_parent) as temp:
        temp_dir = Path(temp)
        notify("COLLECTION")
        collected = temp_dir / "collected.json"
        collect_env = {**env_base, "RC6_CI_MANIFEST_PATH": str(collected)}
        collect = _invoke([python, "-m", "pytest", *plugin,
                           "--collect-only", "-q", *args],
                          cwd=repo, env=collect_env)
        if collect.returncode != 0:
            _red(_collection_failure_reason(collect))
        if collected.is_symlink():
            _red("COLLECTION_MANIFEST_ALIAS")
        if not collected.is_file():
            _red("COLLECTION_MANIFEST_MISSING")
        try:
            manifest = json.loads(collected.read_text(encoding="utf-8"))
            parsed = shards._validate_manifest(manifest)
        except (ValueError, OSError, TypeError, shards.ShardQuorumRed):
            _red("COLLECTION_MANIFEST_INVALID")
        if (parsed["source_sha"] != candidate_sha or
                parsed["source_tree_sha"] != source_tree_sha):
            _red("COLLECTION_SOURCE_MISMATCH")
        try:
            discovered_total = parsed["discovered"]
            # Verify every explicitly retired test exists in global
            # automatic discovery before excluding it from execution.
            # The retirement registry stores base nodeids, whereas pytest
            # expands parametrized cases into base[case]. Preserve the
            # historical exact-base retirement semantics without dropping
            # neighboring tests or accepting a missing retired test.
            bases = {item.split("[", 1)[0] for item in parsed["nodeids"]}
            retired_bases = set(retired)
            if retired_bases - bases:
                _red("RETIRED_TEST_NOT_COLLECTED")
            selected = [item for item in parsed["nodeids"]
                        if item.split("[", 1)[0] not in retired_bases]
            retired_testcases = discovered_total - len(selected)
            if not selected or retired_testcases < len(retired):
                _red("COLLECTION_INVALID")
            if retired:
                parsed = shards.manifest(candidate_sha, source_tree_sha, selected)
            groups = shards.partition(parsed, shard_count)
        except ParallelRunnerRed:
            raise
        except (ValueError, OSError, TypeError, shards.ShardQuorumRed):
            _red("COLLECTION_PARTITION_INVALID")
        collection_ms = round((time.monotonic() - start) * 1000)
        notify("COLLECTION_COMPLETE")
        progress_lock = threading.Lock()
        finished_shards = 0

        def run_one(i: int) -> dict:
            nonlocal finished_shards
            with progress_lock:
                notify("SHARD_RUNNING", i, finished_shards)
            runner_dir = temp_dir / f"shard-{i}"
            runner_dir.mkdir(mode=0o700)
            executed = runner_dir / "executed.json"
            xml_path = runner_dir / "junit.xml"
            isolated_base = runner_dir / "basetemp"
            internal_error_file = runner_dir / "internalerror.json"
            env = {**env_base, "RC6_CI_MANIFEST_PATH": str(executed),
                   "RC6_CI_MODULE_TRACE": "1",
                   "RC6_CI_INTERNAL_ERROR_PATH": str(internal_error_file)}
            result = _invoke([python, "-m", "pytest", *plugin, "-q",
                              "-o", "xfail_strict=true",
                              f"--junitxml={xml_path}", f"--basetemp={isolated_base}",
                              "--", *groups[i]], cwd=repo, env=env)
            # Inspect the actual process outcome FIRST. A missing collection
            # receipt after SIGKILL or pytest failure must not hide the signal
            # or nonzero exit behind SHARD_REPORT_INVALID.
            if result.returncode != 0:
                failure_module = (
                    _failed_module_from_junit(xml_path, repo)
                    or _failed_module_from_pytest_summary(result, repo))
                reason = ("SHARD_PROCESS_SIGNAL" if type(result.returncode) is int
                          and result.returncode < 0 else
                          "SHARD_PYTEST_INTERNAL_ERROR" if result.returncode == 3
                          else "SHARD_PYTEST_NONZERO")
                detail = (_verified_internalerror_sidecar(internal_error_file, repo)
                          if result.returncode == 3 else None)
                _red(reason, failure_module=failure_module,
                     last_active_module=_last_active_module_from_output(result, repo),
                     process_exit_code=result.returncode, internal_error=detail,
                     failed_test_base=_failed_test_base_from_junit(xml_path, repo))
            if not executed.is_file() or executed.is_symlink():
                _red("SHARD_REPORT_INVALID",
                     last_active_module=_last_active_module_from_output(result, repo),
                     process_exit_code=result.returncode)
            try:
                actual = json.loads(executed.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                _red("SHARD_REPORT_INVALID",
                     last_active_module=_last_active_module_from_output(result, repo),
                     process_exit_code=result.returncode)
            with progress_lock:
                finished_shards += 1
                notify("SHARD_COMPLETE", i, finished_shards)
            return {"shard_index": i, "manifest": actual, "junit_file": xml_path,
                    "pytest_exit_code": result.returncode}

        started_parallel = time.monotonic()
        receipts = []
        if max_workers == 1:
            # A serial CI shard must complete successfully before the next
            # is launched. Submitting all futures first can mask the initial
            # error behind ThreadPoolExecutor.shutdown(wait=True) and the
            # outer 2500-second deadline. Keep identical global JUnit quorum.
            for i in range(shard_count):
                receipts.append(run_one(i))
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(run_one, i) for i in range(shard_count)]
                # All concurrent results must enter the exact JUnit quorum.
                for future in as_completed(futures):
                    receipts.append(future.result())
        test_ms = round((time.monotonic() - started_parallel) * 1000)
        started_quorum = time.monotonic()
        notify("JUNIT_QUORUM", -1, finished_shards)
        try:
            receipt = verify_parallel(parsed, receipts, shard_count, output_junit)
        except (ValueError, OSError, JunitQuorumRed, shards.ShardQuorumRed):
            _red("JUNIT_QUORUM_INVALID")
        receipt["phase_elapsed_ms"] = {"collect": collection_ms,
                                       "parallel_test": test_ms,
                                       "quorum": round((time.monotonic() - started_quorum) * 1000)}
        receipt["ci_activated"] = False  # The pilot alone cannot certify release.
        receipt["globally_discovered"] = discovered_total
        receipt["retired"] = sorted(retired)
        receipt["retired_testcases"] = retired_testcases
        receipt["execution_workers"] = max_workers
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
    except (ParallelRunnerRed, OSError, ValueError):
        print("RC6_PARALLEL_PILOT=RED|automatic_go=NO", file=sys.stderr)
        return 2
    print("RC6_PARALLEL_PILOT=" + json.dumps(proof, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
