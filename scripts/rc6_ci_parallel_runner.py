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
import stat
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
MAX_MANIFEST_BYTES = 64 * 1024 * 1024


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
                 trace_after: int | float = 420, progress_owner: bool = False) -> list[str]:
    """Run pytest via its normal main() with a bounded stack-only timer."""
    if (not isinstance(trace_after, (int, float)) or
            not 0 < trace_after <= 3600):
        _red()
    bootstrap = (
        "import faulthandler,sys,os;"
        + ("os.environ['RC6_CI_PROGRESS_PID']=str(os.getpid());" if progress_owner else "") +
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
        # Ambient GIT_DIR/WORK_TREE/INDEX/CONFIG must never attest another tree.
        with tempfile.TemporaryDirectory(prefix="rc6-git-home-") as home:
            env = {"PATH": os.defpath, "HOME": home, "XDG_CONFIG_HOME": home,
                   "LANG": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
            for command, expected in ((["git", "rev-parse", "HEAD"], candidate_sha),
                                      (["git", "rev-parse", "HEAD^{tree}"], source_tree_sha)):
                response = _invoke(command, cwd=repo, env=env, timeout=30)
                if response.returncode != 0 or response.stdout.strip() != expected:
                    _red()
            response = _invoke(["git", "diff", "--quiet", "HEAD", "--"],
                               cwd=repo, env=env, timeout=30)
            if response.returncode != 0:
                _red()
            response = _invoke(["git", "status", "--porcelain=v1",
                                "--untracked-files=normal"],
                               cwd=repo, env=env, timeout=30)
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


def _read_regular(path: Path, limit: int) -> bytes:
    """Bounded, nonblocking, single-FD evidence read; reject links and swaps."""
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                not 0 <= before.st_size <= limit):
            _red()
        raw = os.read(fd, limit + 1)
        after = os.fstat(fd)
        current = os.stat(path, follow_symlinks=False)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if (len(raw) != before.st_size or after.st_nlink != 1 or
                not stat.S_ISREG(current.st_mode) or
                any(getattr(before, k) != getattr(after, k) or
                    getattr(after, k) != getattr(current, k) for k in fields)):
            _red()
        return raw
    except OSError:
        _red()
    finally:
        if fd is not None:
            os.close(fd)


def _red_diagnostic_junit(manifest: dict, groups: list[list[str]],
                          receipts: list[dict], output_dir: Path,
                          phase_elapsed_ms: dict | None = None, *,
                          allow_clean_diagnostic: bool = False) -> dict:
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
            root = ET.fromstring(_read_regular(Path(receipt["junit_file"]), junit.MAX_XML_BYTES))
            if root.tag not in {"testsuite", "testsuites"}:
                _red()
        except (KeyError, ValueError, TypeError, OSError, shards.ShardQuorumRed,
                JunitQuorumRed, ET.ParseError):
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
    if failures + errors + skipped == 0 and not timeouts and not allow_clean_diagnostic:
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


def bounded_segments(manifest: dict, shard_count: int, shard_index: int,
                     split_path: str = "") -> list[list[str]]:
    """Bisect sorted exact shard IDs, then retain module-local segments.

    Binary path 0 selects the left half, 1 the right. No automatic recursion:
    another invocation must explicitly select a narrower interval or offset.
    """
    try:
        groups = shards.partition(manifest, shard_count)
        if (type(shard_index) is not int or not 0 <= shard_index < shard_count or
                not isinstance(split_path, str) or re.fullmatch(r"[01]{0,17}", split_path) is None):
            _red()
        selected = groups[shard_index]
        for bit in split_path:
            if len(selected) < 2:
                _red()
            midpoint = len(selected) // 2
            selected = selected[:midpoint] if bit == "0" else selected[midpoint:]
        modules = {}
        for node in selected:
            modules.setdefault(node.split("::", 1)[0], []).append(node)
        return list(modules.values())
    except shards.ShardQuorumRed:
        _red()


def _read_progress(path: Path, manifest: dict) -> dict:
    """Validate event identity/ordering; finished is NOT JUnit execution credit."""
    binding = {"source_sha": manifest["source_sha"],
               "source_tree_sha": manifest["source_tree_sha"],
               "segment_sha256": manifest["collection_sha256"]}
    allowed = {nodeid_digest(node) for node in manifest["nodeids"]}
    seen, completed = set(), set()
    active = last_phase = last_hash = None
    elapsed = 0
    try:
        raw = _read_regular(path, len(allowed) * 5 * 1024)
        for sequence, line in enumerate(raw.splitlines()):
            if len(line) > 1024:
                _red()
            item = json.loads(line)
            if (not isinstance(item, dict) or set(item) != set(binding) | {
                    "sequence", "nodeid_sha256", "phase", "outcome", "elapsed_ms"} or
                    any(item[k] != v for k, v in binding.items()) or
                    type(item["sequence"]) is not int or item["sequence"] != sequence or
                    type(item["elapsed_ms"]) is not int or item["elapsed_ms"] < elapsed):
                _red()
            node, phase, outcome = item["nodeid_sha256"], item["phase"], item["outcome"]
            if node not in allowed or outcome not in {"none", "passed", "failed", "skipped"}:
                _red()
            if phase == "start":
                if active is not None or node in seen or outcome != "none":
                    _red()
                active = node
                seen.add(node)
            else:
                valid_next = {"start": {"setup"}, "setup": {"call", "teardown"},
                              "call": {"teardown"}, "teardown": {"finish"}}
                if active != node or phase not in valid_next.get(last_phase, set()):
                    _red()
                if (phase == "finish") != (outcome == "none"):
                    _red()
                if phase == "finish":
                    completed.add(node)
                    active = None
            last_phase, last_hash, elapsed = phase, node, item["elapsed_ms"]
        return {"started": len(seen), "finished": len(completed),
                "last_nodeid_sha256": last_hash, "last_phase": last_phase,
                "elapsed_ms": elapsed, "sha256": hashlib.sha256(raw).hexdigest()}
    except (OSError, ValueError, TypeError, KeyError):
        _red()


def _durable_json(path: Path, value: dict) -> None:
    """Atomic checkpoint replacement inside the newly owned diagnostic folder."""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".receipt-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def run_bounded(candidate_sha: str, source_tree_sha: str, *, repo: Path,
                shard_count: int, shard_index: int, diagnostics_dir: Path,
                split_path: str = "", batch_offset: int = 0,
                batch_limit: int = 1, timeout_seconds: int = 90) -> dict:
    """Explicit OFFHOST diagnostic only. Every result remains RED, even all-pass."""
    if (type(timeout_seconds) is not int or not 60 <= timeout_seconds <= 120 or
            type(batch_limit) is not int or not 1 <= batch_limit <= 8 or
            type(batch_offset) is not int or batch_offset < 0 or
            type(shard_count) is not int or not 1 <= shard_count <= 16 or
            type(shard_index) is not int or not 0 <= shard_index < shard_count or
            not isinstance(split_path, str) or re.fullmatch(r"[01]{0,17}", split_path) is None):
        _red()
    repo = Path(repo).resolve(strict=True)
    output = Path(diagnostics_dir)
    if (output.exists() or output.is_symlink() or not output.parent.is_dir() or
            output.resolve().is_relative_to(repo)):
        _red()
    _verify_checkout(repo, candidate_sha, source_tree_sha)
    args = _test_args(repo)
    output.mkdir(mode=0o700)
    proof = {"schema_version": 1, "status": "RED_BOUNDED_DIAGNOSTIC",
             "source_sha": candidate_sha, "source_tree_sha": source_tree_sha,
             "shards": shard_count, "shard_index": shard_index,
             "split_path": split_path, "batch_offset": batch_offset,
             "batch_limit": batch_limit, "timeout_seconds": timeout_seconds,
             "phase": "COLLECTING", "batches": [], "completed_batches": 0,
             "automatic_go": False, "runtime_authority": False,
             "release_gate_activated": False, "host_written": False}
    _durable_json(output / "receipt.json", proof)
    with tempfile.TemporaryDirectory(prefix="rc6-ci-bounded-") as directory:
        private = Path(directory)
        # No inherited broker/provider/GitHub secrets, shell startup or shared HOME.
        env = {"PATH": os.defpath, "HOME": str(private), "TMPDIR": str(private),
               "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
               "PYTHONPATH": os.pathsep.join([str(repo), *filter(None, sys.path)]),
               "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "RC6_CI_IDENTITY_PROOF": "1",
               "RC6_CI_SOURCE_SHA": candidate_sha, "RC6_CI_SOURCE_TREE_SHA": source_tree_sha,
               "RC6_CI_MANIFEST_PATH": str(private / "collected.json")}
        plugin = ["-p", "scripts.rc6_ci_pytest_identity", "-p", "no:cacheprovider"]
        try:
            started_collect = time.monotonic()
            result = _invoke(_pytest_argv(sys.executable, [*plugin, "--collect-only", "-q", *args],
                                         trace_after=timeout_seconds / 2),
                             cwd=repo, env=env, timeout=timeout_seconds)
            collection_ms = round((time.monotonic() - started_collect) * 1000)
            if result.returncode != 0:
                _red()
            collected = private / "collected.json"
            manifest = shards._validate_manifest(json.loads(_read_regular(collected, MAX_MANIFEST_BYTES)))
            if (manifest["source_sha"] != candidate_sha or manifest["source_tree_sha"] != source_tree_sha):
                _red()
            segments = bounded_segments(manifest, shard_count, shard_index, split_path)
            if batch_offset >= len(segments):
                _red()
            selected = segments[batch_offset:batch_offset + batch_limit]
            proof.update(collection_sha256=manifest["collection_sha256"],
                         discovered=manifest["discovered"], planned_batches=len(segments),
                         segment_sha256s=[shards.manifest(candidate_sha, source_tree_sha, s)[
                             "collection_sha256"] for s in segments],
                         selected_count=sum(map(len, selected)),
                         unselected_count=manifest["discovered"] - sum(map(len, selected)),
                         phase="RUNNING")
            _durable_json(output / "receipt.json", proof)
            for index, nodes in enumerate(selected, start=batch_offset):
                segment = shards.manifest(candidate_sha, source_tree_sha, nodes)
                progress = output / f"progress-{index}.jsonl"
                work = private / str(index)
                work.mkdir()
                batch_env = {**env, "RC6_CI_MANIFEST_PATH": str(work / "executed.json"),
                             "RC6_CI_PROGRESS_PATH": str(progress),
                             "RC6_CI_SEGMENT_SHA256": segment["collection_sha256"]}
                receipt = {"shard_index": 0}
                started_batch = time.monotonic()
                try:
                    result = _invoke(_pytest_argv(sys.executable, [*plugin, "-q", "-o",
                        "xfail_strict=true", f"--junitxml={work / 'junit.xml'}",
                        f"--basetemp={work / 'basetemp'}", "--", *nodes],
                        trace_after=timeout_seconds / 2, progress_owner=True), cwd=repo, env=batch_env,
                        timeout=timeout_seconds)
                    executed = work / "executed.json"
                    receipt.update(manifest=json.loads(_read_regular(executed, MAX_MANIFEST_BYTES)),
                                   junit_file=work / "junit.xml", pytest_exit_code=result.returncode)
                except ParallelRunnerRed as exc:
                    if exc.diagnostic is None:
                        raise
                    receipt.update(state="PYTEST_TIMEOUT",
                        faulthandler_seen=bool(exc.diagnostic.get("faulthandler_seen")),
                        parent_exited_before_timeout=bool(exc.diagnostic.get("parent_exited_before_timeout")))
                receipt["elapsed_ms"] = round((time.monotonic() - started_batch) * 1000)
                observed = _read_progress(progress, segment)
                if receipt.get("state") != "PYTEST_TIMEOUT" and observed["finished"] != len(nodes):
                    _red()
                clean = _red_diagnostic_junit(segment, [nodes], [receipt],
                    output / f"batch-{index}", allow_clean_diagnostic=True,
                    phase_elapsed_ms={"collect": collection_ms,
                                      "parallel_test": receipt["elapsed_ms"]})
                proof["batches"].append({"batch_index": index,
                    "segment_sha256": segment["collection_sha256"],
                    "assigned_count": len(nodes), "progress": observed,
                    "status": clean["status"], "executed": clean["executed"],
                    "missing": clean["missing"], "failures": clean["failures"],
                    "errors": clean["errors"], "skipped": clean["skipped"]})
                proof["completed_batches"] += 1
                _durable_json(output / "receipt.json", proof)
            _verify_checkout(repo, candidate_sha, source_tree_sha)
            proof["phase"] = "FINISHED"
        except (ParallelRunnerRed, OSError, ValueError, TypeError, shards.ShardQuorumRed):
            proof["phase"] = "INCOMPLETE"
            _durable_json(output / "receipt.json", proof)
            _red()
    _durable_json(output / "receipt.json", proof)
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
    for key in ("RC6_CI_PROGRESS_PATH", "RC6_CI_PROGRESS_PID", "RC6_CI_SEGMENT_SHA256"):
        env_base.pop(key, None)
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
            manifest = json.loads(_read_regular(collected, MAX_MANIFEST_BYTES))
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
                actual = json.loads(_read_regular(executed, MAX_MANIFEST_BYTES))
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
            _verify_checkout(repo, candidate_sha, source_tree_sha)
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
    parser.add_argument("--output-junit", type=Path)
    parser.add_argument("--diagnostics-dir", type=Path,
                        help="Sanitized RED-only diagnostics, never a CI gate")
    parser.add_argument("--bounded-shard-index", type=int,
                        help="Opt-in RED-only module diagnostic; zero-based shard index")
    parser.add_argument("--split-path", help="Deterministic binary shard interval, e.g. 010")
    parser.add_argument("--batch-offset", type=int)
    parser.add_argument("--batch-limit", type=int)
    parser.add_argument("--timeout-seconds", type=int)
    args = parser.parse_args(argv)
    try:
        if args.bounded_shard_index is not None:
            if args.diagnostics_dir is None or args.output_junit is not None:
                _red()
            proof = run_bounded(args.source_sha, args.source_tree_sha, repo=args.repo,
                shard_count=args.shards, shard_index=args.bounded_shard_index,
                diagnostics_dir=args.diagnostics_dir,
                split_path=args.split_path if args.split_path is not None else "",
                batch_offset=args.batch_offset if args.batch_offset is not None else 0,
                batch_limit=args.batch_limit if args.batch_limit is not None else 1,
                timeout_seconds=args.timeout_seconds if args.timeout_seconds is not None else 90)
            print("RC6_BOUNDED_DIAGNOSTIC=" + json.dumps(proof, sort_keys=True, separators=(",", ":")))
            print("RC6_BOUNDED_DIAGNOSTIC=RED|automatic_go=NO", file=sys.stderr)
            return 2
        if args.output_junit is None or any(option is not None for option in (
                args.split_path, args.batch_offset, args.batch_limit, args.timeout_seconds)):
            _red()
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
