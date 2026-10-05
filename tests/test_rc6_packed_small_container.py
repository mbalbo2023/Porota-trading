"""Whole-byte differential against the append loop from product 4ba47b87.

The reference _append is frozen from Git blob
42e92789d93cf5bbcb6fcf342ae8de5fd0fc02fb. The new shortcut may combine
plain tokens only when the complete value fits the existing pending chunk.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from rc6_shadow_runtime import packed_storage as packed, serialization
from tests.test_rc6_packed_capture_allocations import LegacyCaptureBuilder, count_graph


LIMITS = dict(durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)


class PriorAppendBuilder(LegacyCaptureBuilder):
    def _append(self, value, name, buffer, *, root=False):
        if isinstance(value, (dict, list, tuple)) and not root and (
                self.incoming.get(id(value), 0) >= 2 or name in packed._FIELDS):
            cached = self.cache.get(id(value))
            if cached is None or cached[0] is not value:
                capture = self._named(value)
                self.cache[id(value)] = value, capture
            else:
                capture = cached[1]
            if len(capture.template) >= 64:
                buffer.boundary(capture); return
        if isinstance(value, dict):
            buffer.append(b"{")
            for ordinal, key in enumerate(sorted(value)):
                buffer.append((b"," if ordinal else b"")+self._scalar(key)+b":")
                child = value[key]
                if packed._root_volatile(key):
                    buffer.bind(packed._canonical(child))
                else:
                    self._append(child, key, buffer)
            buffer.append(b"}")
        elif isinstance(value, (list, tuple)):
            buffer.append(b"[")
            for ordinal, child in enumerate(value):
                if ordinal:
                    buffer.append(b",")
                self._append(child, name, buffer)
            buffer.append(b"]")
        else:
            buffer.append(self._scalar(value))

    def capture(self, value, name=""):
        if packed._root_volatile(name):
            return (packed.Capture(b"\0"+b"\0"*4, (packed._canonical(value),)),)
        buffer = packed._CaptureBuffer()
        self._append(value, name, buffer, root=True)
        buffer.flush()
        return tuple(buffer.result)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      default=str, allow_nan=False).encode()


def append_after_prefix(builder_class, value, *, bindings=False):
    builder = builder_class(value)
    buffer = packed._CaptureBuffer()
    if bindings:
        buffer.append(b'{"as_of":')
        buffer.bind(canonical("2026-10-05T16:00:00.000001+00:00"))
        buffer.append(b',"rows":')
    else:
        buffer.append(b"[0,")
    before = bytes(buffer.template)
    builder._append(value, "rows", buffer, root=True)
    buffer.append(b"}" if bindings else b"]")
    buffer.flush()
    return tuple(buffer.result), before


def value_for(case):
    if case == "dict":
        return {"delta": -0.0, "positive": 0.0, "flag": False, "quantity": 0, "note": "Ñ/~\0"}
    if case == "list":
        return [False, 0, -0.0, 0.0, None, "observación/~", "\0"]
    if case == "tuple":
        return tuple(value_for("list"))
    if case == "empty_dict":
        return {}
    if case == "empty_list":
        return []
    raise AssertionError(case)


@pytest.mark.parametrize("case", ["dict", "list", "tuple", "empty_dict", "empty_list"])
@pytest.mark.parametrize("delta", [-1, 0, 1])
@pytest.mark.parametrize("bindings", [False, True])
def test_whole_plain_block_keeps_prior_cuts_at_exact_remaining_capacity(monkeypatch, case, delta, bindings):
    value = value_for(case)
    prefix = b'{"as_of":'+b"\0"*5+b',"rows":' if bindings else b"[0,"
    monkeypatch.setattr(packed, "PACK_TARGET", len(prefix)+len(canonical(value))+delta)
    expected, prior_prefix = append_after_prefix(PriorAppendBuilder, value, bindings=bindings)
    actual, current_prefix = append_after_prefix(packed._CaptureBuilder, value, bindings=bindings)
    assert current_prefix == prior_prefix == prefix
    assert actual == expected
    expanded = b"".join(packed._expanded(row, expansion_limit=LIMITS["expansion_limit"]) for row in actual)
    expected_raw = (b'{"as_of":'+canonical("2026-10-05T16:00:00.000001+00:00")+b',"rows":'+canonical(value)+b"}"
                    if bindings else b"[0,"+canonical(value)+b"]")
    assert expanded == expected_raw


def test_fitting_plain_scalar_block_removes_only_internal_append_dispatches(monkeypatch):
    value = value_for("dict")
    monkeypatch.setattr(packed, "PACK_TARGET", 65536)
    prior, current = [], []

    class Buffer(packed._CaptureBuffer):
        def __init__(self, calls):
            super().__init__()
            self.calls = calls

        def append(self, raw):
            self.calls.append(raw)
            return super().append(raw)

    old_buffer, new_buffer = Buffer(prior), Buffer(current)
    PriorAppendBuilder(value)._append(value, "rows", old_buffer, root=True)
    packed._CaptureBuilder(value)._append(value, "rows", new_buffer, root=True)
    old_buffer.flush(); new_buffer.flush()
    assert tuple(new_buffer.result) == tuple(old_buffer.result)
    assert current == [canonical(value)]
    assert len(prior) > len(current)
    # This verifies dispatch structure only; it is not a CPU or RSS measure.


@pytest.mark.parametrize("value", [value_for("dict"), value_for("list"), (), {}])
def test_preexisting_oversized_token_keeps_prior_soft_target_flush(monkeypatch, value):
    monkeypatch.setattr(packed, "PACK_TARGET", 5)
    prefix = canonical("an earlier single token already exceeds the soft target")
    results = []
    for builder_class in (PriorAppendBuilder, packed._CaptureBuilder):
        buffer = packed._CaptureBuffer()
        buffer.append(prefix)
        assert len(buffer.template) > packed.PACK_TARGET
        builder_class(value)._append(value, "rows", buffer, root=True)
        buffer.flush()
        results.append(tuple(buffer.result))
    assert results[0] == results[1]


@pytest.mark.parametrize("value", [list(range(16)), ["\U0001f9ed"*256], {"k"*256: False}, [(1 << 4096)-1]])
def test_private_shortcut_bounds_still_preserve_prior_wire_at_their_last_eligible_value(monkeypatch, value):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    data = {"rows": value}
    actual = packed.encode_packed_storage(data, **LIMITS)
    with monkeypatch.context() as previous:
        previous.setattr(packed, "_CaptureBuilder", PriorAppendBuilder)
        expected = packed.encode_packed_storage(data, **LIMITS)
    assert canonical(actual) == canonical(expected)
    assert canonical(serialization.decode_storage(actual, **LIMITS)) == canonical(data)


class Dictionary(dict):
    pass


class Array(list):
    pass


class FixedArray(tuple):
    pass


class Text(str):
    pass


class Integer(int):
    pass


class Floating(float):
    pass


class DefaultString:
    def __str__(self):
        return "default-str/observación"


@pytest.mark.parametrize("value", [Dictionary(a=1), Array([1, False]), FixedArray((1, False)),
    {"value": Text("observación")}, [Integer(7)], [Floating(-0.0)], [DefaultString()],
    {"source_at": None}, {"payload": "native"}, {"cross_payload_hashes": "none"},
    list(range(17)), {"k"*257: 1}, ["Ñ"*257], [1 << 4096], {"nested": {"source_at": None}}])
def test_subclasses_bindings_nonjson_and_larger_values_keep_original_walk(monkeypatch, value):
    original, root_encodings = packed._canonical, []
    def tracked(member, **kwargs):
        if member is value:
            root_encodings.append(member)
        return original(member, **kwargs)
    monkeypatch.setattr(packed, "_canonical", tracked)
    actual = packed._CaptureBuilder(value).capture(value)
    assert root_encodings == []
    expected = PriorAppendBuilder(value).capture(value)
    assert actual == expected
    assert b"".join(packed._expanded(row, expansion_limit=LIMITS["expansion_limit"]) for row in actual) == canonical(value)


@pytest.mark.parametrize("target", [1, 5, 17, 63, 64, 65, 65536])
@pytest.mark.parametrize("case", ["dag", "subclasses", "default_str"])
def test_fast_plain_append_preserves_alias_fields_full_envelope_and_public_decode(monkeypatch, target, case):
    monkeypatch.setattr(packed, "PACK_TARGET", target)
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    value, _, _, _ = count_graph(case, [])
    assert packed._CaptureBuilder(value).capture(value) == PriorAppendBuilder(value).capture(value)
    actual = packed.encode_packed_storage(value, **LIMITS)
    with monkeypatch.context() as previous:
        previous.setattr(packed, "_CaptureBuilder", PriorAppendBuilder)
        expected = packed.encode_packed_storage(value, **LIMITS)
    assert canonical(actual) == canonical(expected)
    raw = canonical(value)
    assert actual["logical_sha256"] == hashlib.sha256(raw).hexdigest()
    assert actual["logical_bytes"] == len(raw)
    assert canonical(serialization.decode_storage(actual, **LIMITS)) == raw


def test_fresh_short_named_plain_capture_is_reused_only_after_the_strict_filter(monkeypatch):
    value = {"signal": False, "count": 0, "delta": -0.0}
    original, calls = packed._canonical, []
    def tracked(member, **kwargs):
        if member is value:
            calls.append(member)
        return original(member, **kwargs)
    monkeypatch.setattr(packed, "_canonical", tracked)
    actual = packed._CaptureBuilder(value).capture(value, "stages")
    assert len(canonical(value)) < 64
    assert len(calls) == 1
    assert actual == PriorAppendBuilder(value).capture(value, "stages")


def test_caller_cached_short_capture_cannot_supply_new_plain_bytes_or_hide_mutations():
    shared = [False, 0, -0.0, 0.0, "Ñ"]
    value = {"rows": [shared, shared]}
    cache = {id(shared): (shared, packed.Capture(b'"untrusted-short-cache"', ()))}
    actual_builder = packed._CaptureBuilder(value, cache=dict(cache))
    expected_builder = PriorAppendBuilder(value, cache=dict(cache))
    assert actual_builder.capture(value) == expected_builder.capture(value)
    assert b"".join(row.template for row in actual_builder.capture(value)) == canonical(value)
    shared[0] = True
    assert actual_builder.capture(value) == expected_builder.capture(value)
    assert b"".join(row.template for row in actual_builder.capture(value)) == canonical(value)


@pytest.mark.parametrize("maximum", [0, 1, 2])
def test_fitting_plain_child_preserves_existing_pending_literal_capacity_guard(monkeypatch, maximum):
    monkeypatch.setattr(packed, "MAX_BINDINGS", maximum)
    value = {"a_at": None, "b_at": False, "rows": value_for("dict")}
    if maximum < 2:
        for builder in (PriorAppendBuilder, packed._CaptureBuilder):
            with pytest.raises(ValueError, match="^SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED$"):
                builder(value).capture(value)
    else:
        assert packed._CaptureBuilder(value).capture(value) == PriorAppendBuilder(value).capture(value)


def test_plain_static_sections_freeze_and_mutable_roots_match_prior_entire_wire(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    value = {"rows": [value_for("dict"), value_for("list")], "status": "OBSERVING",
             "cross_payload_hashes": {"report": "old"}}
    original = deepcopy(value)
    current = packed.PreparedPackedStorage(value, mutable={"status", "cross_payload_hashes"}, **LIMITS)
    with monkeypatch.context() as previous:
        previous.setattr(packed, "_CaptureBuilder", PriorAppendBuilder)
        expected = packed.PreparedPackedStorage(original, mutable={"status", "cross_payload_hashes"}, **LIMITS)
    value["rows"][0]["delta"] = 4.0
    value["rows"][1].append("external mutation")
    for status in ("OBSERVING", "RETENTION_PRESSURE"):
        value.update(status=status, cross_payload_hashes={"report": status})
        original.update(status=status, cross_payload_hashes={"report": status})
        wire, logical = current.encode(value)
        with monkeypatch.context() as previous:
            previous.setattr(packed, "_CaptureBuilder", PriorAppendBuilder)
            prior_wire, prior_logical = expected.encode(original)
        assert canonical(wire) == canonical(prior_wire)
        assert logical == prior_logical == hashlib.sha256(canonical(original)).hexdigest()
        assert canonical(serialization.decode_storage(wire, **LIMITS)) == canonical(original)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_canonical_plain_block_preserves_public_nonfinite_rejection(monkeypatch, value):
    for builder in (PriorAppendBuilder, packed._CaptureBuilder):
        with monkeypatch.context() as implementation:
            implementation.setattr(packed, "_CaptureBuilder", builder)
            with pytest.raises(ValueError):
                packed.PreparedPackedStorage({"rows": [value]}, **LIMITS)
