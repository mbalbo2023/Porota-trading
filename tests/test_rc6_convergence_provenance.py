"""Native Git/CLI adversarial validation; JUnit and closure inputs are fixtures.

Original handoff bytes and all fifteen original Git heads remain unchanged.
The controlled closure/JUnit fixture exercises the receipt validator only;
it does not certify market executions or implement all fifty-five requirements.
Predeploy fetches the original refs before running these network-free tests.
"""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest
import yaml

from scripts import rc6_convergence_provenance as provenance


REPO = Path(__file__).resolve().parents[1]
GUARD = "tests/test_rc6_convergence_sre_binding.py::test_approved_unique_run_attempt_artifact_origin_is_green"


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
    attrs = {"name": "fixture", "tests": "2" if duplicate else "1", "errors": "0",
        "failures": "0", "skipped": "0"}
    if outcome:
        attrs[{"failure": "failures", "error": "errors", "skipped": "skipped"}[outcome]] = "1"
    attrs.update(counters or {})
    root = ET.Element("testsuites"); suite = ET.SubElement(root, "testsuite", attrs)
    for _ in range(2 if duplicate else 1):
        case = ET.SubElement(suite, "testcase", {"classname": "tests.test_rc6_convergence_sre_binding",
            "name": GUARD.split("::")[1], "time": "0.01"})
        if outcome: ET.SubElement(case, outcome, {"message": "controlled fixture nonpass"})
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


@pytest.fixture(scope="module")
def fixture_base(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-convergence-base") / "repo"
    native_git(REPO, "clone", "--quiet", "--shared", "--no-hardlinks", str(REPO), str(root))
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
    front = rows(provenance.FRONT_VARIANTS)
    for row in front:
        row["original_columns"] = original_front[row["id"]]
    # Fresh pinned inputs and the controlled closure file itself are additional
    # committed sources in this fixture. List them explicitly before commit.
    paths = set(provenance.tree(root, "HEAD"))
    paths.update(str(path.relative_to(root)) for path in inputs.iterdir() if path.is_file())
    paths.add(provenance.INPUT_ROOT + "/" + provenance.CLOSURE)
    matrix = {"schema": "rc6.convergence-closure.v1", "requirements": rows(provenance.REQUIREMENTS),
        "scenarios": rows({f"R{i:02d}" for i in range(1, 81)}),
        "front_variants": front,
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


def test_native_git_cli_preserves_all_original_sources_and_truthful_overlap_counts(candidate):
    result = cli(candidate)
    assert result.returncode == 0, result.stderr
    report = json.loads(candidate[2].read_text())
    assert report["candidate_sha"] == native_git(candidate[0], "rev-parse", "HEAD")
    assert report["candidate_tree"] == native_git(candidate[0], "rev-parse", "HEAD^{tree}")
    assert len(report["sources"]) == 15 and report["source_union_paths"] == 170
    assert len(report["requirements"]) == 55 and len(report["scenarios"]) == 80
    assert len(report["front_variants"]) == 90
    assert {row["id"] for row in report["front_variants"]} == provenance.FRONT_VARIANTS
    assert all(row["assertion_scope"] == "EXPLICIT_SYNTHETIC_ONLY" for row in report["front_variants"])
    assert report["test_execution"]["executed_unique_cases"] == 1
    assert report["test_execution"]["overlapping_suite_counts_added"] is False
    assert report["merge_authorized"] is report["deploy_authorized"] is False
    assert report["runtime"] == report["provider_open_capacity"] == "NO_VERIFICADO"
    assert report["economic_edge"] == "NO_DEMOSTRADO"
    assert report["assertion_scope"] == "OFFLINE_CODE_AND_FROZEN_TEST_RECEIPTS; NOT_RUNTIME_ATTESTATION"


def test_native_inventory_without_junit_cannot_claim_executed_guards(candidate):
    result = cli(candidate, inventory=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(candidate[2].read_text())
    assert report["software_status"] == "INVENTORY_NOT_EXECUTED"
    assert report["test_execution"]["executed_unique_cases"] is None
    assert all(row["software_status"] == "NOT_EXECUTED" for row in report["requirements"])


@pytest.mark.parametrize("wrong_head", [False, True])
def test_original_pr_refs_are_checked_by_real_fetch_from_isolated_local_origin(candidate, tmp_path, wrong_head):
    root = candidate[0]; origin = tmp_path / "isolated-origin.git"
    native_git(root, "clone", "--quiet", "--bare", "--shared", str(root), str(origin))
    manifest = json.loads((root / provenance.INPUT_ROOT / provenance.MANIFEST).read_text())
    for index, source in enumerate(manifest["sources"]):
        sha = native_git(root, "rev-parse", "HEAD") if wrong_head and index == 0 else source["head_sha"]
        native_git(origin, "update-ref", f"refs/pull/{source['pr']}/head", sha)
    native_git(root, "remote", "set-url", "origin", str(origin))
    if wrong_head:
        assert_rejected(candidate, "SOURCE_PR_HEAD_CHANGED", fetch_source_refs=True)
    else:
        result = cli(candidate, fetch_source_refs=True)
        assert result.returncode == 0, result.stderr
        for source in manifest["sources"]:
            assert native_git(root, "rev-parse", f"refs/porota/convergence-source/{source['pr']}") == source["head_sha"]


def test_predeploy_inventory_fetches_before_governed_tests_and_the_only_build():
    workflow = yaml.safe_load((REPO / ".github/workflows/porota-predeploy-v2.yml").read_text())
    steps = workflow["jobs"]["artifact-gate"]["steps"]
    names = [step["name"] for step in steps]
    inventory = names.index("Inventory original convergence source refs before governed tests")
    tests = names.index("Governed automatic test discovery and execution")
    build = names.index("Build candidate exactly once")
    final = names.index("Final convergence input and guard provenance")
    assert inventory < tests < build < final
    assert "--fetch-source-refs" in steps[inventory]["run"] and "--junit" not in steps[inventory]["run"]
    assert "--junit /tmp/porota-governed-tests.xml" in steps[final]["run"]
    assert sum("docker build" in step.get("run", "") for step in steps) == 1


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


def test_native_product_tree_replacement_cannot_replace_original_pinned_tree(candidate):
    root = candidate[0]
    manifest = json.loads((root / provenance.INPUT_ROOT / provenance.MANIFEST).read_text())
    native_git(root, "replace", manifest["product"]["sha"], native_git(root, "rev-parse", "HEAD"))
    assert_rejected(candidate, "PRODUCT_TREE_CHANGED")


def test_losing_an_original_git_source_path_is_detected_after_a_clean_commit(candidate):
    root = candidate[0]
    manifest = json.loads((root / provenance.INPUT_ROOT / provenance.MANIFEST).read_text())
    path = next(iter(manifest["expected_source_paths"].values()))[0]["path"]
    (root / path).unlink(); commit(root)
    assert_rejected(candidate, "SOURCE_DELTA_PATH_LOST")


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


@pytest.mark.parametrize("mutation", ["empty_nodes", "not_executed", "missing_test_source", "node_alias", "missing_requirement", "duplicate_requirement", "unexplained_evolution"])
def test_missing_or_ambiguous_closure_guards_never_bless_source_evolution(candidate, mutation):
    root = candidate[0]; path = root / provenance.INPUT_ROOT / provenance.CLOSURE
    matrix = json.loads(path.read_text()); row = matrix["requirements"][0]
    if mutation == "empty_nodes": row["test_nodes"] = []
    elif mutation == "not_executed": row["test_nodes"] = ["tests/test_rc6_convergence_sre_binding.py::test_merge_approval_is_concrete_and_rejects_duplicates_and_old_inputs"]
    elif mutation == "missing_test_source": row["test_nodes"] = ["tests/test_missing_native_guard.py::test_guard"]
    elif mutation == "node_alias": row["test_nodes"] = [GUARD.replace("tests/", "tests/../tests/")]
    elif mutation == "missing_requirement": matrix["requirements"].pop()
    elif mutation == "duplicate_requirement": matrix["requirements"][-1] = deepcopy(row)
    else: matrix["path_evolution"] = {}
    write_json(path, matrix); commit(root)
    assert_rejected(candidate, "CLOSURE" if mutation != "unexplained_evolution" else "UNEXPLAINED_SOURCE_EVOLUTION")


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
