"""A883: fail-closed cross-job quorum for RED-only bounded QA evidence.

This combines already-sanitized A863 receipts from independent GitHub Actions
matrix jobs. It does NOT reimplement the pytest collection/JUnit identity
verifier and NEVER grants a release, Predeploy, host or runtime authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from scripts import rc6_ci_junit_quorum as junit
from scripts import rc6_ci_parallel_runner as runner
from scripts import rc6_ci_shard_quorum as shard


SHARDS = 2
SHARD_INDEX = 1
BATCH_LIMIT = 8
TIMEOUT_SECONDS = 90
MIN_BATCHES = 265  # Required to make all 34 static offsets nonempty.
MAX_BATCHES = 272  # At most 34 jobs with eight bounded batches apiece.
MATRIX_JOBS = 34  # Offsets 0..264; final batch may have 1..8 segments.
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_PROGRESS_BYTES = 64 * 1024 * 1024
HEX64 = re.compile(r"[0-9a-f]{64}")
_PROOF_FLAGS = ("automatic_go", "runtime_authority", "release_gate_activated",
                "host_written")
_TOP_KEYS = {"schema_version", "status", "source_sha", "source_tree_sha",
             "collection_sha256", "discovered", "shards", "shard_index",
             "split_path", "batch_offset", "batch_limit", "timeout_seconds",
             "phase", "batches", "completed_batches", "planned_batches",
             "segment_sha256s", "selected_count", "unselected_count",
             *_PROOF_FLAGS}
_ENTRY_KEYS = {"batch_index", "segment_sha256", "assigned_count", "progress",
               "status", "executed", "missing", "failures", "errors", "skipped"}
_PROGRESS_KEYS = {"started", "finished", "last_phase", "last_nodeid_sha256",
                  "elapsed_ms", "sha256"}
_SUB_KEYS = {"schema_version", "status", "source_sha", "source_tree_sha",
             "collection_sha256", "discovered", "shards", "executed",
             "missing", "failures", "errors", "skipped", "xfail",
             "timed_out_shards", "timeouts", "sanitized_junit_sha256",
             "phase_elapsed_ms", "shard_elapsed_ms", *_PROOF_FLAGS}


class MatrixQuorumRed(ValueError):
    """A source, artifact, shard segment or JUnit quorum is unproven."""


def _red(code: str = "MATRIX_QUORUM_RED") -> None:
    raise MatrixQuorumRed(code)


def _hash(value: object, length: int = 64) -> str:
    try:
        return shard._sha(value, length)
    except shard.ShardQuorumRed:
        _red("INVALID_HASH")


def _integer(value: object, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _red("INVALID_COUNT")
    return value


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(runner._read_regular(path, MAX_RECEIPT_BYTES))
    except (OSError, TypeError, ValueError, runner.ParallelRunnerRed):
        _red("RECEIPT_UNREADABLE")
    if not isinstance(value, dict):
        _red("RECEIPT_NOT_OBJECT")
    return value


def _false_flags(item: dict) -> None:
    if any(item.get(name) is not False for name in _PROOF_FLAGS):
        _red("FALSE_GO_FLAG")


def _artifact_dir(directory: Path) -> None:
    if directory.is_symlink() or not directory.is_dir():
        _red("UNTRUSTED_ARTIFACT_DIRECTORY")


def _batch(diag: Path, entry: dict, source_sha: str, tree_sha: str,
           global_segments: list[str]) -> dict:
    if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
        _red("BATCH_NOT_OBJECT")
    index = _integer(entry.get("batch_index"))
    if index >= len(global_segments):
        _red("BATCH_OUT_OF_RANGE")
    seg = _hash(entry.get("segment_sha256"))
    if seg != global_segments[index]:
        _red("SEGMENT_HASH_MISMATCH")
    assigned = _integer(entry.get("assigned_count"), minimum=1)
    executed = _integer(entry.get("executed"))
    missing = _integer(entry.get("missing"))
    failures = _integer(entry.get("failures"))
    errors = _integer(entry.get("errors"))
    skipped = _integer(entry.get("skipped"))
    if (executed + missing != assigned or
            failures + errors + skipped > executed or
            entry.get("status") not in ("RED_DIAGNOSTIC_OFFLINE_ONLY",
                                        "RED_INCOMPLETE_DIAGNOSTIC")):
        _red("INCONSISTENT_BATCH_COUNTS")

    progress = entry.get("progress")
    if not isinstance(progress, dict) or set(progress) != _PROGRESS_KEYS:
        _red("MISSING_PROGRESS")
    started = _integer(progress.get("started"))
    finished = _integer(progress.get("finished"))
    _integer(progress.get("elapsed_ms"))
    if (started > assigned or finished > started or
            (missing == 0 and (started != assigned or finished != assigned or
                               progress.get("last_phase") != "finish"))):
        _red("PROGRESS_COUNT_MISMATCH")
    _hash(progress.get("sha256"))
    _hash(progress.get("last_nodeid_sha256"))
    progress_file = diag / f"progress-{index}.jsonl"
    try:
        raw_progress = runner._read_regular(progress_file, MAX_PROGRESS_BYTES)
    except (OSError, ValueError, runner.ParallelRunnerRed):
        _red("PROGRESS_UNREADABLE")
    if hashlib.sha256(raw_progress).hexdigest() != progress["sha256"]:
        _red("PROGRESS_SHA_MISMATCH")

    folder = diag / f"batch-{index}"
    _artifact_dir(folder)
    sub = _read_json(folder / "receipt.json")
    if set(sub) != _SUB_KEYS:
        _red("SUBRECEIPT_SCHEMA_DRIFT")
    _false_flags(sub)
    expected = {"source_sha": source_sha, "source_tree_sha": tree_sha,
                "collection_sha256": seg, "discovered": assigned,
                "executed": executed, "missing": missing,
                "failures": failures, "errors": errors, "skipped": skipped,
                "shards": 1, "status": entry["status"]}
    if (sub.get("schema_version") != 1 or
            any(sub.get(key) != value for key, value in expected.items()) or
            _integer(sub.get("timed_out_shards")) != (1 if missing else 0) or
            not isinstance(sub.get("timeouts"), list) or
            len(sub["timeouts"]) != (1 if missing else 0)):
        _red("SUBRECEIPT_MISMATCH")
    _integer(sub["xfail"])
    if sub["xfail"] > skipped:
        _red("SUBRECEIPT_XFAIL_COUNT")
    elapsed = sub["phase_elapsed_ms"]
    if not isinstance(elapsed, dict) or set(elapsed) != {
            "collect", "parallel_test", "sanitize"}:
        _red("SUBRECEIPT_TIMING_SCHEMA")
    for value in elapsed.values():
        _integer(value)
    if (not isinstance(sub["shard_elapsed_ms"], list) or
            len(sub["shard_elapsed_ms"]) != 1):
        _red("SUBRECEIPT_SHARD_TIMING")
    _integer(sub["shard_elapsed_ms"][0])

    try:
        root, xml_bytes = junit._xml(folder / "junit-sanitized.xml")
    except (junit.JunitQuorumRed, OSError, ValueError):
        _red("SANITIZED_XML_UNREADABLE")
    if hashlib.sha256(xml_bytes).hexdigest() != _hash(sub.get("sanitized_junit_sha256")):
        _red("SANITIZED_XML_SHA_MISMATCH")
    cases = list(root.iter("testcase"))
    if (root.tag != "testsuite" or root.attrib != {
            "name": "rc6-a820-hashed-diagnostic", "tests": str(executed),
            "failures": str(failures),
            "errors": str(errors), "skipped": str(skipped)} or
            len(root) != len(cases) or len(cases) != executed or
            root.text or root.tail):
        _red("XML_CASE_COUNT_MISMATCH")
    case_hashes = []
    observed = {"failure": 0, "error": 0, "skipped": 0}
    for case in cases:
        name = case.get("name")
        if (not isinstance(name, str) or HEX64.fullmatch(name) is None or
                case.attrib != {"name": name, "classname": "rc6.offhost.hashed"}
                or case.text or case.tail):
            _red("RAW_OR_INVALID_TEST_IDENTITY")
        case_hashes.append(name)
        for child in case:
            if (child.tag not in observed or child.attrib != {
                    "type": "SANITIZED_DIAGNOSTIC"} or child.text or
                    child.tail or len(child)):
                _red("RAW_OR_INVALID_TEST_OUTPUT")
            observed[child.tag] += 1
    if (len(set(case_hashes)) != len(case_hashes) or
            any(observed[key] != count for key, count in (
                ("failure", failures), ("error", errors), ("skipped", skipped)))):
        _red("XML_OUTCOME_COUNT_MISMATCH")
    return {"assigned": assigned, "executed": executed, "missing": missing,
            "failures": failures, "errors": errors, "skipped": skipped,
            "case_hashes": case_hashes}


def verify_matrix(artifacts_root: Path, source_sha: str, tree_sha: str,
                  expected_batches: int, expected_collection: str) -> dict:
    """Cross-check all sanitized, matching, non-overlapping shard1 batches."""
    source_sha = _hash(source_sha, 40)
    tree_sha = _hash(tree_sha, 40)
    expected_batches = _integer(expected_batches, minimum=MIN_BATCHES)
    if expected_batches > MAX_BATCHES:
        _red("PLAN_OUTSIDE_34_JOB_BUDGET")
    expected_collection = _hash(expected_collection)
    root = Path(artifacts_root)
    _artifact_dir(root)
    prefix = f"rc6-a883-shard1-"
    suffix = f"-{source_sha}"
    directories = list(root.iterdir())
    if len(directories) != MATRIX_JOBS:
        _red("MATRIX_ARTIFACT_COUNT")
    by_offset = {}
    global_identity = None
    for directory in directories:
        _artifact_dir(directory)
        name = directory.name
        if not (name.startswith(prefix) and name.endswith(suffix)):
            _red("UNEXPECTED_MATRIX_ARTIFACT")
        raw_index = name[len(prefix):-len(suffix)]
        if re.fullmatch(r"(0|[1-9][0-9]*)", raw_index) is None:
            _red("INVALID_MATRIX_OFFSET")
        offset = int(raw_index)
        if offset % BATCH_LIMIT or offset in by_offset:
            _red("DUPLICATE_OR_MISALIGNED_OFFSET")
        diag = directory / "diagnostic"
        _artifact_dir(diag)
        receipt = _read_json(diag / "receipt.json")
        if set(receipt) != _TOP_KEYS:
            _red("TOP_RECEIPT_SCHEMA_DRIFT")
        _false_flags(receipt)
        if (receipt.get("schema_version") != 1 or
                receipt.get("source_sha") != source_sha or
                receipt.get("source_tree_sha") != tree_sha or
                receipt.get("status") != "RED_BOUNDED_DIAGNOSTIC" or
                receipt.get("phase") != "FINISHED" or
                receipt.get("shards") != SHARDS or
                receipt.get("shard_index") != SHARD_INDEX or
                receipt.get("split_path") != "" or
                receipt.get("batch_offset") != offset or
                receipt.get("batch_limit") != BATCH_LIMIT or
                receipt.get("timeout_seconds") != TIMEOUT_SECONDS):
            _red("MATRIX_RECEIPT_CONTRACT")
        segment_hashes = receipt.get("segment_sha256s")
        planned = _integer(receipt.get("planned_batches"), minimum=1)
        discovered = _integer(receipt.get("discovered"), minimum=1)
        collection = _hash(receipt.get("collection_sha256"))
        if (planned != expected_batches or collection != expected_collection or
                not isinstance(segment_hashes, list) or
                not all(isinstance(item, str) for item in segment_hashes) or
                len(segment_hashes) != planned or
                len(set(segment_hashes)) != planned):
            _red("SEGMENT_INVENTORY_BUDGET")
        for item in segment_hashes:
            _hash(item)
        identity = (source_sha, tree_sha, collection, discovered,
                    tuple(segment_hashes), planned)
        if global_identity is None:
            global_identity = identity
        elif identity != global_identity:
            _red("CROSS_JOB_COLLECTION_DRIFT")
        by_offset[offset] = (diag, receipt)

    planned = global_identity[-1]
    expected_offsets = list(range(0, planned, BATCH_LIMIT))
    if sorted(by_offset) != expected_offsets:
        _red("MISSING_MATRIX_OFFSETS")
    counters = {name: 0 for name in ("assigned", "executed", "missing",
                                    "failures", "errors", "skipped")}
    covered = set()
    unique_case_hashes = set()
    global_segments = list(global_identity[-2])
    for offset in expected_offsets:
        diag, receipt = by_offset[offset]
        indices = list(range(offset, min(offset + BATCH_LIMIT, planned)))
        batches = receipt.get("batches")
        if (not isinstance(batches, list) or len(batches) != len(indices) or
                receipt.get("completed_batches") != len(indices)):
            _red("INCOMPLETE_MATRIX_JOB")
        if [b.get("batch_index") if isinstance(b, dict) else None
                for b in batches] != indices:
            _red("REORDERED_OR_DUPLICATE_BATCH")
        for entry in batches:
            index = entry["batch_index"]
            if index in covered:
                _red("DUPLICATE_BATCH")
            covered.add(index)
            result = _batch(diag, entry, source_sha, tree_sha, global_segments)
            hashes = result.pop("case_hashes")
            if any(item in unique_case_hashes for item in hashes):
                _red("CROSS_JOB_DUPLICATE_JUNIT_TESTCASE")
            unique_case_hashes.update(hashes)
            for name in counters:
                counters[name] += result[name]
        if (receipt.get("selected_count") != sum(b["assigned_count"] for b in batches) or
                receipt.get("unselected_count") != global_identity[3] - receipt[
                    "selected_count"]):
            _red("JOB_SELECTION_COUNT_MISMATCH")
        allowed_paths = {"receipt.json"}
        for index in indices:
            allowed_paths.update({f"progress-{index}.jsonl",
                                  f"batch-{index}/receipt.json",
                                  f"batch-{index}/junit-sanitized.xml"})
        observed_paths = {p.relative_to(diag).as_posix() for p in diag.rglob("*")
                          if p.is_file()}
        if observed_paths != allowed_paths:
            _red("UNEXPECTED_DIAGNOSTIC_FILE")
    if covered != set(range(planned)) or counters["assigned"] != (
            counters["executed"] + counters["missing"]):
        _red("SHARD1_SEGMENT_QUORUM")
    clean = all(counters[name] == 0 for name in
                ("missing", "failures", "errors", "skipped"))
    return {"schema_version": 1,
            "status": "RED_DIAGNOSTIC_ONLY" if clean else "RED_MATRIX_INCOMPLETE",
            "source_sha": source_sha, "source_tree_sha": tree_sha,
            "collection_sha256": global_identity[2],
            "discovered": global_identity[3], "shards": SHARDS,
            "shard_index": SHARD_INDEX, "matrix_jobs": len(expected_offsets),
            "planned_batches": planned, "completed_batches": len(covered),
            **counters, "automatic_go": False,
            "runtime_authority": False, "release_gate_activated": False,
            "host_written": False, "full_suite_certified": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-root", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree-sha", required=True)
    parser.add_argument("--expected-batches", required=True, type=int)
    parser.add_argument("--expected-collection-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        proof = verify_matrix(args.artifacts_root, args.source_sha, args.source_tree_sha,
                              args.expected_batches, args.expected_collection_sha256)
    except (MatrixQuorumRed, OSError, ValueError) as exc:
        proof = {"schema_version": 1, "status": "RED_MATRIX_INCOMPLETE",
                 "source_sha": args.source_sha, "source_tree_sha": args.source_tree_sha,
                 "reason": str(exc) if isinstance(exc, MatrixQuorumRed)
                 else "MATRIX_IO_OR_SCHEMA_ERROR", "automatic_go": False,
                 "runtime_authority": False, "release_gate_activated": False,
                 "host_written": False, "full_suite_certified": False}
    output = args.output
    if output.exists() or not output.parent.is_dir():
        print("A883_MATRIX=RED|reason=OUTPUT_NOT_EXCLUSIVE")
        return 2
    runner._durable_json(output, proof)
    print("A883_MATRIX=RED|automatic_go=NO|status=" + proof["status"])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
