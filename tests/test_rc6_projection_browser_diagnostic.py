"""V2 NOGATE observations preserve canonical readers, failures and source bytes."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import gc
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from threading import Barrier
from time import monotonic
from types import SimpleNamespace

import pytest

from rc6_shadow_runtime import packed_storage, persistence, serialization
from tests.ci_rc6_projection_browser_diagnostic import (
    DiagnosticTrace, DiagnosticWindowEnded, require_then_window_end, run,
)
from tests.ci_rc6_projection_large_reader import GateFailure, custody_inventory, protected_bytes, require
from tests.rc6_dashboard_native_fixture import native_fixture

REPOSITORY = Path(__file__).resolve().parents[1]
BASE_SHA = "HEAD"
LIMITS = {"durable_limit": 64 * 1024**2, "expansion_limit": 512 * 1024**2}


@pytest.fixture(scope="module")
def native_diagnostic(tmp_path_factory):
    # Force the supported representation using the real PAPER caller and
    # SHADOW publisher. No report, financial shape or clock is fabricated.
    with pytest.MonkeyPatch.context() as writer_format:
        writer_format.setattr(serialization, "THRESHOLD", 1)
        fixture = native_fixture(tmp_path_factory.mktemp("native-diagnostic-v2"))
    generation = fixture.root / ("gen-" + fixture.cut["pointer"]["generation_id"])
    wires = {}
    for role, name in persistence.ROLES.items():
        member = protected_bytes(generation / name)
        if name.endswith(".gz"):
            member = gzip.decompress(member)
        wires[role] = json.loads(member)["payload"]
        assert wires[role]["schema"] == serialization.PACKED_SCHEMA
    return fixture, wires


def trace_for(fixture, render=0):
    return DiagnosticTrace({record["payload_digest"]: role
        for role, record in fixture.cut["manifest"]["files"].items()}, {"render": render})


def instrument_slices(trace, monkeypatch):
    for name in ("_slots", "_expanded", "_array", "_inflate"):
        monkeypatch.setattr(packed_storage, name, trace.timed("packed" + name, getattr(packed_storage, name)))


def test_v2_trace_aggregates_native_unique_expansion_and_preserves_all_occurrence_sha(native_diagnostic, monkeypatch):
    fixture, wires = native_diagnostic
    wire = wires["report"]
    trace = trace_for(fixture)
    instrument_slices(trace, monkeypatch)
    wrapped = trace.timed("fresh_unpack_wire", packed_storage.unpack_wire, codec=True)
    before = deepcopy(wire)
    for _ in range(2):
        raw = wrapped(wire, retain=True, deadline=monotonic()+1, **LIMITS)
        assert len(raw) == wire["logical_bytes"]
        assert hashlib.sha256(raw).hexdigest() == wire["logical_sha256"]
        assert json.loads(raw) == fixture.cut["report"]
    records = {row["label"]: row for row in trace.records()}
    assert records["fresh_unpack_wire"]["calls"] == records["fresh_unpack_wire"]["completed_calls"] == 2
    assert records["packed_slots"]["calls"] == 2 * wire["templates_count"]
    assert records["packed_expanded"]["calls"] == 2 * wire["instances"]["count"]
    assert records["packed_array"]["calls"] == 8
    assert records["packed_inflate"]["calls"] == 2 * (len(wire["packets"]) + 4)
    assert all(row["metadata_last_declared"]["role"] == "report" for row in records.values())
    declared = records["fresh_unpack_wire"]["metadata_last_declared"]["codec_counts_declared"]
    assert declared["references"] == wire["references"]["count"]
    assert declared["instances"] == wire["instances"]["count"]
    assert len(records) == 5  # No event per packet, slot, binding or reference.
    assert wire == before and trace.context.codec == {}


def test_v2_trace_roles_remain_separate_in_parallel_native_readers(native_diagnostic, monkeypatch):
    fixture, wires = native_diagnostic
    trace = trace_for(fixture)
    instrument_slices(trace, monkeypatch)
    wrapped = trace.timed("fresh_unpack_wire", packed_storage.unpack_wire, codec=True)
    barrier = Barrier(2)
    def read(role):
        barrier.wait(timeout=2)
        return wrapped(wires[role], retain=True, deadline=monotonic()+1, **LIMITS)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {role: pool.submit(read, role) for role in ("report", "checkpoint")}
        for role, future in futures.items():
            assert hashlib.sha256(future.result()).hexdigest() == wires[role]["logical_sha256"]
    records = trace.records()
    unpack = [row for row in records if row["label"] == "fresh_unpack_wire"]
    assert {row["metadata_last_declared"]["role"] for row in unpack} == {"report", "checkpoint"}
    assert len({row["thread_id"] for row in unpack}) == 2
    for row in records:
        metadata = row["metadata_last_declared"]
        assert metadata["logical_sha256"] == wires[metadata["role"]]["logical_sha256"]
        assert row["completed_calls"] == row["calls"] and not row["errors_by_class"]


@pytest.mark.parametrize("fault", ("expired_deadline", "schema_list"))
def test_v2_trace_preserves_native_contract_failure_and_clears_role_context(native_diagnostic, fault):
    fixture, wires = native_diagnostic
    wire = deepcopy(wires["report"])
    deadline = monotonic()-1 if fault == "expired_deadline" else monotonic()+1
    if fault == "schema_list":
        wire["schema"] = []
    with pytest.raises(ValueError) as native:
        packed_storage.unpack_wire(wire, deadline=deadline, **LIMITS)
    trace = trace_for(fixture)
    wrapped = trace.timed("fresh_unpack_wire", packed_storage.unpack_wire, codec=True)
    with pytest.raises(type(native.value)) as observed:
        wrapped(wire, deadline=deadline, **LIMITS)
    assert str(observed.value) == str(native.value)
    assert trace.context.codec == {}
    row = trace.records()[0]
    assert row["calls"] == 1 and row["completed_calls"] == 0
    assert row["errors_by_class"] == {"ValueError": 1}


def test_v2_trace_observes_each_fresh_member_read_and_same_cut_across_requests(native_diagnostic, monkeypatch):
    fixture, _ = native_diagnostic
    before = custody_inventory(fixture.database, fixture.root)
    trace = trace_for(fixture)
    monkeypatch.setattr(persistence.EvidenceFiles, "_bytes", trace.timed("member_bytes", persistence.EvidenceFiles._bytes,
        member_roles={name: role for role, name in persistence.GENERATION_ROLES.items()}))
    monkeypatch.setattr(packed_storage, "unpack_wire", trace.timed("fresh_unpack_wire", packed_storage.unpack_wire, codec=True))
    for render in range(2):
        trace.state["render"] = render
        cut = persistence.read_committed_projection(fixture.root, offset=render*10, deadline=monotonic()+1)
        assert cut["pointer"] == fixture.cut["pointer"]
        assert cut["export_contract"]["verification_level"] == "WIRE_AND_PROJECTION_SEMANTICS"
        assert cut["dataset_pages"]["opportunities"]["offset"] == render*10
    for render in range(2):
        members = [row for row in trace.records() if row["render"] == render and row["label"] == "member_bytes"]
        assert {row["metadata_last_declared"]["role"] for row in members} == set(persistence.GENERATION_ROLES)
        assert all(row["calls"] == row["completed_calls"] == 1 and row["returned_bytes_total"] > 0 for row in members)
        assert all(row["metadata_last_declared"]["logical_sha256"] == fixture.cut["manifest"]["files"][
            row["metadata_last_declared"]["role"]]["payload_digest"] for row in members)
    assert custody_inventory(fixture.database, fixture.root) == before


def test_v2_trace_keeps_gc_policy_and_reports_aggregates_only(native_diagnostic, monkeypatch):
    fixture, wires = native_diagnostic
    trace = trace_for(fixture)
    enabled, threshold, callbacks = gc.isenabled(), gc.get_threshold(), gc.callbacks[:]
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Diagnostic changed GC policy")
    for method in ("disable", "enable", "collect", "freeze", "unfreeze", "set_threshold"):
        if hasattr(gc, method):
            monkeypatch.setattr(gc, method, forbidden)
    gc.callbacks.append(trace.collect)
    try:
        trace.timed("fresh_unpack_wire", packed_storage.unpack_wire, codec=True)(
            wires["report"], deadline=monotonic()+1, **LIMITS)
    finally:
        gc.callbacks.remove(trace.collect)
    assert gc.isenabled() == enabled and gc.get_threshold() == threshold and gc.callbacks == callbacks
    assert all(row["collections"] >= 1 and "elapsed_seconds_total" in row for row in trace.gc_records())


def test_diagnostic_window_never_hides_a_real_failure_or_changes_native_check():
    with pytest.raises(GateFailure, match="^NATIVE_FAILURE$"):
        require_then_window_end(require, False, "NATIVE_FAILURE", {"reason": "unchanged"}, end=monotonic()-1)
    with pytest.raises(DiagnosticWindowEnded):
        require_then_window_end(require, True, "PASSED_NATIVE_CHECK", end=monotonic()-1)
    assert require_then_window_end(require, True, "PASSED_NATIVE_CHECK", end=monotonic()+1) is None


@pytest.mark.parametrize("destination", ("archive", "custody", "authority", "source", "existing"))
def test_diagnostic_rejects_output_into_protected_paths_before_product_import(native_diagnostic, tmp_path, destination):
    fixture, _ = native_diagnostic
    index = tmp_path/"index.json"
    index.write_text(json.dumps({"extracted_root": str(REPOSITORY), "overlays": []}))
    existing = tmp_path/"existing"
    existing.mkdir()
    output = {"archive": REPOSITORY/"diagnostic-must-not-exist", "custody": fixture.root/"diagnostic",
              "authority": Path(str(fixture.root)+".authority")/"diagnostic", "source": fixture.database,
              "existing": existing}[destination]
    before = custody_inventory(fixture.database, fixture.root)
    with pytest.raises(ValueError, match="^NEW_DIAGNOSTIC_OUTPUT_OUTSIDE_SOURCE_REQUIRED$"):
        run(SimpleNamespace(index=index, database=fixture.database, root=fixture.root, output=output))
    assert not list(existing.iterdir()) and custody_inventory(fixture.database, fixture.root) == before


def test_diagnostic_cli_native_small_v2_cut_retains_rejection_and_never_claims_acceptance(native_diagnostic, tmp_path):
    fixture, _ = native_diagnostic
    archive = tmp_path/"source.tar"
    extracted = tmp_path/"source"
    extracted.mkdir()
    with archive.open("wb") as stream:
        subprocess.run(["git", "archive", BASE_SHA], cwd=REPOSITORY, stdout=stream, check=True)
    with tarfile.open(archive) as members:
        members.extractall(extracted, filter="data")
    hashes = {str(path.relative_to(extracted)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in extracted.rglob("*") if path.is_file()}
    source_sha = subprocess.check_output(["git", "rev-parse", BASE_SHA], cwd=REPOSITORY, text=True).strip()
    tree_sha = subprocess.check_output(["git", "rev-parse", BASE_SHA+"^{tree}"], cwd=REPOSITORY, text=True).strip()
    index = tmp_path/"index.json"
    index.write_text(json.dumps({"source_sha": source_sha, "candidate_tree_sha": tree_sha,
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "source_file_hashes": hashes,
        "source_file_modes": {str(path.relative_to(extracted)): path.stat().st_mode & 0o777
                              for path in extracted.rglob("*") if path.is_file()},
        "extracted_root": str(extracted), "overlays": []}))
    output = tmp_path/"diagnostic"
    command = [sys.executable, "-I", str(REPOSITORY/"tests/ci_rc6_projection_browser_diagnostic.py"),
               "--product-python", sys.executable, "--product-python-version", f"{sys.version_info.major}.{sys.version_info.minor}",
               "--index", str(index), "--database", str(fixture.database), "--root", str(fixture.root),
               "--output", str(output)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    receipt = json.loads((output/"diagnostic.json").read_text())
    assert receipt["schema"] == "rc6.native-browser-components-diagnostic.v2"
    assert receipt["status"] == "DIAGNOSTIC_ONLY_NATIVE_OR_BROWSER_FAILURE_RETAINED"
    assert receipt["details"]["gate"] == "NATIVE_LARGE_REPORT_NOT_EXERCISED"
    assert receipt["source_proof_pass"] and receipt["native_custody_unchanged"]
    assert receipt["acceptance_complete"] is receipt["native_gate_acceptance_claim"] is False
    assert not receipt["renders"] and receipt["network_attempts"] == receipt["source_sqlite_attempts"] == 0
    unpack = [row for row in receipt["stage_aggregates"] if row["label"] == "fresh_unpack_wire"]
    assert unpack and all(row["metadata_last_declared"]["storage_schema"] == serialization.PACKED_SCHEMA for row in unpack)
    assert not (output/"browser").exists()
