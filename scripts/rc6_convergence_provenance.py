#!/usr/bin/env python3
"""Reconcile the complete handoff against Git and executed native guards.

The final SHA/tree are emitted after freeze, never committed as a circular
self-attestation. This report is evidence, not permission to merge or deploy.
"""
from __future__ import annotations

import argparse
import ast
import csv
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

INPUT_ROOT = "docs/audits/convergence"
MANIFEST = "INPUT_MANIFEST_RC6_CONVERGENCIA.json"
REGISTRY = "REGISTRO_CONVERGENCIA_RC6.csv"
CLOSURE = "REQUIREMENT_CLOSURE_MATRIX.json"
SCENARIOS = "ORIGINAL_SCENARIOS_469.json"
SOURCE_PRS = {447, 448, 449, 450, 451, 453, 454, 455, 456, 457, 459, 461, 463, 466, 470}
REQUIREMENTS = {f"U{i:02d}" for i in range(1, 30)} | {f"AUD-468-{i:02d}" for i in range(1, 22)} | {f"UX470-I{i:02d}" for i in range(1, 6)}
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
    result = subprocess.run(["git", "-C", str(root), *arguments], check=False,
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


def executed_cases(junit):
    require(junit.is_file() and not junit.is_symlink() and junit.stat().st_size <= 16 * 1024**2,
            "JUNIT_INPUT_INVALID")
    document = ET.parse(junit)
    suites = list(document.iter("testsuite"))
    require(len(suites) == 1, "JUNIT_GOVERNED_SUITE_SHAPE")
    cases = list(document.iter("testcase"))
    for field in ("tests", "failures", "errors", "skipped"):
        value = suites[0].get(field, "")
        require(re.fullmatch(r"[0-9]+", value) is not None, "JUNIT_SUMMARY_INVALID")
        require(int(value) == (len(cases) if field == "tests" else 0), "JUNIT_SUMMARY_MISMATCH")
    require(bool(cases), "JUNIT_EMPTY")
    identities, nodes = set(), {}
    for case in cases:
        require(all(case.find(tag) is None for tag in ("failure", "error", "skipped")), "JUNIT_NONPASS")
        name, classname = case.get("name", ""), case.get("classname", "")
        require((classname, name) not in identities, "JUNIT_DUPLICATE_CASE")
        identities.add((classname, name))
        node = classname.replace(".", "/") + ".py::" + name.split("[", 1)[0]
        nodes[node] = nodes.get(node, 0) + 1
    return nodes, len(cases)


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
            require(isinstance(node, str) and re.fullmatch(r"(?:tests/)?test_[A-Za-z0-9_]+\.py::test_[A-Za-z0-9_]+", node),
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
    require(re.fullmatch(r"[0-9a-f]{40}", candidate_sha) is not None, "CANDIDATE_SHA_INVALID")
    require(git(root, "rev-parse", "HEAD") == candidate_sha, "CANDIDATE_CHECKOUT_MISMATCH")
    require(not git(root, "status", "--porcelain", "--untracked-files=no"), "TRACKED_CHECKOUT_CHANGED")
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
    guard_files = {node.split("::")[0] for row in matrix["requirements"] + matrix["scenarios"] + front_rows + restored_rows
                   for node in row.get("test_nodes", []) if isinstance(node, str)}
    for filename in guard_files:
        require(filename in final_tree, "CLOSURE_GUARD_SOURCE_MISSING")
        source = git(root, "show", candidate_sha + ":" + filename, binary=True)
        require(len(source) <= 4 * 1024**2, "CLOSURE_GUARD_SOURCE_TOO_LARGE")
        parsed = ast.parse(source, filename=filename)
        declared_guards.update(filename + "::" + node.name for node in parsed.body
                               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))
    executed, count = executed_cases(junit) if junit is not None else (None, None)
    requirements = guard_rows(matrix["requirements"], REQUIREMENTS, final_tree, executed, declared_guards)
    scenarios = guard_rows(matrix["scenarios"], scenario_ids, final_tree, executed, declared_guards)
    front_variants = guard_rows(front_rows, FRONT_VARIANTS, final_tree, executed, declared_guards)
    restored_controls = guard_rows(restored_rows, RESTORED_CONTROLS, final_tree, executed, declared_guards)
    legacy_tests = preserved_legacy_tests(root, candidate_sha, next(row["head_sha"] for row in sources if row["pr"] == 466), executed)
    source_trees, source_reports, source_deltas = {}, [], set()
    product_sha = manifest["product"]["sha"]
    source_trees["product"] = tree(root, product_sha)
    require(git(root, "rev-parse", product_sha + "^{tree}") == manifest["product"]["tree"], "PRODUCT_TREE_CHANGED")
    for row in sources:
        sha = row["head_sha"]
        if fetch_source_refs:
            reference = "refs/porota/convergence-source/" + str(row["pr"])
            git(root, "fetch", "--no-tags", "origin", "refs/pull/" + str(row["pr"]) + "/head:" + reference)
            require(git(root, "rev-parse", reference) == sha, "SOURCE_PR_HEAD_CHANGED:" + str(row["pr"]))
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
                          "comparison": "EXACT_SOURCE_BYTES" if original == final else
                              "EVOLVED_REQUIRES_GUARDED_RECONCILIATION" if original else "SOURCE_REMOVAL_HISTORY"})
        source_reports.append({**row, "tree_sha": git(root, "rev-parse", sha + "^{tree}"),
                               "actual_delta_path_count": len(changed), "delta_paths": delta,
                               "final_promotion_authority": False})
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
        blobs = {key: values[path]["blob"] for key, values in source_trees.items() if path in values}
        expected = expected_sources.get(path)
        changed_expected = bool(expected and expected["blob"] != final["blob"])
        evolved_delta = path in source_deltas and any(values.get(path) and values[path] != final
                                                      for key, values in source_trees.items() if key != "product")
        new_or_changed_source = not any(blob == final["blob"] for blob in blobs.values())
        guard_ids, reason = [], None
        if changed_expected or evolved_delta or new_or_changed_source:
            proof = evolution.get(path, {})
            reason, guard_ids = proof.get("reason"), proof.get("requirement_ids", [])
            require(isinstance(reason, str) and bool(reason.strip()) and isinstance(guard_ids, list)
                    and bool(guard_ids) and all(item in by_id for item in guard_ids), "UNEXPLAINED_SOURCE_EVOLUTION:" + path)
        paths.append({"path": path, "final_blob": final["blob"], "git_mode": final["git_mode"],
                      "source_blobs": blobs, "byte_preserved_from": sorted(key for key, blob in blobs.items() if blob == final["blob"]),
                      "expected_input": expected, "evolution_reason": reason, "requirement_ids": guard_ids,
                      "workstreams": sorted({by_id[item].get("workstream", "CONVERGENCE_INTEGRATION") for item in guard_ids}),
                      "guard_nodes": sorted({node for item in guard_ids for node in by_id[item]["test_nodes"]}),
                      "preservation": "EVOLVED_WITH_NATIVE_GUARDS" if changed_expected or evolved_delta else
                          "ADDITIONAL_WITH_NATIVE_GUARDS" if new_or_changed_source else
                          "EXACT_INPUT_BYTES" if expected else "EXACT_BASELINE_OR_SOURCE_BYTES"})
    return {"schema": "rc6.final-input-provenance.v1", "candidate_sha": candidate_sha,
            "candidate_tree": git(root, "rev-parse", "HEAD^{tree}"), "product": manifest["product"],
            "sources": source_reports, "source_union_paths": len(expected_sources),
            "expected_source_paths_preserved": len(expected_sources), "final_path_count": len(paths), "paths": paths,
            "requirements": requirements, "scenarios": scenarios, "original_scenarios": original_scenarios,
            "front_variants": front_variants,
            "restored_controls": restored_controls, "preserved_test_modules_344": legacy_tests,
            "test_execution": {"junit_sha256": hashlib.sha256(junit.read_bytes()).hexdigest() if junit else None,
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
