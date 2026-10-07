"""Native Git/CLI adversarial validation; JUnit and closure inputs are fixtures.

Original handoff bytes and all fifteen original Git heads remain unchanged.
The controlled closure/JUnit fixture exercises the receipt validator only;
it does not certify market executions or implement all fifty-five requirements.
Predeploy fetches the original refs before running these network-free tests.
"""
from copy import deepcopy
import ast
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types
import xml.etree.ElementTree as ET

import pytest
import yaml

from scripts import rc6_convergence_provenance as provenance
from scripts import rc6_issue465_audit_gate as audit465


REPO = Path(__file__).resolve().parents[1]
GUARD = "tests/test_rc6_convergence_sre_binding.py::test_approved_unique_run_attempt_artifact_origin_is_green"
LEGACY_CASES = sorted(filename + "::" + item.name
    for filename in provenance.PRESERVED_TESTS_344
    for item in ast.parse((REPO / filename).read_bytes()).body
    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith("test_"))
SUCCESSOR_CASES = sorted(specification["successor_node"]
    for specification in provenance.prior_successions.SUCCESSORS.values())


def native_git(root, *arguments):
    process = subprocess.run(["git", "-C", str(root), *arguments], check=True,
        capture_output=True, text=True, timeout=90,
        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})
    return process.stdout.strip()


def commit(root):
    native_git(root, "add", "-A")
    native_git(root, "commit", "-qm", "isolated adversarial provenance fixture")
    return native_git(root, "rev-parse", "HEAD")


def write_json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    path.chmod(0o644)


def junit(path, *, outcome=None, duplicate=False, counters=None):
    nodes = [GUARD, *LEGACY_CASES, *SUCCESSOR_CASES]
    attrs = {"name": "fixture", "tests": str(len(nodes) * (2 if duplicate else 1)), "errors": "0",
        "failures": "0", "skipped": "0"}
    if outcome:
        attrs[{"failure": "failures", "error": "errors", "skipped": "skipped"}[outcome]] = "1"
    attrs.update(counters or {})
    root = ET.Element("testsuites"); suite = ET.SubElement(root, "testsuite", attrs)
    for _ in range(2 if duplicate else 1):
        for node in nodes:
            filename, function = node.split("::")
            case = ET.SubElement(suite, "testcase", {"classname": filename[:-3].replace("/", "."),
                "name": function, "time": "0.01"})
            if outcome: ET.SubElement(case, outcome, {"message": "controlled fixture nonpass"})
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def prepare_explicit_receipt_parser_seed(root):
    """Create complete synthetic Git INPUTS, never a runtime/artifact Source.

    Every original Git object remains real and read-only through the same
    native alternates mechanism the original fixtures used. Only this NEW
    private repository's ref/index/worktree is constructed; no existing
    checkout, Source object, fixture or resource is pruned or overwritten.
    The CLI and every imported validator still execute from complete REPO.
    """
    import stat

    source_sha = native_git(REPO, "rev-parse", "HEAD")
    source_tree_sha = native_git(REPO, "rev-parse", "HEAD^{tree}")
    final = provenance.tree(REPO, source_sha)
    input_directory = REPO / provenance.INPUT_ROOT
    manifest_raw = (input_directory / provenance.MANIFEST).read_bytes()
    assert hashlib.sha256(manifest_raw).hexdigest() == provenance.ORIGINAL_DIGESTS[provenance.MANIFEST]
    manifest = json.loads(manifest_raw)
    matrix = json.loads((REPO / audit465.MATRIX).read_bytes())
    original_sha = next(item["head_sha"] for item in manifest["sources"] if item["pr"] == 466)
    original_matrix = json.loads(provenance.git(REPO, "show", original_sha + ":" + audit465.MATRIX, binary=True))
    assert matrix["workstreams"] == original_matrix["workstreams"]
    revisions = {manifest["product"]["sha"]}
    revisions.update(item[key] for item in manifest["sources"] for key in ("head_sha", "base_sha"))
    revisions.update(item["head"] for item in original_matrix["workstreams"])
    originals, object_proofs = {}, []
    for revision in sorted(revisions):
        raw = provenance.git(REPO, "cat-file", "commit", revision, binary=True)
        assert hashlib.sha1(b"commit " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == revision
        tree_sha = next(line.split(b" ", 1)[1].decode() for line in raw.splitlines() if line.startswith(b"tree "))
        assert provenance.git(REPO, "rev-parse", revision + "^{tree}") == tree_sha
        originals[revision] = provenance.tree(REPO, revision)
        object_proofs.append({"sha": revision, "tree": tree_sha,
                              "raw_commit_sha256": hashlib.sha256(raw).hexdigest(),
                              "regular_paths_in_original_tree": len(originals[revision])})
    assert len(manifest["sources"]) == 15 and len(revisions) == 25
    assert provenance.git(REPO, "rev-parse", manifest["product"]["sha"] + "^{tree}") == manifest["product"]["tree"]
    for item in original_matrix["workstreams"]:
        assert provenance.git(REPO, "rev-parse", item["head"] + "^{tree}") == item["tree"]
    deltas, required = set(), set()
    for item in manifest["sources"]:
        head, baseline = originals[item["head_sha"]], originals[item["base_sha"]]
        changed = {name for name in set(head) | set(baseline) if head.get(name) != baseline.get(name)}
        deltas.update(changed)
        required.update(name for name in changed if name in head)
    expected = {item["path"] for rows in manifest["expected_source_paths"].values() for item in rows}
    assert len(expected) == 170 and len(required) == 175 and expected <= required
    required.update(expected)
    required.update(name for item in original_matrix["workstreams"] for name in item["paths"])
    required.update(provenance.PRESERVED_TESTS_344)
    required.update(binding["node"].split("::")[0] for row in original_matrix["findings"]
                    for clause in row["coverage"] for binding in clause["tests"])
    required.update(node.split("::")[0] for node in provenance.prior_successions.SUCCESSORS)
    required.update(provenance.INPUT_ROOT + "/" + name for name in
                    {*provenance.ORIGINAL_DIGESTS, "ORIGINAL_INPUT_DIGESTS.json",
                     provenance.SCENARIOS, provenance.CLOSURE})
    required.update({GUARD.split("::")[0], "scripts/porota_predeploy_binding.py",
                     "scripts/rc6_prior_regression_successors.py",
                     str(Path(provenance.__file__).resolve().relative_to(REPO))})
    # Preserve native Git ignore/attribute recipes, including ignored tracked
    # historical *.log bytes. This is fixture input derivation, not a runtime
    # module allowlist or a partial-source acceptance policy.
    required.update(name for name in (".gitignore", ".gitattributes") if name in final)
    comparisons = [originals[manifest["product"]["sha"]]]
    comparisons.extend(originals[item["head_sha"]] for item in manifest["sources"])
    witness = next(name for name, record in sorted(final.items()) if record["git_mode"] == "100644"
                   and name not in deltas and any(tree.get(name) == record for tree in comparisons))
    required.add(witness)
    assert witness == ".dockerignore" and final[witness] == {
        "git_mode": "100644", "blob": "cf4ba5888390bf0f34285286cef359f9f4191498"}
    assert len(required) == 210 and required <= set(final)

    native_git(REPO, "init", "--quiet", str(root))  # Fresh, physically empty worktree.
    native_git(root, "config", "user.name", "Offline provenance fixture")
    native_git(root, "config", "user.email", "fixture@example.invalid")
    native_git(root, "remote", "add", "origin", str(REPO))
    objects = Path(native_git(REPO, "rev-parse", "--git-path", "objects"))
    objects = objects if objects.is_absolute() else REPO / objects
    assert objects.absolute() == REPO / ".git/objects"
    for directory in (REPO / ".git", objects, objects / "info"):
        info = directory.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid()
    assert not os.path.lexists(objects / "info/alternates")
    alternate = root / ".git/objects/info/alternates"
    descriptor = os.open(alternate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(descriptor, "wb") as output:
        output.write(os.fsencode(str(objects.absolute())) + b"\n")
    for revision in sorted(revisions):
        native_git(root, "cat-file", "-e", revision + "^{tree}")
    # A NEW private unborn ref acquires the real REPO parent. No original ref
    # is replaced. The empty own index becomes the complete synthetic 210-tree;
    # no checkout, sparse index, trimming or Seed-as-Source gate is performed.
    private_ref = native_git(root, "symbolic-ref", "HEAD")
    native_git(root, "update-ref", private_ref, source_sha, "0" * 40)
    copied = {}
    for name in sorted(required):
        source = REPO / name
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
        try:
            before = os.fstat(descriptor)
            assert stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == os.geteuid()
            wanted_mode = 0o755 if final[name]["git_mode"] == "100755" else 0o644
            assert stat.S_IMODE(before.st_mode) == wanted_mode
            chunks = []
            while True:
                piece = os.read(descriptor, 65536)
                if not piece:
                    break
                chunks.append(piece)
            raw = b"".join(chunks)
            fields = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
                      "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
            assert all(getattr(before, field) == getattr(os.fstat(descriptor), field) ==
                       getattr(source.lstat(), field) for field in fields)
        finally:
            os.close(descriptor)
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == final[name]["blob"]
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fchmod(output.fileno(), wanted_mode)  # Only this newly created file.
        copied[name] = {**final[name], "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    # The initial index is explicit and COMPLETE for this synthetic tree; force
    # original ignored log members in once. Every later commit uses unchanged
    # native add -A and the original adversarial mutation recipe.
    native_git(root, "add", "-f", "--", *sorted(required))
    seed_sha = commit(root)
    assert provenance.tree(root, seed_sha) == {name: final[name] for name in required}
    assert native_git(root, "rev-parse", seed_sha + "^") == source_sha
    assert native_git(REPO, "rev-parse", "HEAD") == source_sha
    assert native_git(REPO, "rev-parse", "HEAD^{tree}") == source_tree_sha

    # Truthful physical accounting includes ALL own .git metadata. Alternates
    # is an ordinary own file; its object-store target is not traversed/count-
    # fabricated. Borrowed original objects are available, not materialized
    # here. The unchanged aggregate retained guard still measures all 91 later
    # clones, other fixtures, .git files, mutations and controls after real FIN.
    counts = {"namespace_entries_including_root": 0, "regular_files": 0, "directories": 0,
              "symlinks": 0, "special_entries": 0, "hardlinked_regular_entries": 0,
              "allocated_bytes_unique_physical_inodes": 0, "git_entries_including_git_root": 0}
    physical, observed = set(), {}
    pending = [(root, ".")]
    while pending:
        current, relative = pending.pop()
        info = current.lstat()
        counts["namespace_entries_including_root"] += 1
        if relative == ".git" or relative.startswith(".git/"):
            counts["git_entries_including_git_root"] += 1
        identity = (info.st_dev, info.st_ino)
        if identity not in physical:
            physical.add(identity)
            counts["allocated_bytes_unique_physical_inodes"] += info.st_blocks * 512
        observed[relative] = {"mode": info.st_mode, "nlink": info.st_nlink, "dev": info.st_dev,
                              "ino": info.st_ino, "uid": info.st_uid, "bytes": info.st_size}
        if stat.S_ISDIR(info.st_mode):
            counts["directories"] += 1
            descriptor = os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
            try:
                assert (os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino) == identity
                for name in sorted(os.listdir(descriptor), reverse=True):
                    pending.append((current / name, name if relative == "." else relative + "/" + name))
            finally:
                os.close(descriptor)
        elif stat.S_ISREG(info.st_mode):
            counts["regular_files"] += 1
            counts["hardlinked_regular_entries"] += int(info.st_nlink > 1)
        elif stat.S_ISLNK(info.st_mode):
            counts["symlinks"] += 1
        else:
            counts["special_entries"] += 1
    receipt = {"schema": "rc6.receipt-parser-explicit-synthetic-native-git-seed.v1",
               "assertion_scope": "EXPLICIT_SYNTHETIC_ONLY",
               "Source_acceptance_claimed": False, "runtime_or_artifact_source_claimed": False,
               "partial_runtime_module_allowlist": False, "native_resource_gate_claimed": False,
               "MAX_FILES_changed": False, "existing_fixtures_deleted": False,
               "source_sha": source_sha, "source_tree": source_tree_sha,
               "actual_complete_source_paths": len(final), "seed_sha": seed_sha,
               "seed_tree": native_git(root, "rev-parse", "HEAD^{tree}"),
               "real_source_parent_sha": native_git(root, "rev-parse", "HEAD^"),
               "source_original_objects": object_proofs, "real_readonly_object_directory": str(objects),
               "materialized_synthetic_worktree_paths": copied, "mode_only_witness": witness,
               "physical_seed_including_git": counts, "physical_seed_entries": observed,
               "object_store_target_followed_for_measurement": False,
               "borrowed_objects_claimed_as_materialized": False,
               "real_orders_sent": 0}
    receipt_path = root.parent / "receipt-parser-synthetic-seed.json"
    descriptor = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(receipt, output, sort_keys=True, indent=2)
        output.write("\n")


@pytest.fixture(scope="module")
def fixture_base(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-convergence-base") / "repo"
    prepare_explicit_receipt_parser_seed(root)
    native_git(root, "config", "user.name", "Offline provenance fixture")
    native_git(root, "config", "user.email", "fixture@example.invalid")
    inputs = root / provenance.INPUT_ROOT; inputs.mkdir(parents=True, exist_ok=True)
    for name in [*provenance.ORIGINAL_DIGESTS, "ORIGINAL_INPUT_DIGESTS.json", provenance.SCENARIOS]:
        shutil.copyfile(REPO / provenance.INPUT_ROOT / name, inputs / name)
        (inputs / name).chmod(0o644)
    # Every original object must already be available. The tests never fetch,
    # skip a missing source, or replace the immutable original manifest.
    original = json.loads((inputs / provenance.MANIFEST).read_text())
    for source in original["sources"]:
        native_git(root, "cat-file", "-e", source["head_sha"] + "^{tree}")
        native_git(root, "cat-file", "-e", source["base_sha"] + "^{tree}")
    def rows(ids):
        return [{"id": item, "rca": "Controlled receipt-parser fixture, not requirement closure.",
            "fix": "Validate exact source inventory and executed-guard linking.",
            "native_caller": "Offline convergence CLI only; no market/provider runtime.",
            "remaining_uncertainty": "Synthetic XML is not external execution authentication.",
            "assertion_scope": "EXPLICIT_SYNTHETIC_ONLY",
            "code_paths": ["scripts/porota_predeploy_binding.py"], "test_nodes": [GUARD]}
            for item in sorted(ids)]
    original_front = provenance.original_front_variants(
        (inputs / "ORIGINAL_RA_A_F01_F02.md").read_text(),
        (inputs / "ORIGINAL_RA_A_F03_F05.md").read_text())
    with (inputs / provenance.REGISTRY).open(newline="") as source:
        original_requirements = {row["id"]: row for row in csv.DictReader(source)}
    original_scenarios = {row["id"]: row for row in
        json.loads((inputs / provenance.SCENARIOS).read_text())["rows"]}
    requirements = rows(provenance.REQUIREMENTS)
    for specification in provenance.prior_successions.SUCCESSORS.values():
        for identifier in specification["linked_requirement_ids"]:
            next(row for row in requirements if row["id"] == identifier)["test_nodes"].append(
                specification["successor_node"])
    scenarios = rows({f"R{i:02d}" for i in range(1, 81)})
    for row in requirements:
        row["original_requirement"] = original_requirements[row["id"]]
    for row in scenarios:
        row["original_scenario"] = original_scenarios[row["id"]]
    front = rows(provenance.FRONT_VARIANTS)
    for row in front:
        row["original_columns"] = original_front[row["id"]]
    # Fresh pinned inputs and the controlled closure file itself are additional
    # committed sources in this fixture. List them explicitly before commit.
    paths = set(provenance.tree(root, "HEAD"))
    paths.update(str(path.relative_to(root)) for path in inputs.iterdir() if path.is_file())
    paths.add(provenance.INPUT_ROOT + "/" + provenance.CLOSURE)
    matrix = {"schema": "rc6.convergence-closure.v1", "requirements": requirements,
        "scenarios": scenarios,
        "front_variants": front,
        "restored_controls": rows(provenance.RESTORED_CONTROLS),
        "additional_findings": [{**row, "linked_requirement_ids": ["U11"]}
                                for row in rows(provenance.DISCOVERED_CONVERGENCE_FINDINGS | {"RC6_INT_RECEIPT_PARSER"})],
        "path_evolution": {path: {"reason": "Controlled fixture evolution requires a referenced guard.",
            "requirement_ids": ["U01"]} for path in paths}}
    write_json(inputs / provenance.CLOSURE, matrix)
    commit(root)
    return root


@pytest.fixture
def candidate(fixture_base, tmp_path):
    root = tmp_path / "repo"
    native_git(fixture_base, "clone", "--quiet", "--shared", "--no-hardlinks", str(fixture_base), str(root))
    native_git(root, "config", "user.name", "Offline provenance fixture")
    native_git(root, "config", "user.email", "fixture@example.invalid")
    xml = tmp_path / "actual-input-fixture.xml"; junit(xml)
    return root, xml, tmp_path / "report.json"


def cli(candidate, sha=None, *, inventory=False, fetch_source_refs=False):
    root, xml, output = candidate
    arguments = [sys.executable, str(REPO / "scripts/rc6_convergence_provenance.py"),
        "--repo-root", str(root), "--candidate-sha", sha or native_git(root, "rev-parse", "HEAD"),
        "--out", str(output)]
    if not inventory: arguments += ["--junit", str(xml)]
    if fetch_source_refs: arguments += ["--fetch-source-refs"]
    return subprocess.run(arguments, cwd=root, capture_output=True, text=True, timeout=90)


def assert_rejected(candidate, signature, **kwargs):
    result = cli(candidate, **kwargs)
    assert result.returncode != 0
    signatures = (signature,) if isinstance(signature, str) else signature
    assert any(item in result.stderr for item in signatures)
    assert not candidate[2].exists()


def path_guard_proof():
    """Controlled representation only; these rows claim no test execution."""
    return {"schema": "rc6.final-input-provenance.v1",
        "path_guard_derivation": provenance.PATH_GUARD_DERIVATION,
        "requirements": [
            {"id": "U04", "test_nodes": [SUCCESSOR_CASES[0], GUARD]},
            {"id": "U24", "test_nodes": [GUARD]},
            {"id": "U29", "test_nodes": [SUCCESSOR_CASES[-1]]}],
        "paths": [{"path": "controlled-changed", "requirement_ids": ["U24", "U04"]},
                  {"path": "controlled-unchanged", "requirement_ids": []}]}


def test_path_guard_derivation_preserves_historical_semantics_and_roundtrip():
    proof = path_guard_proof()
    roundtrip = provenance.read_json((json.dumps(proof, sort_keys=True, indent=2) + "\n").encode())
    expected = [sorted({GUARD, SUCCESSOR_CASES[0]}), []]
    assert [provenance.path_guard_nodes(roundtrip, row) for row in roundtrip["paths"]] == expected
    assert roundtrip == proof
    historical = deepcopy(roundtrip)
    historical.pop("path_guard_derivation")
    for row, nodes in zip(historical["paths"], expected):
        row["guard_nodes"] = nodes
    assert [provenance.path_guard_nodes(historical, row) for row in historical["paths"]] == expected
    # Original controlled verifier-result fixtures predate requirement.test_nodes.
    for requirement in historical["requirements"]:
        requirement.pop("test_nodes")
    assert [provenance.path_guard_nodes(historical, row) for row in historical["paths"]] == expected


@pytest.mark.parametrize("attack,signature", [
    ("unknown_marker", "PATH_GUARD_DERIVATION_INVALID"),
    ("null_marker", "PATH_GUARD_DERIVATION_INVALID"),
    ("missing_marker", "PATH_GUARD_INLINE_INVALID"),
    ("shadow_inline", "PATH_GUARD_SHADOW_INLINE"),
    ("empty_shadow_inline", "PATH_GUARD_SHADOW_INLINE"),
    ("null_shadow_inline", "PATH_GUARD_SHADOW_INLINE"),
    ("other_path_shadow_inline", "PATH_GUARD_SHADOW_INLINE"),
    ("missing_reference", "PATH_GUARD_REFERENCE_INVALID"),
    ("unknown_reference", "PATH_GUARD_REFERENCE_INVALID"),
    ("unhashable_reference", "PATH_GUARD_REFERENCE_INVALID"),
    ("duplicate_reference", "PATH_GUARD_REFERENCE_DUPLICATE"),
    ("other_path_unknown_reference", "PATH_GUARD_REFERENCE_INVALID"),
    ("missing_requirements", "PATH_GUARD_REQUIREMENTS_MISSING"),
    ("duplicate_requirement", "PATH_GUARD_REQUIREMENT_DUPLICATE"),
    ("invalid_requirement", "PATH_GUARD_REQUIREMENT_INVALID"),
    ("missing_nodes", "PATH_GUARD_TEST_NODES_INVALID"),
    ("unreferenced_missing_nodes", "PATH_GUARD_TEST_NODES_INVALID"),
    ("empty_nodes", "PATH_GUARD_TEST_NODES_INVALID"),
    ("invalid_node", "PATH_GUARD_TEST_NODES_INVALID"),
    ("unhashable_node", "PATH_GUARD_TEST_NODES_INVALID"),
    ("duplicate_node", "PATH_GUARD_TEST_NODES_DUPLICATE"),
    ("invalid_paths", "PATH_GUARD_PATHS_INVALID"),
    ("invalid_path", "PATH_GUARD_PATH_INVALID"),
    ("duplicate_inline_node", "PATH_GUARD_INLINE_DUPLICATE"),
    ("invalid_inline_node", "PATH_GUARD_INLINE_INVALID")])
def test_path_guard_derivation_rejects_ambiguous_or_unbound_representations(attack, signature):
    proof = path_guard_proof()
    row = proof["paths"][0]
    requirement = proof["requirements"][0]
    if attack == "unknown_marker": proof["path_guard_derivation"] = "sorted-union.v2"
    elif attack == "null_marker": proof["path_guard_derivation"] = None
    elif attack == "missing_marker": proof.pop("path_guard_derivation")
    elif attack == "shadow_inline": row["guard_nodes"] = [GUARD]
    elif attack == "empty_shadow_inline": row["guard_nodes"] = []
    elif attack == "null_shadow_inline": row["guard_nodes"] = None
    elif attack == "other_path_shadow_inline": proof["paths"][1]["guard_nodes"] = []
    elif attack == "missing_reference": row.pop("requirement_ids")
    elif attack == "unknown_reference": row["requirement_ids"] = ["U01"]
    elif attack == "unhashable_reference": row["requirement_ids"] = [["U04"]]
    elif attack == "duplicate_reference": row["requirement_ids"].append("U04")
    elif attack == "other_path_unknown_reference": proof["paths"][1]["requirement_ids"] = ["U01"]
    elif attack == "missing_requirements": proof.pop("requirements")
    elif attack == "duplicate_requirement": proof["requirements"].append(deepcopy(requirement))
    elif attack == "invalid_requirement": requirement["id"] = "UNBOUND"
    elif attack == "missing_nodes": requirement.pop("test_nodes")
    elif attack == "unreferenced_missing_nodes": proof["requirements"][-1].pop("test_nodes")
    elif attack == "empty_nodes": requirement["test_nodes"] = []
    elif attack == "invalid_node": requirement["test_nodes"] = [GUARD.replace("tests/", "tests/../tests/")]
    elif attack == "unhashable_node": requirement["test_nodes"] = [[GUARD]]
    elif attack == "duplicate_node": requirement["test_nodes"].append(GUARD)
    elif attack == "invalid_paths": proof["paths"] = None
    elif attack == "invalid_path": proof["paths"].append(None)
    elif attack in {"duplicate_inline_node", "invalid_inline_node"}:
        proof.pop("path_guard_derivation")
        row["guard_nodes"] = [GUARD, GUARD] if attack == "duplicate_inline_node" else ["not-a-native-node"]
    else: raise AssertionError(attack)
    with pytest.raises(provenance.ConvergenceError, match=signature):
        provenance.path_guard_nodes(proof, row)


def test_native_git_cli_preserves_all_original_sources_and_truthful_overlap_counts(candidate):
    result = cli(candidate)
    assert result.returncode == 0, result.stderr
    report = provenance.read_json(candidate[2].read_bytes())
    assert report["path_guard_derivation"] == provenance.PATH_GUARD_DERIVATION
    by_id = {row["id"]: row for row in report["requirements"]}
    for row in report["paths"]:
        assert "guard_nodes" not in row
        expected = sorted({node for identifier in row["requirement_ids"] for node in by_id[identifier]["test_nodes"]})
        assert provenance.path_guard_nodes(report, row) == expected
    assert report["candidate_sha"] == native_git(candidate[0], "rev-parse", "HEAD")
    assert report["candidate_tree"] == native_git(candidate[0], "rev-parse", "HEAD^{tree}")
    assert len(report["sources"]) == 15 and report["source_union_paths"] == 170
    assert len(report['prior_frozen_fronts']) == 7
    assert sum(len(row['files']) for row in report['prior_frozen_fronts']) == 45
    assert len(report["requirements"]) == 55 and len(report["scenarios"]) == 80
    assert len(report["front_variants"]) == 90
    assert {row["id"] for row in report["front_variants"]} == provenance.FRONT_VARIANTS
    assert all(row["assertion_scope"] == "EXPLICIT_SYNTHETIC_ONLY" for row in report["front_variants"])
    assert report["test_execution"]["executed_unique_cases"] == 1 + len(LEGACY_CASES) + len(SUCCESSOR_CASES)
    assert len(report["prior_regression_successions"]) == 5
    assert provenance.DISCOVERED_CONVERGENCE_FINDINGS <= {row['id'] for row in report['additional_findings']}
    assert all(row["preservation"] == "TYPED_SUCCESSOR" for row in report["prior_regression_successions"])
    assert {row["id"] for row in report["restored_controls"]} == provenance.RESTORED_CONTROLS
    assert len(report["preserved_test_modules_344"]) == 12
    assert sum(row["preservation"] == "EXACT_BYTES" for row in report["preserved_test_modules_344"]) == 10
    assert report["test_execution"]["overlapping_suite_counts_added"] is False
    assert report["merge_authorized"] is report["deploy_authorized"] is False
    assert report["runtime"] == report["provider_open_capacity"] == "NO_VERIFICADO"
    assert report["economic_edge"] == "NO_DEMOSTRADO"
    assert report['final_candidate_eligible'] is False
    assert report["assertion_scope"] == "OFFLINE_CODE_AND_FROZEN_TEST_RECEIPTS; NOT_RUNTIME_ATTESTATION"


def test_native_inventory_without_junit_cannot_claim_executed_guards(candidate):
    result = cli(candidate, inventory=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(candidate[2].read_text())
    assert report["software_status"] == "INVENTORY_NOT_EXECUTED"
    assert report["test_execution"]["executed_unique_cases"] is None
    assert all(row["software_status"] == "NOT_EXECUTED" for row in report["requirements"])


@pytest.mark.parametrize('all_rows', [False, True])
def test_previously_discovered_findings_cannot_be_omitted_from_a_clean_candidate(candidate, all_rows):
    root = candidate[0]
    path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    matrix = json.loads(path.read_bytes())
    if all_rows:
        matrix['additional_findings'] = []
    else:
        target = sorted(provenance.DISCOVERED_CONVERGENCE_FINDINGS)[0]
        matrix['additional_findings'] = [row for row in matrix['additional_findings'] if row['id'] != target]
    write_json(path, matrix)
    commit(root)
    assert_rejected(candidate, 'DISCOVERED_CONVERGENCE_FINDING_OMITTED')


def test_executed_mapped_guards_do_not_close_pending_material_programming_gates(candidate):
    root = candidate[0]
    path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    matrix = json.loads(path.read_bytes())
    row = next(row for row in matrix['requirements'] if row['id'] == 'U14')
    row['disposition'] = 'MATERIAL_RESOURCE_GATE_PENDING_REMEDIATION'
    row['remaining_uncertainty'] = 'Explicit controlled missing complete physical archive horizon.'
    matrix['status'] = 'CLOSED_WITH_NATIVE_RESOURCE_GATES'
    write_json(path, matrix)
    commit(root)
    result = cli(candidate)
    assert result.returncode == 0, result.stderr
    report = json.loads(candidate[2].read_bytes())
    assert report['software_status'] == 'EXECUTED_NATIVE_GREEN'
    assert report['material_programming_gates_closed'] is False
    assert report['final_candidate_eligible'] is False
    assert [row['id'] for row in report['pending_material_programming_gates']] == ['U14']


def prior_audit_parser_fixture(root, xml):
    matrix = json.loads((root / audit465.MATRIX).read_bytes())
    document = ET.parse(xml)
    suite = document.find('.//testsuite')
    existing = {(case.get('classname'), case.get('name')) for case in suite}
    for finding in matrix['findings']:
        for clause in finding['coverage']:
            for binding in clause['tests']:
                path, function = binding['node'].split('::')
                classname = path[:-3].replace('/', '.')
                for index in range(binding.get('minimum_cases', 1)):
                    name = function + '[controlled-parser-' + str(index) + ']'
                    if (classname, name) not in existing:
                        ET.SubElement(suite, 'testcase', classname=classname, name=name, time='0')
                        existing.add((classname, name))
    count = len(list(suite))
    suite.set('tests', str(count))
    document.write(xml, encoding='utf-8', xml_declaration=True)
    governed = root.parent / 'controlled-prior-audit-governed.json'
    write_json(governed, {'scope': 'repository-root automatic pytest discovery', 'status': 'GREEN',
        'candidate_sha': native_git(root, 'rev-parse', 'HEAD'),
        'candidate_tree': native_git(root, 'rev-parse', 'HEAD^{tree}'),
        'junit_sha256': hashlib.sha256(xml.read_bytes()).hexdigest(), 'junit_bytes': xml.stat().st_size,
        'source_unchanged': True,
        'pytest_exit_code': 0, 'executed': count, 'discovered': count, 'failures': 0, 'errors': 0,
        'skipped': 0, 'xfail': 0, 'exclusions': matrix['governed_exclusions']})
    return governed


def test_prior_audit_gate_accepts_actual_native_convergence_parser_with_controlled_junit(candidate):
    """Real original Git/authority and both validators; XML is a parser fixture."""
    root, xml, _ = candidate
    governed = prior_audit_parser_fixture(root, xml)
    result = audit465.verify(root, xml, governed)
    assert result['status'] == 'GREEN'
    assert result['front_preservation'] == 'GUARDED_CONVERGENCE_SUCCESSOR'
    assert result['convergence_baseline'] == 'c27dfd963c4fe83465c0f2105347e974fbbe6356'
    assert len(result['workstream_heads']) == 7
    assert result['evolved_fronts']
    assert len(result['prior_regression_successions']) == 5
    assert result['original_test_bindings_preserved'] == result['required_test_functions_executed']
    assert result['convergence_junit_sha256'] == hashlib.sha256(xml.read_bytes()).hexdigest()
    assert result['safety']['deploy'] is False and result['independent_reaudit'] == 'PENDING'


def test_phantom_old_JUnit_names_cannot_replace_absent_native_successor_execution(candidate):
    """Old synthetic case names exist, but cannot authorize missing current guards."""
    root, xml, _ = candidate
    governed = prior_audit_parser_fixture(root, xml)
    target = SUCCESSOR_CASES[0]
    path, function = target.split('::')
    document = ET.parse(xml)
    suite = document.find('.//testsuite')
    for case in list(suite):
        if case.get('classname') == path[:-3].replace('/', '.') and case.get('name').split('[', 1)[0] == function:
            suite.remove(case)
    count = len(list(suite))
    suite.set('tests', str(count))
    document.write(xml, encoding='utf-8', xml_declaration=True)
    proof = json.loads(governed.read_bytes())
    proof.update(executed=count, discovered=count,
                 junit_sha256=hashlib.sha256(xml.read_bytes()).hexdigest(), junit_bytes=xml.stat().st_size)
    write_json(governed, proof)
    with pytest.raises(audit465.AuditGateError, match='CONVERGENCE_SUCCESSOR_NOT_VERIFIED'):
        audit465.verify(root, xml, governed)


def test_late_nonfront_tracked_mutation_is_rejected_after_actual_full_provenance(candidate, monkeypatch):
    """Real validators and Git; XML is an explicit controlled parser input."""
    root, xml, _ = candidate
    governed = prior_audit_parser_fixture(root, xml)
    original = audit465._guarded_front_path
    helper = root / 'scripts/rc6_prior_regression_successors.py'
    assert not any(helper.relative_to(root).as_posix() in row['paths']
                   for row in json.loads((root / audit465.MATRIX).read_bytes())['workstreams'])
    changed = []
    def mutate_after_full_proof(*args):
        result = original(*args)
        if not changed:
            helper.write_bytes(helper.read_bytes() + b'\n# controlled late tracked mutation\n')
            changed.append(True)
        return result
    monkeypatch.setattr(audit465, '_guarded_front_path', mutate_after_full_proof)
    with pytest.raises(audit465.AuditGateError, match='TRACKED_CHECKOUT_CHANGED'):
        audit465.verify(root, xml, governed)
    assert changed == [True]


def test_original_gate_receipt_binding_counterexample_and_current_native_rejection(candidate):
    """Real old/current validators; controlled XML is not execution authentication."""
    root, xml, _ = candidate
    governed = prior_audit_parser_fixture(root, xml)
    proof = json.loads(governed.read_bytes())
    proof.update(candidate_sha='0' * 40, candidate_tree='0' * 40, junit_sha256='0' * 64)
    write_json(governed, proof)
    old_sha = '003dccb5d07ebf7f443e58e0bb6499837a0a8a52'
    old_source = provenance.git(REPO, 'show', old_sha + ':scripts/rc6_issue465_audit_gate.py', binary=True)
    old = types.ModuleType('controlled_original_receipt_binding_gate')
    exec(compile(old_source, old_sha + ':scripts/rc6_issue465_audit_gate.py', 'exec'), old.__dict__)
    def historical_inline(*arguments, **options):
        native = provenance.verify(*arguments, **options)
        inline = {**native, 'paths': [{**row, 'guard_nodes': provenance.path_guard_nodes(native, row)}
                                    for row in native['paths']]}
        inline.pop('path_guard_derivation')
        return inline
    # Bridge only the representation expected by the frozen consumer. Keep the
    # native verifier, source authority and JUnit checks, and isolate the adapter
    # from the current gate's imported convergence module.
    old.convergence = types.SimpleNamespace(**vars(provenance))
    old.convergence.verify = historical_inline
    assert audit465.convergence is provenance and old.convergence is not provenance
    # Preserve the actual native validator counterexample at its immutable old
    # source, without presenting synthetic case metadata as a real governed run.
    assert old.verify(root, xml, governed)['status'] == 'GREEN'
    with pytest.raises(audit465.AuditGateError, match='GOVERNED_EXACT_RECEIPT_MISMATCH'):
        audit465.verify(root, xml, governed)


def test_rewritten_legacy_matrix_cannot_hide_evolved_fronts_behind_unchanged_paths(candidate):
    root, xml, _ = candidate
    governed = prior_audit_parser_fixture(root, xml)
    path = root / audit465.MATRIX
    matrix = json.loads(path.read_bytes())
    for stream in matrix['workstreams']:
        unchanged = [name for name in stream['paths']
            if audit465._source_record(root, stream['head'], name)
            == audit465._source_record(root, 'HEAD', name)]
        assert unchanged
        stream['paths'] = unchanged[:1]
    write_json(path, matrix)
    commit(root)
    with pytest.raises(audit465.AuditGateError, match='CONVERGENCE_LEGACY_AUTHORITY_CHANGED'):
        audit465.verify(root, xml, governed)


def test_single_junit_snapshot_binds_parsed_cases_and_digest_despite_later_file_substitution(candidate, monkeypatch):
    root, xml, _ = candidate
    original = xml.read_bytes()
    parse = provenance.executed_cases
    def substitute(receipt):
        assert type(receipt) is provenance.JunitReceipt
        xml.write_bytes(original.replace(GUARD.split('::')[1].encode(), b'test_unexecuted_substitute'))
        return parse(receipt)
    monkeypatch.setattr(provenance, 'executed_cases', substitute)
    result = provenance.verify(root, native_git(root, 'rev-parse', 'HEAD'), xml)
    assert xml.read_bytes() != original
    assert result['test_execution']['junit_sha256'] == hashlib.sha256(original).hexdigest()
    assert result['software_status'] == 'EXECUTED_NATIVE_GREEN'


@pytest.mark.parametrize('change', ['head', 'dirty'])
def test_candidate_cannot_change_during_native_convergence_inventory(candidate, monkeypatch, change):
    root, xml, _ = candidate
    sha = native_git(root, 'rev-parse', 'HEAD')
    original_tree = provenance.tree
    calls = []
    def mutate(actual_root, revision):
        result = original_tree(actual_root, revision)
        if calls and not any(calls):
            (root / 'AGENTS.md').write_bytes((root / 'AGENTS.md').read_bytes() + b'\ncontrolled late source mutation\n')
            if change == 'head':
                commit(root)
            calls.append(True)
        else:
            calls.append(False)
        return result
    monkeypatch.setattr(provenance, 'tree', mutate)
    reason = 'CANDIDATE_CHANGED_DURING_VERIFICATION' if change == 'head' else 'TRACKED_CHECKOUT_CHANGED'
    with pytest.raises(provenance.ConvergenceError, match=reason):
        provenance.verify(root, sha, xml)
    assert any(calls)


@pytest.mark.parametrize('kind', ['fifo', 'directory', 'symlink', 'hardlink', 'oversize'])
def test_junit_capture_rejects_nonregular_aliased_or_oversize_inputs_without_blocking(tmp_path, kind):
    source = tmp_path / 'junit-input'
    if kind == 'fifo': os.mkfifo(source)
    elif kind == 'directory': source.mkdir()
    elif kind in {'symlink', 'hardlink'}:
        target = tmp_path / 'other.xml'; target.write_bytes(b'<testsuites/>')
        if kind == 'symlink': source.symlink_to(target)
        else: os.link(target, source)
    else:
        with source.open('wb') as output: output.truncate(16 * 1024**2 + 1)
    result = subprocess.run([sys.executable, '-c',
        'from pathlib import Path; import sys; from scripts.rc6_convergence_provenance import capture_junit; capture_junit(Path(sys.argv[1]))', str(source)],
        cwd=REPO, capture_output=True, text=True, timeout=3)
    assert result.returncode != 0 and 'JUNIT_INPUT_INVALID' in result.stderr


@pytest.mark.parametrize("wrong_head", [False, True])
def test_original_pr_refs_are_checked_by_real_fetch_from_isolated_local_origin(candidate, tmp_path, wrong_head):
    root = candidate[0]; origin = tmp_path / "isolated-origin.git"
    native_git(root, "clone", "--quiet", "--bare", "--shared", str(root), str(origin))
    manifest = json.loads((root / provenance.INPUT_ROOT / provenance.MANIFEST).read_text())
    for index, source in enumerate(manifest["sources"]):
        sha = native_git(root, "rev-parse", "HEAD") if wrong_head and index == 0 else source["head_sha"]
        native_git(origin, "update-ref", f"refs/pull/{source['pr']}/head", sha)
    for stream in json.loads((root / audit465.MATRIX).read_bytes())['workstreams']:
        native_git(origin, 'update-ref', 'refs/heads/' + stream['branch'], stream['head'])
    native_git(root, "remote", "set-url", "origin", str(origin))
    if wrong_head:
        assert_rejected(candidate, "SOURCE_PR_HEAD_CHANGED", fetch_source_refs=True)
    else:
        result = cli(candidate, fetch_source_refs=True)
        assert result.returncode == 0, result.stderr
        for source in manifest["sources"]:
            assert native_git(root, "rev-parse", f"refs/porota/convergence-source/{source['pr']}") == source["head_sha"]
        for stream in json.loads((root / audit465.MATRIX).read_bytes())['workstreams']:
            assert native_git(root, 'rev-parse', 'refs/porota/convergence-prior-front/' + stream['id']) == stream['head']


def test_cold_native_fetch_recovers_all_historical_fronts_without_candidate_ancestry(candidate, tmp_path):
    root, xml, _ = candidate
    origin = tmp_path / 'cold-origin.git'
    native_git(root, 'clone', '--quiet', '--bare', '--shared', str(root), str(origin))
    manifest = json.loads((root / provenance.INPUT_ROOT / provenance.MANIFEST).read_bytes())
    streams = json.loads((root / audit465.MATRIX).read_bytes())['workstreams']
    for source in manifest['sources']:
        native_git(origin, 'update-ref', f"refs/pull/{source['pr']}/head", source['head_sha'])
    for stream in streams:
        native_git(origin, 'update-ref', 'refs/heads/' + stream['branch'], stream['head'])
    # The local bootstrap may contain exact original commit/tree objects but
    # lack older ancestors. Declare only that actual missing-parent frontier
    # in this temporary origin; never fabricate history or change ROOT.
    pending = [native_git(root, 'rev-parse', 'HEAD')]
    pending += [source[key] for source in manifest['sources'] for key in ('head_sha', 'base_sha')]
    pending += [stream['head'] for stream in streams]
    visited, frontier, complete = set(), set(), {}
    while pending:
        revision = pending.pop()
        if revision in visited: continue
        visited.add(revision)
        header = native_git(origin, 'cat-file', '-p', revision).split('\n\n', 1)[0]
        for line in header.splitlines():
            if not line.startswith('parent '): continue
            parent = line.split()[1]
            if parent not in complete:
                objects = subprocess.run(['git', '-C', str(origin), 'rev-list', '--objects',
                    '--no-walk', '--missing=print', parent], capture_output=True)
                complete[parent] = objects.returncode == 0 and not any(
                    item.startswith(b'?') for item in objects.stdout.splitlines())
            if not complete[parent]: frontier.add(revision)
            else: pending.append(parent)
    if frontier:
        (origin / 'shallow').write_text('\n'.join(sorted(frontier)) + '\n')
    cold = tmp_path / 'cold-repo'; native_git(root, 'init', '--quiet', str(cold))
    native_git(cold, 'remote', 'add', 'origin', str(origin))
    # An orphan with the same current tree proves fetch ordering without an
    # inherited c27 commit or any original front/source ancestry.
    tree_sha = native_git(root, 'rev-parse', 'HEAD^{tree}')
    sha = native_git(origin, '-c', 'user.name=Offline cold fixture', '-c',
        'user.email=cold@example.invalid', 'commit-tree', tree_sha, '-m', 'controlled ancestry-free fixture')
    native_git(origin, 'update-ref', 'refs/heads/controlled-orphan', sha)
    native_git(cold, 'fetch', '--quiet', '--no-tags', 'origin', sha)
    native_git(cold, 'checkout', '--quiet', '--detach', 'FETCH_HEAD')
    assert not (cold / '.git/objects/info/alternates').exists()
    for head in [source['head_sha'] for source in manifest['sources']] + [stream['head'] for stream in streams]:
        missing = subprocess.run(['git', '-C', str(cold), 'cat-file', '-e', head + '^{tree}'],
            capture_output=True, check=False)
        assert missing.returncode != 0
    output = tmp_path / 'cold-proof.json'
    result = cli((cold, xml, output), fetch_source_refs=True)
    assert result.returncode == 0, result.stderr
    proof = json.loads(output.read_bytes())
    assert len(proof['prior_frozen_fronts']) == 7
    for stream in streams:
        assert native_git(cold, 'rev-parse', 'refs/porota/convergence-prior-front/' + stream['id']) == stream['head']
    for source in manifest['sources']:
        assert native_git(cold, 'rev-parse', 'refs/porota/convergence-source-base/' + str(source['pr'])) == source['base_sha']


def test_predeploy_inventory_fetches_before_governed_tests_and_the_only_build():
    workflow = yaml.safe_load((REPO / ".github/workflows/porota-predeploy-v2.yml").read_text())
    steps = workflow["jobs"]["artifact-gate"]["steps"]
    names = [step["name"] for step in steps]
    inventory = names.index("Inventory original convergence source refs before governed tests")
    tests = names.index("Governed automatic test discovery and execution")
    build = names.index("Build candidate exactly once")
    final = names.index("Final convergence input and guard provenance")
    assert inventory < tests < final < build
    assert "--fetch-source-refs" in steps[inventory]["run"] and "--junit" not in steps[inventory]["run"]
    assert '--junit "${POROTA_PREDEPLOY_TMP}/porota-governed-tests.xml"' in steps[final]["run"]
    assert "final_material_receipt_binding(" in steps[final]["run"]
    assert '''Path(os.path.join(os.environ["POROTA_PREDEPLOY_TMP"], 'porota-governed-tests.json'))''' in steps[final]["run"]
    assert '''Path(os.path.join(os.environ["POROTA_PREDEPLOY_TMP"], 'porota-governed-tests.xml'))''' in steps[final]["run"]
    assert sum("docker build" in step.get("run", "") for step in steps) == 1


@pytest.fixture
def final_build_receipts(candidate):
    """Controlled metadata/JUnit inputs; no final source acceptance claim."""
    root, xml, _ = candidate
    receipt = provenance.capture_junit(xml)
    _nodes, count = provenance.executed_cases(receipt)
    sha = native_git(root, "rev-parse", "HEAD")
    tree_sha = native_git(root, "rev-parse", "HEAD^{tree}")
    original = provenance.read_json(provenance.git(root, "show",
        "c27dfd963c4fe83465c0f2105347e974fbbe6356:" + provenance.PRIOR_AUDIT_MATRIX, binary=True))
    proof = {"schema": "rc6.final-input-provenance.v1", "candidate_sha": sha,
        "candidate_tree": tree_sha, "software_status": "EXECUTED_NATIVE_GREEN",
        "final_candidate_eligible": True, "material_programming_gates_closed": True,
        "pending_material_programming_gates": [],
        "test_execution": {"junit_sha256": receipt.sha256, "executed_unique_cases": count}}
    governed = {"schema_version": 1, "candidate_sha": sha, "candidate_tree": tree_sha,
        "status": "GREEN", "source_unchanged": True,
        "scope": "repository-root automatic pytest discovery", "junit_sha256": receipt.sha256,
        "junit_bytes": len(receipt.data), "discovered": count, "executed": count,
        "exclusions": original["governed_exclusions"],
        **{name: 0 for name in ("pytest_exit_code", "failures", "errors", "skipped", "xfail")}}
    return root, sha, tree_sha, proof, governed, xml


def test_final_prebuild_binds_one_governed_capture_and_original_exclusion_authority(final_build_receipts):
    result = provenance.final_material_receipt_binding(*final_build_receipts)
    assert result["status"] == "GREEN"
    assert result["junit_sha256"] == final_build_receipts[3]["test_execution"]["junit_sha256"]
    assert result["executed_unique_cases"] == final_build_receipts[4]["executed"]
    assert result["scope"] == "PREBUILD_SOURCE_AND_GOVERNED_RECEIPT_BINDING_ONLY"


@pytest.mark.parametrize("mutation,signature", [
    ("fip_hash", "JUNIT_BINDING_INVALID"), ("fip_count", "JUNIT_BINDING_INVALID"),
    ("fip_boolean_count", "JUNIT_BINDING_INVALID"), ("junit_changed", "JUNIT_BINDING_INVALID"),
    ("gov_hash", "GOVERNED_BINDING_INVALID"), ("gov_bytes", "GOVERNED_BINDING_INVALID"),
    ("gov_count", "GOVERNED_BINDING_INVALID"), ("gov_boolean_zero", "GOVERNED_BINDING_INVALID"),
    ("gov_head", "GOVERNED_BINDING_INVALID"), ("gov_scope", "GOVERNED_BINDING_INVALID"),
    ("source_changed", "GOVERNED_BINDING_INVALID"), ("eligible_integer", "GATES_NOT_CLOSED"),
    ("pending_dict", "GATES_NOT_CLOSED"), ("exclusions_missing", "EXCLUSION_POLICY_DRIFT"),
    ("exclusions_added", "EXCLUSION_POLICY_DRIFT"), ("exclusions_rebound", "EXCLUSION_POLICY_DRIFT"),
])
def test_final_prebuild_rejects_changed_raw_receipts_and_typed_metadata(final_build_receipts, mutation, signature):
    root, sha, tree_sha, proof, governed, xml = final_build_receipts
    if mutation == "fip_hash": proof["test_execution"]["junit_sha256"] = "f" * 64
    elif mutation == "fip_count": proof["test_execution"]["executed_unique_cases"] += 1
    elif mutation == "fip_boolean_count": proof["test_execution"]["executed_unique_cases"] = True
    elif mutation == "junit_changed": xml.write_bytes(xml.read_bytes() + b"\n")
    elif mutation == "gov_hash": governed["junit_sha256"] = "f" * 64
    elif mutation == "gov_bytes": governed["junit_bytes"] += 1
    elif mutation == "gov_count": governed["discovered"] += 1
    elif mutation == "gov_boolean_zero": governed["pytest_exit_code"] = False
    elif mutation == "gov_head": governed["candidate_sha"] = "f" * 40
    elif mutation == "gov_scope": governed["scope"] = "focal tests only"
    elif mutation == "source_changed": governed["source_unchanged"] = False
    elif mutation == "eligible_integer": proof["final_candidate_eligible"] = 1
    elif mutation == "pending_dict": proof["pending_material_programming_gates"] = {}
    elif mutation == "exclusions_missing": governed["exclusions"] = []
    elif mutation == "exclusions_added": governed["exclusions"] += ["tests/test_finance.py|NEW_OMISSION"]
    elif mutation == "exclusions_rebound": governed["exclusions"] = [
        "tests/test_finance.py|SUPERSEDED_DUPLICATE_MODULE|tests/test_a3_primary_readonly_hf6.py"]
    with pytest.raises(provenance.ConvergenceError, match=signature):
        provenance.final_material_receipt_binding(root, sha, tree_sha, proof, governed, xml)


@pytest.mark.parametrize("sha", ["HEAD", "A" * 40, "f" * 40])
def test_wrong_head_or_revision_alias_never_certifies_native_checkout(candidate, sha):
    assert_rejected(candidate, "CANDIDATE_SHA_INVALID" if sha != "f"*40 else "CANDIDATE_CHECKOUT_MISMATCH", sha=sha)


def test_actual_tracked_source_drift_cannot_claim_an_unchanged_commit(candidate):
    root = candidate[0]; path = root / "scripts/porota_predeploy_binding.py"
    path.write_bytes(path.read_bytes() + b"\n# byte drift after frozen checkout\n")
    assert_rejected(candidate, "TRACKED_CHECKOUT_CHANGED")


def test_another_existing_clean_commit_is_not_the_approved_candidate(candidate):
    root = candidate[0]; old_sha = native_git(root, "rev-parse", "HEAD")
    (root / "additional-governed-source.txt").write_text("additional committed fixture source\n")
    commit(root)
    assert_rejected(candidate, "CANDIDATE_CHECKOUT_MISMATCH", sha=old_sha)


def test_committed_symlink_cannot_become_supported_git_source(candidate):
    root = candidate[0]; (root / "unsupported-source-alias.py").symlink_to("porota_mode_manager.py")
    commit(root)
    assert_rejected(candidate, "UNSUPPORTED_GIT_SOURCE_TYPE")


@pytest.mark.parametrize('target', ['product', 'candidate', 'source'])
def test_native_product_tree_replacement_cannot_replace_original_pinned_tree(candidate, target):
    root = candidate[0]
    manifest = json.loads((root / provenance.INPUT_ROOT / provenance.MANIFEST).read_text())
    replacement = native_git(root, 'rev-parse', 'HEAD')
    if target == 'candidate':
        original, replacement = replacement, manifest['product']['sha']
    else:
        original = manifest['product']['sha'] if target == 'product' else manifest['sources'][0]['head_sha']
    native_git(root, "replace", original, replacement)
    assert_rejected(candidate, "GIT_REPLACE_REFS_FORBIDDEN")
    with pytest.raises(audit465.AuditGateError, match='^GIT_REPLACE_REFS_FORBIDDEN$'):
        audit465.verify(root, candidate[1], root / 'missing-governed.json')


def test_git_authority_helpers_read_original_objects_despite_native_replacement(tmp_path):
    from scripts import porota_artifact_provenance as artifact
    root = tmp_path / 'private-git-authority'
    root.mkdir()
    native_git(root, 'init', '-q')
    native_git(root, 'config', 'user.name', 'Controlled Git authority fixture')
    native_git(root, 'config', 'user.email', 'fixture@example.invalid')
    source = root / 'authority.txt'
    source.write_text('Original bytes\n')
    original = commit(root)
    original_tree = native_git(root, 'rev-parse', original + '^{tree}')
    source.write_text('Replacement bytes\n')
    replacement = commit(root)
    replacement_tree = native_git(root, 'rev-parse', replacement + '^{tree}')
    native_git(root, 'replace', original, replacement)
    assert original_tree != replacement_tree
    assert native_git(root, 'rev-parse', original + '^{tree}') == replacement_tree
    assert provenance.git(root, 'rev-parse', original + '^{tree}') == original_tree
    assert audit465.git(root, 'rev-parse', original + '^{tree}') == original_tree
    assert artifact._git(root, 'rev-parse', original + '^{tree}').decode().strip() == original_tree


def test_losing_an_original_git_source_path_is_detected_after_a_clean_commit(candidate):
    root = candidate[0]
    manifest = json.loads((root / provenance.INPUT_ROOT / provenance.MANIFEST).read_text())
    path = next(iter(manifest["expected_source_paths"].values()))[0]["path"]
    (root / path).unlink(); commit(root)
    assert_rejected(candidate, "SOURCE_DELTA_PATH_LOST")


def test_mode_only_change_to_unchanged_original_source_requires_a_guarded_evolution(candidate):
    root = candidate[0]
    manifest = json.loads((root / provenance.INPUT_ROOT / provenance.MANIFEST).read_text())
    final = provenance.tree(root, "HEAD")
    originals = [provenance.tree(root, manifest["product"]["sha"])]
    changed = set()
    for source in manifest["sources"]:
        current = provenance.tree(root, source["head_sha"])
        baseline = provenance.tree(root, source["base_sha"])
        originals.append(current)
        changed.update(path for path in current if current[path] != baseline.get(path))
    filename = next(path for path, value in sorted(final.items()) if value["git_mode"] == "100644"
                    and path not in changed and any(tree.get(path) == value for tree in originals))
    source = root / filename; source.chmod(0o755)
    matrix_path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    matrix = json.loads(matrix_path.read_text()); matrix["path_evolution"].pop(filename)
    write_json(matrix_path, matrix); commit(root)
    assert provenance.tree(root, "HEAD")[filename]["blob"] == final[filename]["blob"]
    assert_rejected(candidate, "UNEXPLAINED_SOURCE_EVOLUTION")


@pytest.mark.parametrize("mutation", ["missing_source", "duplicate_source", "edited_original_order"])
def test_rehashing_original_input_index_cannot_authorize_changed_original_bytes(candidate, mutation):
    root = candidate[0]; inputs = root / provenance.INPUT_ROOT
    name = provenance.MANIFEST if mutation != "edited_original_order" else "ORDEN_UNICA_CODEX_RC6_CONVERGENCIA_468_469_470.md"
    path = inputs / name
    if mutation == "edited_original_order": path.write_bytes(path.read_bytes() + b"\nAltered authorization fixture.\n")
    else:
        manifest = json.loads(path.read_text())
        manifest["sources"] = manifest["sources"][:-1] if mutation == "missing_source" else [manifest["sources"][0]] + manifest["sources"][1:-1] + [manifest["sources"][0]]
        write_json(path, manifest)
    index = json.loads((inputs / "ORIGINAL_INPUT_DIGESTS.json").read_text())
    index["files"][name] = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    write_json(inputs / "ORIGINAL_INPUT_DIGESTS.json", index); commit(root)
    assert_rejected(candidate, "ORIGINAL_HANDOFF_BYTES_CHANGED")


def test_derived_original_scenario_cannot_relabel_the_pinned_markdown(candidate):
    root = candidate[0]; path = root / provenance.INPUT_ROOT / provenance.SCENARIOS
    rows = json.loads(path.read_text()); rows["rows"][0]["expected"] = "Fabricated replacement expectation"
    write_json(path, rows); commit(root)
    assert_rejected(candidate, "ORIGINAL_SCENARIOS")


@pytest.mark.parametrize("field", ["expected", "original_observed", "original_verdict"])
def test_closure_scenario_cannot_rebind_pinned_original_with_a_valid_executed_guard(candidate, field):
    root = candidate[0]; path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    matrix = json.loads(path.read_text())
    matrix["scenarios"][0]["original_scenario"][field] = "Fabricated original statement"
    write_json(path, matrix); commit(root)
    assert_rejected(candidate, "ORIGINAL_CLOSURE_SCENARIO_REBOUND")


def test_closure_requirement_cannot_rebind_pinned_registry_with_a_valid_executed_guard(candidate):
    root = candidate[0]; path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    matrix = json.loads(path.read_text())
    original = matrix["requirements"][0]["original_requirement"]
    field = next(name for name in original if name != "id")
    original[field] = "Fabricated original requirement"
    write_json(path, matrix); commit(root)
    assert_rejected(candidate, "ORIGINAL_CLOSURE_REQUIREMENT_REBOUND")


@pytest.mark.parametrize("duplicate", [False, True])
def test_missing_or_duplicated_restored_control_cannot_issue_complete_receipts(candidate, duplicate):
    root = candidate[0]; path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    matrix = json.loads(path.read_text())
    if duplicate:
        matrix["restored_controls"][-1] = deepcopy(matrix["restored_controls"][0])
    else:
        matrix["restored_controls"].pop()
    write_json(path, matrix); commit(root)
    assert_rejected(candidate, ("CLOSURE_CARDINALITY", "CLOSURE_IDS_MISSING_OR_DUPLICATE"))


@pytest.mark.parametrize("mutation", ["unknown_parent", "duplicate", "no_guard"])
def test_new_integration_findings_require_unique_ids_real_guards_and_original_parent(candidate, mutation):
    root = candidate[0]; path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    matrix = json.loads(path.read_text()); row = matrix["additional_findings"][0]
    if mutation == "unknown_parent": row["linked_requirement_ids"] = ["INVENTED_REQUIREMENT"]
    elif mutation == "duplicate": matrix["additional_findings"].append(deepcopy(row))
    else: row["test_nodes"] = []
    write_json(path, matrix); commit(root)
    assert_rejected(candidate, ("ADDITIONAL_FINDINGS_INVALID", "CLOSURE_CARDINALITY", "CLOSURE_GUARDS_MISSING"))


@pytest.mark.parametrize("filename", ["tests/test_dashboard_session.py", "tests/test_candle_archive_v17.py", "tests/test_rc4_acceptance.py"])
def test_unreviewed_legacy_test_drift_cannot_hide_behind_complete_native_receipts(candidate, filename):
    root = candidate[0]; source = root / filename
    raw = source.read_bytes(); assert b"assert " in raw
    source.write_bytes(raw.replace(b"assert ", b"assert not ", 1)); commit(root)
    assert_rejected(candidate, "LEGACY_344_UNREVIEWED_DRIFT")


def test_missing_original_input_is_not_replaced_by_a_complete_self_asserted_index(candidate):
    root = candidate[0]
    (root / provenance.INPUT_ROOT / provenance.MANIFEST).unlink(); commit(root)
    assert_rejected(candidate, "GIT_SOURCE_UNAVAILABLE")


def test_duplicate_original_index_keys_cannot_override_the_original_integrity_contract(candidate):
    root = candidate[0]; path = root / provenance.INPUT_ROOT / "ORIGINAL_INPUT_DIGESTS.json"
    raw = path.read_text().rstrip()
    path.write_text(raw[:-1] + ', "files": {} }\n'); commit(root)
    assert_rejected(candidate, "DUPLICATE_JSON_KEY")


@pytest.mark.parametrize("alias", ["symlink", "hardlink", "parent_symlink"])
def test_output_alias_never_overwrites_an_unrelated_existing_receipt(candidate, tmp_path, alias):
    existing = tmp_path / "unrelated-existing.json"; existing.write_bytes(b"UNRELATED_PRESERVED\n")
    output = candidate[2]
    if alias == "symlink": output.symlink_to(existing)
    elif alias == "hardlink": os.link(existing, output)
    else:
        directory = tmp_path / "unrelated-dir"; directory.mkdir()
        (directory / "report.json").write_bytes(existing.read_bytes())
        link = tmp_path / "aliased-output-dir"; link.symlink_to(directory, target_is_directory=True)
        output = link / "report.json"; candidate = (*candidate[:2], output)
        existing = directory / "report.json"
    result = cli(candidate)
    assert result.returncode != 0
    assert "OUTPUT_" in result.stderr
    assert existing.read_bytes() == b"UNRELATED_PRESERVED\n"


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped"])
def test_actual_junit_nonpass_cannot_be_hidden_behind_complete_closure_rows(candidate, outcome):
    junit(candidate[1], outcome=outcome)
    assert_rejected(candidate, ("JUNIT_NONPASS", "JUNIT_SUMMARY_MISMATCH"))


def test_duplicate_junit_case_cannot_inflate_guard_execution_evidence(candidate):
    junit(candidate[1], duplicate=True)
    assert_rejected(candidate, "JUNIT_DUPLICATE_CASE")


@pytest.mark.parametrize("counters", [{"tests": "2"}, {"failures": "1"}, {"errors": "1"},
    {"skipped": "1"}, {"tests": "-1"}, {"tests": "true"}])
def test_junit_summary_must_equal_actual_case_outcomes(candidate, counters):
    junit(candidate[1], counters=counters)
    assert_rejected(candidate, "JUNIT")


@pytest.mark.parametrize("mutation", ["empty_nodes", "not_executed", "missing_test_source", "node_alias", "missing_requirement", "duplicate_requirement", "unexplained_evolution", "duplicate_evolution_requirement"])
def test_missing_or_ambiguous_closure_guards_never_bless_source_evolution(candidate, mutation):
    root = candidate[0]; path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    matrix = json.loads(path.read_text()); row = matrix["requirements"][0]
    if mutation == "empty_nodes": row["test_nodes"] = []
    elif mutation == "not_executed": row["test_nodes"] = ["tests/test_rc6_convergence_sre_binding.py::test_merge_approval_is_concrete_and_rejects_duplicates_and_old_inputs"]
    elif mutation == "missing_test_source": row["test_nodes"] = ["tests/test_missing_native_guard.py::test_guard"]
    elif mutation == "node_alias": row["test_nodes"] = [GUARD.replace("tests/", "tests/../tests/")]
    elif mutation == "missing_requirement": matrix["requirements"].pop()
    elif mutation == "duplicate_requirement": matrix["requirements"][-1] = deepcopy(row)
    elif mutation == "duplicate_evolution_requirement":
        for evolution in matrix["path_evolution"].values():
            evolution["requirement_ids"] *= 2
    else: matrix["path_evolution"] = {}
    write_json(path, matrix); commit(root)
    assert_rejected(candidate, "UNEXPLAINED_SOURCE_EVOLUTION" if mutation in
        {"unexplained_evolution", "duplicate_evolution_requirement"} else "CLOSURE")


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "rebound_clause", "missing_guard"])
def test_all_ninety_original_front_variants_and_their_exact_clauses_are_mandatory(candidate, mutation):
    root = candidate[0]; path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    matrix = json.loads(path.read_text()); fronts = matrix["front_variants"]
    if mutation == "missing": fronts.pop()
    elif mutation == "duplicate": fronts[-1] = deepcopy(fronts[0])
    elif mutation == "rebound_clause": fronts[0]["original_columns"][-1] = "Invented replacement claim"
    else: fronts[0]["test_nodes"] = []
    write_json(path, matrix); commit(root)
    assert_rejected(candidate, "ORIGINAL_FRONT_CLAUSE_REBOUND" if mutation == "rebound_clause" else "CLOSURE")


@pytest.mark.parametrize("filename", ["ORIGINAL_RA_A_F01_F02.md", "ORIGINAL_RA_A_F03_F05.md"])
def test_rehashing_front_originals_cannot_replace_the_independent_original_byte_pins(candidate, filename):
    root = candidate[0]; inputs = root / provenance.INPUT_ROOT; path = inputs / filename
    path.write_bytes(path.read_bytes() + b"\nControlled replacement of a pinned original.\n")
    index = json.loads((inputs / "ORIGINAL_INPUT_DIGESTS.json").read_text())
    index["files"][filename] = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    write_json(inputs / "ORIGINAL_INPUT_DIGESTS.json", index); commit(root)
    assert_rejected(candidate, "ORIGINAL_HANDOFF_BYTES_CHANGED")


def test_synthetic_matching_junit_cannot_invent_a_guard_absent_from_committed_source_ast(candidate):
    root, xml, _ = candidate; path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    invented = "test_invented_matching_receipt_without_native_declaration"
    matrix = json.loads(path.read_text())
    matrix["requirements"][0]["test_nodes"] = [GUARD.split("::")[0] + "::" + invented]
    write_json(path, matrix); commit(root)
    document = ET.parse(xml); document.find(".//testcase").set("name", invented); document.write(xml, encoding="utf-8")
    assert_rejected(candidate, "CLOSURE_GUARD_DECLARATION_MISSING")


def test_new_committed_source_requires_an_explicit_guarded_reconciliation(candidate):
    root = candidate[0]; (root / "new-unexplained-source.py").write_text("VALUE = 'additional native source'\n")
    commit(root)
    assert_rejected(candidate, "UNEXPLAINED_SOURCE_EVOLUTION:new-unexplained-source.py")
