"""Typed source/count guards; controlled cases are not financial executions.

The final test reads five existing native test definitions at their original
Git objects and the selected case metadata from a prior recorded JUnit. It
does not rerun those implementations or attest their economic independence.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from scripts import rc6_prior_regression_successors as successions


REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs/audits/convergence/PRIOR_REGRESSION_SUCCESSIONS.json"


def source(data, mode="100644"):
    return {"data": data, "git_mode": mode,
            "blob": hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()}


def node_source(nodes, value="original"):
    grouped = {}
    for node in nodes:
        path, function = node.split("::")
        grouped.setdefault(path, []).append("def " + function + "():\n    return " + repr(value) + "\n")
    return {path: source("\n".join(parts).encode()) for path, parts in grouped.items()}


def parent_rows(nodes):
    rows = {}
    for original, current in nodes.items():
        for identifier in successions.SUCCESSORS[original]["linked_requirement_ids"]:
            rows.setdefault(identifier, {"id": identifier, "test_nodes": []})["test_nodes"].append(current)
    return list(rows.values())


@pytest.fixture
def controlled():
    originals = list(successions.SUCCESSORS)
    selected = {node: specification["successor_node"] for node, specification in successions.SUCCESSORS.items()}
    matrix = {"findings": [{"id": "CONTROLLED", "coverage": [
        {"id": "CONTROLLED-01", "tests": [{"node": node, "minimum_cases": 1} for node in originals]}]}]}
    return {"original_matrix": matrix, "original_sources": node_source(originals),
            "current_sources": node_source(selected.values(), "current"),
            "requirements": parent_rows(selected), "executed": {node: 1 for node in selected.values()}}


def verify(fixture):
    return successions.verify_successions(**fixture)


def test_fixed_successors_require_original_native_sources_parents_and_case_counts(controlled):
    result = verify(controlled)
    assert len(result) == 5 and len(successions.SUCCESSORS) == 5
    assert all(row["status"] == "EXECUTED_NATIVE_GREEN" and row["minimum_cases"] == 1 for row in result)
    assert all(row["preservation"] == "TYPED_SUCCESSOR" for row in result)
    for row in result:
        specification = successions.SUCCESSORS[row["original_node"]]
        assert row["current_node"] == specification["successor_node"]
        assert row["linked_requirement_ids"] == list(specification["linked_requirement_ids"])
        assert row["contract_change"] == specification["contract_change"]
        assert row["original_ast_sha256"] != row["current_ast_sha256"]
        assert row["assertion_scope"] == successions.ASSERTION_SCOPE
        for anchor, sources in ((row["original_anchor"], controlled["original_sources"]),
                                (row["current_anchor"], controlled["current_sources"])):
            assert anchor["blob"] == sources[anchor["path"]]["blob"]
            assert anchor["sha256"] == hashlib.sha256(sources[anchor["path"]]["data"]).hexdigest()
            assert anchor["git_mode"] == "100644" and anchor["line"] > 0


@pytest.mark.parametrize("updated", [False, True])
def test_available_unique_original_node_has_priority_and_reports_AST_changes(controlled, updated):
    selected = {node: node for node in successions.SUCCESSORS}
    controlled["current_sources"] = (node_source(selected, "updated") if updated
                                      else deepcopy(controlled["original_sources"]))
    controlled["requirements"] = parent_rows(selected)
    controlled["executed"] = {node: 1 for node in selected}
    result = verify(controlled)
    assert all(row["current_node"] == row["original_node"] for row in result)
    assert all(row["preservation"] == ("UPDATED_AST" if updated else "EXACT_AST") for row in result)
    assert all("no successor contract" in row["contract_change"] for row in result)
    assert all((row["original_ast_sha256"] == row["current_ast_sha256"]) == (not updated) for row in result)


def test_existing_old_guard_cannot_borrow_only_the_successor_case_counts(controlled):
    selected = {node: node for node in successions.SUCCESSORS}
    for path, original in controlled["original_sources"].items():
        controlled["current_sources"][path] = source(
            controlled["current_sources"][path]["data"] + b"\n" + original["data"])
    controlled["requirements"] = parent_rows(selected)
    with pytest.raises(successions.SuccessionError, match="GUARD_NOT_EXECUTED"):
        verify(controlled)
    controlled["executed"].update({node: 1 for node in selected})
    assert all(row["current_node"] == row["original_node"] for row in verify(controlled))


def test_inventory_without_JUnit_cannot_claim_executed_green(controlled):
    controlled["executed"] = None
    result = verify(controlled)
    assert all(row["status"] == "NOT_EXECUTED" and row["executed_cases"] is None for row in result)


@pytest.mark.parametrize("count", [None, True, False, 0, -1, 1.0, "1"])
def test_missing_or_malformed_case_count_cannot_close_a_successor(controlled, count):
    controlled["executed"][next(iter(controlled["executed"]))] = count
    with pytest.raises(successions.SuccessionError, match="GUARD_NOT_EXECUTED"):
        verify(controlled)


def test_original_minimum_is_retained_across_duplicate_clause_bindings(controlled):
    original = next(iter(successions.SUCCESSORS))
    current = successions.SUCCESSORS[original]["successor_node"]
    controlled["original_matrix"]["findings"][0]["coverage"].append(
        {"id": "CONTROLLED-02", "tests": [{"node": original, "minimum_cases": 3}]})
    controlled["executed"][current] = 2
    with pytest.raises(successions.SuccessionError, match="GUARD_NOT_EXECUTED"):
        verify(controlled)
    controlled["executed"][current] = 3
    row = next(row for row in verify(controlled) if row["original_node"] == original)
    assert row["minimum_cases"] == row["executed_cases"] == 3


@pytest.mark.parametrize("minimum", [None, True, False, 0, -1, 101, 1.0, "1"])
def test_original_minimum_types_and_bounds_are_not_relaxed(controlled, minimum):
    controlled["original_matrix"]["findings"][0]["coverage"][0]["tests"][0]["minimum_cases"] = minimum
    with pytest.raises(successions.SuccessionError, match="MINIMUM_CASES_INVALID"):
        verify(controlled)


def test_missing_original_coverage_cannot_be_replaced_by_current_receipts(controlled):
    controlled["original_matrix"]["findings"][0]["coverage"][0]["tests"].pop()
    with pytest.raises(successions.SuccessionError, match="ORIGINAL_BINDING_MISSING"):
        verify(controlled)


@pytest.mark.parametrize("role", ["original_sources", "current_sources"])
@pytest.mark.parametrize("attack", ["blob", "mode", "data", "missing", "syntax", "duplicate"])
def test_source_hash_mode_and_unique_native_definition_are_required(controlled, role, attack):
    original = next(iter(successions.SUCCESSORS))
    current = successions.SUCCESSORS[original]["successor_node"]
    node = original if role == "original_sources" else current
    path, function = node.split("::")
    record = controlled[role][path]
    if attack == "blob": record["blob"] = "0" * 40
    elif attack == "mode": record["git_mode"] = "120000"
    elif attack == "data": record["data"] = bytearray(record["data"])
    elif attack == "missing": del controlled[role][path]
    elif attack == "syntax": controlled[role][path] = source(b"def invalid(:\n")
    elif attack == "duplicate":
        controlled[role][path] = source(record["data"] + ("\ndef " + function + "():\n    return False\n").encode())
    with pytest.raises(successions.SuccessionError):
        verify(controlled)


def test_duplicate_old_node_cannot_be_evaded_by_present_successor(controlled):
    original = next(iter(successions.SUCCESSORS))
    path, function = original.split("::")
    original_definition = ("\ndef " + function + "():\n    return True\n").encode()
    controlled["current_sources"][path] = source(controlled["current_sources"][path]["data"] + original_definition * 2)
    with pytest.raises(successions.SuccessionError, match="AST_MISSING_OR_DUPLICATE"):
        verify(controlled)


def test_unlisted_current_name_cannot_borrow_the_successor_execution(controlled):
    original = next(iter(successions.SUCCESSORS))
    current = successions.SUCCESSORS[original]["successor_node"]
    path, function = current.split("::")
    controlled["current_sources"][path] = source(controlled["current_sources"][path]["data"].replace(
        function.encode(), (function + "_alias").encode()))
    with pytest.raises(successions.SuccessionError, match="AST_MISSING_OR_DUPLICATE"):
        verify(controlled)


@pytest.mark.parametrize("attack", ["missing_parent", "missing_node", "parameter_alias", "duplicate_parent"])
def test_every_parent_must_explicitly_bind_the_selected_native_node(controlled, attack):
    row = next(row for row in controlled["requirements"] if row["id"] == "U18")
    if attack == "missing_parent": controlled["requirements"].remove(row)
    elif attack == "missing_node": row["test_nodes"] = []
    elif attack == "parameter_alias": row["test_nodes"] = [row["test_nodes"][0] + "[borrowed]"]
    else: controlled["requirements"].append(deepcopy(row))
    with pytest.raises(successions.SuccessionError):
        verify(controlled)


def test_parent_mapping_and_list_have_identical_contract(controlled):
    expected = verify(controlled)
    controlled["requirements"] = {row["id"]: row for row in controlled["requirements"]}
    assert verify(controlled) == expected


def test_five_recorded_native_AST_and_case_families_are_linked_to_exact_Git_bytes():
    """Read prior native metadata; this is not a fresh implementation replay."""
    document = json.loads(DOC.read_bytes())
    original_sha = document["original_source_sha"]
    current_sha = document["recorded_native_source_sha"]
    def git_bytes(revision, path):
        return subprocess.check_output(["git", "-C", str(REPO), "show", revision + ":" + path], timeout=30)
    matrix = json.loads(git_bytes(original_sha, "docs/audits/ISSUE465_REAUDIT.json"))
    original_sources, current_sources, selected, counts = {}, {}, {}, {}
    for row in document["recorded_native_relations"]:
        original, current = row["original_node"], row["successor_node"]
        path = original.split("::")[0]
        original_data, current_data = git_bytes(original_sha, path), git_bytes(current_sha, path)
        assert hashlib.sha256(original_data).hexdigest() == row["original_file_sha256"]
        assert hashlib.sha256(current_data).hexdigest() == row["current_file_sha256"]
        original_sources[path] = source(original_data, row["original_git_mode"])
        current_sources[path] = source(current_data, row["current_git_mode"])
        assert original_sources[path]["blob"] == row["original_blob"]
        assert current_sources[path]["blob"] == row["current_blob"]
        selected[original] = current
        assert row["recorded_cases"] and all(case["outcome"] == "PASS" for case in row["recorded_cases"])
        for case in row["recorded_cases"]:
            node = case["classname"].replace(".", "/") + ".py::" + case["name"].split("[", 1)[0]
            assert node == current
        counts[current] = len(row["recorded_cases"])
    # Parent rows here are explicitly controlled parser inputs. ROOT verifies
    # the actual fifty-five requirement rows independently in its FIP wiring.
    result = successions.verify_successions(matrix, original_sources, current_sources,
                                           parent_rows(selected), counts)
    assert len(result) == 5 and all(row["preservation"] == "TYPED_SUCCESSOR" for row in result)
    assert all(row["minimum_cases"] == 1 for row in result)
    assert all(row["executed_cases"] == counts[row["current_node"]] for row in result)
