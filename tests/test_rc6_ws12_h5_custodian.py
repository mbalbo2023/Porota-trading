import importlib
import importlib.util
import hashlib
import json
from xml.etree import ElementTree as ET
import pytest


def module():
    name = "scripts.rc6_ci_ws12_h5_custodian"
    assert importlib.util.find_spec(name) is not None
    return importlib.import_module(name)


def test_unreviewed_or_incomplete_capture_stays_hold():
    h5 = module()
    custody = h5.Custody()
    assert custody.receipt()["candidate_reported_pass"] is False
    with pytest.raises(h5.CustodyHold):
        custody.capture("junit.xml", b"candidate-controlled")
    receipt = custody.receipt()
    assert receipt == {"status": "HOLD", "candidate_reported_pass": False,
                       "physical_execution_observed": False,
                       "af_unix_independently_verified": False,
                       "qa_certified": False, "automatic_go": False}


def test_capture_is_ordered_bounded_and_does_not_accept_mutable_buffers():
    h5 = module()
    custody = h5.Custody()
    with pytest.raises(h5.CustodyHold):
        custody.capture("full-collected.json", bytearray(b"{}"))
    custody.capture("full-collected.json", b"{}")
    with pytest.raises(h5.CustodyHold):
        custody.capture("full-collected.json", b"new")
    with pytest.raises(h5.CustodyHold):
        custody.capture("subset-collected.json", b"x" * (16 * 1024 * 1024 + 1))


def test_consistent_synthetic_reports_never_claim_physical_custody():
    # Synthetic identities exercise parsing only; no pytest suite is executed.
    from tests.test_rc6_ws12_trusted_receipt import AF_UNIX, C, D, T, TREE, manifest
    nodes = [AF_UNIX] + [f"tests/test_synthetic.py::test_{i:05d}" for i in range(8124)]
    subset = nodes[:8102]
    full = json.dumps(manifest(nodes)).encode()
    selected = json.dumps(manifest(subset)).encode()
    root = ET.Element("testsuite", tests="8125", failures="0", errors="0", skipped="0")
    for node in nodes:
        case = ET.SubElement(root, "testcase")
        props = ET.SubElement(case, "properties")
        ET.SubElement(props, "property", name="rc6_nodeid_sha256",
                      value=hashlib.sha256(node.encode()).hexdigest())
    custody = module().Custody()
    for name, data in (("full-collected.json", full), ("subset-collected.json", selected),
                       ("full-executed.json", full), ("junit.xml", ET.tostring(root))):
        custody.capture(name, data)
    receipt = custody.review(trusted_main_sha=T, candidate_sha=C, tree=TREE,
                             qa_image_digest=D, run_id="123", attempt="1",
                             pytest_exit_code=0)
    assert receipt["candidate_reported_pass"] is True
    assert receipt["physical_execution_observed"] is False
    assert receipt["af_unix_independently_verified"] is False
    assert receipt["qa_certified"] is False


def test_invalid_private_evidence_is_not_echoed():
    h5 = module()
    custody = h5.Custody()
    for name in ("full-collected.json", "subset-collected.json", "full-executed.json", "junit.xml"):
        custody.capture(name, b"PRIVATE-CANDIDATE-CONTENT")
    with pytest.raises(h5.CustodyHold, match="REVIEW_REJECTED"):
        custody.review(trusted_main_sha="a" * 40, candidate_sha="b" * 40,
                       tree="c" * 40, qa_image_digest="sha256:" + "d" * 64,
                       run_id="1", attempt="1", pytest_exit_code=0)
    assert custody.receipt()["candidate_reported_pass"] is False
    assert "PRIVATE" not in str(custody.receipt())
