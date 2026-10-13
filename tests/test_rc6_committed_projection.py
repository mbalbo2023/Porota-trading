"""Actual native producer -> sealed four-role cut -> bounded read-only pages."""
from datetime import timedelta
import json
from pathlib import Path
import time

import pytest

from rc6_shadow_runtime import persistence
from rc6_shadow_runtime.persistence import read_committed_generation, read_committed_projection
from rc6_shadow_runtime.projection import cohort_id, open_projection, logical_digest, row_dictionary_from_header, decode_row
from tests.rc6_dashboard_native_fixture import native_fixture


def custody_stats(root):
    paths = [*Path(root).rglob("*"), *Path(str(root)+".authority").rglob("*")]
    return {str(path): (path.stat().st_ino, path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_atime_ns)
            for path in paths if path.is_file()}


@pytest.mark.parametrize("row_codec", ["ZLIB_CANONICAL_JSON_DICTIONARY_V1", "ZLIB_CANONICAL_JSON_V1"])
def test_native_projection_encoder_template_preserves_every_byte_index_digest_and_page(tmp_path, monkeypatch, row_codec):
    from rc6_shadow_runtime import projection
    native = native_fixture(tmp_path, count=12)
    original = projection.index_record
    digests = {role: native.cut["manifest"]["files"][role]["payload_digest"] for role in persistence.ROLES}
    optimized = projection.build_projection(native.cut["report"], digests, row_codec=row_codec)
    def independent(*args, **kwargs):
        kwargs.pop("_compressor", None)
        return original(*args, **kwargs)
    monkeypatch.setattr(projection, "index_record", independent)
    baseline = projection.build_projection(native.cut["report"], digests, row_codec=row_codec)
    assert optimized == baseline
    connection, header = open_projection(optimized[0])
    try:
        assert logical_digest(connection, header) == (optimized[2]["payload_digest"], optimized[2]["logical_bytes"])
        pages, scope = projection.query_projection(connection, header, filters={"currency": "ARS"}, offset=10,
                                                  limit=10, deadline=time.monotonic()+1)
        assert pages["opportunities"]["total"] == 24
        assert len(pages["opportunities"]["rows"]) == 10
        assert scope["selected"]["currency"] == "ARS"
    finally:
        connection.close()


def test_native_four_role_cut_projects_exact_counts_last_page_and_preserves_source_custody(tmp_path):
    native = native_fixture(tmp_path, count=25)
    before = custody_stats(native.root)
    page = read_committed_projection(native.root, offset=40, deadline=time.monotonic()+1)
    assert page["export_contract"]["schema"] == "rc6.shadow-ui-committed-projection.v1"
    assert page["export_contract"]["verification_level"] == "WIRE_AND_PROJECTION_SEMANTICS"
    assert set(page["manifest"]["files"]) == set(persistence.GENERATION_ROLES)
    assert page["pointer"] == native.cut["pointer"]
    expected = sum(len(plan["telemetry"]) for plan in native.cut["report"]["engines"].values())
    assert page["dataset_pages"]["opportunities"]["total"] == expected == 50
    assert len(page["dataset_pages"]["opportunities"]["rows"]) == 10
    assert len(json.dumps(page).encode()) < 4*1024**2
    for role, record in page["manifest"]["files"].items():
        assert page["export_contract"]["verified_payloads"][role]["payload_digest"] == record["payload_digest"]
    assert page["export_contract"]["derivation"] == {
        role: page["manifest"]["files"][role]["payload_digest"] for role in persistence.ROLES}
    assert custody_stats(native.root) == before
    full = read_committed_generation(native.root, roles=("report", "status"))
    assert "checkpoint" not in full and full["pointer"] == page["pointer"]
    assert full["export_contract"]["verification_level"] == "FULL_LOGICAL_SEMANTICS"


def test_native_atomic_admission_is_not_a_second_funnel_decision_and_preserves_entry(tmp_path):
    native = native_fixture(tmp_path, count=3, with_future=False)
    funnel = native.cut["report"]["operational_funnel"]
    with native.store.connect() as connection:
        snapshots = connection.execute("SELECT payload_json FROM decision_evidence_snapshots").fetchall()
    phases = [json.loads(row[0]).get("capture_phase", "NATIVE_DECISION") for row in snapshots]
    assert phases.count("ATOMIC_PAPER_ADMISSION") == 1
    assert phases.count("NATIVE_DECISION") >= 1
    assert funnel["denominators"]["lifetime_evaluations_since_watermark"] == phases.count("NATIVE_DECISION")
    assert len(native.cut["report"]["economic_exit_lab"]["entries"]) == 1
    native.worker.tick(native.as_of+timedelta(seconds=30))
    after = read_committed_generation(native.root)
    assert after["report"]["operational_funnel"]["denominators"]["lifetime_evaluations_since_watermark"] == phases.count("NATIVE_DECISION")


def test_native_cohort_pagination_and_filters_keep_selected_denominators_over_all_matches(tmp_path):
    native = native_fixture(tmp_path, count=25)
    all_groups = native.cut["report"]["operational_funnel"]["cohorts"]
    page = read_committed_projection(native.root, filters={"family": "ACCIONES", "funnel_offset": "10", "state": "HOT"})
    scope = page["funnel_scope"]
    expected = sum((row.get("family") or row["identity"][1]) == "ACCIONES" for row in all_groups)
    assert scope["state"] == "AVAILABLE" and scope["total_groups"] == expected
    assert scope["groups_offset"] == 10 and len(scope["groups"]) == 10
    selected_id = cohort_id(scope["selected"])
    only = read_committed_projection(native.root, filters={"cohort": selected_id})
    assert only["funnel_scope"]["total_groups"] == 1
    assert only["funnel_scope"]["selected"] == scope["selected"]
    assert only["dataset_pages"]["opportunities"]["total"] == 50
    assert scope["selected"]["currency"] in scope["label"] and scope["selected"]["channel"] in scope["label"]


def test_preopen_capacity_preserves_planned_revisits_with_unknown_achieved_clock(tmp_path):
    native = native_fixture(tmp_path, count=12, with_future=False, with_spot=False)
    page = read_committed_projection(native.root)
    assert page["dataset_pages"]["capacity"]["rows"]
    for row in page["dataset_pages"]["capacity"]["rows"]:
        original = native.cut["report"]["engines"][row["engine"]]["telemetry"][:10]
        assert row["telemetry"] == original


@pytest.mark.parametrize("role", tuple(persistence.GENERATION_ROLES))
def test_any_wire_member_corruption_blocks_projected_consumer(tmp_path, role):
    native = native_fixture(tmp_path, count=3)
    generation = native.root / ("gen-"+native.cut["pointer"]["generation_id"])
    member = generation / persistence.GENERATION_ROLES[role]
    raw = member.read_bytes(); member.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    with pytest.raises(ValueError, match="FILE_HASH_MISMATCH"):
        read_committed_projection(native.root)


def test_projection_deadline_and_boolean_offset_are_rejected(tmp_path):
    native = native_fixture(tmp_path, count=3)
    with pytest.raises(ValueError, match="DEADLINE"):
        read_committed_projection(native.root, deadline=time.monotonic()-1)
    with pytest.raises(ValueError, match="QUERY_INVALID"):
        read_committed_projection(native.root, offset=False)


@pytest.mark.parametrize("column,value", [("currency", "USD"), ("state", "FORGED_STATE"), ("cohort", "bad"),
    ("priority", -1), ("rank", -1), ("event_at", "9999-01-01T00:00:00+00:00")])
def test_full_logical_projection_hash_binds_every_query_index_column(tmp_path, column, value):
    native = native_fixture(tmp_path, count=3)
    generation = native.root / ("gen-"+native.cut["pointer"]["generation_id"])
    member = generation / persistence.GENERATION_ROLES["projection"]
    original = member.read_bytes()
    connection, header = open_projection(original)
    try:
        assert logical_digest(connection, header)[0] == native.cut["manifest"]["files"]["projection"]["payload_digest"]
        connection.execute("PRAGMA query_only=OFF")
        connection.execute("UPDATE projection_rows SET "+column+"=? WHERE dataset='planner' AND ticker='T000'", (value,))
        with pytest.raises(ValueError, match="DERIVATION_MISMATCH"):
            logical_digest(connection, header)
        assert member.read_bytes() == original
    finally:
        connection.close()


def test_projection_dictionary_is_bound_and_wrong_dictionary_or_crc_never_decodes(tmp_path):
    native = native_fixture(tmp_path, count=3)
    generation = native.root / ("gen-"+native.cut["pointer"]["generation_id"])
    connection, header = open_projection((generation / persistence.GENERATION_ROLES["projection"]).read_bytes())
    try:
        dictionary = row_dictionary_from_header(header)
        assert dictionary and len(dictionary) <= 32768
        raw = connection.execute("SELECT payload_json FROM projection_rows WHERE dataset='planner' LIMIT 1").fetchone()[0]
        assert decode_row(raw, dictionary=dictionary)["identity"]
        import zlib
        with pytest.raises(zlib.error): decode_row(raw, dictionary=b"wrong")
        with pytest.raises(zlib.error): decode_row(raw[:-1]+bytes([raw[-1]^1]), dictionary=dictionary)
        header["row_codec"]["sha256"] = "0"*64
        with pytest.raises(ValueError, match="DICTIONARY_INVALID"):
            row_dictionary_from_header(header)
    finally:
        connection.close()


def test_native_writer_restore_checks_every_wire_and_decodes_checkpoint_only_once(tmp_path, monkeypatch):
    from rc6_shadow_runtime import serialization
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    native = native_fixture(tmp_path, count=3)
    calls = []
    original_verify = persistence.verify_storage_wire
    original_decode = persistence.decode_storage
    def checked_verify(value, **kwargs):
        calls.append(("verify", value["logical_sha256"]))
        return original_verify(value, **kwargs)
    def checked_decode(value, **kwargs):
        calls.append(("decode", value["logical_sha256"]))
        return original_decode(value, **kwargs)
    monkeypatch.setattr(persistence, "verify_storage_wire", checked_verify)
    monkeypatch.setattr(persistence, "decode_storage", checked_decode)
    result = native.worker.files.read_writer_generation(checkpoint=True)
    expected = [("verify" if role == "report" else "decode",
                 native.cut["manifest"]["files"][role]["payload_digest"]) for role in persistence.ROLES]
    assert sorted(calls) == sorted(expected)
    assert result["checkpoint"] == native.cut["checkpoint"]
    assert result["export_contract"]["verification_level"] == "WIRE_AND_CHECKPOINT_SEMANTICS"


def test_native_writer_transaction_receipts_reuse_only_verified_bytes_and_never_return_a_cached_graph(tmp_path, monkeypatch):
    from collections import Counter
    from rc6_shadow_runtime import serialization
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    native = native_fixture(tmp_path, count=3)
    original_verify = persistence.verify_storage_wire
    original_decode = persistence.decode_storage
    calls = []
    def checked_verify(value, **kwargs):
        calls.append(("verify", value["logical_sha256"]))
        return original_verify(value, **kwargs)
    def checked_decode(value, **kwargs):
        calls.append(("decode", value["logical_sha256"]))
        return original_decode(value, **kwargs)
    monkeypatch.setattr(persistence, "verify_storage_wire", checked_verify)
    monkeypatch.setattr(persistence, "decode_storage", checked_decode)
    files = native.worker.files
    with files:
        first = files.read_writer_generation(checkpoint=True)
        first["checkpoint"]["as_of"] = "2099-01-01T00:00:00+00:00"
        first["export_contract"]["verified_payloads"]["report"]["payload_digest"] = "0"*64
        second = files.read_writer_generation(checkpoint=False)
        third = files.read_writer_generation(checkpoint=True)
        assert second["export_contract"]["verified_payloads"]["report"]["payload_digest"] != "0"*64
        assert third["checkpoint"] == native.cut["checkpoint"]
        expected = {role: native.cut["manifest"]["files"][role]["payload_digest"] for role in persistence.ROLES}
        assert Counter(calls) == Counter({("verify", expected["report"]): 1,
            ("decode", expected["checkpoint"]): 2, ("decode", expected["status"]): 3})
    assert files._wire_receipts == {}
    calls.clear()
    files.read_writer_generation(checkpoint=False)
    assert Counter(calls) == Counter(("decode" if role == "status" else "verify", value)
                                    for role, value in expected.items())


@pytest.mark.parametrize("attack", ["member", "hardlink", "authority", "deadline", "limit", "failure"])
def test_native_writer_transaction_receipts_always_recheck_sources_custody_policy_and_failure(tmp_path, monkeypatch, attack):
    from rc6_shadow_runtime import serialization
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    native = native_fixture(tmp_path, count=3)
    files = native.worker.files
    with files:
        files.read_writer_generation(checkpoint=True)
        member = native.root / ("gen-"+native.cut["pointer"]["generation_id"]) / persistence.GENERATION_ROLES["report"]
        if attack == "member":
            raw = member.read_bytes(); member.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
        elif attack == "hardlink":
            import os
            os.link(member, tmp_path/"alias")
        elif attack == "authority":
            authority = files.authority_root/"HEAD.json"
            value = json.loads(authority.read_bytes()); value["committed"] = None
            authority.write_text(json.dumps(value))
        elif attack == "limit":
            files.payload_limit = 1
        elif attack == "failure":
            files.record_failure(as_of=native.as_of.isoformat(), error=ValueError("SHADOW_SYNTHETIC_FAILURE"))
        with pytest.raises(ValueError, match="SHADOW_|RETENTION_"):
            files._wire_generation(checkpoint=False, deadline=time.monotonic()-1 if attack == "deadline" else None)


@pytest.mark.parametrize("failure", [False, True])
def test_native_projection_verifies_both_roles_concurrently_and_joins_before_success_or_failure(tmp_path, monkeypatch, failure):
    import threading
    from rc6_shadow_runtime import serialization
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    native = native_fixture(tmp_path, count=3)
    barrier = threading.Barrier(2)
    original = persistence.verify_storage_wire
    hashes = {role: native.cut["manifest"]["files"][role]["payload_digest"] for role in persistence.ROLES}
    observed = []
    def checked(value, **kwargs):
        observed.append(value["logical_sha256"])
        barrier.wait(timeout=1)
        if failure and value["logical_sha256"] == hashes["checkpoint"]:
            raise ValueError("SHADOW_SYNTHETIC_WIRE_FAILURE")
        return original(value, **kwargs)
    monkeypatch.setattr(persistence, "verify_storage_wire", checked)
    if failure:
        with pytest.raises(ValueError, match="SYNTHETIC_WIRE_FAILURE"):
            read_committed_projection(native.root, deadline=time.monotonic()+2)
    else:
        page = read_committed_projection(native.root, deadline=time.monotonic()+2)
        assert page["pointer"] == native.cut["pointer"]
        assert set(page["export_contract"]["verified_payloads"]) == set(persistence.GENERATION_ROLES)
    assert sorted(observed) == sorted([hashes["report"], hashes["checkpoint"]])
    assert not any(thread.name.startswith(("rc6-shadow-sha", "rc6-shadow-wire")) for thread in threading.enumerate())
