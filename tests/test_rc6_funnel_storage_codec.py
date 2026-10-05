"""Native funnel equivalence and adversarial lossless-storage contracts."""
from copy import deepcopy
from datetime import timedelta
import base64
import gzip
import hashlib
import json

import pytest

from be_paper_engine import PaperStore
from rc6_shadow_runtime import funnel, serialization
from tests.test_rc6_shadow_operational_funnel import START, plan


def payload():
    row = {"native_configuration_fingerprint": "a" * 64, "available_at": START.isoformat(),
        "identity": ["S", "ACCIONES", "A-24HS", "ARS", "BYMA"],
        "~": {"~1": False, "~~literal": 0}, "native_reason_codes": ["METADATA_OBSERVED_ONLY"]}
    return {"rows": [deepcopy(row) for _ in range(2000)], "schema": "test.logical.v1"}


def seal(value):
    value["storage_sha256"] = hashlib.sha256(serialization.canonical_bytes(
        {key: item for key, item in value.items() if key != "storage_sha256"})).hexdigest()


def encode(value):
    return serialization.encode_storage(value, durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)


def decode(value):
    return serialization.decode_storage(value, durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)


def test_lossless_storage_preserves_types_escaped_keys_every_row_and_logical_digest():
    original = payload(); packed = encode(original)
    assert packed["schema"] == serialization.SCHEMA
    assert len(serialization.canonical_bytes(packed)) < len(serialization.canonical_bytes(original))
    restored = decode(packed)
    assert restored == original and len(restored["rows"]) == 2000
    assert type(restored["rows"][0]["~"]["~1"]) is bool and type(restored["rows"][0]["~"]["~~literal"]) is int
    assert hashlib.sha256(serialization.canonical_bytes(restored)).hexdigest() == packed["logical_sha256"]
    assert encode(original) == packed


@pytest.mark.parametrize("attack", ["digest", "smaller_expansion", "boolean_size", "base64", "crc",
    "duplicate_logical_key", "future_schema", "noncanonical_json", "depth", "nodes"])
def test_rehashed_storage_cannot_override_logical_digest_types_expansion_or_keys(attack, monkeypatch):
    packed = encode(payload())
    if attack == "digest": packed["logical_sha256"] = "0" * 64
    elif attack == "smaller_expansion": packed["logical_bytes"] = 100
    elif attack == "boolean_size": packed["logical_bytes"] = False
    elif attack == "base64": packed["payload"] = "!"
    elif attack == "crc":
        raw = bytearray(base64.b64decode(packed["payload"])); raw[-8] ^= 1
        packed["payload"] = base64.b64encode(raw).decode()
    elif attack in {"duplicate_logical_key", "noncanonical_json", "depth"}:
        raw = b'{"a":1,"a":2}' if attack == "duplicate_logical_key" else b'{ "a": 1 }' if attack == "noncanonical_json" else b'['*65+b'0'+b']'*65
        packed.update(payload=base64.b64encode(gzip.compress(raw, mtime=0)).decode(),
            logical_bytes=len(raw), logical_sha256=hashlib.sha256(raw).hexdigest())
    elif attack == "future_schema": packed["schema"] = "rc6.lossless-json-storage.v99"
    elif attack == "nodes": monkeypatch.setattr(serialization, "MAX_NODES", 1)
    seal(packed)
    with pytest.raises(ValueError, match="SHADOW_"):
        decode(packed)


def test_native_funnel_large_metadata_roundtrip_restarts_with_identical_complete_outputs(tmp_path):
    store = PaperStore(str(tmp_path / "source.db"))
    initial = plan(START)
    original = initial["engines"]["EQUITY_SPOT"]["telemetry"][0]
    initial["engines"]["EQUITY_SPOT"]["telemetry"] = [{**deepcopy(original),
        "identity": ["S" + str(index), "ACCIONES", "BYMA", "ARS", "A-24HS"]} for index in range(1000)]
    report, state = funnel.evaluate_runtime_funnel(store.path, as_of=START, planner_report=initial)
    packed = funnel.encode_funnel_checkpoint(state)
    assert packed["schema"] == serialization.PACKED_SCHEMA
    assert len(state["reaches"]) == len(state["cohorts"]) == 1000
    assert funnel.decode_funnel_checkpoint(packed) == state
    later = START + timedelta(seconds=30)
    plain_report, plain_state = funnel.evaluate_runtime_funnel(store.path, as_of=later, previous=state, planner_report=initial)
    packed_report, packed_state = funnel.evaluate_runtime_funnel(store.path, as_of=later, previous=packed, planner_report=initial)
    assert packed_report == plain_report and packed_state == plain_state
    assert packed_report["denominators"]["stage_identity_reaches"] == 1000
    assert packed_report["denominators"]["events_recorded"] == report["denominators"]["events_recorded"]
    assert funnel.decode_funnel_checkpoint(packed) == state
    assert state["last_as_of"] == START.isoformat()


def test_prepared_storage_is_exact_for_unicode_aliases_and_mutable_root_headers():
    row = {"identity": ["ÑANDÚ", "ACCIONES", "BYMA", "ARS", "A-24HS"], "typed": {"flag": False, "zero": 0},
           "literal/~key": "observación"}
    body = {"schema": "native", "rows": [row]*4000, "as_of": START.isoformat()}
    prepared = serialization.PreparedStorage(body, mutable={"cross_payload_hashes", "status"},
        durable_limit=32*1024**2, expansion_limit=64*1024**2)
    for status in ("OBSERVING", "RETENTION_PRESSURE"):
        body.update(status=status, cross_payload_hashes={"report": "a"*64})
        packed, logical_sha = prepared.encode(body)
        expected = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        assert logical_sha == hashlib.sha256(expected).hexdigest()
        assert decode(packed) == body
        assert serialization.verify_storage_wire(packed, durable_limit=32*1024**2,
                                                 expansion_limit=64*1024**2)["payload_digest"] == logical_sha


def test_shape_memo_counts_logical_alias_expansion_and_rejects_cycles(monkeypatch):
    repeated = {"fields": list(range(100))}
    value = [repeated]*100
    assert serialization._shape(value) == 10201
    monkeypatch.setattr(serialization, "MAX_NODES", 1000)
    with pytest.raises(ValueError, match="COMPLEXITY"):
        serialization._shape(value)
    cyclic = []; cyclic.append(cyclic)
    with pytest.raises(ValueError, match="COMPLEXITY"):
        serialization._shape(cyclic)


def test_bulk_shape_counts_every_scalar_and_enforces_depth_at_leaf_boundary(monkeypatch):
    assert serialization._shape({"values": [None, False, 0, "x", {"leaf": 1}]}) == 8
    monkeypatch.setattr(serialization, "MAX_DEPTH", 2)
    assert serialization._shape({"values": [None]}) == 3
    with pytest.raises(ValueError, match="COMPLEXITY"):
        serialization._shape({"values": [{"leaf": 1}]})
    monkeypatch.setattr(serialization, "MAX_NODES", 0)
    with pytest.raises(ValueError, match="COMPLEXITY"):
        serialization._shape(None)


@pytest.mark.parametrize("attack", [None, "logical_digest", "crc", "deadline", "expansion", "base64"])
def test_bounded_parallel_hash_matches_serial_proof_and_joins_every_worker(attack):
    import threading
    import time
    packed = encode(payload())
    if attack == "logical_digest": packed["logical_sha256"] = "0"*64
    elif attack == "crc":
        raw = bytearray(base64.b64decode(packed["payload"])); raw[-8] ^= 1
        packed["payload"] = base64.b64encode(raw).decode()
    elif attack == "expansion": packed["logical_bytes"] = 1
    elif attack == "base64": packed["payload"] = "!"
    seal(packed)
    arguments = dict(durable_limit=32*1024**2, expansion_limit=64*1024**2,
                     deadline=time.monotonic()-1 if attack == "deadline" else None)
    if attack is None:
        expected = serialization.verify_storage_wire(packed, **arguments)
        assert serialization.verify_storage_wire(packed, **arguments, _pipeline_hash=True) == expected
    else:
        with pytest.raises(ValueError, match="SHADOW_"):
            serialization.verify_storage_wire(packed, **arguments, _pipeline_hash=True)
    assert not any(thread.name.startswith("rc6-shadow-sha") for thread in threading.enumerate())


def test_hash_workers_receive_bounded_immutable_scatter_buffers_without_joining_expanded_json(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    observed = []
    class Captured(ThreadPoolExecutor):
        def submit(self, operation, parts):
            assert type(parts) is tuple and 0 < len(parts) <= 64
            assert all(type(raw) is bytes for raw in parts)
            assert sum(map(len, parts)) < 2 * 1024**2
            observed.append((len(parts), sum(map(len, parts))))
            return super().submit(operation, parts)
    monkeypatch.setattr(serialization, "ThreadPoolExecutor", Captured)
    body = {"rows": [{"identity": ["ÑANDÚ", "ACCIONES", "BYMA", "ARS", "A-24HS"],
                     "literal/~key": "observación", "typed": [False, 0, None]}] * 20000}
    packed = serialization.PreparedStorage(body, mutable=(), durable_limit=32 * 1024**2,
        expansion_limit=64 * 1024**2).encode(body)[0]
    expected = serialization.verify_storage_wire(packed, durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)
    actual = serialization.verify_storage_wire(packed, durable_limit=32 * 1024**2,
        expansion_limit=64 * 1024**2, _pipeline_hash=True)
    assert actual == expected and len(observed) >= 2
    assert not any(thread.name.startswith("rc6-shadow-sha") for thread in threading.enumerate())
