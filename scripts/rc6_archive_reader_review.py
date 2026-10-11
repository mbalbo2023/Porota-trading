#!/usr/bin/env python3
"""Exact-Source reader inventory and cheap execution record, never V4 enablement.

This record becomes authority only when a caller selects its exact hash inside
an authenticated gate artifact. It grants neither a native writer capability,
an original Horizon bound, nor contractual equivalence. The inventory starts
from all tracked executable Source, follows Python callers transitively, and
keeps inline namespace loaders and metadata-only consumers explicit.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import os
from pathlib import Path
import re
import subprocess

from rc6_shadow_runtime import archive_components as components
from scripts.rc6_controlled_governed_runner import FIELDS as SOURCE_FIELDS, STABLE_CODE_FIELDS


SCHEMA = "rc6.original-cas-reader-review.v2"
INVENTORY_SCHEMA = "rc6.archive-consumer-source-inventory.v1"
SCOPE = "PRIVATE_DEVELOPMENT_COMPARISON_NOT_NATIVE_V4_ENABLEMENT"
CAS_REVIEW_FILES = {
    "cas_reader_review": "execution.cas-reader-review.json",
    "cas_private_ack_review": "execution.cas-private-ack-review.json",
}
CAS_REVIEW_MODULES = frozenset({
    "tests/test_rc6_archive_slice_reuse.py", "tests/test_rc6_archive_reader_review.py",
    "tests/test_rc6_archive_v3_image_smoke.py", "tests/test_rc6_cas_original_comparison.py",
})
MAX_SOURCE_FILE = 4 * 1024**2
MAX_SCAN_BYTES = 64 * 1024**2
MAX_SCAN_FILES = 8192
CODE_SUFFIXES = frozenset({".py", ".sh", ".c", ".yml", ".yaml"})
TOKENS = re.compile(r"archive_components|ComponentArchive|dependency_graph|archive_namespace|inspect_archive|"
    r"restore_generation|archive_generation|maintain_archive|RC6_SHADOW_ARCHIVE|local-private://|"
    r"\.recipe\.gz|\.receipt\.json|\.cas\.pack|EvidenceRetention|EvidenceFiles|read_committed_generation|"
    r"rc6_archive_v3_image_smoke|rc6_cas_original_comparison|rc6_archive_reader_review|rc6_archive_maximum_member_probe")
CORE_MODULES = frozenset({"rc6_shadow_runtime.archive_components", "rc6_shadow_runtime.archive_namespace",
    "rc6_shadow_runtime.retention", "rc6_shadow_runtime.persistence"})
SLICE_CASES = frozenset({
    "test_native_v4_exact_five_members_mixed_v3_chain_and_reachability_without_encoder",
    "test_existing_v4_remains_readable_and_next_native_write_is_v3",
    "test_v4_gc_recovery_rejects_reachable_whole_parent_pack_before_any_unlink",
    "test_slice_hash_and_whole_parent_corruption_are_fatal_on_fresh_public_read",
    "test_v3_v4_array_dispatch_cannot_reinterpret_each_others_width",
})
GC_STAGES = ("archive_gc_after_intent", "archive_gc_before_checkpoint", "archive_gc_after_checkpoint",
    "archive_gc_before_unlink", "archive_gc_after_unlink", "archive_gc_before_directory_fsync",
    "archive_gc_after_directory_fsync")
REQUIRED_NODEIDS = frozenset({
    *("tests/test_rc6_archive_slice_reuse.py::" + name for name in SLICE_CASES
      ),
    "tests/test_rc6_archive_reader_review.py::test_mixed_v3_v4_gc_keeps_whole_parents_and_actual_native_pins",
    *("tests/test_rc6_archive_reader_review.py::test_mixed_v3_v4_sigkill_gc_recovers_exact_sources[" + stage + "]"
      for stage in GC_STAGES),
    "tests/test_rc6_archive_v3_image_smoke.py::test_native_tiny_cli_executes_all_legacy_v3_corruption_and_missing_dependency_controls",
})


def require(condition, signature):
    if not condition:
        raise ValueError(signature)


def _module(path):
    if not path.endswith(".py"):
        return None
    return path[:-3].replace("/", ".").removesuffix(".__init__")


def _python_imports(tree, module, path):
    imported = set()
    package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".") if package else []
                require(node.level <= len(parts) + 1, "ARCHIVE_REVIEW_RELATIVE_IMPORT_INVALID")
                prefix = ".".join(parts[:len(parts) - node.level + 1])
                base = ".".join(part for part in (prefix, base) if part)
            imported.add(base)
            imported.update(base + "." + alias.name for alias in node.names)
    return imported


def inventory_source_bytes(source_files):
    """Pure bounded inventory for an already authenticated complete Source set.

    A hit denotes a consumer or supporting control to review, not a claim that
    regex or import analysis proves semantic correctness. All scanned hashes
    remain in the record, so removing an apparent consumer cannot hide Source.
    """
    require(type(source_files) is dict and 0 < len(source_files) <= MAX_SCAN_FILES,
            "ARCHIVE_REVIEW_COMPLETE_SOURCE_SET_REQUIRED")
    scanned, direct, imports, nodes = {}, {}, {}, {}
    total = 0
    for path, raw in sorted(source_files.items()):
        require(isinstance(path, str) and not Path(path).is_absolute() and ".." not in Path(path).parts
                and type(raw) is bytes and len(raw) <= MAX_SOURCE_FILE, "ARCHIVE_REVIEW_SOURCE_INVALID")
        total += len(raw)
        require(total <= MAX_SCAN_BYTES, "ARCHIVE_REVIEW_SCAN_BOUND")
        scanned[path] = components.sha(raw)
        text = raw.decode("utf-8")
        hits = [{"line": number, "tokens": sorted(set(match.group() for match in TOKENS.finditer(line)))}
                for number, line in enumerate(text.splitlines(), 1) if TOKENS.search(line)]
        if hits:
            direct[path] = hits
        module = _module(path)
        if module is not None:
            tree = ast.parse(text, filename=path)
            nodes[path] = tree
            imports[path] = _python_imports(tree, module, path)
    consumers = set(direct)
    modules = {_module(path) for path in consumers} | set(CORE_MODULES)
    changed = True
    while changed:
        changed = False
        for path, imported in imports.items():
            if path not in consumers and imported & modules:
                consumers.add(path); modules.add(_module(path)); changed = True
    rows = []
    for path in sorted(consumers):
        functions = []
        lines = source_files[path].decode().splitlines(keepends=True)
        tree = nodes.get(path)
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = "".join(lines[node.lineno - 1:node.end_lineno]).encode()
                    if TOKENS.search(body.decode()):
                        functions.append({"name": node.name, "line": node.lineno, "end_line": node.end_lineno,
                                          "source_sha256": components.sha(body)})
        if path.endswith("archive_namespace.py"):
            scope = "FILESYSTEM_CUSTODY_METADATA_ONLY_NOT_RECIPE_PROOF"
        elif path.endswith("archive_components.py") or path.endswith("retention.py"):
            scope = "RECIPE_RECEIPT_RESTORE_ACK_ROTATION_GC_RECOVERY_SEMANTIC_CONSUMER"
        elif path.endswith("archive_physical_model.py"):
            scope = "CONDITIONAL_PHYSICAL_MODEL_NOT_AUTHENTICATION"
        else:
            scope = "CALLER_OR_INLINE_LOADER_REQUIRES_EXACT_SOURCE_REVIEW"
        rows.append({"path": path, "source_sha256": scanned[path], "scope": scope,
            "direct_token_lines": direct.get(path, []), "archive_imports": sorted(imports.get(path, set()) & modules),
            "functions": sorted(functions, key=lambda node: (node["line"], node["name"]))})
    result = {"schema": INVENTORY_SCHEMA, "scan_scope": "ALL_TRACKED_EXECUTABLE_SOURCE_EXCLUDING_TESTS_AND_DOCS",
        "scanned_source_sha256": scanned, "scanned_files": len(scanned), "scanned_bytes": total,
        "consumers": rows, "consumer_count": len(rows),
        "semantic_review_is_not_inferred_from_imports": True}
    result["inventory_sha256"] = components.sha(components.canonical(result))
    return result


def source_inventory(root, *, source_sha, source_tree):
    from scripts.rc6_cas_original_comparison import _read
    root = Path(root).absolute()
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_LAZY_FETCH": "1"}
    def git(*args):
        return subprocess.check_output(["git", "--no-replace-objects", "-C", str(root), *args], env=env,
                                       stderr=subprocess.PIPE)
    require(git("rev-parse", "HEAD").decode().strip() == source_sha
            and git("rev-parse", "HEAD^{tree}").decode().strip() == source_tree,
            "ARCHIVE_REVIEW_EXACT_NATIVE_SOURCE_REQUIRED")
    require(not git("status", "--porcelain", "--untracked-files=normal"), "ARCHIVE_REVIEW_SOURCE_DIRTY")
    raw_tree = git("ls-tree", "-rz", "--full-tree", "HEAD")
    require(len(raw_tree) <= MAX_SOURCE_FILE, "ARCHIVE_REVIEW_GIT_INVENTORY_BOUND")
    files = {}
    for item in raw_tree.split(b"\0"):
        if not item:
            continue
        metadata, path_raw = item.split(b"\t", 1)
        path = path_raw.decode()
        mode, kind, oid = metadata.decode().split()
        if path.startswith(("tests/", "docs/", ".agents/")) or Path(path).suffix not in CODE_SUFFIXES:
            continue
        require(mode in {"100644", "100755"} and kind == "blob", "ARCHIVE_REVIEW_SOURCE_ALIAS_OR_NONBLOB")
        raw = _read(root / path, maximum=MAX_SOURCE_FILE)
        require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == oid,
                "ARCHIVE_REVIEW_SOURCE_BLOB_CHANGED")
        files[path] = raw
    require(not git("status", "--porcelain", "--untracked-files=normal")
            and git("rev-parse", "HEAD").decode().strip() == source_sha,
            "ARCHIVE_REVIEW_SOURCE_CHANGED_DURING_CAPTURE")
    return {"source_sha": source_sha, "source_tree": source_tree, **inventory_source_bytes(files)}


def inventory_from_source_pin(root, pin, *, capture):
    """Reuse the original authenticated full Source pin; no Git or Source copy.

    The caller supplies the original NOATIME/all11 capture. This does not
    authenticate a supplied JSON pin or confer artifact/contract approval.
    """
    require(type(pin) is dict and pin.get("physical_namespace_exact_to_literal_tree") is True
            and pin.get("overlay_count") == 0 and type(pin.get("files")) is dict
            and bool(pin["files"])
            and all(isinstance(pin.get(key), str) and re.fullmatch(r"[0-9a-f]{40}", pin[key])
                    for key in ("source_sha", "source_tree")), "ARCHIVE_REVIEW_ORIGINAL_FULL_SOURCE_PIN_REQUIRED")
    files, total = {}, 0
    for path, expected in sorted(pin["files"].items()):
        require(isinstance(path, str) and Path(path).as_posix() == path and not Path(path).is_absolute()
                and ".." not in Path(path).parts and "\\" not in path,
                "ARCHIVE_REVIEW_SOURCE_PIN_PATH_INVALID")
        if path.startswith(("tests/", "docs/", ".agents/")) or Path(path).suffix not in CODE_SUFFIXES:
            continue
        require(len(files) < MAX_SCAN_FILES, "ARCHIVE_REVIEW_SCAN_FILE_BOUND")
        require(type(expected) is dict and type(expected.get("bytes")) is int
                and 0 <= expected["bytes"] <= MAX_SOURCE_FILE, "ARCHIVE_REVIEW_SOURCE_PIN_BOUND")
        total += expected["bytes"]
        require(total <= MAX_SCAN_BYTES, "ARCHIVE_REVIEW_SCAN_BOUND")
        raw, fields = capture(Path(root) / path, limit=MAX_SOURCE_FILE)
        original_fields = expected.get("stat_fields")
        # Original Source admits/report CODE atime changes caused by imports.
        # capture itself still proves all11 unchanged during this NOATIME read.
        require(type(raw) is bytes and len(raw) == expected["bytes"]
                and components.sha(raw) == expected.get("sha256")
                and type(original_fields) is dict and set(fields) == set(original_fields) == set(SOURCE_FIELDS)
                and all(fields[key] == original_fields[key] for key in STABLE_CODE_FIELDS)
                and hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == expected.get("git_blob"),
                "ARCHIVE_REVIEW_SOURCE_PIN_BYTES_OR_STABLE_CUSTODY_CHANGED")
        files[path] = raw
    return {"source_sha": pin["source_sha"], "source_tree": pin["source_tree"], **inventory_source_bytes(files)}


def executed_reader_nodes(execution, *, source_sha, source_tree):
    from scripts.rc6_architectural_gates import finalization_closed
    require(type(execution) is dict and execution.get("phase") == "execution"
            and execution.get("source_sha") == source_sha and execution.get("source_tree") == source_tree
            and execution.get("source_namespace_exact_before_after") is True
            and type(execution.get("pytest_exit_code")) is int and execution["pytest_exit_code"] == 0
            and execution.get("native_execution_coverage_exact") is True
            and execution.get("inet_socket_attempts") == [] and execution.get("unexpected_product_imports") == []
            and execution.get("phase_validation_errors") == []
            and finalization_closed(execution.get("child_infrastructure_finalization", {})),
            "ARCHIVE_REVIEW_AUTHENTIC_GREEN_SOURCE_EXECUTION_AND_FIN_REQUIRED")
    nodes = execution.get("items")
    reports = execution.get("original_pytest_reports")
    require(type(nodes) is list and type(reports) is list and bool(nodes), "ARCHIVE_REVIEW_EXECUTED_NODES_REQUIRED")
    names, nodeids = set(), []
    for item in nodes:
        require(type(item) is dict and isinstance(item.get("nodeid"), str) and isinstance(item.get("name"), str),
                "ARCHIVE_REVIEW_EXECUTED_NODE_INVALID")
        nodeid = item["nodeid"]
        require(nodeid not in nodeids and item["name"] == nodeid.rsplit("::", 1)[-1],
                "ARCHIVE_REVIEW_EXECUTED_NODE_REBOUND")
        nodeids.append(nodeid); names.add(item["name"].split("[", 1)[0])
        actual = [report for report in reports if report.get("nodeid") == nodeid]
        require(len(actual) == 3 and {report.get("when") for report in actual} == {"setup", "call", "teardown"}
                and all(report.get("outcome") == "passed" for report in actual),
                "ARCHIVE_REVIEW_CASE_NOT_GENUINELY_EXECUTED_GREEN")
    require(execution.get("actual_execution_started_nodeids") == nodeids
            and execution.get("actual_execution_finished_nodeids") == nodeids,
            "ARCHIVE_REVIEW_NATIVE_EXECUTION_SEQUENCE_MISMATCH")
    require(REQUIRED_NODEIDS <= set(nodeids) and SLICE_CASES <= names,
            "ARCHIVE_REVIEW_COMPLETE_MIXED_GC_RECOVERY_IMAGE_CASES_REQUIRED")
    return tuple(sorted(nodeid for nodeid in nodeids if nodeid.startswith((
        "tests/test_rc6_archive_slice_reuse.py::", "tests/test_rc6_archive_reader_review.py::",
        "tests/test_rc6_archive_v3_image_smoke.py::"))))


def build_review(inventory, execution, execution_wire, *, source_sha, source_tree):
    require(type(inventory) is dict and inventory.get("source_sha") == source_sha
            and inventory.get("source_tree") == source_tree and type(execution_wire) is bytes
            and components.loads(execution_wire) == execution, "ARCHIVE_REVIEW_SOURCE_OR_EXECUTION_BINDING")
    nodes = executed_reader_nodes(execution, source_sha=source_sha, source_tree=source_tree)
    return {"schema": SCHEMA, "scope": SCOPE, "source_sha": source_sha, "source_tree": source_tree,
        "recipe_schemas": [components.RECIPE_SCHEMA, components.SLICE_RECIPE_SCHEMA],
        "native_write_recipe_schema": components.RECIPE_SCHEMA, "native_v4_write_enabled": False,
        "consumer_inventory": inventory, "execution_sha256": components.sha(execution_wire),
        "executed_reader_nodeids": list(nodes),
        "executed_reader_case_names": sorted({node.rsplit("::", 1)[-1].split("[", 1)[0] for node in nodes}),
        "reader_execution_scope": "AUTHENTIC_CHEAP_EPOCH_REQUIRES_DUAL_ARTIFACT_AUTHENTICATION_BY_CALLER",
        "artifact_authority": "CALLER_MUST_AUTHENTICATE_GATE_ARTIFACT_AND_SELECT_THIS_EXACT_RECORD_HASH",
        "native_writer_contract_approved": False, "g5_equivalence_demonstrated": False,
        "certified_physical_bound_bytes": None, "runtime_validated": False, "real_orders_sent": 0}


def validate_review(record, inventory, execution, execution_wire, *, source_sha, source_tree):
    expected = build_review(inventory, execution, execution_wire, source_sha=source_sha, source_tree=source_tree)
    require(record == expected, "ARCHIVE_REVIEW_ALL_CONSUMERS_OR_EXECUTION_REBOUND")
    return record


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--source-tree", required=True)
    parser.add_argument("--execution-json", type=Path, required=True)
    args = parser.parse_args(argv)
    from scripts.rc6_cas_original_comparison import _read
    raw = _read(args.execution_json, maximum=16 * 1024**2)
    record = build_review(source_inventory(args.source_root, source_sha=args.source_sha, source_tree=args.source_tree),
        components.loads(raw), raw, source_sha=args.source_sha, source_tree=args.source_tree)
    print(components.canonical(record).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
