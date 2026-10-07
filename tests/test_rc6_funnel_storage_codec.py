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


@pytest.mark.parametrize("key", (1, None, False, ()), ids=("integer", "none", "boolean", "tuple"))
def test_shape_exact_key_preclassification_never_accepts_original_nonstring_keys(key):
    with pytest.raises(ValueError, match="SHADOW_STORAGE_STRING_KEY_REQUIRED"):
        serialization._shape({"prefix": [], key: "scalar"})


def test_shape_key_type_identity_never_invokes_metaclass_hash_or_equality():
    import sys
    calls = []
    class ExplosiveMeta(type):
        def __eq__(self, other):
            raise AssertionError("KEY_TYPE_EQUALITY_CALLBACK_FORBIDDEN")
        def __hash__(self):
            raise AssertionError("KEY_TYPE_HASH_CALLBACK_FORBIDDEN")
    class LegacyStringKey(metaclass=ExplosiveMeta):
        @property
        def __class__(self):
            calls.append(sys._getframe(1).f_code.co_name)
            return str
    assert serialization._shape({LegacyStringKey(): [1, 2]}) == 4
    assert calls == ["<genexpr>"]


def test_shape_string_subclass_keeps_original_builtin_isinstance_without_string_callbacks():
    class LegacyString(str):
        @property
        def __class__(self):
            raise AssertionError("ACTUAL_STRING_SUBCLASS_CLASS_CALLBACK_FORBIDDEN")
        def __str__(self):
            raise AssertionError("SHAPE_STRING_CONVERSION_FORBIDDEN")
    assert serialization._shape({LegacyString("key"): [1, 2]}) == 4


def test_shape_unknown_key_keeps_original_callback_frame_and_exact_exception():
    import sys
    calls = []
    failure = RuntimeError("ORIGINAL_KEY_CALLBACK_FAILURE")
    class LegacyKey:
        @property
        def __class__(self):
            calls.append(sys._getframe(1).f_code.co_name)
            raise failure
    with pytest.raises(RuntimeError, match="ORIGINAL_KEY_CALLBACK_FAILURE") as observed:
        serialization._shape({"prefix": [], LegacyKey(): 1})
    assert observed.value is failure
    assert calls == ["<genexpr>"]


def test_shape_key_callback_mutation_keeps_original_iterator_failure():
    import sys
    calls = []
    value = {"prefix": []}
    class MutatingKey:
        @property
        def __class__(self):
            calls.append(sys._getframe(1).f_code.co_name)
            value["inserted_by_original_callback"] = 1
            return str
    value[MutatingKey()] = 1
    with pytest.raises(RuntimeError, match="dictionary changed size during iteration"):
        serialization._shape(value)
    assert calls == ["<genexpr>"]


def test_shape_original_key_validation_finishes_before_current_values_are_walked():
    value = {"prefix": []}
    class MutatingKey:
        @property
        def __class__(self):
            value["prefix"] = [1, 2, 3]
            return str
    value[MutatingKey()] = 1
    assert serialization._shape(value) == 6


def test_shape_dictionary_subclass_keeps_original_key_and_values_callbacks():
    calls = []
    class ObservedDict(dict):
        def __iter__(self):
            calls.append("keys")
            return super().__iter__()
        def values(self):
            calls.append("values")
            return super().values()
    assert serialization._shape(ObservedDict({"x": [1, 2]})) == 4
    assert calls == ["keys", "values"]


def test_shape_legacy_any_binding_is_called_instead_of_bypassed(monkeypatch):
    original = any
    calls = []
    def observed(values):
        calls.append(values.gi_code.co_name)
        return original(values)
    monkeypatch.setattr(serialization, "any", observed, raising=False)
    assert serialization._shape({"x": [1, 2]}) == 4
    assert calls == ["<genexpr>"]


def test_shape_legacy_type_binding_keeps_original_python_caller_frames(monkeypatch):
    import sys
    original = type
    calls = []
    def observed(value):
        calls.append(sys._getframe(1).f_code.co_name)
        return original(value)
    monkeypatch.setattr(serialization, "type", observed, raising=False)
    assert serialization._shape({"x": 1}) == 2
    assert calls == ["visit", "<genexpr>", "visit"]


def test_shape_legacy_isinstance_binding_keeps_original_nonstring_denial(monkeypatch):
    original = isinstance
    calls = []
    def observed(value, classes):
        calls.append((value, classes))
        return original(value, classes)
    monkeypatch.setattr(serialization, "isinstance", observed, raising=False)
    with pytest.raises(ValueError, match="SHADOW_STORAGE_STRING_KEY_REQUIRED"):
        serialization._shape({1: 2})
    assert calls == [(1, str)]


def test_shape_binding_changed_by_original_callback_still_denies_next_dictionary(monkeypatch):
    failure = RuntimeError("ORIGINAL_ANY_BINDING_DENIAL")
    def deny(values):
        raise failure
    class ChangingValue:
        @property
        def __class__(self):
            monkeypatch.setattr(serialization, "any", deny, raising=False)
            return ChangingValue
    with pytest.raises(RuntimeError, match="ORIGINAL_ANY_BINDING_DENIAL") as observed:
        serialization._shape({"trigger": ChangingValue(), "later": {"x": 1}})
    assert observed.value is failure


@pytest.mark.parametrize("binding", ("all", "map", "_key_type_is", "_key_type_repeat"))
def test_shape_untrusted_fast_operation_never_calls_user_callback_or_accepts_bad_key(monkeypatch, binding):
    calls = []
    def unsafe(*args, **kwargs):
        calls.append(True)
        raise AssertionError("UNTRUSTED_FAST_OPERATION_CALLED")
    monkeypatch.setattr(serialization, binding, unsafe, raising=False)
    with pytest.raises(ValueError, match="SHADOW_STORAGE_STRING_KEY_REQUIRED"):
        serialization._shape({1: 2})
    assert calls == []


def test_shape_alias_expansion_cycles_original_depth_and_memo_caps_remain_exact():
    shared = {"x": [1, 2]}
    assert serialization._shape({"a": shared, "b": shared}) == 9
    assert serialization._shape({}) == 1
    cyclic = {}
    cyclic["cycle"] = cyclic
    with pytest.raises(ValueError, match="SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED"):
        serialization._shape(cyclic)
    deep = None
    for _ in range(serialization.MAX_DEPTH + 1):
        deep = {"child": deep}
    with pytest.raises(ValueError, match="SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED"):
        serialization._shape(deep)
    value = {"x": 1}
    with pytest.raises(ValueError, match="SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED"):
        serialization._shape(value, memo={id(value): (serialization.MAX_NODES + 1, 0)})


@pytest.mark.parametrize("binding", ("dict", "map", "_key_type_repeat"))
def test_shape_fake_type_metadata_and_namespace_descriptors_are_never_resolved(monkeypatch, binding):
    calls = []
    class ForbiddenMetadata:
        def __get__(self, instance, owner):
            calls.append("descriptor")
            raise AssertionError("UNTRUSTED_TYPE_METADATA_DESCRIPTOR_CALLED")
        def __eq__(self, other):
            calls.append("equality")
            raise AssertionError("UNTRUSTED_TYPE_METADATA_EQUALITY_CALLED")
    class FakeType:
        __module__ = ForbiddenMetadata()
        __name__ = ForbiddenMetadata()
        __dict__ = ForbiddenMetadata()
        __new__ = ForbiddenMetadata()
    monkeypatch.setattr(serialization, binding, FakeType, raising=False)
    if binding == "dict":
        # Original binding semantics: a builtin dict is not this fake type.
        assert serialization._shape({1: 2}) == 1
    else:
        with pytest.raises(ValueError, match="SHADOW_STORAGE_STRING_KEY_REQUIRED"):
            serialization._shape({1: 2})
    assert calls == []


@pytest.mark.parametrize("binding", ("map", "_key_type_repeat"))
def test_shape_borrowed_builtin_constructor_owner_cannot_authorize_fake_type(monkeypatch, binding):
    calls = []
    class ForbiddenMetadata:
        def __get__(self, instance, owner):
            calls.append("descriptor")
            raise AssertionError("BORROWED_CONSTRUCTOR_METADATA_DESCRIPTOR_CALLED")
        def __eq__(self, other):
            calls.append("equality")
            raise AssertionError("BORROWED_CONSTRUCTOR_METADATA_EQUALITY_CALLED")
    class FakeType(dict):
        __module__ = ForbiddenMetadata()
        __name__ = ForbiddenMetadata()
        __dict__ = ForbiddenMetadata()
    # A real C class method can be bound to this subclass. Its owner alone
    # is insufficient; the original C constructor name must also match.
    FakeType.__new__ = dict.__dict__["fromkeys"].__get__(None, FakeType)
    monkeypatch.setattr(serialization, binding, FakeType, raising=False)
    with pytest.raises(ValueError, match="SHADOW_STORAGE_STRING_KEY_REQUIRED"):
        serialization._shape({1: 2})
    assert calls == []
