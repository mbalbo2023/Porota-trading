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
