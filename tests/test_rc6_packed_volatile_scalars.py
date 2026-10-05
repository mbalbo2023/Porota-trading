"""Exact volatile literal bytes against the pre-cache c42bfdba producer.

The reference append/capture methods are frozen from Git blob
f2d2c238c64b93a3a414d3427dbe9464e7812ffb. They do not call the new
binding helper. Structure counts are not publisher CPU/RSS measurements.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from rc6_shadow_runtime import packed_storage as packed, serialization


CLOCK = "2026-10-05T16:00:00.000001+00:00"
LIMITS = dict(durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)


class PriorBindingsBuilder(packed._CaptureBuilder):
    def _append(self, value, name, buffer, *, root=False):
        container = isinstance(value, (dict, list, tuple))
        short_raw = None
        if container and not root and (
                self.incoming.get(id(value), 0) >= 2 or name in packed._FIELDS):
            cached = self.cache.get(id(value))
            fresh = cached is None or cached[0] is not value
            if fresh:
                capture = self._named(value)
                self.cache[id(value)] = value, capture
            else:
                capture = cached[1]
            if len(capture.template) >= 64:
                buffer.boundary(capture); return
            if fresh and not capture.literals:
                short_raw = capture.template
        if container and packed._small_plain(value):
            raw = short_raw if short_raw is not None else packed._canonical(value)
            # With no bindings and the whole value fitting, every original
            # token also fits. Append once without moving any legacy cut.
            if len(raw) <= packed.PACK_TARGET-len(buffer.template):
                buffer.append(raw); return
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


def payload(case):
    typed = [None, False, 0, -0.0, 0.0, "Ñ/\0", CLOCK]
    rows = [{"source_at": CLOCK, "received_at": CLOCK, "known_at": None,
             "elapsed_seconds": -0.0, "positive_seconds": 0.0, "flag_at": False,
             "count_at": 0, "literal_at": "\\u0000", "value": index}
            for index in range(12)]
    if case == "typed":
        return {"rows": rows, "typed": typed}
    if case == "aliases":
        shared = {"source_at": CLOCK, "received_at": CLOCK, "typed": typed}
        return {"rows": [shared, shared, deepcopy(shared)],
                "pipeline": {"as_of": CLOCK, "known_at": None, "values": rows}}
    if case == "large":
        return {"rows": rows, "payload": "Ñ"*300, "large_at": 1 << 4096,
                "value_at": {"nested": typed}}
    if case == "special_keys":
        return {"á_at": CLOCK, "clock1_at": None, "clock1_seconds": -0.0,
                "last_as_of": CLOCK, "cross_payload_hashes": {"report": "old"},
                "evidence_retention": {"used": 12}, "rows": rows}
    raise AssertionError(case)


@pytest.mark.parametrize("target", [5, 17, 63, 64, 65, 65536])
@pytest.mark.parametrize("case", ["typed", "aliases", "large", "special_keys"])
def test_private_binding_cache_preserves_prior_cuts_slots_entire_wire_and_full_decode(monkeypatch, target, case):
    monkeypatch.setattr(packed, "PACK_TARGET", target)
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    value = payload(case)
    actual = packed._CaptureBuilder(value).capture(value)
    expected = PriorBindingsBuilder(value).capture(value)
    assert actual == expected
    wire = packed.encode_packed_storage(value, **LIMITS)
    with monkeypatch.context() as previous:
        previous.setattr(packed, "_CaptureBuilder", PriorBindingsBuilder)
        prior_wire = packed.encode_packed_storage(value, **LIMITS)
    raw = canonical(value)
    assert canonical(wire) == canonical(prior_wire)
    assert wire["logical_sha256"] == hashlib.sha256(raw).hexdigest()
    assert wire["logical_bytes"] == len(raw)
    for sharing in (False, True):
        assert canonical(serialization.decode_storage(wire, share_subtrees=sharing, **LIMITS)) == raw


@pytest.mark.parametrize("name", ["as_of", "payload", "elapsed_seconds"])
@pytest.mark.parametrize("value", [None, False, 0, -0.0, 0.0, CLOCK, "Ñ/\0",
                                   {"typed": [False, 0, -0.0, 0.0]}])
def test_volatile_root_keeps_exact_single_literal_and_no_context_between_calls(name, value):
    builder = packed._CaptureBuilder(value)
    prior = PriorBindingsBuilder(value)
    for _ in range(2):
        actual = builder.capture(value, name)
        assert actual == prior.capture(value, name)
        assert actual == (packed.Capture(b"\0"*5, (canonical(value),)),)
        assert builder._binding_scalars is None
        assert builder._binding_bytes == 0


def test_private_binding_reuse_removes_only_repeat_canonical_dispatch_within_one_capture(monkeypatch):
    value = {"rows": [{"source_at": CLOCK, "received_at": CLOCK, "known_at": None,
                       "negative_seconds": -0.0, "positive_seconds": 0.0,
                       "flag_at": False, "count_at": 0} for _ in range(40)]}
    original, calls = packed._canonical, []
    def tracked(member, **kwargs):
        if type(member) in (type(None), bool, int, float) or member is CLOCK:
            calls.append((type(member), member.hex() if type(member) is float else member))
        return original(member, **kwargs)
    monkeypatch.setattr(packed, "_canonical", tracked)
    current = packed._CaptureBuilder(value)
    actual = current.capture(value)
    assert len(calls) == 6
    first = list(calls)
    assert len(set(first)) == 6
    assert current._binding_scalars is None and current._binding_bytes == 0
    calls.clear()
    assert current.capture(value) == actual
    assert calls == first  # No retained cache from the earlier capture call.
    calls.clear()
    assert PriorBindingsBuilder(value).capture(value) == actual
    assert len(calls) == 40*7
    # Dispatch count proves structure only, not a CPU/RSS improvement.


@pytest.mark.parametrize("entries,byte_budget", [(0, 1024), (1, 1024), (16, 0), (16, 6), (16, 12)])
def test_private_cache_budget_exhaustion_only_falls_back_and_keeps_all_literals(monkeypatch, entries, byte_budget):
    monkeypatch.setattr(packed, "_BINDING_SCALAR_ENTRIES", entries)
    monkeypatch.setattr(packed, "_BINDING_SCALAR_BYTES", byte_budget)
    value = {"rows": [{"a_at": text, "b_at": text, "known_at": None}
                       for text in ("a", "bb", "ccc", "a", "bb", "ccc")]}
    observations = []
    class ObservedBuilder(packed._CaptureBuilder):
        def _binding_scalar(self, member):
            raw = super()._binding_scalar(member)
            cache = self._binding_scalars
            observations.append((len(cache), self._binding_bytes, sum(map(len, cache.values()))))
            return raw
    builder = ObservedBuilder(value)
    actual = builder.capture(value)
    assert actual == PriorBindingsBuilder(value).capture(value)
    assert observations
    assert all(count <= entries and size == actual_size and size <= byte_budget
               for count, size, actual_size in observations)
    assert builder._binding_scalars is None and builder._binding_bytes == 0
    assert sum(len(row.literals) for row in actual) == 6*3


class Text(str):
    def __hash__(self):
        raise AssertionError("scalar subclass must not enter the cache")


class Integer(int):
    def __hash__(self):
        raise AssertionError("scalar subclass must not enter the cache")


class Floating(float):
    def __hash__(self):
        raise AssertionError("scalar subclass must not enter the cache")


class MutableDefaultString:
    text = "first synthetic value"
    def __str__(self):
        return self.text


@pytest.mark.parametrize("value", [Text(CLOCK), Integer(0), Floating(-0.0), "Ñ"*257,
                                   1 << 4096, {"nested_at": CLOCK}, [False, 0, -0.0, 0.0]])
def test_noneligible_binding_values_keep_fresh_original_canonical_fallback(monkeypatch, value):
    source = {"a_at": value, "b_at": value}
    original, calls = packed._canonical, []
    def tracked(member, **kwargs):
        if member is value:
            calls.append(member)
        return original(member, **kwargs)
    monkeypatch.setattr(packed, "_canonical", tracked)
    actual = packed._CaptureBuilder(source).capture(source)
    assert len(calls) == 2
    assert actual == PriorBindingsBuilder(source).capture(source)
    assert b"".join(packed._expanded(row, expansion_limit=LIMITS["expansion_limit"]) for row in actual) == canonical(source)


def test_default_string_and_mutable_nested_bindings_are_not_reused_across_capture_calls():
    item = MutableDefaultString()
    nested = {"counter": 1}
    source = {"a_at": item, "payload": nested}
    current, prior = packed._CaptureBuilder(source), PriorBindingsBuilder(source)
    before = current.capture(source)
    assert before == prior.capture(source)
    item.text = "second synthetic value"
    nested["counter"] = 2
    after = current.capture(source)
    assert after == prior.capture(source)
    assert after != before
    assert current._binding_scalars is None and current._binding_bytes == 0


def test_caller_named_cache_and_plain_scalar_cache_cannot_supply_binding_bytes():
    shared = {"known_at": None}
    value = {"rows": [shared, shared], "source_at": CLOCK, "received_at": CLOCK}
    supplied = {id(shared): (shared, packed.Capture(b'"fake-short-cache"', ())),
                (str, CLOCK): b'"fake-clock"', (type(None), None): b'"fake-null"'}
    actual, prior = packed._CaptureBuilder(value, cache=dict(supplied)), PriorBindingsBuilder(value, cache=dict(supplied))
    for builder in (actual, prior):
        builder.scalars[(str, CLOCK)] = b'"poisoned-plain-clock"'
        builder.scalars[(type(None), None)] = b'"poisoned-plain-null"'
    result = actual.capture(value)
    assert result == prior.capture(value)
    assert b"".join(packed._expanded(row, expansion_limit=LIMITS["expansion_limit"]) for row in result) == canonical(value)
    assert supplied[(str, CLOCK)] == b'"fake-clock"'
    assert actual._binding_scalars is None and actual._binding_bytes == 0


@pytest.mark.parametrize("maximum", [0, 1, 2, 3])
def test_private_reuse_cannot_coalesce_or_remove_literal_slots_under_max_bindings(monkeypatch, maximum):
    monkeypatch.setattr(packed, "MAX_BINDINGS", maximum)
    value = {"a_at": CLOCK, "b_at": CLOCK, "c_at": None}
    current, prior = packed._CaptureBuilder(value), PriorBindingsBuilder(value)
    if maximum < 3:
        for builder in (current, prior):
            with pytest.raises(ValueError, match="^SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED$"):
                builder.capture(value)
    else:
        result = current.capture(value)
        assert result == prior.capture(value)
        assert result[0].literals == (canonical(CLOCK), canonical(CLOCK), b"null")
        positions, count = packed._slots(result[0].template)
        assert count == len(positions) == 3
        assert [index for _, index in positions] == [0, 1, 2]
    assert current._binding_scalars is None and current._binding_bytes == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_binding_failure_retains_prior_rejection_and_discards_context(value):
    source = {"a_at": CLOCK, "z_at": value}
    current, prior = packed._CaptureBuilder(source), PriorBindingsBuilder(source)
    for builder in (current, prior):
        with pytest.raises(ValueError):
            builder.capture(source)
    assert current._binding_scalars is None and current._binding_bytes == 0
    for builder_class in (packed._CaptureBuilder, PriorBindingsBuilder):
        with pytest.raises(ValueError):
            builder_class(value).capture(value, "as_of")


class CaptureFault:
    def __str__(self):
        raise RuntimeError("synthetic_capture_fault")


def test_failed_default_string_discards_private_context_and_preserves_the_original_exception():
    source = {"a_at": CLOCK, "payload": CaptureFault()}
    current, prior = packed._CaptureBuilder(source), PriorBindingsBuilder(source)
    for builder in (current, prior):
        with pytest.raises(RuntimeError, match="^synthetic_capture_fault$"):
            builder.capture(source)
    assert current._binding_scalars is None and current._binding_bytes == 0
    source["payload"] = "recovered synthetic value"
    assert current.capture(source) == prior.capture(source)
    assert current._binding_scalars is None and current._binding_bytes == 0


@pytest.mark.parametrize("inner_fault", [False, True])
def test_reentrant_capture_restores_outer_context_after_inner_success_or_failure(inner_fault):
    def run(builder_class):
        class ReentrantString:
            def __str__(self):
                outer_cache, outer_bytes = builder._binding_scalars, builder._binding_bytes
                inner = {"inner_at": CLOCK, "payload": CaptureFault() if inner_fault else "nested synthetic value"}
                if inner_fault:
                    with pytest.raises(RuntimeError, match="^synthetic_capture_fault$"):
                        builder.capture(inner)
                else:
                    self.inner = builder.capture(inner)
                assert builder._binding_scalars is outer_cache
                assert builder._binding_bytes == outer_bytes
                return "reentrant synthetic value"
        text = ReentrantString()
        value = {"a_at": CLOCK, "payload": text, "z_at": CLOCK}
        builder = builder_class(value)
        result = builder.capture(value)
        assert builder._binding_scalars is None and builder._binding_bytes == 0
        return result, getattr(text, "inner", None)
    assert run(packed._CaptureBuilder) == run(PriorBindingsBuilder)


def test_prepared_sections_and_mutable_headers_preserve_prior_full_bytes_after_external_mutation(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    value = {**payload("typed"), "as_of": CLOCK, "status": "OBSERVING",
             "cross_payload_hashes": {"report": "old"}}
    frozen = deepcopy(value)
    actual = packed.PreparedPackedStorage(value, mutable={"status", "cross_payload_hashes"}, **LIMITS)
    with monkeypatch.context() as old:
        old.setattr(packed, "_CaptureBuilder", PriorBindingsBuilder)
        prior = packed.PreparedPackedStorage(frozen, mutable={"status", "cross_payload_hashes"}, **LIMITS)
    value["rows"][0]["known_at"] = "external mutation"
    value["rows"].append({"source_at": "external mutation"})
    for state in ("OBSERVING", "RETENTION_PRESSURE"):
        headers = dict(status=state, cross_payload_hashes={"report": state})
        value.update(headers); frozen.update(headers)
        wire, logical = actual.encode(value)
        with monkeypatch.context() as old:
            old.setattr(packed, "_CaptureBuilder", PriorBindingsBuilder)
            prior_wire, prior_logical = prior.encode(frozen)
        assert canonical(wire) == canonical(prior_wire)
        assert logical == prior_logical == hashlib.sha256(canonical(frozen)).hexdigest()
        assert actual.metrics(value) == (logical, len(canonical(frozen)))
        assert canonical(serialization.decode_storage(wire, **LIMITS)) == canonical(frozen)
