"""Trusted-main WS12 publisher: adversarial data never grants release."""

import hashlib
import json

import pytest

from scripts import rc6_ci_ws12_trusted_receipt as publisher


T = "a" * 40
C = "b" * 40
TREE = "c" * 40
D = "sha256:" + "d" * 64
AF_UNIX = (
    "tests/test_rc6_a678_ppi_shared_feed.py::PPISharedFeedTests::"
    "test_unix_socket_exposes_data_only_pull_not_provider_or_order_command"
)
SUBSET = [AF_UNIX, "tests/test_safe.py::test_1", "tests/test_safe.py::test_2"]
FULL = SUBSET + ["test_hf6_history_data912_v2.py::test_legacy"]


def manifest(nodes):
    names = sorted(nodes)
    base = {"v": 1, "source_sha": C, "source_tree_sha": TREE, "nodeids": names}
    serialized = json.dumps(base, sort_keys=True, separators=(",", ":"))
    return {"schema_version": 1, "source_sha": C, "source_tree_sha": TREE,
            "nodeids": names, "discovered": len(names),
            "collection_sha256": hashlib.sha256(serialized.encode()).hexdigest()}


def receipt_input(*, full=FULL, subset=SUBSET, outcomes=None, rc=0):
    cases = outcomes if outcomes is not None else [
        {"nodeid_sha256": hashlib.sha256(name.encode()).hexdigest(), "outcome": "passed"}
        for name in full
    ]
    return {"full_manifest": manifest(full), "subset_manifest": manifest(subset),
            "cases": cases, "pytest_exit_code": rc,
            "junit_sha256": "e" * 64}


def publish(value):
    return publisher.review_evidence(
        value, trusted_main_sha=T, candidate_sha=C, candidate_tree_sha=TREE,
        qa_image_digest=D, run_id="42555555555", attempt="1",
        expected_full=4, expected_subset=3,
    )


def test_full_and_subset_quorum_emit_hash_only_review_without_runtime_authority():
    summary = publish(receipt_input())
    assert summary["status"] == "READY_FOR_PHYSICAL_ATTESTATION"
    assert (summary["full_collected"], summary["full_executed"],
            summary["subset_collected"], summary["subset_executed"]) == (4, 4, 3, 3)
    # The subset passed inside the single full-suite invocation; it was
    # collected separately, but it was not run a second time.
    assert summary["subset_execution_mode"] == "covered_by_full_execution"
    assert summary["subset_independent_run"] is False
    assert summary["af_unix_passed"] is True
    assert summary["trusted_main_sha"] == T
    assert summary["candidate_sha"] == C
    assert summary["candidate_tree_sha"] == TREE
    assert summary["qa_image_digest"] == D
    assert summary["runtime_artifact_digest"] is None
    assert summary["runtime_artifact_tested"] is False
    assert "image_digest" not in summary
    assert (summary["run_id"], summary["attempt"]) == ("42555555555", "1")
    assert summary["qa_certified"] is False
    assert summary["release_authority"] is False
    assert summary["automatic_go"] is False
    assert "nodeids" not in json.dumps(summary)
    assert "tests/test_" not in json.dumps(summary)


@pytest.mark.parametrize("change", [
    lambda x: x["cases"].pop(),
    lambda x: x["cases"].append(x["cases"][0]),
    lambda x: x["cases"][0].update(outcome="skipped"),
    lambda x: x["cases"][0].update(outcome="failed"),
    lambda x: x["cases"][0].update(outcome="xpassed"),
    lambda x: x.update(pytest_exit_code=1),
    lambda x: x["full_manifest"].update(collection_sha256="0" * 64),
    lambda x: x["full_manifest"].update(source_sha="f" * 40),
    lambda x: x["subset_manifest"].update(nodeids=["tests/foreign.py::test_a"] * 3),
    lambda x: x["full_manifest"].update(discovered=True),
    lambda x: x["cases"][0].update(nodeid_sha256="SYNTHETIC_SECRET"),
    lambda x: x["cases"][0].update(unexpected="candidate-controlled"),
])
def test_incomplete_or_forged_candidate_evidence_cannot_pass(change):
    value = receipt_input()
    change(value)
    with pytest.raises(publisher.EvidenceRejected):
        publish(value)


def test_subset_must_be_exact_membership_in_full_suite():
    full = FULL[:-1] + ["tests/test_extra.py::test_new"]
    subset = SUBSET[:-1] + ["tests/test_unrelated.py::test_unbound"]
    with pytest.raises(publisher.EvidenceRejected):
        publish(receipt_input(full=full, subset=subset))


def test_af_unix_test_must_be_present_and_passed():
    full = ["tests/test_safe.py::test_1", "tests/test_safe.py::test_2",
            "tests/test_safe.py::test_3", "test_hf6_history_data912_v2.py::test_legacy"]
    subset = full[:3]
    with pytest.raises(publisher.EvidenceRejected):
        publish(receipt_input(full=full, subset=subset))


def test_oversized_collection_is_rejected_before_digest_allocation(monkeypatch):
    value = receipt_input()
    value["full_manifest"]["nodeids"] = ["tests/x.py::test_" + str(i) for i in range(100)]
    def forbidden_hash(_):
        raise AssertionError("untrusted huge node list must not be hashed")
    monkeypatch.setattr(publisher.hashlib, "sha256", forbidden_hash)
    with pytest.raises(publisher.EvidenceRejected):
        publish(value)


def test_nodeid_utf8_bound_rejects_giant_name():
    value = receipt_input()
    value["subset_manifest"]["nodeids"][0] = "tests/x.py::test_" + "x" * 2049
    with pytest.raises(publisher.EvidenceRejected):
        publish(value)


@pytest.mark.parametrize("run,attempt,image", [
    ("0", "1", D),
    ("not-a-run", "1", D),
    ("42555555555", "0", D),
    ("42555555555", "1", "python:latest"),
    ("42555555555", "1", "sha256:" + "0" * 63),
])
def test_binding_inputs_require_exact_numeric_run_and_digest(run, attempt, image):
    with pytest.raises(publisher.EvidenceRejected):
        publisher.review_evidence(
            receipt_input(), trusted_main_sha=T, candidate_sha=C,
            candidate_tree_sha=TREE, qa_image_digest=image,
            run_id=run, attempt=attempt, expected_full=4, expected_subset=3,
        )
