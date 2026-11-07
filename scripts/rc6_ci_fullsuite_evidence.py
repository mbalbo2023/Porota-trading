"""A963: hash-only JUnit/collection receipt for the existing FULL pytest job.

Evidence-only: this module neither runs tests nor grants Predeploy, Deploy,
runtime or PAPER trading authority. Raw JUnit and manifests stay runner-private.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from scripts import rc6_ci_junit_quorum as junit
from scripts import rc6_ci_parallel_runner as runner
from scripts import rc6_ci_shard_quorum as shard
from scripts.rc6_ci_pytest_identity import PROPERTY, nodeid_digest


class FullSuiteEvidenceRed(ValueError):
    """The collected/executed/JUnit identity is unproven."""


def _red(reason: str) -> None:
    raise FullSuiteEvidenceRed(reason)


def _manifest(path: Path) -> dict:
    try:
        raw = json.loads(runner._read_regular(Path(path), 16 * 1024 * 1024))
        return shard._validate_manifest(raw)
    except (OSError, ValueError, TypeError, runner.ParallelRunnerRed,
            shard.ShardQuorumRed):
        _red("MANIFEST_UNVERIFIED")


def _exit_code(path: Path) -> int:
    try:
        raw = runner._read_regular(Path(path), 10)
        if re.fullmatch(rb"[0-5]\n?", raw) is None:
            _red("PYTEST_EXIT_UNVERIFIED")
        return int(raw)
    except (OSError, ValueError, TypeError, runner.ParallelRunnerRed):
        _red("PYTEST_EXIT_UNVERIFIED")


def _cases(path: Path) -> list[tuple[str, str | None]]:
    try:
        root, _ = junit._xml(Path(path))
    except (junit.JunitQuorumRed, OSError, ValueError):
        _red("JUNIT_UNVERIFIED")
    cases = list(root.iter("testcase"))
    if not cases:
        _red("JUNIT_EMPTY")
    result: list[tuple[str, str | None]] = []
    for case in cases:
        properties = [node.get("value") for node in case.findall("./properties/property")
                      if node.get("name") == PROPERTY]
        if (len(properties) != 1 or not isinstance(properties[0], str) or
                re.fullmatch(r"[0-9a-f]{64}", properties[0]) is None):
            _red("JUNIT_HASH_PROPERTY_INVALID")
        outcomes = [name for name in ("failure", "error", "skipped")
                    if case.find(name) is not None]
        if len(outcomes) > 1 or any(child.tag not in {
                "properties", "failure", "error", "skipped", "system-out", "system-err"}
                for child in case):
            _red("JUNIT_OUTCOME_AMBIGUOUS")
        result.append((properties[0], outcomes[0] if outcomes else None))
    return result


def summarize(collected_path: Path, executed_path: Path, junit_path: Path,
              rc_path: Path, output_dir: Path, *, source_sha: str,
              source_tree_sha: str) -> dict:
    """Verify and redact evidence; write RED receipt even on incomplete input.

    Only evidence of an exact, completed JUnit union may be marked complete.
    Functional test failures are retained as RED while still proving coverage.
    """
    output = Path(output_dir)
    if (not output.is_dir() or output.is_symlink() or
            (output / "receipt.json").exists() or
            (output / "junit-hashed.xml").exists()):
        _red("OUTPUT_NOT_EXCLUSIVE")
    result = {"schema_version": 1, "status": "RED_INCOMPLETE",
              "source_sha": source_sha, "source_tree_sha": source_tree_sha,
              "coverage_complete": False, "automatic_go": False,
              "release_gate_activated": False, "host_written": False,
              "runtime_authority": False, "full_suite_certified": False}
    xml_data: bytes | None = None
    try:
        source = shard._sha(source_sha)
        tree = shard._sha(source_tree_sha)
        collected = _manifest(collected_path)
        executed = _manifest(executed_path)
        rc = _exit_code(rc_path)
        if (collected["source_sha"] != source or
                collected["source_tree_sha"] != tree or
                executed["source_sha"] != source or
                executed["source_tree_sha"] != tree):
            _red("SOURCE_IDENTITY_MISMATCH")
        expected = collected["nodeids"]
        result.update(discovered=len(expected),
                      collection_sha256=collected["collection_sha256"])
        if executed != collected:
            _red("COLLECTION_DRIFT")
        observations = _cases(junit_path)
        names = [name for name, _ in observations]
        actual = set(names)
        expected_hashes = {nodeid_digest(node) for node in expected}
        result.update(executed=len(names),
                      missing=len(expected_hashes.difference(actual)))
        if (len(names) != len(set(names)) or actual != expected_hashes or
                len(names) != len(expected)):
            _red("JUNIT_MEMBERSHIP_DRIFT")
        counts = {status: sum(outcome == status for _, outcome in observations)
                  for status in ("failure", "error", "skipped")}
        result.update(failures=counts["failure"], errors=counts["error"],
                      skipped=counts["skipped"], pytest_exit_code=rc,
                      coverage_complete=True)
        if ((rc == 0) != (counts["failure"] + counts["error"] == 0)):
            _red("PYTEST_EXIT_INCONSISTENT")
        if rc not in (0, 1):
            _red("PYTEST_EXIT_INCONSISTENT")

        safe_root = ET.Element("testsuite", name="rc6-fullsuite-hashed-evidence",
                               tests=str(len(names)), failures=str(counts["failure"]),
                               errors=str(counts["error"]),
                               skipped=str(counts["skipped"]))
        for name, outcome in observations:
            safe_case = ET.SubElement(safe_root, "testcase", name=name,
                                      classname="rc6.offhost.hashed")
            if outcome:
                ET.SubElement(safe_case, outcome, type="SANITIZED_DIAGNOSTIC")
        xml_data = ET.tostring(safe_root, encoding="utf-8", xml_declaration=True)
        result["sanitized_junit_sha256"] = hashlib.sha256(xml_data).hexdigest()
        if counts["failure"] or counts["error"]:
            result["status"] = "RED_FUNCTIONAL_FAILURES"
        elif counts["skipped"]:
            result["status"] = "RED_SKIPPED"
        else:
            result["status"] = "GREEN_EVIDENCE_ONLY"
    except (FullSuiteEvidenceRed, shard.ShardQuorumRed, ValueError, TypeError) as exc:
        result["status"] = "RED_INCOMPLETE"
        result["coverage_complete"] = False
        result["reason"] = str(exc) if isinstance(exc, FullSuiteEvidenceRed) else "INPUT_UNVERIFIED"
        xml_data = None

    if xml_data is not None:
        path = output / "junit-hashed.xml"
        with path.open("xb") as handle:
            handle.write(xml_data)
    runner._durable_json(output / "receipt.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collected", type=Path, required=True)
    parser.add_argument("--executed", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--pytest-rc", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree-sha", required=True)
    args = parser.parse_args(argv)
    receipt = summarize(args.collected, args.executed, args.junit,
                        args.pytest_rc, args.out, source_sha=args.source_sha,
                        source_tree_sha=args.source_tree_sha)
    print("RC6_FULLSUITE_COVERAGE=" + receipt["status"])
    return 0 if receipt["status"] == "GREEN_EVIDENCE_ONLY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
