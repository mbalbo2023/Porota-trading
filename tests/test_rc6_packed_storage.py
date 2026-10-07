"""Canonical native output, dual dispatch and resealed adversarial wire cases."""
from copy import deepcopy
import base64
import gzip
import hashlib
import json
import struct
import time

import pytest

from rc6_shadow_runtime import packed_storage as packed, serialization


LIMITS = dict(durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)


def original():
    common = {"identity": ["ÑANDÚ", "ACCIONES", "BYMA", "ARS", "A-24HS"],
        "as_of": "2026-10-05T13:35:00+00:00", "book_at": None,
        "typed": [False, 0, -0.0, "\\u0000", "\0", "observación/~"],
        "rank_components": {"last_useful_observation_at": None, "ratio": 1.25}}
    return {"schema": "synthetic.native.v1", "rows": [common] * 4000}


def seal(value):
    value["storage_sha256"] = hashlib.sha256(serialization.canonical_bytes(
        {key: member for key, member in value.items() if key != "storage_sha256"})).hexdigest()


def array(value, name):
    return gzip.decompress(base64.b64decode(value[name]["payload"]))


def replace_array(value, name, raw, *, count=None):
    compressed = gzip.compress(raw, mtime=0, compresslevel=1)
    value[name]["payload"] = base64.b64encode(compressed).decode("ascii")
    value[name]["sha256"] = hashlib.sha256(compressed).hexdigest()
    if count is not None:
        value[name]["count"] = count


def test_packed_native_aliases_preserve_all_occurrences_types_unicode_clocks_and_full_ascii_sha():
    value = original()
    wire = packed.encode_packed_storage(value, **LIMITS)
    assert wire["schema"] == serialization.PACKED_SCHEMA
    expected = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    assert wire["logical_bytes"] == len(expected)
    assert wire["logical_sha256"] == hashlib.sha256(expected).hexdigest()
    restored = serialization.decode_storage(wire, **LIMITS)
    assert restored == value and len(restored["rows"]) == 4000
    assert restored["rows"][0] is not restored["rows"][1]
    restored["rows"][0]["rank_components"]["ratio"] = 8
    assert restored["rows"][1]["rank_components"]["ratio"] == 1.25
    assert serialization.decode_storage(wire, **LIMITS) == value
    proof = serialization.verify_storage_wire(wire, **LIMITS)
    assert proof == {"payload_digest": hashlib.sha256(expected).hexdigest(),
        "logical_bytes": len(expected), "storage_schema": serialization.PACKED_SCHEMA}
    assert packed.encode_packed_storage(value, **LIMITS) == wire


def test_prepared_packed_header_updates_are_exact_and_do_not_mutate_static_or_prior_encoded_values():
    body = original()
    prior = deepcopy(body)
    prepared = packed.PreparedPackedStorage(body, mutable={"cross_payload_hashes", "status"}, **LIMITS)
    previous = None
    for status in ("OBSERVING", "RETENTION_PRESSURE"):
        body.update(status=status, cross_payload_hashes={"report": status * 3})
        wire, logical = prepared.encode(body)
        expected = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        assert prepared.metrics(body) == (hashlib.sha256(expected).hexdigest(), len(expected))
        assert logical == hashlib.sha256(expected).hexdigest()
        assert serialization.decode_storage(wire, **LIMITS) == body
        if previous is not None:
            assert serialization.decode_storage(previous, **LIMITS)["status"] == "OBSERVING"
        previous = wire
    assert body["rows"] == prior["rows"]


@pytest.mark.parametrize("schema", [serialization.SCHEMA, serialization.PACKED_SCHEMA])
def test_dual_reader_reports_the_actual_storage_schema(schema):
    value = original()
    wire = (serialization.encode_storage(value, **LIMITS) if schema == serialization.SCHEMA
            else packed.encode_packed_storage(value, **LIMITS))
    assert serialization.is_storage(wire)
    assert serialization.verify_storage_wire(wire, **LIMITS)["storage_schema"] == schema
    assert serialization.decode_storage(wire, **LIMITS) == value


def signed_zero_values(reverse):
    first, second = ((0.0, -0.0) if reverse else (-0.0, 0.0))
    return {"rows": [{"delta": first}, {"delta": second}, {"delta": first}],
        "nested": [[first], [second], [first]],
        "types": [{"value": False}, {"value": 0}, {"value": first}, {"value": second}],
        "padding": "observación" * 30000}


@pytest.mark.parametrize("schema", [serialization.SCHEMA, serialization.PACKED_SCHEMA])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("share_subtrees", [False, True])
def test_both_storage_versions_preserve_signed_zero_nested_repetitions_and_scalar_types(schema, reverse, share_subtrees):
    value = signed_zero_values(reverse)
    encoded = (serialization.encode_storage(value, **LIMITS) if schema == serialization.SCHEMA
               else packed.encode_packed_storage(value, **LIMITS))
    assert encoded["schema"] == schema
    restored = serialization.decode_storage(encoded, **LIMITS, share_subtrees=share_subtrees)
    expected = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    assert json.dumps(restored, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() == expected
    assert encoded["logical_sha256"] == hashlib.sha256(expected).hexdigest()
    assert restored["rows"][0] is not restored["rows"][1]
    assert [type(row["value"]) for row in restored["types"]] == [bool, int, float, float]


@pytest.mark.parametrize("schema", [serialization.SCHEMA, serialization.PACKED_SCHEMA])
@pytest.mark.parametrize("reverse", [False, True])
def test_native_publisher_and_full_committed_reader_preserve_signed_zero_and_exact_role_digests(tmp_path, monkeypatch, schema, reverse):
    from rc6_shadow_runtime import persistence
    from rc6_shadow_runtime.persistence import EvidenceFiles, read_committed_generation
    from tests.test_rc6_shadow_runtime_wiring import PRE
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    if schema == serialization.SCHEMA:
        monkeypatch.setattr(persistence, "PreparedStorage", serialization.PreparedStorage)
    native = signed_zero_values(reverse)
    base = {"as_of": PRE.isoformat(), "mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED"}
    report = {**base, **native, "source_reports": [], "source_audit": {}}
    with EvidenceFiles(tmp_path / "shadow") as files:
        cut = files.commit_generation(report, {**base, **native}, {**base, "status": "SHADOW_OBSERVING"},
            source_watermark={"as_of": PRE.isoformat(), "source_identity": "synthetic-signed-zero"},
            configuration_fingerprint="synthetic-signed-zero-config")
    restored = read_committed_generation(files.root)
    assert restored["pointer"] == cut["pointer"]
    for role in ("report", "checkpoint"):
        actual = {key: restored[role][key] for key in native}
        assert serialization.canonical_metrics(actual, ensure_ascii=True) == serialization.canonical_metrics(native, ensure_ascii=True)
        proof = restored["export_contract"]["verified_payloads"][role]
        assert proof["storage_schema"] == schema
        assert proof["payload_digest"] == restored["manifest"]["files"][role]["payload_digest"]
        assert serialization.canonical_metrics(restored[role], ensure_ascii=True) == (proof["payload_digest"], proof["logical_bytes"])


@pytest.mark.parametrize("attack", ["digest", "boolean_size", "small_size", "template_count_bool", "bindings_none",
    "bindings_list", "packet_crc", "packet_base64", "packet_cid", "array_crc", "array_count_bool",
    "ref_oob", "ref_reorder", "binding_oob", "directory_overlap", "directory_extra", "instance_duplicate",
    "unknown_key", "unknown_codec", "unknown_schema", "trailing_packet", "directory_kind"])
def test_resealed_packed_wire_attacks_fail_closed_with_contractual_errors(attack):
    wire = packed.encode_packed_storage(original(), **LIMITS)
    if attack == "digest": wire["logical_sha256"] = "0" * 64
    elif attack == "boolean_size": wire["logical_bytes"] = False
    elif attack == "small_size": wire["logical_bytes"] = 1
    elif attack == "template_count_bool": wire["templates_count"] = True
    elif attack == "bindings_none": wire["bindings"] = None
    elif attack == "bindings_list": wire["bindings"] = []
    elif attack in {"packet_crc", "packet_cid", "packet_base64", "trailing_packet"}:
        packet = wire["packets"][0]
        if attack == "packet_base64": packet["payload"] = "!"
        elif attack == "packet_cid": packet["sha256"] = "0" * 64
        else:
            raw = bytearray(base64.b64decode(packet["payload"]))
            if attack == "packet_crc": raw[-8] ^= 1
            else: raw.extend(b"TRAILING")
            packet.update(payload=base64.b64encode(raw).decode("ascii"), sha256=hashlib.sha256(raw).hexdigest())
    elif attack == "array_crc":
        raw = bytearray(base64.b64decode(wire["references"]["payload"])); raw[-8] ^= 1
        wire["references"].update(payload=base64.b64encode(raw).decode("ascii"), sha256=hashlib.sha256(raw).hexdigest())
    elif attack == "array_count_bool": wire["references"]["count"] = True
    elif attack in {"ref_oob", "ref_reorder"}:
        raw = bytearray(array(wire, "references"))
        raw[0:4] = struct.pack("!I", wire["instances"]["count"] if attack == "ref_oob" else struct.unpack_from("!I", raw, 4)[0])
        replace_array(wire, "references", raw)
    elif attack == "binding_oob":
        raw = bytearray(array(wire, "bindings")); raw[:4] = struct.pack("!I", wire["literals_count"])
        replace_array(wire, "bindings", raw)
    elif attack in {"directory_overlap", "directory_extra", "directory_kind"}:
        raw = bytearray(array(wire, "directory"))
        if attack == "directory_overlap": struct.pack_into("!I", raw, 4, 9)
        elif attack == "directory_kind": struct.pack_into("!I", raw, 0, len(wire["packets"])-1)
        else: raw.extend(raw[:12])
        replace_array(wire, "directory", raw, count=len(raw)//12)
    elif attack == "instance_duplicate":
        raw = bytearray(array(wire, "instances")); raw[12:24] = raw[:12]
        replace_array(wire, "instances", raw)
    elif attack == "unknown_key": wire["unused"] = None
    elif attack == "unknown_codec": wire["codec"] = "UNKNOWN"
    elif attack == "unknown_schema": wire["schema"] = "rc6.lossless-json-storage.v99"
    seal(wire)
    with pytest.raises(ValueError, match="SHADOW_"):
        serialization.decode_storage(wire, **LIMITS)


@pytest.mark.parametrize("raw", [b'{ "x": 1 }', b'{"x":1,"x":2}', b'{"x":NaN}', b'[' * 65 + b'0' + b']' * 65])
def test_full_reader_keeps_duplicate_nonfinite_depth_and_canonical_guards_after_valid_wire(raw):
    wire = packed._encode_captures((packed.Capture(raw, ()),), logical_sha=hashlib.sha256(raw).hexdigest(),
        logical_bytes=len(raw), **LIMITS)
    # WIRE_AND_PROJECTION_SEMANTICS promises byte integrity, not this parse.
    assert serialization.verify_storage_wire(wire, **LIMITS)["payload_digest"] == hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="SHADOW_"):
        serialization.decode_storage(wire, **LIMITS)


def test_marker_and_independent_entry_expansion_limits_precede_unbounded_materialization(monkeypatch):
    monkeypatch.setattr(packed, "MAX_TEMPLATE_MARKERS", 2)
    raw = (b"\0" + struct.pack("!I", 0)) * 3
    with pytest.raises(ValueError, match="MARKER_CAPACITY"):
        packed._expanded(packed.Capture(raw, (b"0",)), expansion_limit=1024)
    monkeypatch.setattr(packed, "MAX_ENTRY_BYTES", 10)
    with pytest.raises(ValueError, match="ENTRY_CAPACITY"):
        packed._encode_captures((packed.Capture(b'"' + b"x" * 11 + b'"', ()),), logical_sha="0"*64,
                               logical_bytes=13, **LIMITS)


def test_wire_validates_each_template_plan_once_and_hashes_every_reference(monkeypatch):
    wire = packed.encode_packed_storage(original(), **LIMITS)
    calls = []
    actual = packed._slots
    def tracked(template, **kwargs):
        calls.append(template)
        return actual(template, **kwargs)
    monkeypatch.setattr(packed, "_slots", tracked)
    proof = serialization.verify_storage_wire(wire, **LIMITS)
    assert len(calls) == wire["templates_count"]
    assert len(set(calls)) == wire["templates_count"]
    assert wire["references"]["count"] > wire["instances"]["count"]
    assert proof["logical_bytes"] == len(json.dumps(original(), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())


def test_durable_size_guard_counts_the_actual_complete_canonical_envelope_once():
    wire = packed.encode_packed_storage(original(), **LIMITS)
    size = len(serialization.canonical_bytes(wire))
    assert serialization.verify_storage_wire(wire, durable_limit=size, expansion_limit=LIMITS["expansion_limit"])
    with pytest.raises(ValueError, match="DURABLE_CAPACITY"):
        serialization.verify_storage_wire(wire, durable_limit=size-1, expansion_limit=LIMITS["expansion_limit"])


def test_deadline_is_checked_inside_packets_and_references():
    wire = packed.encode_packed_storage(original(), **LIMITS)
    with pytest.raises(ValueError, match="QUERY_DEADLINE"):
        serialization.verify_storage_wire(wire, **LIMITS, deadline=time.monotonic()-1)


def test_original_envelope_gzip_frames_restore_every_field_without_an_encoder(monkeypatch):
    wire = packed.encode_packed_storage(original(), **LIMITS)
    envelope = {"digest": wire["logical_sha256"], "payload": wire}
    frames = packed.envelope_components(envelope)
    expected = serialization.canonical_bytes(envelope)
    monkeypatch.setattr(packed, "_compress", lambda *_: (_ for _ in ()).throw(AssertionError("RESTORE_ENCODER_CALLED")))
    assert gzip.decompress(b"".join(frames)) == expected
    assert json.loads(gzip.decompress(b"".join(frames))) == envelope
    expanded = {**envelope, "retained_extra": "observación"}
    # The closed optimization never silently drops an added envelope field.
    monkeypatch.undo()
    assert gzip.decompress(b"".join(packed.envelope_components(expanded))) == json.dumps(
        expanded, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def test_native_publisher_legacy_storage_forward_transition_keeps_both_cuts_and_actual_role_proofs(tmp_path, monkeypatch):
    from rc6_shadow_runtime import persistence
    from rc6_shadow_runtime.persistence import EvidenceFiles, read_committed_generation, read_committed_projection
    from tests.test_issue465_generations import publish
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    with EvidenceFiles(tmp_path / "shadow") as files:
        with monkeypatch.context() as legacy:
            legacy.setattr(persistence, "PreparedStorage", serialization.PreparedStorage)
            first = publish(files, 1)
        first_cut = files.read_generation()
        second = publish(files, 2)
    cut = read_committed_generation(files.root)
    projected = read_committed_projection(files.root, limit=1)
    assert cut["pointer"]["sequence"] == first_cut["pointer"]["sequence"] + 1
    assert cut["manifest"]["previous_manifest_sha256"] == first["pointer"]["manifest_sha256"]
    assert (files.root / ("gen-" + first["pointer"]["generation_id"])).is_dir()
    assert cut["pointer"] == second["pointer"] == projected["pointer"]
    for role in ("report", "checkpoint", "status"):
        assert first_cut["export_contract"]["verified_payloads"][role]["storage_schema"] == serialization.SCHEMA
        assert cut["export_contract"]["verified_payloads"][role]["storage_schema"] == serialization.PACKED_SCHEMA
        assert projected["export_contract"]["verified_payloads"][role]["storage_schema"] == serialization.PACKED_SCHEMA


class _StoragePhaseUnitCollector:
    """Unit metadata sink; no kernel, artifact or runtime qualification."""
    def __init__(self):
        self.messages = []

    def put(self, message):
        self.messages.append(dict(message))


def test_storage_phase_observation_keeps_wire_callbacks_aliases_and_input_immutable():
    from scripts.rc6_issue465_stress import _storage_constructor_observation
    trace = []
    class Scalar:
        def __str__(self):
            trace.append("same_original_default_str")
            return "observación/~"
    body = original()
    body["callback"] = Scalar()
    body["status"] = "OBSERVING"
    before = deepcopy({key: value for key, value in body.items() if key != "callback"})
    scalar = body["callback"]
    assert packed._STORAGE_PHASE_OBSERVER.get() is None
    prepared = packed.PreparedPackedStorage(body, mutable={"status"}, **LIMITS)
    wire_none, digest_none = prepared.encode(body)
    baseline_trace = list(trace)
    trace.clear()
    sink, errors = _StoragePhaseUnitCollector(), []
    with _storage_constructor_observation(sink, 1, time.process_time(), errors):
        observed = packed.PreparedPackedStorage(body, mutable={"status"}, **LIMITS)
    assert packed._STORAGE_PHASE_OBSERVER.get() is None
    wire_observed, digest_observed = observed.encode(body)
    assert errors == [] and trace == baseline_trace
    assert wire_observed == wire_none and digest_observed == digest_none
    assert list(wire_observed) == list(wire_none)
    assert {key: value for key, value in body.items() if key != "callback"} == before
    assert body["callback"] is scalar and body["rows"][0] is body["rows"][1]
    phases = [(row["storage_phase"], row["storage_edge"]) for row in sink.messages]
    assert phases == [(phase, edge) for phase in ("SHAPE", "COUNT", "CAPTURE")
                      for edge in ("ENTER", "RETURN")]
    assert all(row["constructor_ordinal"] == 1 for row in sink.messages)
    assert all(row["phase_elapsed_seconds"] >= 0 and row["phase_cpu_seconds"] >= 0
               for row in sink.messages if row["storage_edge"] == "RETURN")
    prior_messages = list(sink.messages)
    observed.metrics(body)
    observed.encode(body)
    assert sink.messages == prior_messages  # COUNT outside measured ctor is inactive.


def test_storage_phase_original_shape_error_keeps_enter_without_return_and_context_restored():
    from scripts.rc6_issue465_stress import _storage_constructor_observation
    body = {1: "original_invalid_nonstring_key"}
    with pytest.raises(ValueError) as baseline:
        packed.PreparedPackedStorage(body, **LIMITS)
    sink, errors = _StoragePhaseUnitCollector(), []
    enclosing = lambda *_: None
    original_context = packed._STORAGE_PHASE_OBSERVER.set(enclosing)
    try:
        with pytest.raises(type(baseline.value)) as observed:
            with _storage_constructor_observation(sink, 1, time.process_time(), errors):
                packed.PreparedPackedStorage(body, **LIMITS)
        assert packed._STORAGE_PHASE_OBSERVER.get() is enclosing
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(original_context)
    assert packed._STORAGE_PHASE_OBSERVER.get() is None
    assert str(observed.value) == str(baseline.value) and errors == []
    def native_frames(error):
        result, node = [], error.__traceback__
        while node is not None:
            if node.tb_frame.f_globals.get("__name__") in {
                    "rc6_shadow_runtime.packed_storage", "rc6_shadow_runtime.serialization"}:
                result.append((node.tb_frame.f_code.co_name, node.tb_frame.f_code.co_filename))
            node = node.tb_next
        return result
    assert native_frames(observed.value) == native_frames(baseline.value)
    assert [(row["storage_phase"], row["storage_edge"]) for row in sink.messages] == [("SHAPE", "ENTER")]
    assert body == {1: "original_invalid_nonstring_key"}


def test_storage_phase_edges_keep_real_publication_claim_caller_and_new_source_code_constant():
    import sys
    from rc6_shadow_runtime.publication_storage import prepare_publication_storage
    claims, events = [], []
    def observer(phase, edge):
        events.append((phase, edge))
        if phase == "COUNT" and edge == "ENTER":
            builder_frame = sys._getframe(2)
            assert builder_frame.f_code is packed._CaptureBuilder.__init__.__code__
            assert builder_frame.f_back.f_code is packed._PREPARED_CONSTRUCTOR_CODE
            claims.append(builder_frame.f_locals["self"]._publication_scope is not None)
    common = original()["rows"]
    values = {"report": {"rows": common}, "checkpoint": {"rows": common}, "status": {"status": "OBSERVING"}}
    token = packed._STORAGE_PHASE_OBSERVER.set(observer)
    try:
        prepared, cache = prepare_publication_storage(values, mutable={"status"},
            durable_limit=LIMITS["durable_limit"], expansion_limit=LIMITS["expansion_limit"])
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert packed._STORAGE_PHASE_OBSERVER.get() is None
    assert list(prepared) == ["report", "checkpoint", "status"] and claims == [True, True, True]
    assert events == [(phase, edge) for _ in values for phase in ("SHAPE", "COUNT", "CAPTURE")
                      for edge in ("ENTER", "RETURN")]
    for role, value in values.items():
        wire, _ = prepared[role].encode(value)
        assert serialization.decode_storage(wire, **LIMITS) == value


@pytest.mark.parametrize("allow_fail_closed", [False, True])
def test_storage_phase_real_closed_queue_error_vetoes_resource_result_for_both_allow_flags(allow_fail_closed):
    import multiprocessing as mp
    from scripts.rc6_issue465_stress import (
        StressResourceLimit, _storage_constructor_observation, _require_stress_resource_gates)
    queue = mp.get_context("spawn").Queue()
    queue.close()  # Real stdlib failure; no invented child/kernel/PASS receipt.
    errors, body = [], {"rows": [{"identity": "UNIT_ONLY"}]}
    try:
        with _storage_constructor_observation(queue, 1, time.process_time(), errors):
            prepared = packed.PreparedPackedStorage(body, **LIMITS)
        assert packed._STORAGE_PHASE_OBSERVER.get() is None
        wire, _ = prepared.encode(body)
        assert serialization.decode_storage(wire, **LIMITS) == body
    finally:
        queue.join_thread()
    assert len(errors) == 6 and all(row["error_class"] == "ValueError" for row in errors)
    # Boolean inputs isolate a predicate unit control. They assert no database,
    # process, artifact, image, runtime or material gate qualification.
    receipt = {"schema": "rc6.unit-observation-veto.NOT_GATE_ACCEPTANCE",
        "completion_required": not allow_fail_closed,
        "shadow": {"cycle_completion": True, "storage_phase_observation_errors": errors},
        "resource_gates": {"source_database_unchanged": True, "evidence_within_quota": True,
            "rss_within_two_gib": True, "child_cleanup_completed": True,
            "complete_committed_cycle": allow_fail_closed or True,
            "actual_slow_fsync_exit_isolation": True}}
    with pytest.raises(StressResourceLimit) as failed:
        _require_stress_resource_gates(receipt, None)
    assert failed.value.evidence is receipt
    assert receipt["resource_gates"]["storage_phase_observation_healthy"] is False
    assert receipt["business_resource_complete"] is False
    assert receipt["shadow"]["storage_phase_observation_errors"] is errors
    assert receipt["import_proof_complete"] is False
