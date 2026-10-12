"""Bind five authorized Issue465 guard successors to immutable source bytes.

This module performs no filesystem, Git, network, or test execution. Its callers
must obtain the original matrix and source records from their pinned Git objects
and derive case counts from the one validated JUnit receipt. AST equality is a
structural observation, never a semantic or financial attestation.
"""
from __future__ import annotations

import ast
from collections.abc import Mapping
import hashlib
import re
from types import MappingProxyType


SUCCESSORS = MappingProxyType({
    "tests/test_issue465_budget_adversarial.py::test_tighter_global_cap_exposes_unserviceable_demand_without_lower_borrow": MappingProxyType({
        "successor_node": "tests/test_issue465_budget_adversarial.py::test_tighter_global_cap_rejects_unserviceable_exit_demand_before_bootstrap",
        "linked_requirement_ids": ("U05",),
        "contract_change": "Reject insufficient global EXIT capacity before budget bootstrap instead of allowing a smaller EXIT floor to run and merely reporting its deficit.",
    }),
    "tests/test_issue465_budget_adversarial.py::test_approved_dynamic_reserves_real_durable_positions_without_paper_authority_change": MappingProxyType({
        "successor_node": "tests/test_issue465_budget_adversarial.py::test_approved_dynamic_rejects_insufficient_capacity_for_real_durable_positions",
        "linked_requirement_ids": ("U05",),
        "contract_change": "Reject insufficient capacity for actual durable positions while preserving PAPER authority; an approved dynamic cohort does not waive the complete EXIT capacity requirement.",
    }),
    "tests/test_issue465_generations.py::test_stale_current_returns_complete_prior_cut_without_borrowing_newer_members": MappingProxyType({
        "successor_node": "tests/test_issue465_generations.py::test_stale_current_is_rejected_by_durable_high_water_without_borrowing_newer_members",
        "linked_requirement_ids": ("U20",),
        "contract_change": "Reject a stale CURRENT using durable high-water authority instead of returning a complete older cut; members from different generations remain forbidden.",
    }),
    "tests/test_issue465_source_retention_policy.py::test_acknowledged_unpinned_generation_rotates_but_ack_ledger_remains": MappingProxyType({
        "successor_node": "tests/test_issue465_source_retention_policy.py::test_acknowledged_unpinned_generation_rotates_and_receipt_survives_ack_compaction",
        "linked_requirement_ids": ("U15", "U18"),
        "contract_change": "Allow verified unpinned generation rotation and bounded ACK compaction while retaining its durable receipt and recovery authority; a permanent per-generation ACK ledger is not required.",
    }),
    "tests/test_issue465_historical_package.py::test_database_lock_short_timeout_does_not_publish": MappingProxyType({
        "successor_node": "tests/test_issue465_historical_package.py::test_dirty_source_transaction_does_not_publish",
        "linked_requirement_ids": ("U24",),
        "contract_change": "Reject export of an uncommitted source transaction rather than opening the source through SQLite lock negotiation; failed export publishes no bundle and preserves the source.",
    }),
})

AST_HASH_ALGORITHM = "sha256(ast.dump(node, annotate_fields=True, include_attributes=False).encode('utf-8'))"
ASSERTION_SCOPE = "TYPED_SOURCE_AND_PROVIDED_CASE_COUNT_LINK_ONLY; NOT_SEMANTIC_FINANCIAL_OR_EXTERNAL_ATTESTATION"
_NODE = re.compile(r"tests/test_[A-Za-z0-9_]+\.py::test_[A-Za-z0-9_]+\Z")
_BLOB = re.compile(r"[0-9a-f]{40}\Z")


class SuccessionError(ValueError):
    """An original guard cannot be linked to its authorized current execution."""


def _require(condition, reason):
    if not condition:
        raise SuccessionError(reason)


def _original_minimums(matrix):
    _require(isinstance(matrix, Mapping) and isinstance(matrix.get("findings"), list),
             "PRIOR_SUCCESSION_ORIGINAL_MATRIX_INVALID")
    minimums = {}
    for finding in matrix["findings"]:
        _require(isinstance(finding, Mapping) and isinstance(finding.get("coverage", []), list),
                 "PRIOR_SUCCESSION_ORIGINAL_COVERAGE_INVALID")
        for clause in finding.get("coverage", []):
            _require(isinstance(clause, Mapping) and isinstance(clause.get("tests"), list),
                     "PRIOR_SUCCESSION_ORIGINAL_TESTS_INVALID")
            for binding in clause["tests"]:
                _require(isinstance(binding, Mapping) and isinstance(binding.get("node"), str)
                         and _NODE.fullmatch(binding["node"]) is not None,
                         "PRIOR_SUCCESSION_ORIGINAL_NODE_INVALID")
                minimum = binding.get("minimum_cases", 1)
                _require(type(minimum) is int and 1 <= minimum <= 100,
                         "PRIOR_SUCCESSION_MINIMUM_CASES_INVALID")
                node = binding["node"]
                minimums[node] = max(minimums.get(node, 0), minimum)
    for node in SUCCESSORS:
        _require(node in minimums, "PRIOR_SUCCESSION_ORIGINAL_BINDING_MISSING:" + node)
    return minimums


def _parents(requirements):
    if isinstance(requirements, Mapping):
        rows = []
        for identifier, row in requirements.items():
            _require(isinstance(row, Mapping) and row.get("id") == identifier,
                     "PRIOR_SUCCESSION_PARENT_MAPPING_INVALID")
            rows.append(row)
    else:
        _require(isinstance(requirements, (list, tuple)), "PRIOR_SUCCESSION_PARENTS_INVALID")
        rows = requirements
    result = {}
    for row in rows:
        _require(isinstance(row, Mapping) and isinstance(row.get("id"), str)
                 and row["id"] and row["id"] not in result,
                 "PRIOR_SUCCESSION_PARENT_MISSING_OR_DUPLICATE")
        nodes = row.get("test_nodes")
        _require(isinstance(nodes, (list, tuple)) and all(isinstance(node, str) for node in nodes),
                 "PRIOR_SUCCESSION_PARENT_GUARDS_INVALID")
        result[row["id"]] = frozenset(nodes)
    return result


def _source(sources, path, role):
    _require(isinstance(sources, Mapping) and isinstance(sources.get(path), Mapping),
             "PRIOR_SUCCESSION_SOURCE_MISSING:" + role + ":" + path)
    record = dict(sources[path])
    data, blob, mode = record.get("data"), record.get("blob"), record.get("git_mode")
    _require(type(data) is bytes and isinstance(blob, str) and _BLOB.fullmatch(blob) is not None
             and isinstance(mode, str) and mode in {"100644", "100755"},
             "PRIOR_SUCCESSION_SOURCE_RECORD_INVALID:" + role + ":" + path)
    actual = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    _require(actual == blob, "PRIOR_SUCCESSION_SOURCE_BLOB_MISMATCH:" + role + ":" + path)
    try:
        module = ast.parse(data, filename=path)
    except (SyntaxError, UnicodeError, ValueError) as error:
        raise SuccessionError("PRIOR_SUCCESSION_SOURCE_AST_INVALID:" + role + ":" + path) from error
    definitions = {}
    for definition in module.body:
        if isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.setdefault(definition.name, []).append(definition)
    anchor = {"path": path, "blob": blob, "git_mode": mode,
              "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return definitions, anchor


def _unique(definitions, function, role, node):
    candidates = definitions.get(function, [])
    _require(len(candidates) == 1, "PRIOR_SUCCESSION_AST_MISSING_OR_DUPLICATE:" + role + ":" + node)
    return candidates[0]


def _ast_hash(definition):
    return hashlib.sha256(ast.dump(definition, annotate_fields=True, include_attributes=False)
                          .encode("utf-8")).hexdigest()


def verify_successions(original_matrix, original_sources, current_sources, requirements, executed):
    """Return the five source-bound relations, never a free-form alias map.

    Each source record is {blob, git_mode, data: bytes}. Counts are either a
    node-to-int mapping from validated JUnit or None for inventory. Duplicate
    original coverage uses its greatest minimum, so every old clause is retained.
    An available unique old node takes precedence over its fixed successor.
    Every linked requirement must explicitly name the selected current node.
    """
    minimums, parents = _original_minimums(original_matrix), _parents(requirements)
    _require(executed is None or isinstance(executed, Mapping), "PRIOR_SUCCESSION_EXECUTION_MAPPING_INVALID")
    counts = None if executed is None else dict(executed)
    originals, currents, result = {}, {}, []
    for original_node in sorted(SUCCESSORS):
        specification = SUCCESSORS[original_node]
        path, original_function = original_node.split("::")
        if path not in originals:
            originals[path] = _source(original_sources, path, "original")
            currents[path] = _source(current_sources, path, "current")
        original_definitions, original_anchor = originals[path]
        current_definitions, current_anchor = currents[path]
        original = _unique(original_definitions, original_function, "original", original_node)
        available = current_definitions.get(original_function, [])
        _require(len(available) <= 1, "PRIOR_SUCCESSION_AST_MISSING_OR_DUPLICATE:current:" + original_node)
        if available:
            current_node, current = original_node, available[0]
            contract_change = "ORIGINAL_NODE_RETAINED; no successor contract is applied."
        else:
            current_node = specification["successor_node"]
            current_path, current_function = current_node.split("::")
            _require(current_path == path, "PRIOR_SUCCESSION_SUCCESSOR_PATH_INVALID")
            current = _unique(current_definitions, current_function, "current", current_node)
            contract_change = specification["contract_change"]
        linked = list(specification["linked_requirement_ids"])
        for identifier in linked:
            _require(identifier in parents and current_node in parents[identifier],
                     "PRIOR_SUCCESSION_PARENT_GUARD_NOT_BOUND:" + identifier + ":" + current_node)
        minimum = minimums[original_node]
        count = None if counts is None else counts.get(current_node)
        _require(counts is None or type(count) is int and count >= minimum,
                 "PRIOR_SUCCESSION_GUARD_NOT_EXECUTED:" + current_node)
        original_hash, current_hash = _ast_hash(original), _ast_hash(current)
        preservation = ("TYPED_SUCCESSOR" if current_node != original_node else
                        "EXACT_AST" if original_hash == current_hash else "UPDATED_AST")
        result.append({"original_node": original_node, "current_node": current_node,
                       "linked_requirement_ids": linked, "contract_change": contract_change,
                       "preservation": preservation, "minimum_cases": minimum,
                       "original_anchor": {**original_anchor, "function": original.name, "line": original.lineno},
                       "current_anchor": {**current_anchor, "function": current.name, "line": current.lineno},
                       "original_ast_sha256": original_hash, "current_ast_sha256": current_hash,
                       "ast_hash_algorithm": AST_HASH_ALGORITHM, "executed_cases": count,
                       "status": "NOT_EXECUTED" if counts is None else "EXECUTED_NATIVE_GREEN",
                       "assertion_scope": ASSERTION_SCOPE})
    return result
