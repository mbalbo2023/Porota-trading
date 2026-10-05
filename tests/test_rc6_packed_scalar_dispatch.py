"""Exact scalar terminal tokens against the d0507884 append/capture loop.

The independent reference methods are frozen from product Git blob
d55e0164a7c7be6325ad3b12ef7bc2182cdc6bd2. Dispatch counts describe only
call structure; no publisher CPU, RSS or complete BIG90 result is claimed.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from rc6_shadow_runtime import packed_storage as packed, serialization


CLOCK = "2026-10-05T16:00:00.000001+00:00"
LIMITS = dict(durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)


class PriorLeafBuilder(packed._CaptureBuilder):
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
                    buffer.bind(self._binding_scalar(child))
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
        previous = self._binding_scalars, self._binding_bytes
        self._binding_scalars, self._binding_bytes = {}, 0
        try:
            if packed._root_volatile(name):
                return (packed.Capture(b"\0"+b"\0"*4, (self._binding_scalar(value),)),)
            buffer = packed._CaptureBuffer()
            self._append(value, name, buffer, root=True)
            buffer.flush()
            return tuple(buffer.result)
        finally:
            # Reentrant default=str keeps the surrounding capture's private
            # context, while top-level calls leave no cache behind.
            self._binding_scalars, self._binding_bytes = previous


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      default=str, allow_nan=False).encode()


def row():
    leaves = [None, False, 0, -0.0, 0.0, "Ñ/~\0"]
    value = {f"ordinary{i:02}": leaves[i % len(leaves)] for i in range(24)}
    value.update(source_at=CLOCK, received_at=CLOCK,
                 rank_components={"rate": -0.0, "known_at": None, "note": "native bytes"})
    return value


def value_for(case):
    leaves = [None, False, 0, -0.0, 0.0, "Ñ/~\0", CLOCK] * 4
    if case == "rows":
        return {"rows": [row(), row(), row()], "typed": leaves}
    if case == "arrays":
        return {"list": leaves, "tuple": tuple(leaves), "mixed": [row(), leaves, tuple(leaves)]}
    if case == "dag":
        shared = {"source_at": CLOCK, "typed": leaves, "value": "alias native bytes"}
        parent = {"left": shared, "right": shared, "row": row()}
        return {"groups": [parent, parent, deepcopy(parent)], "elsewhere": shared}
    if case == "fields":
        return {"stages": {"small": True}, "basis": {"source_at": CLOCK, "rows": [row()]},
                "pipeline": {"S0": True, "S1": False, "source_at": CLOCK}, "rows": [row()]}
    if case == "special":
        return {"rows": [row()], "á_at": CLOCK, "clock1_seconds": -0.0,
                "cross_payload_hashes": {"report": "prior"}, "evidence_retention": {"used": 1},
                "unicode/~\0": "observación\\u0000", "large": "Ñ" * 257,
                "integer": 1 << 4097}
    raise AssertionError(case)


def traced_capture(monkeypatch, builder, value, name=""):
    operations = []
    base = packed._CaptureBuffer
    class Buffer(base):
        def append(self, raw):
            operations.append(("APPEND", raw))
            return super().append(raw)

        def bind(self, literal):
            operations.append(("BIND", literal))
            return super().bind(literal)

        def boundary(self, capture):
            operations.append(("BOUNDARY", capture))
            return super().boundary(capture)

    with monkeypatch.context() as scoped:
        scoped.setattr(packed, "_CaptureBuffer", Buffer)
        result = builder.capture(value, name)
    return result, operations


@pytest.mark.parametrize("target", [1, 5, 17, 63, 64, 65, 65536])
@pytest.mark.parametrize("case", ["rows", "arrays", "dag", "fields", "special"])
def test_builtin_terminal_tokens_keep_prior_operations_cuts_bindings_entire_wire_and_full_reader(monkeypatch, target, case):
    monkeypatch.setattr(packed, "PACK_TARGET", target)
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    value = value_for(case)
    actual = traced_capture(monkeypatch, packed._CaptureBuilder(value), value)
    prior = traced_capture(monkeypatch, PriorLeafBuilder(value), value)
    assert actual == prior
    wire = packed.encode_packed_storage(value, **LIMITS)
    with monkeypatch.context() as previous:
        previous.setattr(packed, "_CaptureBuilder", PriorLeafBuilder)
        prior_wire = packed.encode_packed_storage(value, **LIMITS)
    raw = canonical(value)
    assert canonical(wire) == canonical(prior_wire)
    assert wire["logical_sha256"] == hashlib.sha256(raw).hexdigest()
    assert wire["logical_bytes"] == len(raw)
    for sharing in (False, True):
        assert canonical(serialization.decode_storage(wire, share_subtrees=sharing, **LIMITS)) == raw


def test_direct_builtin_leaf_dispatch_keeps_the_exact_scalar_call_order(monkeypatch):
    monkeypatch.setattr(packed, "PACK_TARGET", 65536)
    value = value_for("rows")
    def run(base):
        dispatches, scalars = [], []
        class Builder(base):
            def _append(self, member, name, buffer, *, root=False):
                dispatches.append(type(member))
                return super()._append(member, name, buffer, root=root)

            def _scalar(self, member):
                raw = super()._scalar(member)
                scalars.append((type(member), id(member), raw))
                return raw
        result = Builder(value).capture(value)
        return result, scalars, dispatches
    current, current_scalars, current_dispatches = run(packed._CaptureBuilder)
    prior, prior_scalars, prior_dispatches = run(PriorLeafBuilder)
    assert current == prior and current_scalars == prior_scalars
    leaf_types = (type(None), bool, int, float, str)
    assert sum(kind in leaf_types for kind in prior_dispatches) > 0
    assert not any(kind in leaf_types for kind in current_dispatches)
    # Exact call structure only; not a timing or publisher performance result.


@pytest.mark.parametrize("delta", [-1, 0, 1])
@pytest.mark.parametrize("value", [None, False, 0, -0.0, 0.0, "Ñ/~\0", "x" * 257, 1 << 4097])
def test_builtin_leaf_append_keeps_the_exact_remaining_capacity_cut(monkeypatch, delta, value):
    source = row()
    source["ordinary00"] = value
    prefix = b'{"ordinary00":'
    raw = canonical(value)
    monkeypatch.setattr(packed, "PACK_TARGET", len(prefix)+len(raw)+delta)
    current = traced_capture(monkeypatch, packed._CaptureBuilder(source), source)
    prior = traced_capture(monkeypatch, PriorLeafBuilder(source), source)
    assert current == prior
    captures, _ = current
    if delta < 0:
        assert captures[0].template == prefix
    else:
        assert captures[0].template.startswith(prefix+raw)


@pytest.mark.parametrize("kind", ["dict", "list", "tuple"])
def test_container_subclass_iteration_getitem_and_scalar_order_remain_identical(monkeypatch, kind):
    monkeypatch.setattr(packed, "PACK_TARGET", 17)
    def run(builder_class):
        trace = []
        class Dictionary(dict):
            def __iter__(self):
                trace.append("DICT_ITER")
                return iter(reversed(list(dict.keys(self))))

            def values(self):
                trace.append("DICT_VALUES")
                return dict.values(self)

            def __getitem__(self, key):
                trace.append(("DICT_GET", key))
                return dict.__getitem__(self, key)

        class Array(list):
            def __iter__(self):
                trace.append("LIST_ITER")
                return iter([list.__getitem__(self, index) for index in reversed(range(list.__len__(self)))])

        class Sequence(tuple):
            def __iter__(self):
                trace.append("TUPLE_ITER")
                return iter([tuple.__getitem__(self, index) for index in reversed(range(tuple.__len__(self)))])

        leaves = [None, False, 0, -0.0, 0.0, "Ñ/~\0"] * 4
        nested = {"dict": lambda: Dictionary(row()), "list": lambda: Array(leaves),
                  "tuple": lambda: Sequence(leaves)}[kind]()
        value = {"rows": [nested]}
        builder = builder_class(value)
        captures, operations = traced_capture(monkeypatch, builder, value)
        return captures, operations, trace
    assert run(packed._CaptureBuilder) == run(PriorLeafBuilder)


class Text(str):
    __hash__ = None


class Integer(int):
    __hash__ = None


class Floating(float):
    __hash__ = None


@pytest.mark.parametrize("value", [Text("Ñ/~\0"), Integer(0), Floating(-0.0)])
def test_nonbuiltin_scalar_subclasses_keep_the_original_recursive_fallback(value):
    source = row()
    source["custom"] = value
    def run(base):
        seen = []
        class Builder(base):
            def _append(self, member, name, buffer, *, root=False):
                if member is value:
                    seen.append((name, root))
                return super()._append(member, name, buffer, root=root)
        return Builder(source).capture(source), seen
    current, seen = run(packed._CaptureBuilder)
    assert (current, seen) == run(PriorLeafBuilder)
    assert seen == [("custom", False)]
    assert b"".join(packed._expanded(capture, expansion_limit=LIMITS["expansion_limit"])
                    for capture in current) == canonical(source)


def test_existing_named_and_scalar_cache_behavior_is_not_replaced_by_fresh_canonical_bytes():
    shared = {"known_at": None}
    source = {"rows": [row()], "short": [shared, shared]}
    supplied = {id(shared): (shared, packed.Capture(b'"fake-short-cache"', ()))}
    current, prior = packed._CaptureBuilder(source, cache=dict(supplied)), PriorLeafBuilder(source, cache=dict(supplied))
    for builder in (current, prior):
        builder.scalars[(str, "ordinary00")] = b'"poisoned-key"'
        builder.scalars[(int, 0)] = b"123"
        builder.scalars[(float, (-0.0).hex())] = b"-7.0"
    captures = current.capture(source)
    assert captures == prior.capture(source)
    expanded = b"".join(packed._expanded(capture, expansion_limit=LIMITS["expansion_limit"])
                        for capture in captures)
    assert b'"poisoned-key":null' in expanded and b"123" in expanded and b"-7.0" in expanded
    assert b"fake-short-cache" not in expanded
    # Private cache adversary equivalence only; not a verified financial cut.


@pytest.mark.parametrize("value", [None, False, 0, -0.0, 0.0, "Ñ/~\0", "x" * 257, 1 << 4097])
@pytest.mark.parametrize("name", ["value", "as_of"])
def test_scalar_root_capture_and_direct_append_remain_exact(monkeypatch, value, name):
    monkeypatch.setattr(packed, "PACK_TARGET", 17)
    current, prior = packed._CaptureBuilder(value), PriorLeafBuilder(value)
    assert traced_capture(monkeypatch, current, value, name) == traced_capture(monkeypatch, prior, value, name)
    def append(builder):
        buffer = packed._CaptureBuffer()
        buffer.append(b'{"known_at":')
        buffer.bind(b"null")
        buffer.append(b',"value":')
        builder._append(value, name, buffer, root=True)
        buffer.append(b"}"); buffer.flush()
        return tuple(buffer.result)
    assert append(current) == append(prior)
    assert current._binding_scalars is None and current._binding_bytes == 0


@pytest.mark.parametrize("maximum", [0, 1, 2, 3])
def test_direct_leaves_preserve_literal_occurrences_and_max_bindings_rejection(monkeypatch, maximum):
    monkeypatch.setattr(packed, "MAX_BINDINGS", maximum)
    source = {f"ordinary{i:02}": i for i in range(24)}
    source.update(a_at=CLOCK, b_at=CLOCK, c_at=None)
    current, prior = packed._CaptureBuilder(source), PriorLeafBuilder(source)
    if maximum < 3:
        for builder in (current, prior):
            with pytest.raises(ValueError, match="^SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED$"):
                builder.capture(source)
    else:
        captures = current.capture(source)
        assert captures == prior.capture(source)
        assert captures[0].literals == (canonical(CLOCK), canonical(CLOCK), b"null")
        positions, count = packed._slots(captures[0].template)
        assert count == len(positions) == 3 and [index for _, index in positions] == [0, 1, 2]
    assert current._binding_scalars is None and current._binding_bytes == 0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_leaf_keeps_original_rejection_and_cleans_up_binding_context(monkeypatch, value):
    source = row()
    source["z_invalid"] = value
    errors = []
    for builder_class in (packed._CaptureBuilder, PriorLeafBuilder):
        builder = builder_class(source)
        with pytest.raises(ValueError) as error:
            builder.capture(source)
        errors.append((type(error.value), str(error.value)))
        assert builder._binding_scalars is None and builder._binding_bytes == 0
    assert errors[0] == errors[1]
    errors = []
    for builder_class in (packed._CaptureBuilder, PriorLeafBuilder):
        with monkeypatch.context() as previous:
            previous.setattr(packed, "_CaptureBuilder", builder_class)
            with pytest.raises(ValueError) as error:
                packed.encode_packed_storage(source, **LIMITS)
        errors.append((type(error.value), str(error.value)))
    assert errors[0] == errors[1]


def test_default_string_fault_order_and_reentrant_binding_context_keep_prior_behavior():
    def run(base, fault):
        calls = []
        class TextValue:
            def __str__(self):
                calls.append("DEFAULT_STR")
                outer = builder._binding_scalars, builder._binding_bytes
                inner = {"inner_at": CLOCK, "plain": [-0.0, 0.0, False, 0] * 5}
                self.inner = builder.capture(inner)
                assert builder._binding_scalars is outer[0] and builder._binding_bytes == outer[1]
                if fault:
                    raise RuntimeError("synthetic_scalar_dispatch_fault")
                return "default synthetic scalar"
        text = TextValue()
        source = row(); source["z_custom"] = text
        builder = base(source)
        if fault:
            with pytest.raises(RuntimeError, match="^synthetic_scalar_dispatch_fault$"):
                builder.capture(source)
            result = None
        else:
            result = builder.capture(source)
        assert builder._binding_scalars is None and builder._binding_bytes == 0
        return result, text.inner, calls
    for fault in (False, True):
        assert run(packed._CaptureBuilder, fault) == run(PriorLeafBuilder, fault)


@pytest.mark.parametrize("limits", [dict(durable_limit=64, expansion_limit=64 * 1024**2),
                                    dict(durable_limit=32 * 1024**2, expansion_limit=16)])
def test_direct_leaf_path_keeps_original_public_durable_and_expansion_rejections(monkeypatch, limits):
    source = value_for("rows")
    errors = []
    for builder_class in (packed._CaptureBuilder, PriorLeafBuilder):
        with monkeypatch.context() as previous:
            previous.setattr(packed, "_CaptureBuilder", builder_class)
            with pytest.raises(ValueError) as error:
                packed.encode_packed_storage(source, **limits)
        errors.append((type(error.value), str(error.value)))
    assert errors[0] == errors[1]


def test_prepared_static_sections_and_mutable_headers_keep_full_prior_wire_after_mutation(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    value = {**value_for("rows"), "as_of": CLOCK, "status": "OBSERVING",
             "cross_payload_hashes": {"report": "prior"}}
    frozen = deepcopy(value)
    current = packed.PreparedPackedStorage(value, mutable={"status", "cross_payload_hashes"}, **LIMITS)
    with monkeypatch.context() as previous:
        previous.setattr(packed, "_CaptureBuilder", PriorLeafBuilder)
        prior = packed.PreparedPackedStorage(frozen, mutable={"status", "cross_payload_hashes"}, **LIMITS)
    value["rows"][0]["ordinary00"] = "external static mutation"
    value["typed"].append("external static mutation")
    for status in ("OBSERVING", "RETENTION_PRESSURE"):
        headers = dict(status=status, cross_payload_hashes={"report": status})
        value.update(headers); frozen.update(headers)
        wire, digest = current.encode(value)
        with monkeypatch.context() as previous:
            previous.setattr(packed, "_CaptureBuilder", PriorLeafBuilder)
            prior_wire, prior_digest = prior.encode(frozen)
        raw = canonical(frozen)
        assert canonical(wire) == canonical(prior_wire)
        assert digest == prior_digest == hashlib.sha256(raw).hexdigest()
        assert current.metrics(value) == (digest, len(raw))
        assert canonical(serialization.decode_storage(wire, **LIMITS)) == raw
