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
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
from threading import Thread
import time
from xml.etree import ElementTree as ET

import yaml

from scripts import rc6_ci_shard_quorum as shards
from scripts import rc6_ci_junit_quorum as junit
from scripts.rc6_ci_pytest_identity import PROPERTY, nodeid_digest
from scripts.rc6_ci_junit_quorum import JunitQuorumRed, verify_parallel


OUTPUT_TAIL_LIMIT = 128 * 1024  # Per stream, per process; never spool unbounded test logs.


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
    # Drain both OS pipes concurrently into bounded in-memory tails. PIPE +
    # communicate() captures unbounded pytest output in RAM, potentially OOM
    # before the subprocess timeout ever fires. Never print raw private logs.
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,
                                   start_new_session=True)
    except OSError:
        _red()
    buffers = [bytearray(), bytearray()]
    read_errors = []

    def drain(stream, tail: bytearray) -> None:
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                tail.extend(chunk)
                if len(tail) > OUTPUT_TAIL_LIMIT:
                    del tail[:-OUTPUT_TAIL_LIMIT]
        except OSError:
            read_errors.append(True)
        finally:
            stream.close()

    threads = [Thread(target=drain, args=(stream, tail), daemon=True)
               for stream, tail in zip((process.stdout, process.stderr), buffers)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    timed_out = False
    try:
        try:
            process.wait(timeout=max(0, deadline - time.monotonic()))
            for thread in threads:
                thread.join(timeout=max(0, deadline - time.monotonic()))
            timed_out = any(thread.is_alive() for thread in threads)
        except subprocess.TimeoutExpired:
            timed_out = True
        if not timed_out:
            if read_errors:
                _red()
            return subprocess.CompletedProcess(
                argv, process.returncode,
                buffers[0].decode("utf-8", errors="replace"),
                buffers[1].decode("utf-8", errors="replace"))

        # Parent exit with descendant still holding pipes counts as timeout,
        # as does a live parent: both need the entire owned process group gone.
        parent_exited_before_timeout = process.poll() is not None
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for thread in threads:
            thread.join(timeout=2)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _red()
        if any(thread.is_alive() for thread in threads):
            _red()  # Even cleanup did not establish a bounded terminal state.
        raise ParallelRunnerRed(diagnostic={
            **_sanitized_timeout_trace(bytes(buffers[1])),
            "parent_exited_before_timeout": parent_exited_before_timeout,
        })
    finally:
        # If an unforeseen reader/wait error escaped, never leave the owned
        # pytest process alive in the CI runner.
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass


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


def _red_diagnostic_junit(manifest: dict, groups: list[list[str]],
                          receipts: list[dict], output_dir: Path,
                          phase_elapsed_ms: dict | None = None) -> dict:
    """Publish only aggregate RED counts and identity hashes, never raw JUnit.

    This intentionally cannot certify a release; it is a diagnosis for a
    previously failing full suite on the GitHub runner, separate from GO gates.
    """
    started_sanitizing = time.monotonic()
    if len(groups) != len(receipts) or not receipts:
        _red()
    clean_suite = ET.Element("testsuite", name="rc6-a820-hashed-diagnostic")
    seen = set()
    failures = errors = skipped = xfail = 0
    timeouts = []
    for receipt in receipts:
        index = receipt.get("shard_index")
        if type(index) is not int or not 0 <= index < len(groups) or index in seen:
            _red()
        seen.add(index)
        if receipt.get("state") == "PYTEST_TIMEOUT":
            # A timed-out subprocess was killed as a process group by _invoke.
            # Never trust a possibly half-written manifest/JUnit from it;
            # retain only its deterministic assigned identity *digest*.
            if (any(k in receipt for k in ("manifest", "junit_file", "pytest_exit_code"))
                    or not isinstance(receipt.get("faulthandler_seen"), bool)
                    or not isinstance(receipt.get("parent_exited_before_timeout"), bool)):
                _red()
            digest_input = json.dumps(
                [nodeid_digest(node) for node in groups[index]],
                separators=(",", ":"), sort_keys=True).encode("ascii")
            timeouts.append({
                "shard_index": index, "assigned_count": len(groups[index]),
                "assigned_nodeids_sha256": hashlib.sha256(digest_input).hexdigest(),
                "reason": "PYTEST_TIMEOUT",
                "faulthandler_seen": receipt["faulthandler_seen"],
                "parent_exited_before_timeout": receipt[
                    "parent_exited_before_timeout"],
            })
            continue
        try:
            actual = shards._validate_manifest(receipt["manifest"])
            root, _ = junit._xml(Path(receipt["junit_file"]))
        except (KeyError, ValueError, TypeError, OSError, shards.ShardQuorumRed,
                JunitQuorumRed):
            _red()
        if (actual["source_sha"] != manifest["source_sha"] or
                actual["source_tree_sha"] != manifest["source_tree_sha"] or
                actual["nodeids"] != groups[index]):
            _red()
        cases = list(root.iter("testcase"))
        if len(cases) != len(groups[index]):
            _red()
        observed = []
        shard_failed = 0
        for case in cases:
            props = [p.get("value") for p in case.findall("./properties/property")
                     if p.get("name") == PROPERTY]
            if len(props) != 1 or not isinstance(props[0], str):
                _red()
            observed.append(props[0])
            tags = [tag for tag in ("failure", "error", "skipped")
                    if case.find(tag) is not None]
            if len(tags) > 1:
                _red()
            clean = ET.SubElement(clean_suite, "testcase", name=props[0],
                                  classname="rc6.offhost.hashed")
            if tags:
                tag = tags[0]
                ET.SubElement(clean, tag, type="SANITIZED_DIAGNOSTIC")
                failures += tag == "failure"
                errors += tag == "error"
                skipped += tag == "skipped"
                # Pytest's explicit xfail type is not an ordinary skip.
                if tag == "skipped" and case.find(tag).get("type") == "pytest.xfail":
                    xfail += 1
                shard_failed += tag in {"failure", "error"}
        if sorted(observed) != sorted(nodeid_digest(node) for node in groups[index]):
            _red()
        rc = receipt.get("pytest_exit_code")
        # Exit 1 must correspond to an evidenced failure/error. Exit 0 may
        # include skip/xfail, but those must never become CI GREEN below.
        if type(rc) is not int or rc not in (0, 1) or (rc == 1) != bool(shard_failed):
            _red()
    executed = len(list(clean_suite))
    missing = sum(entry["assigned_count"] for entry in timeouts)
    if len(seen) != len(groups) or executed + missing != manifest["discovered"]:
        _red()
    # Skipped/xfail remain visible as explicit RED evidence; they can NEVER
    # be counted as executed passing cases or grant a release quorum.
    if failures + errors + skipped == 0 and not timeouts:
        _red()
    clean_suite.set("tests", str(executed))
    clean_suite.set("failures", str(failures))
    clean_suite.set("errors", str(errors))
    clean_suite.set("skipped", str(skipped))
    xml_bytes = ET.tostring(clean_suite, encoding="utf-8", xml_declaration=True)
    phase = phase_elapsed_ms or {"collect": 0, "parallel_test": 0}
    if (set(phase) != {"collect", "parallel_test"} or
            any(type(ms) is not int or ms < 0 for ms in phase.values())):
        _red()
    shard_times = []
    for entry in sorted(receipts, key=lambda r: r["shard_index"]):
        elapsed = entry.get("elapsed_ms", 0)
        if type(elapsed) is not int or elapsed < 0:
            _red()
        shard_times.append(elapsed)
    proof = {"schema_version": 1,
             "status": ("RED_INCOMPLETE_DIAGNOSTIC" if timeouts
                        else "RED_DIAGNOSTIC_OFFLINE_ONLY"),
             "source_sha": manifest["source_sha"],
             "source_tree_sha": manifest["source_tree_sha"],
             "collection_sha256": manifest["collection_sha256"],
             "shards": len(groups), "discovered": manifest["discovered"],
             "executed": executed, "missing": missing,
             "timed_out_shards": len(timeouts),
             "timeouts": sorted(timeouts, key=lambda item: item["shard_index"]),
             "failures": failures,
             "errors": errors, "skipped": skipped, "xfail": xfail,
             "sanitized_junit_sha256": hashlib.sha256(xml_bytes).hexdigest(),
             "phase_elapsed_ms": {**phase,
                 "sanitize": round((time.monotonic() - started_sanitizing) * 1000)},
             "shard_elapsed_ms": shard_times,
             "automatic_go": False, "runtime_authority": False,
             "release_gate_activated": False, "host_written": False}
    output_dir = Path(output_dir)
    if output_dir.exists() or output_dir.is_symlink() or not output_dir.parent.is_dir():
        _red()
    try:
        output_dir.mkdir(mode=0o700)
        for name, data in (("junit-sanitized.xml", xml_bytes),
                           ("receipt.json", json.dumps(proof, sort_keys=True,
                               separators=(",", ":")).encode("utf-8"))):
            target = output_dir / name
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600)
            with os.fdopen(fd, "wb") as out:
                out.write(data)
    except OSError:
        _red()
    return proof


def run(candidate_sha: str, source_tree_sha: str, *, repo: Path,
        shard_count: int, output_junit: Path,
        diagnostics_dir: Path | None = None) -> dict:
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
            started_shard = time.monotonic()
            runner_dir = temp_dir / f"shard-{i}"
            runner_dir.mkdir(mode=0o700)
            executed = runner_dir / "executed.json"
            xml_path = runner_dir / "junit.xml"
            isolated_base = runner_dir / "basetemp"
            env = {**env_base, "RC6_CI_MANIFEST_PATH": str(executed)}
            try:
                result = _invoke(_pytest_argv(python, [*plugin, "-q", "--durations=20",
                                  "-o", "xfail_strict=true", f"--junitxml={xml_path}",
                                  f"--basetemp={isolated_base}", "--", *groups[i]]),
                                 cwd=repo, env=env, timeout=720)
            except ParallelRunnerRed as exc:
                if diagnostics_dir is None or exc.diagnostic is None:
                    raise  # Never turn an unclassified error into evidence.
                return {"shard_index": i, "state": "PYTEST_TIMEOUT",
                        "faulthandler_seen": bool(exc.diagnostic.get("faulthandler_seen")),
                        "parent_exited_before_timeout": bool(exc.diagnostic.get(
                            "parent_exited_before_timeout")),
                        "elapsed_ms": round((time.monotonic() - started_shard) * 1000)}
            if not executed.is_file() or executed.is_symlink():
                _red()
            try:
                actual = json.loads(executed.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                _red()
            receipt = {"shard_index": i, "manifest": actual, "junit_file": xml_path,
                       "pytest_exit_code": result.returncode}
            if diagnostics_dir is not None:
                receipt["elapsed_ms"] = round((time.monotonic() - started_shard) * 1000)
            return receipt

        started_parallel = time.monotonic()
        receipts = []
        with ThreadPoolExecutor(max_workers=shard_count) as executor:
            futures = [executor.submit(run_one, i) for i in range(shard_count)]
            # Consume every result; a failed shard cannot silently disappear.
            for future in as_completed(futures):
                receipts.append(future.result())
        test_ms = round((time.monotonic() - started_parallel) * 1000)
        started_quorum = time.monotonic()
        if diagnostics_dir is not None:
            return _red_diagnostic_junit(
                parsed, groups, receipts, diagnostics_dir,
                phase_elapsed_ms={"collect": collection_ms, "parallel_test": test_ms})
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
    parser.add_argument("--diagnostics-dir", type=Path,
                        help="Sanitized RED-only diagnostics, never a CI gate")
    args = parser.parse_args(argv)
    try:
        proof = run(args.source_sha, args.source_tree_sha, repo=args.repo,
                    shard_count=args.shards, output_junit=args.output_junit,
                    diagnostics_dir=args.diagnostics_dir)
    except ParallelRunnerRed as exc:
        if exc.diagnostic is not None:
            print("RC6_PARALLEL_TIMEOUT_DIAG=" + json.dumps(
                exc.diagnostic, separators=(",", ":"), sort_keys=True), file=sys.stderr)
        print("RC6_PARALLEL_PILOT=RED|automatic_go=NO", file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("RC6_PARALLEL_PILOT=RED|automatic_go=NO", file=sys.stderr)
        return 2
    if args.diagnostics_dir is not None:
        print("RC6_PARALLEL_DIAGNOSTIC=" + json.dumps(
            proof, separators=(",", ":"), sort_keys=True))
        print("RC6_PARALLEL_DIAGNOSTIC=RED|automatic_go=NO", file=sys.stderr)
        return 2
    print("RC6_PARALLEL_PILOT=" + json.dumps(proof, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
