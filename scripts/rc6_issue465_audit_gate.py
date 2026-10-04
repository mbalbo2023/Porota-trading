#!/usr/bin/env python3
"""Bind the Issue 465 closure matrix to an exact checkout and executed tests.

This is a predeploy code gate. A GREEN report does not grant deployment,
financial verification, provider capacity, or independent audit approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import xml.etree.ElementTree as ET

MATRIX = "docs/audits/ISSUE465_REAUDIT.json"
REPORT = "docs/audits/ISSUE465_REAUDIT_ORIGINAL_REPORT.md"
REQUIRED = {"F-01", "F-02", "F-03", "F-04", "F-05", "E", "F", "G", "FULL-REGRESSION"} | {
    f"AUDIT17-{number:02d}" for number in range(1, 10)}
EXTERNAL = {f"AUDIT17-{number:02d}" for number in range(6, 10)}
STATES = {"FIXED_AND_TESTED", "HARDENED_AND_TESTED", "EXTERNAL_EVIDENCE_PENDING"}
CLAUSE_COUNTS = {"F-01": 20, "F-02": 12, "F-03": 15, "F-04": 7, "F-05": 11,
                 "E": 9, "F": 12, "G": 13}
ORIGINAL_AUTHORITY = {
    "issue": "mbalbo2023/Porota-trading#465",
    "original_report_url": "https://github.com/mbalbo2023/Porota-trading/pull/463#issuecomment-5980867529",
    "original_report_sha256": "eb72d6586d995e64fc577cb4b23595a8700dfb0e8b55e4fa1ef70c2e947e8d11",
    "product_base": "da697c6e6c2274579f9e4a112fabc4327475dd35",
    "rejected_head": "caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423",
    "rejected_tree": "f1712a72cb49435c4403145bb38d98c4006f8f07",
}


class AuditGateError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise AuditGateError(reason)


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def load(path):
    require(path.is_file() and not path.is_symlink(), "MISSING_OR_ALIASED_INPUT")
    require(path.stat().st_size <= 4 * 1024**2, "INPUT_SIZE_BOUND")
    return json.loads(path.read_bytes(), object_pairs_hook=_pairs)


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                            text=True, timeout=20, check=False)
    require(result.returncode == 0, "GIT_PROVENANCE_UNAVAILABLE")
    return result.stdout.strip()


def _safe_node(value):
    require(isinstance(value, str) and re.fullmatch(r"tests/test_[A-Za-z0-9_]+\.py::test_[A-Za-z0-9_]+", value),
            "INVALID_REGRESSION_NODE")
    path, function = value.split("::")
    return path, function


def verify(root: Path, junit: Path, governed: Path) -> dict:
    root = root.resolve(strict=True)
    matrix_path = root / MATRIX
    matrix = load(matrix_path)
    require(matrix.get("schema") == "rc6.issue465-reaudit.v1", "MATRIX_SCHEMA")
    authority = matrix.get("authority", {})
    require(all(authority.get(key) == value for key, value in ORIGINAL_AUTHORITY.items()),
            "ORIGINAL_AUTHORITY_MISMATCH")
    original = root / REPORT
    require(original.is_file() and not original.is_symlink(), "MISSING_ORIGINAL_AUDIT")
    require(hashlib.sha256(original.read_bytes()).hexdigest() == authority.get("original_report_sha256"),
            "ORIGINAL_AUDIT_DIGEST_MISMATCH")
    safety = matrix.get("safety", {})
    require(safety == {"mode": "PAPER_SHADOW_ONLY", "real_orders_sent": 0,
                       "real_routes": "NOT_CALLED", "merge": False, "deploy": False,
                       "runtime_mutation": False, "ppi_watch": "NOT_TOUCHED",
                       "deploy_owner": "NOT_ACQUIRED"}, "SAFETY_CONTRACT_MISMATCH")
    require(type(safety["real_orders_sent"]) is int and
            all(safety[key] is False for key in ("merge", "deploy", "runtime_mutation")),
            "SAFETY_TYPE_MISMATCH")

    proof = load(governed)
    require(all(type(proof.get(key)) is int and proof[key] >= 0 for key in
                ("pytest_exit_code", "executed", "discovered", "failures", "errors", "skipped", "xfail")),
            "GOVERNED_TYPES_INVALID")
    require(proof.get("scope") == "repository-root automatic pytest discovery", "WRONG_TEST_SCOPE")
    require(proof.get("status") == "GREEN" and proof.get("pytest_exit_code") == 0,
            "GOVERNED_SUITE_NOT_GREEN")
    require(not any(proof.get(key, -1) for key in ("failures", "errors", "skipped", "xfail")),
            "GOVERNED_SUITE_HAS_NONPASS")
    require(type(proof.get("executed")) is int and proof["executed"] > 0 and
            proof.get("discovered") == proof["executed"], "GOVERNED_COLLECTION_MISMATCH")
    require(proof.get("exclusions") == matrix.get("governed_exclusions"), "EXCLUSION_POLICY_DRIFT")
    require(junit.is_file() and not junit.is_symlink() and junit.stat().st_size < 16*1024**2,
            "JUNIT_INPUT_BOUND")
    tree = ET.parse(junit)
    cases = list(tree.iter("testcase"))
    require(len(cases) == proof["executed"], "JUNIT_EXECUTED_MISMATCH")
    require(all(case.find(kind) is None for case in cases for kind in ("failure", "error", "skipped")),
            "JUNIT_NONPASS")
    actual = {}
    identities = set()
    for case in cases:
        class_name, name = case.get("classname", ""), case.get("name", "")
        require((class_name, name) not in identities, "DUPLICATE_JUNIT_CASE")
        identities.add((class_name, name))
        key = (class_name, name.split("[", 1)[0])
        actual[key] = actual.get(key, 0) + 1

    rows = matrix.get("findings", [])
    require(isinstance(rows, list) and len(rows) == len(REQUIRED), "MATRIX_FINDING_CARDINALITY")
    ids = [row.get("id") for row in rows]
    require(len(set(ids)) == len(ids) and set(ids) == REQUIRED, "MATRIX_MISSING_OR_DUPLICATE_REQUIREMENT")
    tested_nodes = set()
    for row in rows:
        identifier = row["id"]
        require(row.get("state") in STATES, "INVALID_CLOSURE_STATE")
        require((row["state"] == "EXTERNAL_EVIDENCE_PENDING") == (identifier in EXTERNAL),
                "FALSE_EXTERNAL_OR_PROGRAMMABLE_CLOSURE")
        for field in ("original_evidence", "rca", "fix_code", "caller", "negative_test", "remaining_uncertainty"):
            require(isinstance(row.get(field), str) and bool(row[field].strip()), "INCOMPLETE_MATRIX_ROW:"+identifier)
        requirements = row.get("coverage", [])
        require(isinstance(requirements, list) and (requirements or identifier in EXTERNAL),
                "MISSING_PROGRAMMABLE_COVERAGE:"+identifier)
        clause_ids = [item.get("id") for item in requirements]
        require(len(set(clause_ids)) == len(clause_ids), "DUPLICATE_REQUIREMENT_CLAUSE")
        if identifier in CLAUSE_COUNTS:
            required_clauses = {f"{identifier}-{number:02d}" for number in range(1, CLAUSE_COUNTS[identifier]+1)}
            require(required_clauses <= set(clause_ids), "MISSING_REQUIRED_CLAUSE:"+identifier)
        for coverage in requirements:
            require(isinstance(coverage.get("requirement"), str) and bool(coverage["requirement"].strip()),
                    "UNNAMED_REQUIREMENT")
            nodes = coverage.get("tests", [])
            require(isinstance(nodes, list) and bool(nodes), "UNTESTED_PROGRAMMABLE_REQUIREMENT")
            for node in nodes:
                path, function = _safe_node(node["node"])
                source = root / path
                require(source.is_file() and not source.is_symlink(), "MISSING_REGRESSION_SOURCE")
                minimum = node.get("minimum_cases", 1)
                require(type(minimum) is int and 1 <= minimum <= 100, "INVALID_CASE_COUNT")
                classname = path[:-3].replace("/", ".")
                count = actual.get((classname, function), 0)
                require(count >= minimum, "REGRESSION_NOT_EXECUTED:"+node["node"])
                tested_nodes.add(node["node"])

    streams = matrix.get("workstreams", [])
    require(isinstance(streams, list) and {item.get("id") for item in streams} == set("ABCDEFG") and
            len(streams) == 7, "INCOMPLETE_WORKSTREAM_PROVENANCE")
    for stream in streams:
        require(stream.get("write_owner") == "RELEASED", "FRONT_WRITE_OWNER_NOT_RELEASED")
        head, tree_sha = stream.get("head", ""), stream.get("tree", "")
        require(re.fullmatch(r"[0-9a-f]{40}", head) and re.fullmatch(r"[0-9a-f]{40}", tree_sha),
                "UNFROZEN_FRONT_HEAD")
        require(git(root, "rev-parse", head+"^{tree}") == tree_sha, "FRONT_TREE_MISMATCH")
        paths = stream.get("paths", [])
        require(isinstance(paths, list) and bool(paths), "EMPTY_FRONT_SCOPE")
        for path in paths:
            require(isinstance(path, str) and path and not path.startswith("/") and ".." not in Path(path).parts,
                    "UNSAFE_FRONT_PATH")
            frozen_blob = git(root, "rev-parse", head+":"+path)
            require(frozen_blob == git(root, "rev-parse", "HEAD:"+path),
                    "FRONT_BYTES_CHANGED_AFTER_FREEZE:"+path)
            source = root / path
            info = source.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "FROZEN_DISK_ALIAS")
            for parent in source.parents:
                if parent == root: break
                require(not parent.is_symlink(), "FROZEN_DISK_PARENT_ALIAS")
            require(git(root, "hash-object", "--no-filters", "--", path) == frozen_blob,
                    "FROZEN_DISK_BYTE_MISMATCH:"+path)
            mode = git(root, "ls-tree", head, "--", path).split()[0]
            require(bool(info.st_mode & 0o111) == (mode == "100755"), "FROZEN_DISK_MODE_MISMATCH")
    sha = git(root, "rev-parse", "HEAD")
    candidate_tree = git(root, "rev-parse", "HEAD^{tree}")
    return {"schema": "rc6.issue465-audit-gate.v1", "status": "GREEN",
            "candidate_sha": sha, "candidate_tree": candidate_tree,
            "matrix_sha256": hashlib.sha256(matrix_path.read_bytes()).hexdigest(),
            "original_report_sha256": authority["original_report_sha256"],
            "governed_executed": proof["executed"], "required_test_functions_executed": len(tested_nodes),
            "findings": {row["id"]: row["state"] for row in rows},
            "workstream_heads": {stream["id"]: stream["head"] for stream in streams},
            "safety": safety, "independent_reaudit": "PENDING",
            "external_evidence": "NO_VERIFICADO"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["verify"])
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--governed", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args.repo_root, args.junit, args.governed)
    except (AuditGateError, ValueError, KeyError, TypeError, OSError, ET.ParseError, subprocess.SubprocessError):
        result = {"schema": "rc6.issue465-audit-gate.v1", "status": "RED",
                  "reason": "MATRIX_OR_EXECUTED_EVIDENCE_INVALID"}
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2)+"\n", encoding="utf-8")
    print("ISSUE465_AUDIT_GATE="+result["status"])
    return 0 if result["status"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
