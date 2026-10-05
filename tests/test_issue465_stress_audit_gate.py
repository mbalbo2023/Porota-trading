"""Reject false closure evidence, omitted clauses and altered frozen source."""
from copy import deepcopy
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET

import pytest
import scripts.rc6_issue465_audit_gate as gate

from scripts.rc6_issue465_audit_gate import (AuditGateError, CLAUSE_COUNTS, EXTERNAL,
    MATRIX, REPORT, REQUIRED, verify)


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / "docs/audits").mkdir(parents=True)
    (root / "tests/test_evidence.py").write_text("def test_evidence():\n    assert True\n")
    (root / REPORT).write_bytes(b"synthetic independent authority fixture\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Offline fixture",
                    "-c", "user.email=offline@example.invalid", "commit", "-qm", "fixture"], check=True)
    def git(ref):
        return subprocess.check_output(["git", "-C", str(root), "rev-parse", ref], text=True).strip()
    head, tree = git("HEAD"), git("HEAD^{tree}")
    findings = []
    for identifier in sorted(REQUIRED):
        coverage = [{"id": f"{identifier}-{n:02d}", "requirement": "synthetic guard fixture",
                     "tests": [{"node": "tests/test_evidence.py::test_evidence", "minimum_cases": 1}]}
                    for n in range(1, CLAUSE_COUNTS.get(identifier, 1)+1)]
        findings.append({"id": identifier, "state": "EXTERNAL_EVIDENCE_PENDING" if identifier in EXTERNAL else "HARDENED_AND_TESTED",
            "original_evidence": "synthetic", "rca": "synthetic", "fix_code": "synthetic", "caller": "synthetic",
            "negative_test": "synthetic", "remaining_uncertainty": "synthetic", "coverage": [] if identifier in EXTERNAL else coverage})
    matrix = {"schema": "rc6.issue465-reaudit.v1", "authority": {
        "issue": "mbalbo2023/Porota-trading#465", "original_report_sha256": hashlib.sha256((root/REPORT).read_bytes()).hexdigest()},
        "safety": {"mode": "PAPER_SHADOW_ONLY", "real_orders_sent": 0, "real_routes": "NOT_CALLED",
                   "merge": False, "deploy": False, "runtime_mutation": False, "ppi_watch": "NOT_TOUCHED", "deploy_owner": "NOT_ACQUIRED"},
        "findings": findings, "governed_exclusions": [], "workstreams": [
            {"id": identifier, "write_owner": "RELEASED", "head": head, "tree": tree,
             "paths": ["tests/test_evidence.py"]} for identifier in "ABCDEFG"]}
    # The private toy authority is scoped to this fixture only. The actual CLI
    # pins the independently fetched issue/report/product/rejected references.
    authority = {
        "issue": "mbalbo2023/Porota-trading#465",
        "original_report_url": "https://github.com/mbalbo2023/Porota-trading/pull/463#issuecomment-5980867529",
        "original_report_sha256": matrix["authority"]["original_report_sha256"],
        "product_base": "da697c6e6c2274579f9e4a112fabc4327475dd35",
        "rejected_head": "caf9bc94b4a1f436ad01a84a9e9e9e7a4a9e9423",
        "rejected_tree": "f1712a72cb49435c4403145bb38d98c4006f8f07",
    }
    matrix["authority"].update(authority)
    monkeypatch.setattr(gate, "ORIGINAL_AUTHORITY", authority, raising=False)
    proof = {"scope": "repository-root automatic pytest discovery", "status": "GREEN", "pytest_exit_code": 0,
             "executed": 1, "discovered": 1, "failures": 0, "errors": 0, "skipped": 0, "xfail": 0, "exclusions": []}
    junit = tmp_path / "junit.xml"
    junit.write_text('<testsuites><testsuite tests="1"><testcase classname="tests.test_evidence" name="test_evidence"/></testsuite></testsuites>')
    governed = tmp_path / "governed.json"
    def persist():
        (root/MATRIX).write_text(json.dumps(matrix))
        governed.write_text(json.dumps(proof))
    persist()
    return root, junit, governed, matrix, proof, persist


def test_gate_binds_current_sha_tree_test_counts_and_external_uncertainty(evidence):
    root, junit, governed, _, _, _ = evidence
    result = verify(root, junit, governed)
    assert result["status"] == "GREEN" and result["governed_executed"] == 1
    assert set(result["findings"]) == REQUIRED
    assert result["independent_reaudit"] == "PENDING"
    assert result["external_evidence"] == "NO_VERIFICADO"


def test_synthetic_JUnit_cannot_authorize_an_absent_native_test_definition(evidence):
    root, junit, governed, matrix, _, persist = evidence
    old = "tests/test_evidence.py::test_evidence"
    for finding in matrix["findings"]:
        for clause in finding["coverage"]:
            for binding in clause["tests"]:
                if binding["node"] == old:
                    binding["node"] = "tests/test_evidence.py::test_phantom"
    junit.write_text('<testsuites><testsuite tests="1"><testcase classname="tests.test_evidence" name="test_phantom"/></testsuite></testsuites>')
    persist()
    with pytest.raises(AuditGateError, match="REGRESSION_SOURCE_DEFINITION_MISSING"):
        verify(root, junit, governed)


@pytest.mark.parametrize('attack', ['detached_case', 'nested_case', 'wrapper', 'extra_suite',
                                  'detached_failure', 'detached_error', 'detached_skipped'])
def test_only_direct_cases_of_one_governed_suite_are_accepted(evidence, attack):
    root, junit, governed, _, _, _ = evidence
    document = ET.parse(junit)
    top = document.getroot()
    suite = document.find('.//testsuite')
    if attack in {'detached_case', 'nested_case'}:
        case = suite.find('testcase')
        suite.remove(case)
        destination = top if attack == 'detached_case' else ET.SubElement(suite, 'properties')
        destination.append(case)
    elif attack == 'wrapper':
        wrapper = ET.Element('unrecognized_wrapper')
        wrapper.append(top)
        document = ET.ElementTree(wrapper)
    elif attack == 'extra_suite':
        ET.SubElement(top, 'testsuite', tests='0', failures='0', errors='0', skipped='0')
    else:
        ET.SubElement(suite, attack.removeprefix('detached_'))
    document.write(junit, encoding='utf-8', xml_declaration=True)
    with pytest.raises(gate.convergence.ConvergenceError, match='JUNIT_GOVERNED_SUITE_SHAPE'):
        gate.convergence.executed_cases(junit)
    with pytest.raises(AuditGateError, match='JUNIT_GOVERNED_SUITE_SHAPE'):
        verify(root, junit, governed)


@pytest.mark.parametrize("attack", ["omit_finding", "duplicate_finding", "omit_clause", "omit_test",
    "unexecuted_test", "unexecuted_parameter", "false_external", "false_live_verified",
    "unreleased_owner", "wrong_front_tree", "wrong_report_digest", "real_orders", "undeclared_exclusion",
    "different_test_scope", "collection_mismatch", "governed_failure"])
def test_false_or_incomplete_closure_is_rejected(evidence, attack):
    root, junit, governed, matrix, proof, persist = evidence
    f01 = next(row for row in matrix["findings"] if row["id"] == "F-01")
    if attack == "omit_finding": matrix["findings"].remove(f01)
    elif attack == "duplicate_finding": matrix["findings"][0] = deepcopy(f01)
    elif attack == "omit_clause": f01["coverage"].pop()
    elif attack == "omit_test": f01["coverage"][0]["tests"] = []
    elif attack == "unexecuted_test": f01["coverage"][0]["tests"][0]["node"] = "tests/test_evidence.py::test_never_executed"
    elif attack == "unexecuted_parameter": f01["coverage"][0]["tests"][0]["minimum_cases"] = 2
    elif attack == "false_external": f01["state"] = "EXTERNAL_EVIDENCE_PENDING"
    elif attack == "false_live_verified": next(row for row in matrix["findings"] if row["id"] in EXTERNAL)["state"] = "HARDENED_AND_TESTED"
    elif attack == "unreleased_owner": matrix["workstreams"][0]["write_owner"] = "ACQUIRED"
    elif attack == "wrong_front_tree": matrix["workstreams"][0]["tree"] = "f"*40
    elif attack == "wrong_report_digest": matrix["authority"]["original_report_sha256"] = "0"*64
    elif attack == "real_orders": matrix["safety"]["real_orders_sent"] = 1
    elif attack == "undeclared_exclusion": proof["exclusions"] = ["tests/test_missing.py"]
    elif attack == "different_test_scope": proof["scope"] = "manual selected allowlist"
    elif attack == "collection_mismatch": proof["discovered"] = 2
    elif attack == "governed_failure": proof["failures"] = 1
    persist()
    with pytest.raises(AuditGateError): verify(root, junit, governed)


@pytest.mark.parametrize("kind", ["failure", "error", "skipped", "duplicate"])
def test_claimed_green_cannot_hide_nonpass_or_duplicate_junit(evidence, kind):
    root, junit, governed, _, proof, persist = evidence
    document = ET.parse(junit)
    case = document.find(".//testcase")
    if kind == "duplicate":
        document.find(".//testsuite").append(deepcopy(case))
        proof.update(executed=2, discovered=2)
    else:
        ET.SubElement(case, kind)
    document.write(junit)
    persist()
    with pytest.raises(AuditGateError): verify(root, junit, governed)


def test_source_changed_after_released_head_cannot_be_certified(evidence):
    root, junit, governed, _, _, _ = evidence
    (root/"tests/test_evidence.py").write_text("def test_evidence():\n    assert False\n")
    subprocess.run(["git", "-C", str(root), "add", "tests/test_evidence.py"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Offline fixture", "-c",
                    "user.email=offline@example.invalid", "commit", "-qm", "changed after release"], check=True)
    with pytest.raises(AuditGateError, match="FRONT_BYTES_CHANGED"):
        verify(root, junit, governed)


def test_duplicate_matrix_keys_cannot_overwrite_safety_authority(evidence):
    root, junit, governed, _, _, _ = evidence
    path = root/MATRIX
    path.write_text(path.read_text().replace('"mode": "PAPER_SHADOW_ONLY"',
                                            '"mode": "LIVE", "mode": "PAPER_SHADOW_ONLY"'))
    with pytest.raises(AuditGateError, match="DUPLICATE_JSON_KEY"):
        verify(root, junit, governed)


def test_uncommitted_disk_bytes_cannot_claim_frozen_git_head(evidence):
    root, junit, governed, _, _, _ = evidence
    (root/"tests/test_evidence.py").write_text("def test_evidence():\n    assert False\n")
    with pytest.raises(AuditGateError, match="FROZEN_DISK"):
        verify(root, junit, governed)


@pytest.mark.parametrize("field,value", [("real_orders_sent", False), ("merge", 0),
                                         ("deploy", 0), ("runtime_mutation", 0)])
def test_bool_int_coercion_cannot_bless_false_safety_types(evidence, field, value):
    root, junit, governed, matrix, _, persist = evidence
    matrix["safety"][field] = value
    persist()
    with pytest.raises(AuditGateError, match="SAFETY"):
        verify(root, junit, governed)


@pytest.mark.parametrize("field", ["product_base", "rejected_head", "rejected_tree", "original_report_url"])
def test_matrix_cannot_replace_independently_fetched_authority(evidence, field):
    root, junit, governed, matrix, _, persist = evidence
    matrix["authority"][field] = "unrelated-authority"
    persist()
    with pytest.raises(AuditGateError, match="AUTHORITY"):
        verify(root, junit, governed)


def test_rehashed_replacement_report_cannot_self_certify_original_authority(evidence):
    root, junit, governed, matrix, _, persist = evidence
    (root/REPORT).write_bytes(b"rewritten audit authority\n")
    matrix["authority"]["original_report_sha256"] = hashlib.sha256((root/REPORT).read_bytes()).hexdigest()
    persist()
    with pytest.raises(AuditGateError, match="AUTHORITY"):
        verify(root, junit, governed)


@pytest.mark.parametrize("field,value", [("pytest_exit_code", False), ("failures", False),
    ("errors", 0.0), ("skipped", None), ("xfail", False), ("discovered", 1.0)])
def test_malformed_governed_types_cannot_claim_green(evidence, field, value):
    root, junit, governed, _, proof, persist = evidence
    proof[field] = value
    persist()
    with pytest.raises(AuditGateError, match="GOVERNED"):
        verify(root, junit, governed)


@pytest.fixture
def controlled_successor(evidence, monkeypatch):
    """Private verifier-result fixture; it does not attest real test execution."""
    root, junit, governed, _, proof, _ = evidence
    manifest = root / gate.convergence.INPUT_ROOT / gate.convergence.MANIFEST
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text('{"controlled_verifier_fixture": true}\n')
    document = ET.parse(junit)
    document.find('.//testsuite').attrib.update(failures='0', errors='0', skipped='0')
    document.write(junit, encoding='utf-8', xml_declaration=True)
    def commit():
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=Offline fixture",
                        "-c", "user.email=offline@example.invalid", "commit", "-qm", "controlled successor"], check=True)
        sha = gate.git(root, "rev-parse", "HEAD")
        proof.update(candidate_sha=sha, candidate_tree=gate.git(root, "rev-parse", sha + "^{tree}"),
                     junit_sha256=hashlib.sha256(junit.read_bytes()).hexdigest(),
                     junit_bytes=junit.stat().st_size, source_unchanged=True)
        governed.write_text(json.dumps(proof))
        return sha
    baseline = commit()
    monkeypatch.setattr(gate, "CONVERGENCE_BASELINE", baseline)
    path = "tests/test_evidence.py"
    frozen = gate._source_record(root, baseline, path)
    (root / path).write_text("def test_evidence():\n    assert 2 + 2 == 4\n")
    current_sha = commit()
    current = gate._source_record(root, "HEAD", path)
    node = path + "::test_evidence"
    result = {"schema": "rc6.final-input-provenance.v1", "software_status": "EXECUTED_NATIVE_GREEN",
        "candidate_sha": current_sha, "candidate_tree": gate.git(root, "rev-parse", "HEAD^{tree}"),
        "test_execution": {"junit_sha256": hashlib.sha256(junit.read_bytes()).hexdigest(),
                           "executed_unique_cases": proof["executed"]},
        "sources": [{"pr": 466, "head_sha": baseline}],
        "requirements": [{"id": "U11", "software_status": "EXECUTED_NATIVE_GREEN",
                          "execution_receipts": [{"node": node, "executed_cases": 1}]}],
        "paths": [{"path": path, "final_blob": current["blob"], "git_mode": current["git_mode"],
            "source_blobs": {"466": frozen["blob"]}, "source_git_modes": {"466": frozen["git_mode"]},
            "preservation": "EVOLVED_WITH_NATIVE_GUARDS", "evolution_reason": "controlled native-parser fixture",
            "requirement_ids": ["U11"], "guard_nodes": [node]}]}
    calls = []
    def recompute(actual_root, sha, xml):
        calls.append((actual_root, sha, xml))
        return result
    monkeypatch.setattr(gate.convergence, "verify", recompute)
    return root, junit, governed, result, calls, commit


def test_guarded_successor_recomputes_proof_and_preserves_original_fronts(controlled_successor):
    root, junit, governed, successor, calls, _ = controlled_successor
    result = verify(root, junit, governed)
    assert result["status"] == "GREEN"
    assert result["front_preservation"] == "GUARDED_CONVERGENCE_SUCCESSOR"
    assert len(calls) == 1 and calls[0][:2] == (root, result["candidate_sha"])
    assert type(calls[0][2]) is gate.convergence.JunitReceipt
    assert calls[0][2].data == junit.read_bytes()
    assert len(result["evolved_fronts"]) == 7  # seven toy streams share one fixture path
    assert result["convergence_junit_sha256"] == successor["test_execution"]["junit_sha256"]
    assert result["independent_reaudit"] == "PENDING"
    assert result["safety"]["deploy"] is False


@pytest.mark.parametrize('field,value', [('candidate_sha', '0' * 40), ('candidate_tree', '0' * 40),
    ('junit_sha256', '0' * 64), ('junit_bytes', 0), ('junit_bytes', True),
    ('source_unchanged', False), ('source_unchanged', 1)])
def test_governed_successor_receipt_must_bind_source_and_immutable_JUnit(controlled_successor, field, value):
    root, junit, governed, _, calls, _ = controlled_successor
    proof = json.loads(governed.read_bytes())
    proof[field] = value
    governed.write_text(json.dumps(proof))
    with pytest.raises(AuditGateError, match='GOVERNED_EXACT_RECEIPT_MISMATCH'):
        verify(root, junit, governed)
    assert calls == []


@pytest.mark.parametrize('field', ['candidate_sha', 'candidate_tree', 'junit_sha256', 'junit_bytes', 'source_unchanged'])
def test_missing_governed_source_binding_cannot_fall_back_to_case_counts(controlled_successor, field):
    root, junit, governed, _, calls, _ = controlled_successor
    proof = json.loads(governed.read_bytes())
    del proof[field]
    governed.write_text(json.dumps(proof))
    with pytest.raises(AuditGateError, match='GOVERNED_EXACT_RECEIPT_MISMATCH'):
        verify(root, junit, governed)
    assert calls == []


@pytest.mark.parametrize('field', ['tests', 'failures', 'errors', 'skipped'])
def test_convergence_receipt_always_validates_counters_before_provenance(controlled_successor, field):
    root, junit, governed, _, calls, _ = controlled_successor
    document = ET.parse(junit)
    document.find('.//testsuite').set(field, '2')
    document.write(junit, encoding='utf-8', xml_declaration=True)
    proof = json.loads(governed.read_bytes())
    proof.update(junit_sha256=hashlib.sha256(junit.read_bytes()).hexdigest(), junit_bytes=junit.stat().st_size)
    governed.write_text(json.dumps(proof))
    with pytest.raises(AuditGateError, match='JUNIT_GOVERNED_SUITE_SHAPE'):
        verify(root, junit, governed)
    assert calls == []


@pytest.mark.parametrize("attack", ["inventory_only", "wrong_sha", "wrong_tree", "wrong_junit",
    "wrong_count", "wrong_baseline", "missing_path", "duplicate_path", "wrong_source_blob",
    "wrong_source_mode", "wrong_final_blob", "wrong_final_mode", "empty_reason", "empty_requirement",
    "invalid_requirement", "empty_guards", "unbound_guard", "unexecuted_requirement", "zero_cases",
    "bool_cases", "no_execution_receipt", "legacy_matrix_rewrite", "legacy_report_rewrite",
    "dirty_bytes", "dirty_executable_mode", "private_mode", "writable_mode"])
def test_guarded_successor_cannot_bypass_source_authority_or_execution(controlled_successor, attack):
    root, junit, governed, successor, _, commit = controlled_successor
    row = successor["paths"][0]
    requirement = successor["requirements"][0]
    if attack == "inventory_only": successor["software_status"] = "INVENTORY_NOT_EXECUTED"
    elif attack == "wrong_sha": successor["candidate_sha"] = "a" * 40
    elif attack == "wrong_tree": successor["candidate_tree"] = "a" * 40
    elif attack == "wrong_junit": successor["test_execution"]["junit_sha256"] = "a" * 64
    elif attack == "wrong_count": successor["test_execution"]["executed_unique_cases"] = 2
    elif attack == "wrong_baseline": successor["sources"][0]["head_sha"] = "a" * 40
    elif attack == "missing_path": successor["paths"] = []
    elif attack == "duplicate_path": successor["paths"].append(deepcopy(row))
    elif attack == "wrong_source_blob": row["source_blobs"]["466"] = "a" * 40
    elif attack == "wrong_source_mode": row["source_git_modes"]["466"] = "100755"
    elif attack == "wrong_final_blob": row["final_blob"] = "a" * 40
    elif attack == "wrong_final_mode": row["git_mode"] = "100755"
    elif attack == "empty_reason": row["evolution_reason"] = " "
    elif attack == "empty_requirement": row["requirement_ids"] = []
    elif attack == "invalid_requirement": row["requirement_ids"] = ["UNRELATED"]
    elif attack == "empty_guards": row["guard_nodes"] = []
    elif attack == "unbound_guard": row["guard_nodes"] = ["tests/test_evidence.py::test_missing"]
    elif attack == "unexecuted_requirement": requirement["software_status"] = "NOT_EXECUTED"
    elif attack == "zero_cases": requirement["execution_receipts"][0]["executed_cases"] = 0
    elif attack == "bool_cases": requirement["execution_receipts"][0]["executed_cases"] = True
    elif attack == "no_execution_receipt": requirement["execution_receipts"] = []
    elif attack == "legacy_matrix_rewrite":
        (root / MATRIX).write_text((root / MATRIX).read_text() + "\n")
        successor["candidate_sha"] = commit()
    elif attack == "legacy_report_rewrite": (root / REPORT).write_bytes(b"rewritten authority\n")
    elif attack == "dirty_bytes": (root / row["path"]).write_text("def test_evidence():\n    assert False\n")
    elif attack == "dirty_executable_mode": (root / row["path"]).chmod(0o755)
    elif attack == "private_mode": (root / row["path"]).chmod(0o600)
    elif attack == "writable_mode": (root / row["path"]).chmod(0o666)
    with pytest.raises(AuditGateError):
        verify(root, junit, governed)


def test_mode_only_successor_requires_the_same_guarded_evolution(controlled_successor):
    root, junit, governed, successor, _, commit = controlled_successor
    path = successor["paths"][0]["path"]
    (root / path).write_bytes(subprocess.check_output([
        "git", "-C", str(root), "show", gate.CONVERGENCE_BASELINE + ":" + path]))
    (root / path).chmod(0o755)
    successor["candidate_sha"] = commit()
    successor["candidate_tree"] = gate.git(root, "rev-parse", "HEAD^{tree}")
    successor["paths"][0]["git_mode"] = "100755"
    successor["paths"][0]["final_blob"] = gate._source_record(root, "HEAD", path)["blob"]
    result = verify(root, junit, governed)
    assert result["status"] == "GREEN"
    assert all(row["candidate"]["git_mode"] == "100755" for row in result["evolved_fronts"])


def test_failure_of_full_native_provenance_is_blocking(controlled_successor, monkeypatch):
    root, junit, governed, _, _, _ = controlled_successor
    def reject(*args):
        raise gate.convergence.ConvergenceError("controlled invalid original input")
    monkeypatch.setattr(gate.convergence, "verify", reject)
    with pytest.raises(AuditGateError, match="CONVERGENCE_SUCCESSOR_NOT_VERIFIED"):
        verify(root, junit, governed)
