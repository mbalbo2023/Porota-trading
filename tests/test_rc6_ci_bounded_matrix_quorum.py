"""A883: RED-only cross-job quorum over A863's sanitized bounded receipts."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from scripts import rc6_ci_bounded_matrix_quorum as matrix


SHA = "a" * 40
TREE = "b" * 40
COLLECTION = "c" * 64


def _json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")


def _get(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _verify(root: Path, count: int = 269, collection: str = COLLECTION) -> dict:
    return matrix.verify_matrix(root, SHA, TREE, count, collection)


def _fixture(root: Path, *, segments: int = 269) -> Path:
    digest_list = [f"{i+1:064x}" for i in range(segments)]
    for offset in range(0, segments, 8):
        diag = root / f"rc6-a883-shard1-{offset}-{SHA}" / "diagnostic"
        entries = []
        for index in range(offset, min(offset+8, segments)):
            digest = digest_list[index]
            case = f"{index+100:064x}"
            xml = (f'<testsuite name="rc6-a820-hashed-diagnostic" '
                   f'tests="1" failures="0" errors="0" skipped="0">'
                   f'<testcase name="{case}" classname="rc6.offhost.hashed"/>'
                   '</testsuite>').encode()
            xml_file = diag / f"batch-{index}" / "junit-sanitized.xml"
            xml_file.parent.mkdir(parents=True, exist_ok=True)
            xml_file.write_bytes(xml)
            progress = (json.dumps({"nodeid_sha256":case,"phase":"finish"})+"\n").encode()
            (diag / f"progress-{index}.jsonl").write_bytes(progress)
            receipt = {"schema_version":1,"status":"RED_DIAGNOSTIC_OFFLINE_ONLY",
                "source_sha":SHA,"source_tree_sha":TREE,
                "collection_sha256":digest,"shards":1,"discovered":1,
                "executed":1,"missing":0,"failures":0,"errors":0,"skipped":0,
                "xfail":0,"timed_out_shards":0,"timeouts":[],
                "phase_elapsed_ms":{"collect":0,"parallel_test":0,"sanitize":0},
                "shard_elapsed_ms":[0],
                "sanitized_junit_sha256":hashlib.sha256(xml).hexdigest(),
                "automatic_go":False,"runtime_authority":False,
                "release_gate_activated":False,"host_written":False}
            _json(diag / f"batch-{index}" / "receipt.json", receipt)
            entries.append({"batch_index":index,"segment_sha256":digest,
                "assigned_count":1,"executed":1,"missing":0,"failures":0,
                "errors":0,"skipped":0,"status":"RED_DIAGNOSTIC_OFFLINE_ONLY",
                "progress":{"started":1,"finished":1,"last_phase":"finish",
                    "last_nodeid_sha256":case,"elapsed_ms":1,
                    "sha256":hashlib.sha256(progress).hexdigest()}})
        top = {"schema_version":1,"status":"RED_BOUNDED_DIAGNOSTIC",
            "source_sha":SHA,"source_tree_sha":TREE,
            "collection_sha256":COLLECTION,"discovered":segments,
            "shards":2,"shard_index":1,"split_path":"",
            "batch_offset":offset,"batch_limit":8,"timeout_seconds":90,
            "planned_batches":segments,"segment_sha256s":digest_list,
            "selected_count":len(entries),"unselected_count":segments-len(entries),
            "completed_batches":len(entries),"phase":"FINISHED",
            "batches":entries,"automatic_go":False,"runtime_authority":False,
            "release_gate_activated":False,"host_written":False}
        _json(diag / "receipt.json", top)
    return root


def test_a883_complete_matrix_is_still_red_and_never_release_go(tmp_path):
    proof = _verify(_fixture(tmp_path))
    assert proof["status"] == "RED_DIAGNOSTIC_ONLY"
    assert proof["source_sha"] == SHA and proof["source_tree_sha"] == TREE
    assert proof["planned_batches"] == 269
    assert proof["completed_batches"] == 269
    assert proof["matrix_jobs"] == 34
    assert proof["executed"] == 269 and proof["missing"] == 0
    assert proof["failures"] == proof["errors"] == proof["skipped"] == 0
    assert proof["automatic_go"] is False
    assert proof["runtime_authority"] is False
    assert proof["release_gate_activated"] is False


def test_a883_missing_artifact_fails_closed_without_silent_credit(tmp_path):
    root = _fixture(tmp_path)
    missing = root / f"rc6-a883-shard1-8-{SHA}" / "diagnostic" / "receipt.json"
    missing.unlink()
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(root)


def test_a883_duplicate_index_between_artifacts_fails_closed(tmp_path):
    root = _fixture(tmp_path)
    p = root / f"rc6-a883-shard1-8-{SHA}" / "diagnostic" / "receipt.json"
    value = _get(p)
    value["batches"][0]["batch_index"] = 0
    _json(p, value)
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(root)


def test_a883_mixed_source_tree_or_collection_fails_closed(tmp_path):
    root = _fixture(tmp_path)
    p = root / f"rc6-a883-shard1-8-{SHA}" / "diagnostic" / "receipt.json"
    original = _get(p)
    for field, bad in (("source_sha","d"*40),("source_tree_sha","e"*40),
                       ("collection_sha256","f"*64)):
        changed = deepcopy(original)
        changed[field] = bad
        _json(p, changed)
        with pytest.raises(matrix.MatrixQuorumRed):
            _verify(root)
    _json(p, original)


def test_a883_sanitized_xml_and_progress_sha_must_match_receipts(tmp_path):
    root = _fixture(tmp_path)
    diag = root / f"rc6-a883-shard1-0-{SHA}" / "diagnostic"
    xml = diag / "batch-0" / "junit-sanitized.xml"
    xml.write_bytes(xml.read_bytes().replace(b"classname=", b"className="))
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(root)
    root = _fixture(tmp_path)
    progress = diag / "progress-0.jsonl"
    progress.write_bytes(progress.read_bytes()+b"tampered\n")
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(root)


def test_a883_false_positive_go_flag_fails_closed(tmp_path):
    root = _fixture(tmp_path)
    p = root / f"rc6-a883-shard1-0-{SHA}" / "diagnostic" / "receipt.json"
    v = _get(p)
    v["automatic_go"] = True
    _json(p, v)
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(root)


def test_a883_shortened_fake_inventory_cannot_claim_complete_matrix(tmp_path):
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(_fixture(tmp_path, segments=9), count=9)


@pytest.mark.parametrize("planned", [265, 266, 267, 268, 269, 270, 271, 272])
def test_a883_dynamic_source_plan_covers_exact_indices_once(tmp_path, planned):
    proof = _verify(_fixture(tmp_path, segments=planned), count=planned)
    assert proof["planned_batches"] == planned
    assert proof["completed_batches"] == planned
    assert proof["executed"] == planned
    assert proof["matrix_jobs"] == 34
    assert proof["automatic_go"] is False


@pytest.mark.parametrize("mismatch", [268, 270, 272])
def test_a883_preflight_count_disagreement_never_gains_credit(tmp_path, mismatch):
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(_fixture(tmp_path, segments=269), count=mismatch)


def test_a883_preflight_collection_hash_disagreement_fails_closed(tmp_path):
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(_fixture(tmp_path), collection="f"*64)


@pytest.mark.parametrize("out_of_budget", [264, 273])
def test_a883_out_of_budget_does_not_run_partial_quorum(tmp_path, out_of_budget):
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(_fixture(tmp_path, segments=out_of_budget), count=out_of_budget)


def test_a883_unredacted_xml_output_cannot_be_accepted_as_sanitized(tmp_path):
    root = _fixture(tmp_path)
    xml = root / f"rc6-a883-shard1-0-{SHA}" / "diagnostic/batch-0/junit-sanitized.xml"
    xml.write_bytes(xml.read_bytes().replace(b"</testsuite>",
        b"<system-out>RAW_SECRET</system-out></testsuite>"))
    sub = xml.parent / "receipt.json"
    v = _get(sub)
    v["sanitized_junit_sha256"] = hashlib.sha256(xml.read_bytes()).hexdigest()
    _json(sub, v)
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(root)


def test_a883_raw_nodeid_key_in_receipt_cannot_be_published(tmp_path):
    root = _fixture(tmp_path)
    top = root / f"rc6-a883-shard1-0-{SHA}" / "diagnostic/receipt.json"
    value = _get(top)
    value["nodeids"] = ["tests/test_private.py::test_secret"]
    _json(top, value)
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(root)


def test_a883_actual_failed_test_is_credited_only_as_red_diagnostic(tmp_path):
    root = _fixture(tmp_path)
    diag = root / f"rc6-a883-shard1-0-{SHA}" / "diagnostic"
    xml = diag / "batch-0/junit-sanitized.xml"
    raw = xml.read_bytes()
    raw = raw.replace(b'failures="0"', b'failures="1"').replace(
        b"/></testsuite>",
        b'><failure type="SANITIZED_DIAGNOSTIC"/></testcase></testsuite>')
    xml.write_bytes(raw)
    sub = xml.parent / "receipt.json"
    subreceipt = _get(sub)
    subreceipt["failures"] = 1
    subreceipt["sanitized_junit_sha256"] = hashlib.sha256(raw).hexdigest()
    _json(sub, subreceipt)
    top = diag / "receipt.json"
    top_receipt = _get(top)
    top_receipt["batches"][0]["failures"] = 1
    _json(top, top_receipt)
    proof = _verify(root)
    assert proof["status"] == "RED_MATRIX_INCOMPLETE"
    assert proof["failures"] == 1 and proof["executed"] == 269
    assert proof["automatic_go"] is False and proof["full_suite_certified"] is False


def test_a883_cli_returns_two_and_writes_explicit_red_even_all_pass(tmp_path):
    root = _fixture(tmp_path / "artifacts")
    summary = tmp_path / "red-only-summary.json"
    rc = matrix.main(["--artifacts-root", str(root), "--source-sha", SHA,
        "--source-tree-sha", TREE, "--expected-batches", "269",
        "--expected-collection-sha256", COLLECTION,
        "--output", str(summary)])
    assert rc == 2
    result = _get(summary)
    assert result["status"] == "RED_DIAGNOSTIC_ONLY"
    assert result["automatic_go"] is False
    assert result["full_suite_certified"] is False


def test_a883_duplicate_junit_testcase_between_jobs_fails_closed(tmp_path):
    root = _fixture(tmp_path)
    diag = root / f"rc6-a883-shard1-8-{SHA}" / "diagnostic"
    xml = diag / "batch-8/junit-sanitized.xml"
    raw = xml.read_bytes().replace(f"{108:064x}".encode(), f"{100:064x}".encode())
    xml.write_bytes(raw)
    sub = xml.parent / "receipt.json"
    subreceipt = _get(sub)
    subreceipt["sanitized_junit_sha256"] = hashlib.sha256(raw).hexdigest()
    _json(sub, subreceipt)
    with pytest.raises(matrix.MatrixQuorumRed):
        _verify(root)
