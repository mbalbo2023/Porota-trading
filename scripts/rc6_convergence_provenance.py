#!/usr/bin/env python3
"""Reconcile the complete handoff against Git and executed native guards.

The final SHA/tree are emitted after freeze, never committed as a circular
self-attestation. This report is evidence, not permission to merge or deploy.
"""
from __future__ import annotations

import argparse
import ast
import csv
from dataclasses import dataclass
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import xml.etree.ElementTree as ET

try:
    from scripts import rc6_prior_regression_successors as prior_successions
except ModuleNotFoundError as error:
    if error.name != "scripts":
        raise
    import rc6_prior_regression_successors as prior_successions

INPUT_ROOT = "docs/audits/convergence"
MANIFEST = "INPUT_MANIFEST_RC6_CONVERGENCIA.json"
REGISTRY = "REGISTRO_CONVERGENCIA_RC6.csv"
CLOSURE = "REQUIREMENT_CLOSURE_MATRIX.json"
SCENARIOS = "ORIGINAL_SCENARIOS_469.json"
SOURCE_PRS = {447, 448, 449, 450, 451, 453, 454, 455, 456, 457, 459, 461, 463, 466, 470}
REQUIREMENTS = {f"U{i:02d}" for i in range(1, 30)} | {f"AUD-468-{i:02d}" for i in range(1, 22)} | {f"UX470-I{i:02d}" for i in range(1, 6)}
PATH_GUARD_DERIVATION = "sorted-union-of-required-test-nodes.v1"
GUARD_NODE_PATTERN = re.compile(r"(?:tests/)?test_[A-Za-z0-9_]+\.py::test_[A-Za-z0-9_]+")
FRONT_VARIANTS = ({f"F01-{i:02d}" for i in range(1, 36)} | {f"F02-{i:02d}" for i in range(1, 21)}
                  | {f"F03F05-{i:02d}" for i in range(1, 36)})
RESTORED_CONTROLS = {"H_LEGACY_ASSET_CLASS_COLLISION", "M_FIXED_INCOME_ANNUAL_LABEL",
    "M_MAIN_SAMPLE_CADENCE", "S_CRASH_AFTER_VERSION_INSERT", "S_HEARTBEAT_NO_PROGRESS",
    "U_IOL_ARCHIVED_LIQUIDITY"}
PRESERVED_TESTS_344 = {"tests/" + name for name in (
    "test_candle_archive_v17.py", "test_dashboard_daily_responsive_hf6.py",
    "test_dashboard_decision_evidence_rc6.py", "test_dashboard_live_policy_hf2.py",
    "test_dashboard_paper_v1634.py", "test_dashboard_session.py", "test_dashboard_v17_rc3.py",
    "test_history_freshness_metrics_rc5.py", "test_hotfix_rc3_hf6.py", "test_rc4_acceptance.py",
    "test_rc4_hf2_consolidation.py", "test_rc6_table_headers_visible_sticky.py")}
TEST_344_ADAPTATIONS = {
    "tests/test_candle_archive_v17.py": (b"SINID: HISTORY_MARKET_IDENTITY_MISSING", b"SINID: HISTORY_FULL_IDENTITY_MISSING"),
    "tests/test_rc4_acceptance.py": (b"'2026-09-02T20:00:00+00:00',{})", b"'2026-09-02T20:00:00+00:00',{'currency':'ARS'})"),
}
DENIED_ARTIFACTS = {11315198085, 11317509383, 11293625514}
DISCOVERED_CONVERGENCE_FINDINGS = frozenset({
    "NEW_SOURCE_SQLITE_SIDECAR_METADATA_MUTATION", "NEW_CANONICAL_LIVE_FILE_QUOTA_DRIFT",
    "NEW_ARCHIVE_DIRECTORY_ATIME_MUTATION",
    "NEW_SIGNED_ZERO_SUBTREE_COLLAPSE", "NEW_GIT_REPLACEMENT_OBJECT_AUTHORITY",
    "NEW_EXIT_RETAINED_RECEIPT_ACTIVATION", "NEW_HISTORY_EXACT_REVISION_LOOKUP",
    "NEW_HISTORY_SEMANTIC_METADATA_REVISION", "NEW_INSTALLED_DISTRIBUTION_METADATA_DUPLICATE",
    "NEW_LEGACY_INIT_PRE_WAL_MUTATION", "NEW_OBSERVER_HISTORY_DIAGNOSTIC",
    "NEW_PREOPEN_AUD06", "NEW_PREOPEN_PROCESSING_DEADLINE", "NEW_PREOPEN_U24",
    "NEW_PROBE_SCRATCH_SAMPLER_RACE", "NEW_RUNTIME_DISK_SCRATCH_QUOTA_CUSTODY",
    "NEW_SOURCE_GIT_MODE_EVOLUTION", "NEW_SUPERSEDED_AUDIT_FRONT_FREEZE",
})
PRIOR_AUDIT_MATRIX = "docs/audits/ISSUE465_REAUDIT.json"
ORIGINAL_DIGESTS = {
    MANIFEST: "3295e3d001e6a28e21fb227f5aed98a5119337d68abb5c2c1b50ee8e528d1ec4",
    REGISTRY: "9c1432c006516d23418bf059adf8c4f8d37e869186cb0ef15e3bb6768c86df21",
    "ORDEN_UNICA_CODEX_RC6_CONVERGENCIA_468_469_470.md": "d41e404d893a2fedfb10017b5f5732282f6e695220305e9b9cf1049b7c310731",
    "ISSUE469_REAUDITORIA_INDEPENDIENTE_466_C27DFD9.md": "270630106697702935da3aeeca7fd936afd48471fb746c2cd56a9089b63a25fb",
    "ORIGINAL_RA_A_F01_F02.md": "74d2d852c9c62754cd185dfb41e86b144c450c31b155d955348e16dce47c84c9",
    "ORIGINAL_RA_A_F03_F05.md": "289f7a89ce48e56a7c3770ece9195d9c58f9d1ea9d23a2004552621c2d7bd87b",
}


class ConvergenceError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise ConvergenceError(reason)


def git(root, *arguments, binary=False):
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(root), *arguments], check=False,
                            capture_output=True, timeout=90)
    require(result.returncode == 0, "GIT_SOURCE_UNAVAILABLE:" + arguments[0])
    return result.stdout if binary else result.stdout.decode().strip()


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, "DUPLICATE_JSON_KEY:" + key)
        result[key] = value
    return result


def read_json(data):
    require(len(data) <= 8 * 1024**2, "CONVERGENCE_INPUT_TOO_LARGE")
    return json.loads(data, object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ConvergenceError("INVALID_JSON_CONSTANT")))


def path_guard_nodes(proof, row):
    """Read historical inline guards or derive their exact union without loss.

    The opt-in discriminator applies to every path: inline shadow values are
    forbidden and the complete requirements remain the sole node authority.
    Historical inline v1 receipts do not need a requirement.test_nodes field.
    This representation check does not attest source or test execution.
    """
    require(isinstance(proof, dict) and proof.get("schema") == "rc6.final-input-provenance.v1"
            and isinstance(row, dict), "PATH_GUARD_PROOF_INVALID")
    derived = "path_guard_derivation" in proof
    require(not derived or proof["path_guard_derivation"] == PATH_GUARD_DERIVATION,
            "PATH_GUARD_DERIVATION_INVALID")
    requirements = proof.get("requirements")
    require(isinstance(requirements, list) and bool(requirements), "PATH_GUARD_REQUIREMENTS_MISSING")
    by_id = {}
    for requirement in requirements:
        require(isinstance(requirement, dict)
                and isinstance(requirement.get("id"), str) and requirement["id"] in REQUIREMENTS,
                "PATH_GUARD_REQUIREMENT_INVALID")
        identifier = requirement["id"]
        require(identifier not in by_id, "PATH_GUARD_REQUIREMENT_DUPLICATE:" + identifier)
        if derived:
            nodes = requirement.get("test_nodes")
            require(isinstance(nodes, list) and bool(nodes)
                    and all(isinstance(node, str) and GUARD_NODE_PATTERN.fullmatch(node) for node in nodes),
                    "PATH_GUARD_TEST_NODES_INVALID:" + identifier)
            require(len(set(nodes)) == len(nodes), "PATH_GUARD_TEST_NODES_DUPLICATE:" + identifier)
        by_id[identifier] = requirement

    def requirement_ids(path_row):
        require(isinstance(path_row, dict), "PATH_GUARD_PATH_INVALID")
        identifiers = path_row.get("requirement_ids")
        require(isinstance(identifiers, list)
                and all(isinstance(identifier, str) and identifier in by_id for identifier in identifiers),
                "PATH_GUARD_REFERENCE_INVALID")
        require(len(set(identifiers)) == len(identifiers), "PATH_GUARD_REFERENCE_DUPLICATE")
        return identifiers

    if derived:
        paths = proof.get("paths")
        require(isinstance(paths, list), "PATH_GUARD_PATHS_INVALID")
        for path_row in paths:
            requirement_ids(path_row)
            require("guard_nodes" not in path_row, "PATH_GUARD_SHADOW_INLINE")
        require("guard_nodes" not in row, "PATH_GUARD_SHADOW_INLINE")
        return sorted({node for identifier in requirement_ids(row)
                       for node in by_id[identifier]["test_nodes"]})
    requirement_ids(row)
    nodes = row.get("guard_nodes")
    require(isinstance(nodes, list)
            and all(isinstance(node, str) and GUARD_NODE_PATTERN.fullmatch(node) for node in nodes),
            "PATH_GUARD_INLINE_INVALID")
    require(len(set(nodes)) == len(nodes), "PATH_GUARD_INLINE_DUPLICATE")
    return list(nodes)


def tree(root, revision):
    result = {}
    for record in git(root, "ls-tree", "-r", "-z", "--full-tree", revision, binary=True).split(b"\0"):
        if not record:
            continue
        meta, raw_path = record.split(b"\t", 1)
        mode, kind, blob = meta.decode().split()
        path = raw_path.decode()
        require(path not in result, "DUPLICATE_GIT_PATH")
        require(kind == "blob" and mode in {"100644", "100755"}, "UNSUPPORTED_GIT_SOURCE_TYPE:" + path)
        result[path] = {"blob": blob, "git_mode": mode}
    require(bool(result), "EMPTY_SOURCE_TREE")
    return result


def committed_input(root, sha, filename):
    return git(root, "show", sha + ":" + INPUT_ROOT + "/" + filename, binary=True)


def prior_frozen_front_inventory(root, anchor, *, fetch_source_refs=False):
    """Recover historical frozen SHA objects, independently of live branches."""
    matrix = read_json(git(root, "show", anchor + ":" + PRIOR_AUDIT_MATRIX, binary=True))
    streams = matrix.get("workstreams", [])
    require(isinstance(streams, list) and len(streams) == 7
            and {item.get("id") for item in streams} == set("ABCDEFG"), "PRIOR_FRONT_SET_INVALID")
    baseline, records, seen_paths = tree(root, anchor), [], set()
    for stream in streams:
        head, expected_tree = stream.get("head"), stream.get("tree")
        require(isinstance(head, str) and re.fullmatch(r"[0-9a-f]{40}", head)
                and isinstance(expected_tree, str) and re.fullmatch(r"[0-9a-f]{40}", expected_tree),
                "PRIOR_FRONT_REF_INVALID")
        if fetch_source_refs:
            reference = "refs/porota/convergence-prior-front/" + stream["id"]
            git(root, "fetch", "--no-tags", "--update-shallow", "origin", head + ":" + reference)
            require(git(root, "rev-parse", reference) == head, "PRIOR_FRONT_FETCH_MISMATCH")
        require(git(root, "rev-parse", head + "^{tree}") == expected_tree, "PRIOR_FRONT_TREE_MISMATCH")
        frozen = tree(root, head)
        paths = stream.get("paths")
        require(isinstance(paths, list) and bool(paths), "PRIOR_FRONT_SCOPE_INVALID")
        files = []
        for path in paths:
            require(isinstance(path, str) and path not in seen_paths and path in frozen
                    and frozen[path] == baseline.get(path), "PRIOR_FRONT_NOT_PRESERVED_AT_ANCHOR")
            seen_paths.add(path)
            files.append({"path": path, **frozen[path]})
        records.append({"id": stream["id"], "head_sha": head, "tree_sha": expected_tree,
                        "anchor_sha": anchor, "files": files, "promotion_authority": False})
    require(len(seen_paths) == 45, "PRIOR_FRONT_SCOPE_CARDINALITY")
    return records


def original_front_variants(f01_f02, f03_f05):
    result = {}
    sections = [("F01", f01_f02, "## 6. Variantes obligatorias §7 — F-01", 35),
                ("F02", f01_f02, "## 7. Variantes obligatorias §8 — F-02", 20),
                ("F03F05", f03_f05, "## Matriz adversarial ejecutada", 35)]
    for prefix, content, heading, expected in sections:
        require(content.count(heading) == 1, "ORIGINAL_FRONT_SECTION_CHANGED")
        section = content.split(heading, 1)[1].split("\n## ", 1)[0]
        rows = []
        for line in section.splitlines():
            if not line.startswith("| "):
                continue
            columns = [value.strip() for value in line.strip("|").split("|")]
            if prefix != "F03F05" and not columns[0].isdigit():
                continue
            if prefix == "F03F05" and columns[0] == "Escenario":
                continue
            rows.append(columns)
        require(len(rows) == expected, "ORIGINAL_FRONT_CARDINALITY_CHANGED:" + prefix)
        for index, columns in enumerate(rows, 1):
            require(len(columns) == (7 if prefix == "F03F05" else 6), "ORIGINAL_FRONT_TABLE_CHANGED")
            if prefix != "F03F05":
                require(columns[0] == str(index), "ORIGINAL_FRONT_ORDER_CHANGED")
            result[f"{prefix}-{index:02d}"] = columns
    require(set(result) == FRONT_VARIANTS, "ORIGINAL_FRONT_IDS_CHANGED")
    return result


@dataclass(frozen=True, slots=True)
class JunitReceipt:
    """One immutable bounded byte snapshot shared by the native validators."""
    data: bytes

    def __post_init__(self):
        require(type(self.data) is bytes and 0 < len(self.data) <= 16 * 1024**2,
                "JUNIT_INPUT_INVALID")

    @property
    def sha256(self):
        return hashlib.sha256(self.data).hexdigest()


def capture_junit(junit):
    if type(junit) is JunitReceipt:
        return junit
    require(isinstance(junit, Path), "JUNIT_INPUT_INVALID")
    try:
        descriptor = os.open(junit, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise ConvergenceError("JUNIT_INPUT_INVALID") from error
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 < before.st_size <= 16 * 1024**2, "JUNIT_INPUT_INVALID")
        parts, size = [], 0
        while True:
            part = os.read(descriptor, min(64 * 1024, 16 * 1024**2 + 1 - size))
            if not part:
                break
            parts.append(part)
            size += len(part)
            require(size <= 16 * 1024**2, "JUNIT_INPUT_INVALID")
        after, location = os.fstat(descriptor), junit.lstat()
        fields = ("st_dev", "st_ino", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns")
        require(all(getattr(before, field) == getattr(after, field) == getattr(location, field)
                    for field in fields) and size == before.st_size, "JUNIT_CHANGED_DURING_CAPTURE")
        return JunitReceipt(b"".join(parts))
    finally:
        os.close(descriptor)


def governed_junit_cases(junit):
    """Use only direct cases of one governed suite; reject detached/nested cases."""
    receipt = capture_junit(junit)
    document = ET.fromstring(receipt.data)
    require(document.tag in {"testsuites", "testsuite"}, "JUNIT_GOVERNED_SUITE_SHAPE")
    suites = list(document.iter("testsuite"))
    require(len(suites) == 1, "JUNIT_GOVERNED_SUITE_SHAPE")
    suite = suites[0]
    require(document is suite or list(document) == [suite], "JUNIT_GOVERNED_SUITE_SHAPE")
    cases = list(suite.findall("testcase"))
    require(list(document.iter("testcase")) == cases, "JUNIT_GOVERNED_SUITE_SHAPE")
    for tag in ("failure", "error", "skipped"):
        require(list(document.iter(tag)) == [outcome for case in cases for outcome in case.iter(tag)],
                "JUNIT_GOVERNED_SUITE_SHAPE")
    return suite, cases


def executed_cases(junit):
    suite, cases = governed_junit_cases(junit)
    for field in ("tests", "failures", "errors", "skipped"):
        value = suite.get(field, "")
        require(re.fullmatch(r"[0-9]+", value) is not None, "JUNIT_SUMMARY_INVALID")
        require(int(value) == (len(cases) if field == "tests" else 0), "JUNIT_SUMMARY_MISMATCH")
    require(bool(cases), "JUNIT_EMPTY")
    identities, nodes = set(), {}
    for case in cases:
        require(all(case.find(".//" + tag) is None for tag in ("failure", "error", "skipped")), "JUNIT_NONPASS")
        name, classname = case.get("name", ""), case.get("classname", "")
        require((classname, name) not in identities, "JUNIT_DUPLICATE_CASE")
        identities.add((classname, name))
        node = classname.replace(".", "/") + ".py::" + name.split("[", 1)[0]
        nodes[node] = nodes.get(node, 0) + 1
    return nodes, len(cases)


def final_material_receipt_binding(root, candidate_sha, candidate_tree, proof, governed, junit):
    """Join final eligibility to the actual approved governed byte capture.

    This source/receipt barrier runs before building. Image execution and
    external artifact identity remain independent later gates.
    """
    require(type(proof) is dict and proof.get("schema") == "rc6.final-input-provenance.v1"
            and proof.get("candidate_sha") == candidate_sha
            and proof.get("candidate_tree") == candidate_tree
            and proof.get("software_status") == "EXECUTED_NATIVE_GREEN",
            "FINAL_MATERIAL_CANDIDATE_BINDING_INVALID")
    require(proof.get("final_candidate_eligible") is True
            and proof.get("material_programming_gates_closed") is True
            and type(proof.get("pending_material_programming_gates")) is list
            and not proof["pending_material_programming_gates"],
            "FINAL_MATERIAL_PROGRAMMING_GATES_NOT_CLOSED")
    receipt = capture_junit(junit)
    _nodes, count = executed_cases(receipt)
    execution = proof.get("test_execution")
    require(type(execution) is dict and execution.get("junit_sha256") == receipt.sha256
            and type(execution.get("executed_unique_cases")) is int
            and execution["executed_unique_cases"] == count,
            "FINAL_MATERIAL_JUNIT_BINDING_INVALID")
    require(type(governed) is dict and type(governed.get("schema_version")) is int
            and governed["schema_version"] == 1 and governed.get("status") == "GREEN"
            and governed.get("source_unchanged") is True
            and governed.get("candidate_sha") == candidate_sha
            and governed.get("candidate_tree") == candidate_tree
            and governed.get("scope") == "repository-root automatic pytest discovery"
            and governed.get("junit_sha256") == receipt.sha256
            and type(governed.get("junit_bytes")) is int
            and governed["junit_bytes"] == len(receipt.data)
            and all(type(governed.get(name)) is int and governed[name] == count
                    for name in ("discovered", "executed"))
            and all(type(governed.get(name)) is int and governed[name] == 0
                    for name in ("pytest_exit_code", "failures", "errors", "skipped", "xfail")),
            "FINAL_MATERIAL_GOVERNED_BINDING_INVALID")
    raw_manifest = committed_input(root, candidate_sha, MANIFEST)
    require(hashlib.sha256(raw_manifest).hexdigest() == ORIGINAL_DIGESTS[MANIFEST],
            "ORIGINAL_HANDOFF_BYTES_CHANGED:" + MANIFEST)
    sources = read_json(raw_manifest)["sources"]
    original_sha = next(row["head_sha"] for row in sources if row["pr"] == 466)
    original_matrix = read_json(git(root, "show", original_sha + ":" + PRIOR_AUDIT_MATRIX, binary=True))
    require(type(governed.get("exclusions")) is list
            and all(type(item) is str for item in governed["exclusions"])
            and governed["exclusions"] == original_matrix["governed_exclusions"],
            "FINAL_MATERIAL_EXCLUSION_POLICY_DRIFT")
    return {"status": "GREEN", "junit_sha256": receipt.sha256,
            "junit_bytes": len(receipt.data), "executed_unique_cases": count,
            "scope": "PREBUILD_SOURCE_AND_GOVERNED_RECEIPT_BINDING_ONLY"}


def guard_rows(rows, expected, final_tree, executed, declared_guards):
    require(isinstance(rows, list) and len(rows) == len(expected), "CLOSURE_CARDINALITY")
    require({row.get("id") for row in rows} == expected, "CLOSURE_IDS_MISSING_OR_DUPLICATE")
    result = []
    for row in rows:
        for field in ("rca", "fix", "native_caller", "remaining_uncertainty"):
            require(isinstance(row.get(field), str) and bool(row[field].strip()), "INCOMPLETE_CLOSURE:" + row["id"])
        paths = row.get("code_paths")
        require(isinstance(paths, list) and bool(paths) and all(path in final_tree for path in paths),
                "CLOSURE_CODE_PATH_INVALID:" + row["id"])
        nodes = row.get("test_nodes")
        require(isinstance(nodes, list) and bool(nodes) and len(set(nodes)) == len(nodes),
                "CLOSURE_GUARDS_MISSING:" + row["id"])
        receipts = []
        for node in nodes:
            require(isinstance(node, str) and GUARD_NODE_PATTERN.fullmatch(node),
                    "CLOSURE_GUARD_INVALID")
            require(node.split("::")[0] in final_tree, "CLOSURE_GUARD_SOURCE_MISSING")
            require(node in declared_guards, "CLOSURE_GUARD_DECLARATION_MISSING:" + node)
            count = None if executed is None else executed.get(node, 0)
            require(count is None or count > 0, "CLOSURE_GUARD_NOT_EXECUTED:" + node)
            receipts.append({"node": node, "executed_cases": count})
        result.append({**row, "execution_receipts": receipts,
                       "software_status": "NOT_EXECUTED" if executed is None else "EXECUTED_NATIVE_GREEN"})
    return result


def preserved_legacy_tests(root, candidate_sha, source_sha, executed):
    result = []
    for filename in sorted(PRESERVED_TESTS_344):
        original = git(root, "show", source_sha + ":" + filename, binary=True)
        current = git(root, "show", candidate_sha + ":" + filename, binary=True)
        adaptation = TEST_344_ADAPTATIONS.get(filename)
        if adaptation:
            old, new = adaptation
            require(original.count(old) == 1, "LEGACY_344_BASELINE_DRIFT:" + filename)
            allowed = original.replace(old, new)
        else:
            allowed = original
        require(current == allowed, "LEGACY_344_UNREVIEWED_DRIFT:" + filename)
        definitions = ast.parse(current, filename=filename).body
        nodes = [filename + "::" + item.name for item in definitions
                 if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_")]
        require(bool(nodes), "LEGACY_344_GUARDS_MISSING")
        require(executed is None or all(executed.get(node, 0) > 0 for node in nodes), "LEGACY_344_NOT_EXECUTED")
        result.append({"path": filename, "source_sha": source_sha,
            "original_sha256": hashlib.sha256(original).hexdigest(), "final_sha256": hashlib.sha256(current).hexdigest(),
            "preservation": "EXACT_BYTES" if not adaptation else "EXPLICIT_SINGLE_ADAPTATION_ALL_OTHER_BYTES_PRESERVED",
            "test_nodes": nodes, "executed_cases": None if executed is None else sum(executed[node] for node in nodes)})
    return result


def verify(root, candidate_sha, junit=None, *, fetch_source_refs=False):
    root = root.resolve(strict=True)
    require(not git(root, "for-each-ref", "--format=%(refname)", "refs/replace/"), "GIT_REPLACE_REFS_FORBIDDEN")
    require(re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is not None, "CANDIDATE_SHA_INVALID")
    require(git(root, "rev-parse", "HEAD") == candidate_sha, "CANDIDATE_CHECKOUT_MISMATCH")
    require(not git(root, "status", "--porcelain", "--untracked-files=no"), "TRACKED_CHECKOUT_CHANGED")
    candidate_tree = git(root, "rev-parse", candidate_sha + "^{tree}")
    junit_receipt = capture_junit(junit) if junit is not None else None
    final_tree = tree(root, candidate_sha)
    digest_index = read_json(committed_input(root, candidate_sha, "ORIGINAL_INPUT_DIGESTS.json"))
    require(set(digest_index["files"]) == set(ORIGINAL_DIGESTS), "ORIGINAL_INPUT_SET_CHANGED")
    for name, expected in digest_index["files"].items():
        data = committed_input(root, candidate_sha, name)
        require(len(data) == expected["bytes"] and hashlib.sha256(data).hexdigest() == expected["sha256"] == ORIGINAL_DIGESTS[name],
                "ORIGINAL_HANDOFF_BYTES_CHANGED:" + name)
    manifest = read_json(committed_input(root, candidate_sha, MANIFEST))
    sources = manifest["sources"]
    require(len(sources) == 15 and {row["pr"] for row in sources} == SOURCE_PRS, "SOURCE_PR_SET_CHANGED")
    if fetch_source_refs:
        for row in sources:
            reference = "refs/porota/convergence-source/" + str(row["pr"])
            git(root, "fetch", "--no-tags", "--update-shallow", "origin", "refs/pull/" + str(row["pr"]) + "/head:" + reference)
            require(git(root, "rev-parse", reference) == row["head_sha"], "SOURCE_PR_HEAD_CHANGED:" + str(row["pr"]))
            base_reference = "refs/porota/convergence-source-base/" + str(row["pr"])
            git(root, "fetch", "--no-tags", "--update-shallow", "origin", row["base_sha"] + ":" + base_reference)
            require(git(root, "rev-parse", base_reference) == row["base_sha"], "SOURCE_BASE_SHA_CHANGED:" + str(row["pr"]))
    # Recover referenced historical objects before any legacy/source consumer.
    prior_fronts = prior_frozen_front_inventory(root,
        next(row["head_sha"] for row in sources if row["pr"] == 466),
        fetch_source_refs=fetch_source_refs)
    registry = list(csv.DictReader(io.StringIO(committed_input(root, candidate_sha, REGISTRY).decode())))
    require(len(registry) == 55 and {row["id"] for row in registry} == REQUIREMENTS, "ORIGINAL_REGISTRY_CHANGED")
    original_scenarios = read_json(committed_input(root, candidate_sha, SCENARIOS))["rows"]
    scenario_ids = {f"R{i:02d}" for i in range(1, 81)}
    require(len(original_scenarios) == 80 and {row["id"] for row in original_scenarios} == scenario_ids,
            "ORIGINAL_SCENARIOS_CHANGED")
    parsed_scenarios = []
    text = committed_input(root, candidate_sha, "ISSUE469_REAUDITORIA_INDEPENDIENTE_466_C27DFD9.md").decode()
    for line in text.splitlines():
        if not re.match(r"^\| R\d{2}\s", line):
            continue
        columns = [value.strip() for value in line.strip("|").split("|")]
        require(len(columns) == 7, "ORIGINAL_SCENARIO_TABLE_INVALID")
        identifier, attack = columns[0].split(" ", 1)
        parsed_scenarios.append(dict(id=identifier, attack=attack, expected=columns[1], original_observed=columns[2],
                                     original_code_path=columns[3], original_test=columns[4], original_gap=columns[5],
                                     original_verdict=columns[6]))
    require(original_scenarios == parsed_scenarios, "ORIGINAL_SCENARIOS_REBOUND")
    matrix = read_json(committed_input(root, candidate_sha, CLOSURE))
    require(matrix.get("schema") == "rc6.convergence-closure.v1", "CLOSURE_SCHEMA_INVALID")
    for field, originals, binding, reason in (
        ("requirements", registry, "original_requirement", "ORIGINAL_CLOSURE_REQUIREMENT_REBOUND"),
        ("scenarios", original_scenarios, "original_scenario", "ORIGINAL_CLOSURE_SCENARIO_REBOUND"),
    ):
        original_by_id = {row["id"]: row for row in originals}
        require(isinstance(matrix.get(field), list), reason)
        for row in matrix[field]:
            require(isinstance(row, dict) and row.get("id") in original_by_id
                    and row.get(binding) == original_by_id[row["id"]], reason)
    original_front = original_front_variants(
        committed_input(root, candidate_sha, "ORIGINAL_RA_A_F01_F02.md").decode(),
        committed_input(root, candidate_sha, "ORIGINAL_RA_A_F03_F05.md").decode())
    front_rows = matrix.get("front_variants", [])
    for row in front_rows:
        require(row.get("original_columns") == original_front.get(row.get("id")), "ORIGINAL_FRONT_CLAUSE_REBOUND")
    declared_guards = set()
    restored_rows = matrix.get("restored_controls", [])
    additional_rows = matrix.get("additional_findings", [])
    require(isinstance(additional_rows, list), "ADDITIONAL_FINDINGS_INVALID")
    for row in additional_rows:
        require(isinstance(row, dict) and isinstance(row.get("id"), str)
                and re.fullmatch(r"[A-Z][A-Z0-9_-]+", row["id"]) is not None
                and isinstance(row.get("linked_requirement_ids"), list) and bool(row["linked_requirement_ids"])
                and set(row["linked_requirement_ids"]) <= REQUIREMENTS, "ADDITIONAL_FINDINGS_INVALID")
    require(DISCOVERED_CONVERGENCE_FINDINGS <= {row["id"] for row in additional_rows},
            "DISCOVERED_CONVERGENCE_FINDING_OMITTED")
    guard_files = {node.split("::")[0] for row in matrix["requirements"] + matrix["scenarios"] + front_rows + restored_rows + additional_rows
                   for node in row.get("test_nodes", []) if isinstance(node, str)}
    for filename in guard_files:
        require(filename in final_tree, "CLOSURE_GUARD_SOURCE_MISSING")
        source = git(root, "show", candidate_sha + ":" + filename, binary=True)
        require(len(source) <= 4 * 1024**2, "CLOSURE_GUARD_SOURCE_TOO_LARGE")
        parsed = ast.parse(source, filename=filename)
        declared_guards.update(filename + "::" + node.name for node in parsed.body
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    executed, count = executed_cases(junit_receipt) if junit_receipt is not None else (None, None)
    requirements = guard_rows(matrix["requirements"], REQUIREMENTS, final_tree, executed, declared_guards)
    scenarios = guard_rows(matrix["scenarios"], scenario_ids, final_tree, executed, declared_guards)
    front_variants = guard_rows(front_rows, FRONT_VARIANTS, final_tree, executed, declared_guards)
    restored_controls = guard_rows(restored_rows, RESTORED_CONTROLS, final_tree, executed, declared_guards)
    additional_findings = guard_rows(additional_rows, {row["id"] for row in additional_rows}, final_tree, executed, declared_guards)
    pending_material = [{"id": row["id"], "disposition": row["disposition"],
                         "remaining_uncertainty": row["remaining_uncertainty"]}
                        for row in requirements
                        if row.get("disposition") == "MATERIAL_RESOURCE_GATE_PENDING_REMEDIATION"]
    legacy_tests = preserved_legacy_tests(root, candidate_sha, next(row["head_sha"] for row in sources if row["pr"] == 466), executed)
    source_trees, source_reports, source_deltas = {}, [], set()
    product_sha = manifest["product"]["sha"]
    source_trees["product"] = tree(root, product_sha)
    require(git(root, "rev-parse", product_sha + "^{tree}") == manifest["product"]["tree"], "PRODUCT_TREE_CHANGED")
    for row in sources:
        sha = row["head_sha"]
        source_trees[str(row["pr"])] = tree(root, sha)
        base_tree = tree(root, row["base_sha"])
        changed = sorted(path for path in set(base_tree) | set(source_trees[str(row["pr"])])
                         if base_tree.get(path) != source_trees[str(row["pr"])].get(path))
        delta = []
        for path in changed:
            original = source_trees[str(row["pr"])].get(path)
            final = final_tree.get(path)
            if original:
                require(final is not None, "SOURCE_DELTA_PATH_LOST:" + str(row["pr"]) + ":" + path)
                source_deltas.add(path)
            delta.append({"path": path, "source_blob": (original or {}).get("blob"),
                          "final_blob": (final or {}).get("blob"),
                          "source_git_mode": (original or {}).get("git_mode"),
                          "final_git_mode": (final or {}).get("git_mode"),
                          "comparison": "EXACT_SOURCE_BYTES" if original == final else
                              "EVOLVED_REQUIRES_GUARDED_RECONCILIATION" if original else "SOURCE_REMOVAL_HISTORY"})
        source_reports.append({**row, "tree_sha": git(root, "rev-parse", sha + "^{tree}"),
                               "base_tree_sha": git(root, "rev-parse", row["base_sha"] + "^{tree}"),
                               "actual_delta_path_count": len(changed), "delta_paths": delta,
                               "final_promotion_authority": False})
    original_sha = next(row["head_sha"] for row in sources if row["pr"] == 466)
    original_matrix = read_json(git(root, "show", original_sha + ":" + PRIOR_AUDIT_MATRIX, binary=True))
    prior_paths = {node.split("::")[0] for node in prior_successions.SUCCESSORS}
    original_records, current_records = {}, {}
    for path in sorted(prior_paths):
        require(path in source_trees["466"] and path in final_tree, "PRIOR_REGRESSION_SOURCE_MISSING:" + path)
        original_records[path] = {**source_trees["466"][path],
            "data": git(root, "show", original_sha + ":" + path, binary=True)}
        current_records[path] = {**final_tree[path],
            "data": git(root, "show", candidate_sha + ":" + path, binary=True)}
    try:
        regression_successions = prior_successions.verify_successions(
            original_matrix, original_records, current_records, requirements, executed)
    except prior_successions.SuccessionError as error:
        raise ConvergenceError(str(error)) from error
    expected_sources = {}
    for pr, rows in manifest["expected_source_paths"].items():
        for row in rows:
            path = row["path"]
            require(path not in expected_sources, "SOURCE_UNION_OVERLAP")
            require(source_trees[pr].get(path, {}).get("blob") == row["blob"], "INPUT_SOURCE_BLOB_MISMATCH:" + path)
            require(path in final_tree, "EXPECTED_SOURCE_PATH_LOST:" + path)
            expected_sources[path] = {**row, "source_pr": int(pr)}
    require(len(expected_sources) == 170, "SOURCE_UNION_CARDINALITY")
    by_id = {row["id"]: row for row in requirements}
    evolution = matrix.get("path_evolution", {})
    paths = []
    for path, final in sorted(final_tree.items()):
        originals = {key: values[path] for key, values in source_trees.items() if path in values}
        blobs = {key: value["blob"] for key, value in originals.items()}
        expected = expected_sources.get(path)
        changed_expected = bool(expected and source_trees[str(expected["source_pr"])][path] != final)
        evolved_delta = path in source_deltas and any(values.get(path) and values[path] != final
                                                      for key, values in source_trees.items() if key != "product")
        new_or_changed_source = not any(value == final for value in originals.values())
        guard_ids, reason = [], None
        if changed_expected or evolved_delta or new_or_changed_source:
            proof = evolution.get(path, {})
            reason, guard_ids = proof.get("reason"), proof.get("requirement_ids", [])
            require(isinstance(reason, str) and bool(reason.strip()) and isinstance(guard_ids, list)
                    and bool(guard_ids) and all(isinstance(item, str) and item in by_id for item in guard_ids)
                    and len(set(guard_ids)) == len(guard_ids), "UNEXPLAINED_SOURCE_EVOLUTION:" + path)
        paths.append({"path": path, "final_blob": final["blob"], "git_mode": final["git_mode"],
                      "source_blobs": blobs, "byte_preserved_from": sorted(key for key, blob in blobs.items() if blob == final["blob"]),
                      "source_git_modes": {key: value["git_mode"] for key, value in originals.items()},
                      "mode_preserved_from": sorted(key for key, value in originals.items() if value["git_mode"] == final["git_mode"]),
                      "expected_input": expected, "evolution_reason": reason, "requirement_ids": guard_ids,
                      "workstreams": sorted({by_id[item].get("workstream", "CONVERGENCE_INTEGRATION") for item in guard_ids}),
                      "preservation": "EVOLVED_WITH_NATIVE_GUARDS" if changed_expected or evolved_delta or new_or_changed_source and originals else
                          "ADDITIONAL_WITH_NATIVE_GUARDS" if new_or_changed_source else
                          "EXACT_INPUT_BYTES" if expected else "EXACT_BASELINE_OR_SOURCE_BYTES"})
    require(git(root, "rev-parse", "HEAD") == candidate_sha, "CANDIDATE_CHANGED_DURING_VERIFICATION")
    require(not git(root, "for-each-ref", "--format=%(refname)", "refs/replace/"), "GIT_REPLACE_REFS_FORBIDDEN")
    require(not git(root, "status", "--porcelain", "--untracked-files=no"), "TRACKED_CHECKOUT_CHANGED")
    return {"schema": "rc6.final-input-provenance.v1", "candidate_sha": candidate_sha,
            "path_guard_derivation": PATH_GUARD_DERIVATION,
            "candidate_tree": candidate_tree, "product": manifest["product"],
            "sources": source_reports, "source_union_paths": len(expected_sources),
            "prior_frozen_fronts": prior_fronts,
            "prior_regression_successions": regression_successions,
            "expected_source_paths_preserved": len(expected_sources), "final_path_count": len(paths), "paths": paths,
            "requirements": requirements, "scenarios": scenarios, "original_scenarios": original_scenarios,
            "front_variants": front_variants,
            "restored_controls": restored_controls, "preserved_test_modules_344": legacy_tests,
            "additional_findings": additional_findings,
            "pending_material_programming_gates": pending_material,
            "material_programming_gates_closed": not pending_material,
            "final_candidate_eligible": not pending_material and junit is not None
                and matrix.get("status") == "CLOSED_WITH_NATIVE_RESOURCE_GATES",
            "final_eligibility_scope": "SOURCE_GUARDS_ONLY; IMMUTABLE_ARTIFACT_AND_INDEPENDENT_REAUDIT_BOUND_SEPARATELY",
            "test_execution": {"junit_sha256": junit_receipt.sha256 if junit_receipt is not None else None,
                               "executed_unique_cases": count, "distinct_attack_ids": 80,
                               "distinct_requirement_ids": 55, "overlapping_suite_counts_added": False},
            "software_status": "INVENTORY_NOT_EXECUTED" if junit is None else "EXECUTED_NATIVE_GREEN",
            "final_artifact": "BOUND_SEPARATELY_BY_CANONICAL_WORKFLOW_API_AND_RAW_ZIP_DIGEST",
            "denied_final_artifact_ids": sorted(DENIED_ARTIFACTS), "merge_authorized": False, "deploy_authorized": False,
            "runtime": "NO_VERIFICADO", "provider_open_capacity": "NO_VERIFICADO", "economic_edge": "NO_DEMOSTRADO",
            "assertion_scope": "OFFLINE_CODE_AND_FROZEN_TEST_RECEIPTS; NOT_RUNTIME_ATTESTATION",
            "real_orders_sent": 0, "ppi_watch": "NOT_TOUCHED"}


def publish_report(path, report):
    """Publish once through real directories; never follow or replace aliases."""
    path = path if path.is_absolute() else Path.cwd() / path
    require(".." not in path.parts and path.name not in {"", ".", ".."}, "OUTPUT_PATH_INVALID")
    directory = None
    temporary = None
    try:
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for component in path.parts[1:-1]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            except FileNotFoundError:
                os.mkdir(component, mode=0o755, dir_fd=directory)
                os.fsync(directory)
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        try:
            existing = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            require(stat.S_ISREG(existing.st_mode) and existing.st_nlink == 1, "OUTPUT_ALIAS")
            raise ConvergenceError("OUTPUT_EXISTS")
        temporary = ".porota-provenance-" + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=directory)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True, indent=2, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        # link() installs without overwriting even if another writer races us.
        os.link(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory)
        temporary = None
        os.fsync(directory)
    except OSError as exc:
        raise ConvergenceError("OUTPUT_PUBLICATION_REJECTED") from exc
    finally:
        if directory is not None:
            if temporary is not None:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            os.close(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--junit", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fetch-source-refs", action="store_true")
    args = parser.parse_args()
    report = verify(args.repo_root, args.candidate_sha, args.junit, fetch_source_refs=args.fetch_source_refs)
    publish_report(args.out, report)
    print("POROTA_CONVERGENCE_PROVENANCE=" + report["software_status"] + "|sha=" + args.candidate_sha
          + "|source_union=170|requirements=55|scenarios=80")


if __name__ == "__main__":
    main()
