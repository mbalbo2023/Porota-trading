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
