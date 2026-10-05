#!/usr/bin/env python3
"""Bind the Issue 465 closure matrix to an exact checkout and executed tests.

This is a predeploy code gate. A GREEN report does not grant deployment,
financial verification, provider capacity, or independent audit approval.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import xml.etree.ElementTree as ET

try:
    from scripts import rc6_convergence_provenance as convergence
except ModuleNotFoundError as error:
    if error.name != "scripts":
        raise
    import rc6_convergence_provenance as convergence

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
CONVERGENCE_BASELINE = "c27dfd963c4fe83465c0f2105347e974fbbe6356"


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


def load(path, *, raw=False):
    require(path.is_file() and not path.is_symlink(), "MISSING_OR_ALIASED_INPUT")
    require(path.stat().st_size <= 4 * 1024**2, "INPUT_SIZE_BOUND")
    data = path.read_bytes()
    return data if raw else json.loads(data, object_pairs_hook=_pairs)


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


def _source_record(root, revision, path):
    record = git(root, "ls-tree", revision, "--", path)
    metadata, separator, name = record.partition("\t")
    fields = metadata.split()
    require(separator and name == path and len(fields) == 3 and fields[1] == "blob"
            and fields[0] in {"100644", "100755"}, "FRONT_SOURCE_RECORD_INVALID")
    return {"blob": fields[2], "git_mode": fields[0]}


def _original_convergence_authority(root, candidate_sha, matrix_blob):
    # The earlier seven frozen front records and all of their coverage remain
    # immutable authority. A successor cannot rewrite the rejected audit.
    for path in (MATRIX, REPORT):
        require(_source_record(root, candidate_sha, path) == _source_record(root, CONVERGENCE_BASELINE, path),
                "CONVERGENCE_LEGACY_AUTHORITY_CHANGED:" + path)
        require(git(root, "hash-object", "--no-filters", "--", path)
                == _source_record(root, candidate_sha, path)["blob"],
                "CONVERGENCE_LEGACY_AUTHORITY_DIRTY:" + path)
    require(matrix_blob == _source_record(root, CONVERGENCE_BASELINE, MATRIX)["blob"],
            "CONVERGENCE_LOADED_MATRIX_NOT_ORIGINAL")


def _verified_successor(root, junit, governed, candidate_sha):
    """Recompute the full convergence proof; never accept a supplied status file."""
    manifest = convergence.INPUT_ROOT + "/" + convergence.MANIFEST
    require(bool(git(root, "ls-tree", candidate_sha, "--", manifest)),
            "FRONT_BYTES_CHANGED_AFTER_FREEZE_WITHOUT_CONVERGENCE")
    try:
        result = convergence.verify(root, candidate_sha, junit)
    except (convergence.ConvergenceError, KeyError, TypeError, OSError, ET.ParseError,
            subprocess.SubprocessError) as error:
        raise AuditGateError("CONVERGENCE_SUCCESSOR_NOT_VERIFIED") from error
    require(result.get("schema") == "rc6.final-input-provenance.v1"
            and result.get("software_status") == "EXECUTED_NATIVE_GREEN",
            "CONVERGENCE_SUCCESSOR_NOT_EXECUTED")
    require(result.get("candidate_sha") == candidate_sha
            and result.get("candidate_tree") == git(root, "rev-parse", candidate_sha+"^{tree}"),
            "CONVERGENCE_SUCCESSOR_SOURCE_MISMATCH")
    execution = result.get("test_execution", {})
    require(execution.get("junit_sha256") == junit.sha256
            and execution.get("executed_unique_cases") == governed["executed"],
            "CONVERGENCE_SUCCESSOR_EXECUTION_MISMATCH")
    require(any(row.get("pr") == 466 and row.get("head_sha") == CONVERGENCE_BASELINE
                for row in result.get("sources", [])), "CONVERGENCE_BASELINE_MISMATCH")
    return result


def _guarded_front_path(root, path, frozen, current, successor):
    """Require exact old provenance plus executed native guards for this path."""
    require(_source_record(root, CONVERGENCE_BASELINE, path) == frozen,
            "FRONT_NOT_PRESERVED_AT_CONVERGENCE_BASELINE:" + path)
    records = [row for row in successor.get("paths", []) if row.get("path") == path]
    require(len(records) == 1, "CONVERGENCE_FRONT_PATH_MISSING_OR_DUPLICATE:" + path)
    row = records[0]
    require(row.get("final_blob") == current["blob"] and row.get("git_mode") == current["git_mode"]
            and row.get("source_blobs", {}).get("466") == frozen["blob"]
            and row.get("source_git_modes", {}).get("466") == frozen["git_mode"],
            "CONVERGENCE_FRONT_SOURCE_MISMATCH:" + path)
    require(row.get("preservation") == "EVOLVED_WITH_NATIVE_GUARDS"
            and isinstance(row.get("evolution_reason"), str) and row["evolution_reason"].strip()
            and bool(row.get("requirement_ids")) and bool(row.get("guard_nodes")),
            "CONVERGENCE_FRONT_EVOLUTION_UNGUARDED:" + path)
    requirements = {item["id"]: item for item in successor["requirements"]}
    executions = {}
    for identifier in row["requirement_ids"]:
        require(identifier in requirements, "CONVERGENCE_FRONT_REQUIREMENT_INVALID:" + path)
        requirement = requirements[identifier]
        require(requirement.get("software_status") == "EXECUTED_NATIVE_GREEN",
                "CONVERGENCE_FRONT_REQUIREMENT_NOT_EXECUTED:" + path)
        for receipt in requirement.get("execution_receipts", []):
            count = receipt.get("executed_cases")
            require(type(count) is int and count > 0, "CONVERGENCE_FRONT_GUARD_NOT_EXECUTED:" + path)
            executions[receipt["node"]] = count
    require(all(node in executions for node in row["guard_nodes"]),
            "CONVERGENCE_FRONT_GUARD_RECEIPT_MISSING:" + path)
    return {"path": path, "frozen": frozen, "candidate": current,
            "requirement_ids": row["requirement_ids"], "guard_nodes": row["guard_nodes"]}


def verify(root: Path, junit: Path, governed: Path) -> dict:
    root = root.resolve(strict=True)
    matrix_path = root / MATRIX
    matrix_data = load(matrix_path, raw=True)
    matrix_blob = hashlib.sha1(b"blob " + str(len(matrix_data)).encode() + b"\0" + matrix_data).hexdigest()
    matrix = json.loads(matrix_data, object_pairs_hook=_pairs)
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
    try:
        junit_receipt = convergence.capture_junit(junit)
    except (convergence.ConvergenceError, OSError) as error:
        raise AuditGateError("JUNIT_INPUT_INVALID") from error
    tree = ET.fromstring(junit_receipt.data)
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
    sha = git(root, "rev-parse", "HEAD")
    manifest = convergence.INPUT_ROOT + "/" + convergence.MANIFEST
    if git(root, "ls-tree", sha, "--", manifest):
        _original_convergence_authority(root, sha, matrix_blob)
    successor = None
    definitions = {}
    original_nodes = set()
    used_successions = {}
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
                if path not in definitions:
                    parsed = ast.parse(git(root, "show", sha + ":" + path), filename=path)
                    functions = [item.name for item in parsed.body
                                 if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    definitions[path] = {name: functions.count(name) for name in set(functions)}
                declared = definitions[path].get(function, 0)
                require(declared <= 1, "DUPLICATE_REGRESSION_DEFINITION:" + node["node"])
                selected_node = node["node"]
                if declared == 0:
                    specification = convergence.prior_successions.SUCCESSORS.get(node["node"])
                    require(specification is not None, "REGRESSION_SOURCE_DEFINITION_MISSING:" + node["node"])
                    if successor is None:
                        successor = _verified_successor(root, junit_receipt, proof, sha)
                    bindings = [item for item in successor.get("prior_regression_successions", [])
                                if item.get("original_node") == node["node"]]
                    require(len(bindings) == 1, "REGRESSION_SUCCESSION_MISSING_OR_DUPLICATE")
                    binding = bindings[0]
                    selected_node = specification["successor_node"]
                    require(binding.get("preservation") == "TYPED_SUCCESSOR"
                            and binding.get("current_node") == selected_node
                            and binding.get("status") == "EXECUTED_NATIVE_GREEN",
                            "REGRESSION_SUCCESSION_NOT_VERIFIED")
                    selected_path, selected_function = _safe_node(selected_node)
                    anchor = binding.get("current_anchor", {})
                    require(selected_path == path and anchor.get("path") == path
                            and anchor.get("function") == selected_function
                            and {key: anchor.get(key) for key in ("blob", "git_mode")}
                                == _source_record(root, sha, path), "REGRESSION_SUCCESSION_SOURCE_MISMATCH")
                    count = actual.get((classname, selected_function), 0)
                    require(type(binding.get("minimum_cases")) is int
                            and binding["minimum_cases"] >= minimum
                            and type(binding.get("executed_cases")) is int
                            and binding["executed_cases"] == count
                            and count >= binding["minimum_cases"], "REGRESSION_SUCCESSION_CASE_MISMATCH")
                    used_successions[node["node"]] = binding
                require(count >= minimum, "REGRESSION_NOT_EXECUTED:"+selected_node)
                original_nodes.add(node["node"])
                tested_nodes.add(selected_node)

    streams = matrix.get("workstreams", [])
    require(isinstance(streams, list) and {item.get("id") for item in streams} == set("ABCDEFG") and
            len(streams) == 7, "INCOMPLETE_WORKSTREAM_PROVENANCE")
    evolved_fronts = []
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
            frozen = _source_record(root, head, path)
            current = _source_record(root, sha, path)
            if frozen != current:
                if successor is None:
                    successor = _verified_successor(root, junit_receipt, proof, sha)
                evolved_fronts.append({"workstream": stream["id"],
                    **_guarded_front_path(root, path, frozen, current, successor)})
            source = root / path
            info = source.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "FROZEN_DISK_ALIAS")
            for parent in source.parents:
                if parent == root: break
                require(not parent.is_symlink(), "FROZEN_DISK_PARENT_ALIAS")
            require(git(root, "hash-object", "--no-filters", "--", path) == current["blob"],
                    "FROZEN_DISK_BYTE_MISMATCH:"+path)
            require(stat.S_IMODE(info.st_mode) == (0o755 if current["git_mode"] == "100755" else 0o644),
                    "FROZEN_DISK_MODE_MISMATCH")
    require(git(root, "rev-parse", "HEAD") == sha, "CANDIDATE_CHANGED_DURING_VERIFICATION")
    candidate_tree = git(root, "rev-parse", sha+"^{tree}")
    return {"schema": "rc6.issue465-audit-gate.v1", "status": "GREEN",
            "candidate_sha": sha, "candidate_tree": candidate_tree,
            "matrix_sha256": hashlib.sha256(matrix_data).hexdigest(),
            "original_report_sha256": authority["original_report_sha256"],
            "governed_executed": proof["executed"], "required_test_functions_executed": len(tested_nodes),
            "original_test_bindings_preserved": len(original_nodes),
            "prior_regression_successions": [used_successions[node] for node in sorted(used_successions)],
            "findings": {row["id"]: row["state"] for row in rows},
            "workstream_heads": {stream["id"]: stream["head"] for stream in streams},
            "front_preservation": "GUARDED_CONVERGENCE_SUCCESSOR" if successor else "EXACT_FROZEN_FRONTS",
            "evolved_fronts": evolved_fronts,
            "convergence_baseline": CONVERGENCE_BASELINE if successor else None,
            "convergence_junit_sha256": successor["test_execution"]["junit_sha256"] if successor else None,
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
