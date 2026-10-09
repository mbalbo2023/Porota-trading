"""Real native suffix dispatch and full wire compatibility; unit controls only.

These controls prove calls and behavior, not CPU savings, native kernel FIN,
resource qualification, source custody, images, runtime or material BIG PASS.
The material producer retains its original 12000/60000 and resource budgets.
"""
from copy import deepcopy
import hashlib
import json
import math
import sys

import pytest

from rc6_shadow_runtime import packed_storage as packed, serialization


LIMITS = dict(durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)


def _legacy_root_volatile(name):
    return (name.endswith("_at") or name.endswith("_seconds") or name in
        {"as_of", "last_as_of", "sequence", "generation_id", "cross_payload_hashes", "report_digest",
         "checkpoint_digest", "evidence_retention", "logical_sha256", "storage_sha256", "sha256", "payload"})


@pytest.mark.parametrize("name,expected", [
    ("clock_at", True), ("duration_seconds", True), ("_at", True),
    ("_seconds", True), ("", False), ("identity", False),
    ("clock_atx", False), ("duration_seconds_more", False),
    ("CLOCK_AT", False), ("évidence_at", True), ("x\0_seconds", True),
])
def test_native_suffix_has_literal_expected_classification(name, expected):
    assert packed._root_volatile(name) is expected


def test_all_original_exact_member_names_keep_their_classification():
    members = ("as_of", "last_as_of", "sequence", "generation_id",
        "cross_payload_hashes", "report_digest", "checkpoint_digest",
        "evidence_retention", "logical_sha256", "storage_sha256", "sha256", "payload")
    for name in members:
        assert packed._root_volatile(name) is True
        assert packed._root_volatile(name + "x") is False


def _native_endswith_calls(function, name):
    calls = []
    previous = sys.getprofile()
    def record(frame, event, argument):
        if (event == "c_call" and getattr(argument, "__self__", None) is name
                and getattr(argument, "__name__", None) == "endswith"):
            calls.append(argument)
    try:
        sys.setprofile(record)
        result = function(name)
    finally:
        sys.setprofile(previous)
    assert sys.getprofile() is previous
    return result, len(calls)


@pytest.mark.parametrize("name,expected,old_calls", [
    ("identity", False, 2), ("duration_seconds", True, 2), ("clock_at", True, 1),
])
def test_actual_builtin_endswith_dispatch_occurs_once_without_inferring_cpu_savings(name, expected, old_calls):
    assert packed._ROOT_VOLATILE_NATIVE_TYPE is ().__class__.__class__
    assert packed._ROOT_VOLATILE_NATIVE_STR is "".__class__
    old_result, observed_old = _native_endswith_calls(_legacy_root_volatile, name)
    result, observed_new = _native_endswith_calls(packed._root_volatile, name)
    assert old_result is expected and result is expected
    assert observed_old == old_calls and observed_new == 1
    # The intrinsic type classifier has its own cost. This only counts actual
    # endswith methods, not total C calls, CPU percentages or timing savings.


@pytest.mark.parametrize("name,expected,suffixes", [
    ("clock_at", True, ["_at"]),
    ("duration_seconds", True, ["_at", "_seconds"]),
    ("identity", False, ["_at", "_seconds"]),
])
def test_string_subclass_uses_original_arguments_shortcircuit_and_real_caller(name, expected, suffixes):
    calls = []
    class Name(str):
        def endswith(self, suffix):
            calls.append(suffix)
            assert sys._getframe(1).f_code is packed._root_volatile.__code__
            return str.endswith(self, suffix)
    assert packed._root_volatile(Name(name)) is expected
    assert calls == suffixes


@pytest.mark.parametrize("stop_at", ["_at", "_seconds"])
def test_unknown_truthy_callback_result_and_bool_order_are_preserved(stop_at):
    calls = []
    class Token:
        def __bool__(self):
            calls.append("bool")
            return True
    token = Token()
    class Name:
        def endswith(self, suffix):
            calls.append(suffix)
            return token if suffix == stop_at else False
    value = Name()
    assert _legacy_root_volatile(value) is token
    baseline = list(calls)
    calls.clear()
    assert packed._root_volatile(value) is token
    assert calls == baseline == (["_at", "bool"] if stop_at == "_at"
                                  else ["_at", "_seconds", "bool"])


def test_unknown_callback_exception_is_the_original_instance_with_original_argument():
    original_error, calls = ValueError("unit-original-suffix-error"), []
    class Name:
        def endswith(self, suffix):
            calls.append(suffix)
            raise original_error
    with pytest.raises(ValueError) as baseline:
        _legacy_root_volatile(Name())
    with pytest.raises(ValueError) as actual:
        packed._root_volatile(Name())
    assert actual.value is baseline.value is original_error
    assert calls == ["_at", "_at"]


def test_unknown_hash_membership_and_suffix_call_order_are_unchanged():
    calls = []
    class Name:
        def endswith(self, suffix):
            calls.append(("endswith", suffix))
            return False
        def __hash__(self):
            calls.append(("hash",))
            return hash("sequence")
        def __eq__(self, other):
            calls.append(("eq", other))
            return other == "sequence"
    value = Name()
    assert _legacy_root_volatile(value) is True
    baseline = list(calls)
    calls.clear()
    assert packed._root_volatile(value) is True
    assert calls == baseline
    assert calls[:2] == [("endswith", "_at"), ("endswith", "_seconds")]
    assert ("hash",) in calls and ("eq", "sequence") in calls


def test_intrinsic_type_identity_does_not_consult_fake_class_properties_or_meta_equality():
    calls = []
    class HostileMeta(type):
        @property
        def __name__(cls):
            raise AssertionError("unit-class-metadata-must-not-be-read")
        def __eq__(cls, other):
            raise AssertionError("unit-class-equality-must-not-be-called")
    class Name(metaclass=HostileMeta):
        @property
        def __class__(self):
            raise AssertionError("unit-fake-class-property-must-not-be-read")
        def endswith(self, suffix):
            calls.append(suffix)
            return False
    value = Name()
    assert _legacy_root_volatile(value) is False
    calls.clear()
    assert packed._root_volatile(value) is False
    assert calls == ["_at", "_seconds"]


@pytest.mark.parametrize("global_name", ["type", "str", "isinstance"])
def test_dynamic_module_bindings_do_not_authenticate_fake_native_strings(monkeypatch, global_name):
    def forbidden(*args, **kwargs):
        raise AssertionError("unit-rebound-global-must-not-authenticate")
    monkeypatch.setattr(packed, global_name, forbidden, raising=False)
    assert packed._root_volatile("clock_at") is True
    assert packed._root_volatile("identity") is False
    class Subclass(str):
        def endswith(self, suffix):
            assert isinstance(suffix, str)
            return suffix == "_seconds"
    assert packed._root_volatile(Subclass("identity")) is True


@pytest.mark.parametrize("name", [None, 7, b"identity"])
def test_unknown_native_nonstring_error_type_and_arguments_match_original(name):
    with pytest.raises((AttributeError, TypeError)) as baseline:
        _legacy_root_volatile(name)
    with pytest.raises(type(baseline.value)) as actual:
        packed._root_volatile(name)
    assert actual.value.args == baseline.value.args


def _body():
    common = {"identity": ["ÑANDÚ", "BYMA"], "clock_at": None,
        "duration_seconds": 0.5, "plain": [False, 0, -0.0, "\0"],
        "padding": "observación/~" * 20,
        "rank_components": {"book_at": None, "ratio": 1.25}}
    return {"schema": "unit.suffix.full-wire.v1", "rows": [common] * 1500,
        "flat": list(range(20000)), "status": "OBSERVING",
        "cross_payload_hashes": {"report": "first"}}


def test_original_and_native_suffix_have_exact_full_wire_cuts_bindings_digests_and_independent_objects(monkeypatch):
    body = _body()
    before = deepcopy(body)
    expected = json.dumps(body, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode()
    assert len(expected) > serialization.THRESHOLD
    with monkeypatch.context() as patch:
        patch.setattr(packed, "_root_volatile", _legacy_root_volatile)
        baseline = packed.PreparedPackedStorage(body, mutable={"status", "cross_payload_hashes"}, **LIMITS)
        baseline_wire, baseline_digest = baseline.encode(body)
    prepared = packed.PreparedPackedStorage(body, mutable={"status", "cross_payload_hashes"}, **LIMITS)
    wire, digest = prepared.encode(body)
    assert prepared.sections == baseline.sections
    assert wire == baseline_wire and list(wire) == list(baseline_wire)
    assert digest == baseline_digest == hashlib.sha256(expected).hexdigest()
    assert wire["schema"] == serialization.PACKED_SCHEMA
    assert wire["logical_bytes"] == len(expected)
    assert packed.unpack_wire(wire, retain=True, **LIMITS) == expected
    # The large flat list crosses actual legacy cuts, rather than relying only
    # on a trivial below-threshold passthrough or a changed threshold.
    assert len(prepared.sections["flat"]) >= 2
    restored = serialization.decode_storage(wire, **LIMITS)
    assert restored == body and restored["rows"][0] is not restored["rows"][1]
    assert type(restored["rows"][0]["plain"][0]) is bool
    assert type(restored["rows"][0]["plain"][1]) is int
    assert math.copysign(1.0, restored["rows"][0]["plain"][2]) == -1.0
    restored["rows"][0]["rank_components"]["ratio"] = 9
    assert restored["rows"][1]["rank_components"]["ratio"] == 1.25
    assert serialization.decode_storage(wire, **LIMITS) == body
    old_wire = deepcopy(wire)
    body.update(status="RETENTION_PRESSURE", cross_payload_hashes={"report": "second"})
    updated, updated_digest = prepared.encode(body)
    reference_updated, reference_digest = baseline.encode(body)
    assert updated == reference_updated and updated_digest == reference_digest
    assert serialization.decode_storage(old_wire, **LIMITS)["status"] == "OBSERVING"
    assert {k: v for k, v in body.items() if k not in {"status", "cross_payload_hashes"}} == {
        k: v for k, v in before.items() if k not in {"status", "cross_payload_hashes"}}
    assert body["rows"][0] is body["rows"][1]


def test_default_str_reentrant_capture_trace_and_input_identity_remain_exact(monkeypatch):
    trace = []
    class Scalar:
        def __str__(self):
            trace.append("original_default_str")
            inner = {"clock_at": "x", "plain": -0.0}
            captures = packed._CaptureBuilder(inner).capture(inner)
            assert b"".join(packed._expanded(part, expansion_limit=LIMITS["expansion_limit"])
                             for part in captures) == b'{"clock_at":"x","plain":-0.0}'
            return "observación/~"
    scalar, body = Scalar(), _body()
    body["callback"] = scalar
    before = deepcopy({key: value for key, value in body.items() if key != "callback"})
    with monkeypatch.context() as patch:
        patch.setattr(packed, "_root_volatile", _legacy_root_volatile)
        baseline = packed.PreparedPackedStorage(body, **LIMITS)
        reference_wire, reference_digest = baseline.encode(body)
        reference_trace = list(trace)
    trace.clear()
    prepared = packed.PreparedPackedStorage(body, **LIMITS)
    wire, digest = prepared.encode(body)
    assert trace == reference_trace and reference_trace
    assert prepared.sections == baseline.sections
    assert wire == reference_wire and digest == reference_digest
    assert body["callback"] is scalar and body["rows"][0] is body["rows"][1]
    assert {key: value for key, value in body.items() if key != "callback"} == before


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_volatile_nonfinite_scalar_remains_original_fail_closed(monkeypatch, number):
    body = {"clock_seconds": number}
    with monkeypatch.context() as patch:
        patch.setattr(packed, "_root_volatile", _legacy_root_volatile)
        with pytest.raises(ValueError) as baseline:
            packed.PreparedPackedStorage(body, **LIMITS).encode(body)
    with pytest.raises(type(baseline.value)) as actual:
        packed.PreparedPackedStorage(body, **LIMITS).encode(body)
    assert actual.value.args == baseline.value.args


@pytest.mark.parametrize("limits,reason", [
    ({"durable_limit": 10, "expansion_limit": 1024}, "SHADOW_STORAGE_DURABLE_CAPACITY_REACHED"),
    ({"durable_limit": 1024, "expansion_limit": 10}, "SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED"),
])
def test_original_physical_byte_caps_remain_closed_for_suffix_fields(monkeypatch, limits, reason):
    body = {"clock_at": "x", "identity": "same_original"}
    with monkeypatch.context() as patch:
        patch.setattr(packed, "_root_volatile", _legacy_root_volatile)
        with pytest.raises(ValueError, match=reason) as baseline:
            packed.PreparedPackedStorage(body, **limits).encode(body)
    with pytest.raises(ValueError, match=reason) as actual:
        packed.PreparedPackedStorage(body, **limits).encode(body)
    assert actual.value.args == baseline.value.args
