"""Private publication reuse versus the frozen 8764 prepared encoder.

Reference classes come from Git blob1fde1525d74fb8d77ed39ed9b017f03472a347cf.
Only global qualifiers, import routing and class names differ. No runtime or
performance acceptance follows from these encoder and native-caller guards.
"""
from copy import deepcopy
from collections import Counter
from contextvars import copy_context
from decimal import Decimal, localcontext
import hashlib
import inspect
import json
from pathlib import Path
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

from rc6_shadow_runtime import packed_storage as packed, serialization
from rc6_shadow_runtime import publication_storage as publication


CLOCK = "2026-10-05T16:00:00.000001+00:00"
LIMITS = dict(durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)
MUTABLE = {"status", "cross_payload_hashes", "evidence_retention"}


class PriorBuilder:
    """Strong references and immutable byte plans within this publication only."""

    def __init__(self, value, *, cache=None):
        self.objects, self.incoming = ({}, {})
        self.cache = {} if cache is None else cache
        self.scalars = {}
        self._binding_scalars, self._binding_bytes = (None, 0)
        self._count(value)

    def _count(self, value):
        if not isinstance(value, (dict, list, tuple)):
            return
        key = id(value)
        self.incoming[key] = self.incoming.get(key, 0) + 1
        if key in self.objects:
            return
        self.objects[key] = value
        for child in value.values() if isinstance(value, dict) else value:
            if isinstance(child, (dict, list, tuple)):
                self._count(child)

    def _scalar(self, value):
        if type(value) not in (type(None), bool, int, float, str):
            return packed._canonical(value)
        key = (type(value), value.hex() if type(value) is float else value)
        if key not in self.scalars:
            self.scalars[key] = packed._canonical(value)
        return self.scalars[key]

    def _binding_scalar(self, value):
        cache = self._binding_scalars
        kind = type(value)
        if cache is None or kind not in (type(None), bool, int, float, str) or (kind is str and len(value) > packed._BINDING_SCALAR_STRING) or (kind is int and value.bit_length() > packed._BINDING_SCALAR_INTEGER_BITS):
            return packed._canonical(value)
        key = (kind, value.hex() if kind is float else value)
        raw = cache.get(key)
        if raw is not None:
            return raw
        raw = packed._canonical(value)
        if len(cache) < packed._BINDING_SCALAR_ENTRIES and self._binding_bytes + len(raw) <= packed._BINDING_SCALAR_BYTES:
            cache[key] = raw
            self._binding_bytes += len(raw)
        return raw

    def _named(self, value):
        literals, indices = ([], {})

        def replace(match):
            key = (match['prefix'], match['value'])
            if key not in indices:
                indices[key] = len(literals)
                literals.append(match['value'])
            return match['prefix'] + b'\x00' + packed.struct.pack('!I', indices[key])
        return packed.Capture(packed._VOLATILE.sub(replace, packed._canonical(value)), tuple(literals))

    def _append(self, value, name, buffer, *, root=False):
        container = isinstance(value, (dict, list, tuple))
        short_raw = None
        if container and (not root) and (self.incoming.get(id(value), 0) >= 2 or name in packed._FIELDS):
            cached = self.cache.get(id(value))
            fresh = cached is None or cached[0] is not value
            if fresh:
                capture = self._named(value)
                self.cache[id(value)] = (value, capture)
            else:
                capture = cached[1]
            if len(capture.template) >= 64:
                buffer.boundary(capture)
                return
            if fresh and (not capture.literals):
                short_raw = capture.template
        if container and packed._small_plain(value):
            raw = short_raw if short_raw is not None else packed._canonical(value)
            if len(raw) <= packed.PACK_TARGET - len(buffer.template):
                buffer.append(raw)
                return
        if isinstance(value, dict):
            buffer.append(b'{')
            for ordinal, key in enumerate(sorted(value)):
                buffer.append((b',' if ordinal else b'') + self._scalar(key) + b':')
                child = value[key]
                if packed._root_volatile(key):
                    buffer.bind(self._binding_scalar(child))
                elif type(child) in (type(None), bool, int, float, str):
                    buffer.append(self._scalar(child))
                else:
                    self._append(child, key, buffer)
            buffer.append(b'}')
        elif isinstance(value, (list, tuple)):
            buffer.append(b'[')
            for ordinal, child in enumerate(value):
                if ordinal:
                    buffer.append(b',')
                if type(child) in (type(None), bool, int, float, str):
                    buffer.append(self._scalar(child))
                else:
                    self._append(child, name, buffer)
            buffer.append(b']')
        else:
            buffer.append(self._scalar(value))

    def capture(self, value, name=''):
        previous = (self._binding_scalars, self._binding_bytes)
        self._binding_scalars, self._binding_bytes = ({}, 0)
        try:
            if packed._root_volatile(name):
                return (packed.Capture(b'\x00' + b'\x00' * 4, (self._binding_scalar(value),)),)
            buffer = packed._CaptureBuffer()
            self._append(value, name, buffer, root=True)
            buffer.flush()
            return tuple(buffer.result)
        finally:
            self._binding_scalars, self._binding_bytes = previous

class PriorPrepared:
    """Freeze static root fields once; mutable root headers are captured fresh."""

    def __init__(self, value, *, mutable=(), durable_limit, expansion_limit, cache=None, shape_memo=None):
        from rc6_shadow_runtime.serialization import _shape
        if not isinstance(value, dict):
            raise ValueError('SHADOW_STORAGE_ROOT_REQUIRED')
        _shape(value, memo=shape_memo)
        self.mutable = frozenset(mutable)
        self.durable_limit, self.expansion_limit = (durable_limit, expansion_limit)
        builder = PriorBuilder(value, cache=cache)
        self.sections = {key: builder.capture(member, key) for key, member in value.items() if key not in self.mutable}
        self.expanded = {}

    def _captures(self, value):
        if set(value) - self.mutable != set(self.sections):
            raise ValueError('SHADOW_STORAGE_STATIC_FIELDS_CHANGED')
        yield packed.Capture(b'{', ())
        for ordinal, key in enumerate(sorted(value)):
            yield packed.Capture((b',' if ordinal else b'') + packed._canonical(key) + b':', ())
            if key in self.mutable:
                from rc6_shadow_runtime.serialization import _shape
                _shape(value[key])
                yield from PriorBuilder(value[key]).capture(value[key], key)
            else:
                yield from self.sections[key]
        yield packed.Capture(b'}', ())

    def _proof(self, captures):
        accumulator, size = (hashlib.sha256(), 0)
        for capture in captures:
            if capture not in self.expanded:
                self.expanded[capture] = packed._expanded(capture, expansion_limit=self.expansion_limit)
            raw = self.expanded[capture]
            size += len(raw)
            if size > self.expansion_limit:
                raise ValueError('SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED')
            accumulator.update(raw)
        return (accumulator.hexdigest(), size)

    def metrics(self, value):
        return self._proof(self._captures(value))

    def encode(self, value):
        from rc6_shadow_runtime.serialization import THRESHOLD
        captures = tuple(self._captures(value))
        logical_sha, logical_bytes = self._proof(captures)
        if logical_bytes < THRESHOLD:
            if logical_bytes > self.durable_limit:
                raise ValueError('SHADOW_STORAGE_DURABLE_CAPACITY_REACHED')
            return (value, logical_sha)
        result = packed._encode_captures(captures, logical_sha=logical_sha, logical_bytes=logical_bytes, durable_limit=self.durable_limit, expansion_limit=self.expansion_limit)
        return (result, logical_sha)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      default=str, allow_nan=False).encode()


def roles_for(case="shared", count=80):
    metadata = {"label": "shared native basis " * 5, "as_of": CLOCK,
                "typed": [-0.0, 0.0, False, 0, "Ñ/\0", None]}
    short = {"n": 0, "ok": False}
    instruments, rows = {}, []
    for index in range(count):
        ident = (f"S{index:04}", "ACCIONES", "BYMA", "ARS", "A-24HS")
        state = {"identity": ident, "attempts": [["intraday", CLOCK, True]],
            "samples": [CLOCK], "basis": metadata, "rank": index, "flag": False,
            "delta": -0.0 if index % 2 else 0.0, "book_at": None, "short": short}
        instruments[str(index)] = state
        rows.append({"identity": ident, "rank_components": metadata, "pipeline": metadata,
            "warmup_progress": {"distinct_samples": 0, "required": 3},
            "source_at": CLOCK, "book_at": None, "state": "DISCOVERY"})
    engines = {"SCALPING": {"capacity": {"limit": 2}, "instruments": instruments,
        "planned_at": CLOCK, "telemetry": rows}, "SWING": {"plan": "untouched"}}
    report = {"engines": engines, "after": [False, 0, -0.0, 0.0, "Ñ/\0", CLOCK],
        "operational_funnel": {"observations": [row["warmup_progress"] for row in rows]},
        "status": "OBSERVING", "cross_payload_hashes": {"report": "old"}}
    checkpoint = {"engines": engines, "after": list(report["after"]),
                  "status": "OBSERVING", "cross_payload_hashes": {"report": "old"}}
    if case == "alias_mismatch":
        child = {"long_plain": "not a named FIELDS value " * 8, "raw": [0, False, -0.0]}
        instruments["0"]["plain_child"] = child
        checkpoint["outside_child"] = child
    elif case == "parent_dag":
        instruments["1"] = instruments["0"]
        report["other_parent"] = instruments["1"]
        checkpoint["other_parent"] = instruments["1"]
    elif case == "equal_distinct":
        checkpoint["engines"] = deepcopy(engines)
    elif case == "different_name":
        checkpoint["different_engines"] = checkpoint.pop("engines")
    elif case == "volatile":
        for state in instruments.values():
            state.update(source_at=CLOCK, received_at=CLOCK, known_at=None,
                         elapsed_seconds=0.0, sha256="0" * 64)
    elif case == "dense_aliases":
        for state in instruments.values():
            state["alias_short"] = short
            state["alias_long"] = metadata
    return {"report": report, "checkpoint": checkpoint,
            "status": {"status": "OBSERVING", "after": list(report["after"])}}


def prepare_prior(values, *, mutable=MUTABLE, limits=LIMITS, after=None):
    prepared, cache, memo = {}, {}, {}
    snapshots = []
    for role, value in values.items():
        prepared[role] = PriorPrepared(value, mutable=mutable, cache=cache, shape_memo=memo, **limits)
        snapshots.append(tuple(cache.items()))
        if after is not None:
            after(role, value)
    return prepared, cache, snapshots


def assert_cache(actual, expected):
    assert list(actual) == list(expected)
    for key in actual:
        assert actual[key][0] is expected[key][0]
        assert actual[key][1] == expected[key][1]


def assert_roles(actual, expected, values, *, decode=True):
    for role, value in values.items():
        assert actual[role].sections == expected[role].sections
        assert actual[role].metrics(value) == expected[role].metrics(value)
        wire, logical = actual[role].encode(value)
        prior, prior_logical = expected[role].encode(value)
        assert wire == prior and logical == prior_logical
        if decode:
            restored = serialization.decode_storage(wire, **LIMITS)
            assert canonical(restored) == canonical(value)
            assert hashlib.sha256(canonical(restored)).hexdigest() == logical


@pytest.mark.parametrize("target", [63, 64, 65, 255, 256, 257, 65536])
@pytest.mark.parametrize("case", ["shared", "alias_mismatch", "parent_dag", "equal_distinct",
                                  "different_name", "volatile", "dense_aliases"])
def test_each_role_preserves_prior_graph_cuts_literals_named_cache_full_wire_and_all_occurrences(monkeypatch, target, case):
    monkeypatch.setattr(packed, "PACK_TARGET", target)
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    values = roles_for(case)
    expected, expected_cache, expected_snapshots = prepare_prior(values)
    observations = []
    original_init = packed.PreparedPackedStorage.__init__
    original_capture = packed._CaptureBuilder._capture_original
    roots = {id(value): role for role, value in values.items()}
    counts = {role: PriorBuilder(value) for role, value in values.items()}
    counted = set()
    def count_observe(builder, value, name=""):
        for key, role in roots.items():
            if key in builder.objects and role not in counted:
                reference = counts[role]
                assert builder.incoming == reference.incoming
                assert builder.objects.keys() == reference.objects.keys()
                assert all(builder.objects[identity] is node for identity, node in reference.objects.items())
                counted.add(role)
        return original_capture(builder, value, name)
    def observe(prepared, value, **kwargs):
        original_init(prepared, value, **kwargs)
        observations.append(tuple(kwargs["cache"].items()))
    monkeypatch.setattr(packed.PreparedPackedStorage, "__init__", observe)
    monkeypatch.setattr(packed._CaptureBuilder, "_capture_original", count_observe)
    actual, actual_cache = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert len(observations) == len(expected_snapshots)
    assert counted == set(values)
    for current, prior in zip(observations, expected_snapshots):
        assert_cache(dict(current), dict(prior))
    assert_cache(actual_cache, expected_cache)
    assert_roles(actual, expected, values)
    # The entire report has extra aliases to telemetry.warmup_progress.
    # The producer must not erase those aliases or borrow another role's count.
    report = packed._CaptureBuilder(values["report"])
    checkpoint = packed._CaptureBuilder(values["checkpoint"])
    if case != "different_name":
        row = values["report"]["engines"]["SCALPING"]["telemetry"][0]
        assert report.incoming[id(row["warmup_progress"])] == 2
        if case != "equal_distinct":
            assert checkpoint.incoming[id(row["warmup_progress"])] == 1


def test_shared_native_engine_shape_reuses_large_instruments_despite_real_warmup_alias_difference(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    calls = []
    original = publication._SectionScope.append
    def observe(scope, builder, value, name, buffer, *, root):
        key = id(value), name, root, scope.active_root
        existed = key in scope.plans
        used = original(scope, builder, value, name, buffer, root=root)
        calls.append((name, existed, used))
        return used
    monkeypatch.setattr(publication._SectionScope, "append", observe)
    values = roles_for()
    expected, cache, _ = prepare_prior(values)
    actual, current = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert any(name == "instruments" and existed and used for name, existed, used in calls)
    assert any(name == "telemetry" and existed and not used for name, existed, used in calls)
    assert_roles(actual, expected, values)
    assert_cache(current, cache)


def test_alias_context_mismatch_changes_actual_legacy_boundaries_and_forces_original_subcapture(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    values = roles_for("alias_mismatch")
    prior, cache, _ = prepare_prior(values)
    assert prior["report"].sections["engines"] != prior["checkpoint"].sections["engines"]
    actual, current = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert_roles(actual, prior, values)
    assert_cache(current, cache)


@pytest.mark.parametrize("mutation", ["leaf", "typed", "signed_zero", "key", "order", "edge", "clock"])
def test_mutation_between_roles_keeps_first_frozen_sections_and_recaptures_second_with_current_context(monkeypatch, mutation):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    def change(values):
        row = values["report"]["engines"]["SCALPING"]["instruments"]["0"]
        if mutation == "leaf": row["rank"] = 9001
        elif mutation == "typed": row["flag"] = 0
        elif mutation == "signed_zero": row["delta"] = -0.0
        elif mutation == "key": row["new_key"] = "new builtin field"
        elif mutation == "order": row["rank"] = row.pop("rank")
        elif mutation == "edge": row["attempts"] = [["changed", CLOCK, False]]
        elif mutation == "clock": row["book_at"] = CLOCK
    expected_values = roles_for()
    def after(role, _):
        if role == "report": change(expected_values)
    expected, _, _ = prepare_prior(expected_values, after=after)
    actual_values = roles_for()
    original_init = packed.PreparedPackedStorage.__init__
    def observe(prepared, value, **kwargs):
        original_init(prepared, value, **kwargs)
        if value is actual_values["report"]: change(actual_values)
    monkeypatch.setattr(packed.PreparedPackedStorage, "__init__", observe)
    actual, _ = publication.prepare_publication_storage(actual_values, mutable=MUTABLE, **LIMITS)
    assert_roles(actual, expected, actual_values, decode=False)
    first, _ = actual["report"].encode(actual_values["report"])
    second, _ = actual["checkpoint"].encode(actual_values["checkpoint"])
    original = roles_for()["report"]
    assert canonical(serialization.decode_storage(first, **LIMITS)) == canonical(original)
    assert canonical(serialization.decode_storage(second, **LIMITS)) == canonical(actual_values["checkpoint"])


@pytest.mark.parametrize("bound", ["MAX_SNAPSHOT_CONTAINERS", "MAX_SNAPSHOT_REFERENCES", "MAX_SNAPSHOT_BYTES",
    "MAX_CACHE_DEPENDENCIES", "MAX_FRAME_PLANS", "MAX_FRAME_PREFIX_BYTES", "MAX_ROOT_CANDIDATES",
    "MAX_ROLE_CONTAINERS", "MAX_ROLE_REFERENCES"])
def test_private_budget_exhaustion_is_only_original_fallback_and_never_new_admission_rejection(monkeypatch, bound):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    monkeypatch.setattr(publication, bound, 0)
    values = roles_for("volatile")
    expected, cache, _ = prepare_prior(values)
    actual, current = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert_roles(actual, expected, values)
    assert_cache(current, cache)


def test_mutable_headers_and_static_freeze_keep_prior_two_pack_results_after_external_mutation(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    values = roles_for("volatile")
    prior, _, _ = prepare_prior(values)
    actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    before = {role: actual[role].encode(value)[0] for role, value in values.items()}
    values["report"]["engines"]["SCALPING"]["instruments"]["0"]["rank"] = 7000
    for role, value in values.items():
        value.update(status="RETENTION_PRESSURE", evidence_retention={"status": "PRESSURE"},
                     cross_payload_hashes={"report": "new", "checkpoint": "new"})
        wire, logical = actual[role].encode(value)
        assert (wire, logical) == prior[role].encode(value)
        if role != "status":
            assert serialization.decode_storage(wire, **LIMITS)["engines"]["SCALPING"]["instruments"]["0"]["rank"] == 0
        assert before[role] != wire


def test_public_cache_callers_keep_original_poison_behavior_and_no_scope():
    values = roles_for("dense_aliases")
    shared = values["report"]["engines"]["SCALPING"]["instruments"]["0"]["basis"]
    supplied = {id(shared): (shared, packed.Capture(b'"fake-short-cache"', ()))}
    class Impostor(dict):
        def _bind_builder(self, *_): raise AssertionError("caller callback added")
    for cache in (dict(supplied), Impostor(supplied)):
        cache.update(supplied)
        actual = packed._CaptureBuilder(values["report"], cache=cache)
        expected = PriorBuilder(values["report"], cache=dict(supplied))
        actual.scalars[(str, "rank")] = b'"poisoned-key"'
        expected.scalars[(str, "rank")] = b'"poisoned-key"'
        assert actual._publication_scope is None
        assert actual.capture(values["report"]["engines"], "engines") == expected.capture(values["report"]["engines"], "engines")
    builder = packed._CaptureBuilder(values["report"], cache=Impostor(supplied))
    assert builder._publication_scope is None


def test_unknown_subclasses_and_default_string_callbacks_keep_prior_order_without_extra_snapshot_callbacks(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    def fixture(events):
        class Sequence(list):
            def __len__(self): events.append("length"); return super().__len__()
            def __iter__(self): events.append("iterate"); return super().__iter__()
        class Unknown:
            def __str__(self): events.append("default"); return "native default string"
        value = Sequence([Unknown(), 0, False, -0.0, 0.0] * 16)
        engine = {"large": value}
        return {"report": {"engines": engine}, "checkpoint": {"engines": engine}}
    actual_events, prior_events = [], []
    actual_values, prior_values = fixture(actual_events), fixture(prior_events)
    prior, _, _ = prepare_prior(prior_values)
    actual, _ = publication.prepare_publication_storage(actual_values, mutable=MUTABLE, **LIMITS)
    assert actual_events == prior_events
    for role in actual:
        assert actual[role].sections == prior[role].sections


def test_named_cache_retains_custom_destructor_to_original_release_point(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    def fixture(events):
        rows = []
        class Canary:
            def __str__(self):
                events.append("default"); rows.clear()
                return "long " * 30
            def __del__(self): events.append("released")
        shared = {"payload_body": Canary()}
        rows.extend([shared, shared])
        return {"report": {"engines": rows}, "checkpoint": {"engines": rows}}
    events = []
    values = fixture(events)
    prepared, cache = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert events == ["default"]
    for role, value in values.items(): prepared[role].encode(value)
    assert events == ["default"]
    prepared.clear()
    assert events == ["default"]
    cache.clear()
    assert events == ["default", "released"]
    prior_events = []
    values = fixture(prior_events)
    prior, old_cache, snapshots = prepare_prior(values)
    del snapshots
    for role, value in values.items(): prior[role].encode(value)
    assert prior_events == ["default"]
    prior.clear(); old_cache.clear()
    assert prior_events == ["default", "released"]


def test_consumed_grant_does_not_activate_nested_public_constructor_during_original_callback(monkeypatch):
    seen, cache_holder = [], []
    original_capture = packed._CaptureBuilder._capture_original
    def observe(builder, value, name=""):
        seen.append((value, builder._publication_scope))
        cache_holder.append(builder.cache)
        return original_capture(builder, value, name)
    monkeypatch.setattr(packed._CaptureBuilder, "_capture_original", observe)
    class Reentrant:
        def __str__(self):
            # The surrounding builder has consumed its expected-root grant.
            outer_cache = cache_holder[-1]
            inside = {"public": list(range(70))}
            prepared = packed.PreparedPackedStorage(inside, cache=outer_cache, **LIMITS)
            assert seen[-1][1] is None
            assert prepared.metrics(inside) == (hashlib.sha256(canonical(inside)).hexdigest(), len(canonical(inside)))
            return "native inner capture"
    rows = [Reentrant()] * 70
    engines = {"rows": rows}
    values = {"report": {"engines": engines}, "checkpoint": {"engines": engines}}
    prepared, cache = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert type(cache) is dict and packed._PUBLICATION_CONSTRUCTION.get() is None
    assert seen and all(scope is None for _, scope in seen)
    assert list(prepared) == ["report", "checkpoint"]


def test_scope_failure_discards_capability_references_and_restores_outer_context(monkeypatch):
    scopes, grants = [], []
    original_scope_init, original_grant_init = publication._SectionScope.__init__, packed._PublicationGrant.__init__
    def scope_init(scope, *args):
        original_scope_init(scope, *args); scopes.append(scope)
    def grant_init(grant, *args):
        original_grant_init(grant, *args); grants.append(grant)
    monkeypatch.setattr(publication._SectionScope, "__init__", scope_init)
    monkeypatch.setattr(packed._PublicationGrant, "__init__", grant_init)
    class Failure:
        def __str__(self): raise RuntimeError("NATIVE_OFFLINE_DEFAULT_FAILURE")
    engines = {"rows": [Failure()] * 70}
    values = {"report": {"engines": engines}, "checkpoint": {"engines": engines}}
    outer = object()
    token = packed._PUBLICATION_CONSTRUCTION.set(outer)
    try:
        with pytest.raises(RuntimeError, match="NATIVE_OFFLINE_DEFAULT_FAILURE"):
            publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
        assert packed._PUBLICATION_CONSTRUCTION.get() is outer
    finally:
        packed._PUBLICATION_CONSTRUCTION.reset(token)
    assert grants and all(grant.builder is None and grant.prepared is None and grant.value is None
                          and grant.cache is None and grant.scope is None for grant in grants)
    assert scopes and all(scope.closed and not scope.plans and scope.active_root is None and scope.trace is None for scope in scopes)


def test_nested_public_and_private_constructors_during_shape_leave_expected_outer_grant_unconsumed(monkeypatch):
    values = roles_for()
    original_shape = serialization._shape
    original_capture = packed._CaptureBuilder._capture_original
    captures, outer_grants = [], []
    busy = False
    def capture(builder, value, name=""):
        captures.append((name, builder._publication_scope))
        return original_capture(builder, value, name)
    def shape(value, **kwargs):
        nonlocal busy
        if value is values["report"] and not busy:
            busy = True
            grant = packed._PUBLICATION_CONSTRUCTION.get()
            assert type(grant) is packed._PublicationGrant and grant.builder is None
            outer_grants.append(grant)
            inside = {"outside": list(range(70))}
            public = packed.PreparedPackedStorage(inside, cache=grant.cache, **LIMITS)
            assert captures[-1][1] is None and grant.builder is None
            assert public.metrics(inside) == (hashlib.sha256(canonical(inside)).hexdigest(), len(canonical(inside)))
            nested, nested_cache = publication.prepare_publication_storage(roles_for(), mutable=MUTABLE, **LIMITS)
            assert nested and type(nested_cache) is dict
            assert packed._PUBLICATION_CONSTRUCTION.get() is grant and grant.builder is None
        return original_shape(value, **kwargs)
    monkeypatch.setattr(serialization, "_shape", shape)
    monkeypatch.setattr(packed._CaptureBuilder, "_capture_original", capture)
    actual, cache = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    prior, old_cache, _ = prepare_prior(values)
    assert_roles(actual, prior, values)
    assert_cache(cache, old_cache)
    assert any(scope is not None for _, scope in captures)
    assert outer_grants and all(grant.prepared is None and grant.builder is None for grant in outer_grants)
    assert packed._PUBLICATION_CONSTRUCTION.get() is None


def test_single_use_grant_is_consumed_before_original_count_invokes_subclass_iteration(monkeypatch):
    events, seen = [], set()
    original_capture = packed._CaptureBuilder._capture_original
    def observe(builder, value, name=""):
        if name == "public": assert builder._publication_scope is None
        return original_capture(builder, value, name)
    monkeypatch.setattr(packed._CaptureBuilder, "_capture_original", observe)
    class DuringCount(list):
        def __iter__(self):
            grant = packed._PUBLICATION_CONSTRUCTION.get()
            if type(grant) is packed._PublicationGrant and grant.builder is not None and id(grant) not in seen:
                seen.add(id(grant)); events.append("original_count_after_claim")
                assert type(grant.builder) is packed._CaptureBuilder
                nested = {"public": list(range(70))}
                packed.PreparedPackedStorage(nested, cache=grant.cache, **LIMITS)
            return super().__iter__()
    values = roles_for()
    sequence = DuringCount([False, 0, -0.0, 0.0])
    values["report"]["after_count"] = values["checkpoint"]["after_count"] = sequence
    actual, cache = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    prior, old_cache, _ = prepare_prior(values)
    assert events == ["original_count_after_claim", "original_count_after_claim"]
    assert_roles(actual, prior, values)
    assert_cache(cache, old_cache)
    assert packed._PUBLICATION_CONSTRUCTION.get() is None


def test_copied_context_cannot_lend_expected_constructor_capability_to_another_thread(monkeypatch):
    values = roles_for()
    value, cache = values["report"], {}
    scope = publication._SectionScope(values, frozenset(MUTABLE))
    instance = packed.PreparedPackedStorage.__new__(packed.PreparedPackedStorage)
    grant = packed._PublicationGrant(instance, value, cache, scope)
    original_capture = packed._CaptureBuilder._capture_original
    observations = []
    def observe(builder, member, name=""):
        observations.append((threading.get_ident(), builder._publication_scope))
        return original_capture(builder, member, name)
    monkeypatch.setattr(packed._CaptureBuilder, "_capture_original", observe)
    token = packed._PUBLICATION_CONSTRUCTION.set(grant)
    try:
        context = copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(context.run, packed.PreparedPackedStorage.__init__, instance, value,
                mutable=MUTABLE, cache=cache, **LIMITS).result(timeout=10)
        assert observations and all(item[1] is None for item in observations)
        assert grant.builder is None
        observations.clear()
        packed.PreparedPackedStorage.__init__(instance, value, mutable=MUTABLE, cache=cache, **LIMITS)
        assert grant.builder is not None and any(item[1] is scope for item in observations)
    finally:
        packed._PUBLICATION_CONSTRUCTION.reset(token)
        grant.close(); scope.close()
    assert packed._PUBLICATION_CONSTRUCTION.get() is None


@pytest.mark.parametrize("where", ["engine_after_instruments", "later_root_field"])
def test_whole_role_callback_state_observer_forces_original_path_after_large_shared_subtree(monkeypatch, where):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    def fixture(events):
        values = roles_for("volatile")
        class StateObserver:
            def __str__(self):
                frame = inspect.currentframe()
                try:
                    while frame is not None:
                        candidate = frame.f_locals.get("self")
                        if type(candidate) in (PriorBuilder, packed._CaptureBuilder):
                            state = (len(candidate.scalars), len(candidate._binding_scalars or {}),
                                     candidate._binding_bytes, type(candidate.cache).__name__)
                            events.append(state)
                            return repr(state)
                        frame = frame.f_back
                    raise AssertionError("no real capture builder in original callback")
                finally:
                    del frame
        callback = StateObserver()
        if where == "engine_after_instruments":
            values["report"]["engines"]["SCALPING"]["zz_callback"] = callback
        else:
            values["report"]["zz_later_callback"] = callback
            values["checkpoint"]["zz_later_callback"] = callback
        return values
    actual_events, prior_events = [], []
    actual_values, prior_values = fixture(actual_events), fixture(prior_events)
    prior, _, _ = prepare_prior(prior_values)
    scopes = []
    original_capture = packed._CaptureBuilder._capture_original
    def observe(builder, value, name=""):
        scopes.append(builder._publication_scope)
        return original_capture(builder, value, name)
    monkeypatch.setattr(packed._CaptureBuilder, "_capture_original", observe)
    actual, cache = publication.prepare_publication_storage(actual_values, mutable=MUTABLE, **LIMITS)
    assert actual_events and actual_events == prior_events
    for role in actual:
        assert actual[role].sections == prior[role].sections
        assert actual[role].encode(actual_values[role]) == prior[role].encode(prior_values[role])
    assert scopes and all(scope is None for scope in scopes[:2])
    assert type(cache) is dict


@pytest.mark.parametrize("capitals", [0, 1])
def test_exact_decimal_and_plain_counter_outside_replayed_subtree_keep_original_serialization_and_reuse(monkeypatch, capitals):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    values = roles_for()
    values["report"]["factual_summary"] = {"stages": Counter({"CATALOG_READY": 80}),
        "net": Decimal("-0.00"), "gross": Decimal("1.2300E+12"), "costs": Decimal("1E-9")}
    calls, reuses = [], []
    original_scalar, original_append = packed._CaptureBuilder._scalar, publication._SectionScope.append
    def scalar(builder, value):
        if type(value) is Decimal: calls.append(value)
        return original_scalar(builder, value)
    def append(scope, builder, value, name, buffer, *, root):
        assert type(value) is not Decimal and type(value) is not Counter
        existed = (id(value), name, root, scope.active_root) in scope.plans
        used = original_append(scope, builder, value, name, buffer, root=root)
        if existed and used: reuses.append(name)
        return used
    monkeypatch.setattr(packed._CaptureBuilder, "_scalar", scalar)
    monkeypatch.setattr(publication._SectionScope, "append", append)
    with localcontext() as context:
        context.capitals = capitals
        prior, old_cache, _ = prepare_prior(values)
        actual, cache = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
        assert_roles(actual, prior, values)
        assert_cache(cache, old_cache)
    assert calls and any(value.as_tuple().sign for value in calls)
    assert "instruments" in reuses


@pytest.mark.parametrize("custom", ["decimal_subclass", "counter_subclass", "counter_values", "counter_items", "counter_descriptor"])
def test_decimal_subclasses_and_counter_overrides_preserve_original_callbacks_and_disable_entire_role(monkeypatch, custom):
    events = []
    class CounterValues:
        def __get__(self, node, owner):
            events.append("descriptor_class" if node is None else "descriptor_instance")
            return lambda: dict.values(node)
    if custom == "counter_descriptor":
        monkeypatch.setattr(Counter, "values", CounterValues(), raising=False)
    def fixture():
        values = roles_for()
        if custom == "decimal_subclass":
            class Amount(Decimal):
                def __str__(self): events.append("decimal"); return super().__str__()
            member = Amount("1.2300")
        elif custom == "counter_subclass":
            class Counts(Counter):
                def values(self): events.append("subclass_values"); return super().values()
            member = Counts({"CATALOG_READY": 80})
        else:
            member = Counter({"CATALOG_READY": 80})
            if custom in ("counter_values", "counter_items"):
                method = custom.split("_")[1]
                def callback():
                    events.append(method); return getattr(dict, method)(member)
                setattr(member, method, callback)
        values["report"]["later_custom_summary"] = member
        values["checkpoint"]["later_custom_summary"] = member
        return values
    prior_values = fixture()
    prior, _, _ = prepare_prior(prior_values)
    expected_events = list(events); events.clear()
    scopes = []
    original_capture = packed._CaptureBuilder._capture_original
    def observe(builder, value, name=""):
        if "later_custom_summary" in builder.objects.get(id(actual_values["report"]), {}):
            scopes.append(builder._publication_scope)
        elif id(actual_values["checkpoint"]) in builder.objects:
            scopes.append(builder._publication_scope)
        return original_capture(builder, value, name)
    monkeypatch.setattr(packed._CaptureBuilder, "_capture_original", observe)
    actual_values = fixture()
    actual, _ = publication.prepare_publication_storage(actual_values, mutable=MUTABLE, **LIMITS)
    assert events == expected_events
    assert "descriptor_class" not in events
    assert scopes and all(scope is None for scope in scopes)
    for role in actual: assert actual[role].sections == prior[role].sections


def test_untrusted_counter_dictionary_descriptor_token_is_not_reuse_authority(monkeypatch):
    calls = []
    def dictionary(node):
        calls.append("custom_dictionary_descriptor")
        return {}
    descriptor = property(dictionary)
    monkeypatch.setattr(publication, "_COUNTER_DICTIONARY_DESCRIPTOR", descriptor)
    values = roles_for()
    values["report"]["summary"] = Counter({"CATALOG_READY": 80})
    prior, old_cache, _ = prepare_prior(values)
    actual, cache = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert calls == []
    assert_roles(actual, prior, values)
    assert_cache(cache, old_cache)


@pytest.mark.parametrize("frontier", ["template", "literals", "result_prefix", "constants", "named_cache"])
def test_frontier_context_mismatch_falls_back_and_existing_result_prefix_survives_valid_replay(monkeypatch, frontier):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    def fixture():
        values = roles_for()
        engine = values["report"]["engines"]["SCALPING"]
        if frontier == "literals": engine["captured_at"] = CLOCK
        if frontier == "result_prefix": engine["basis"] = {"plain": "initial preceding named " * 10}
        return values
    def change(values):
        engine = values["report"]["engines"]["SCALPING"]
        if frontier == "template": engine["capacity"]["limit"] = 9001
        elif frontier == "literals": engine["captured_at"] = "2026-10-05T16:00:00.000009+00:00"
        elif frontier == "result_prefix": engine["basis"] = {"plain": "replaced preceding named " * 10}
    prior_values = fixture()
    prior_target = packed.PACK_TARGET
    def prior_after(role, value):
        if role == "report":
            change(prior_values)
            if frontier == "constants": monkeypatch.setattr(packed, "PACK_TARGET", prior_target + 1)
    prior, _, _ = prepare_prior(prior_values, after=prior_after)
    monkeypatch.setattr(packed, "PACK_TARGET", prior_target)
    actual_values = fixture()
    original_init, original_append = packed.PreparedPackedStorage.__init__, publication._SectionScope.append
    replays = []
    def append(scope, builder, value, name, buffer, *, root):
        existed = (id(value), name, root, scope.active_root) in scope.plans
        before = tuple(buffer.result)
        used = original_append(scope, builder, value, name, buffer, root=root)
        if existed and name == "instruments":
            replays.append(used)
            if used: assert tuple(buffer.result[:len(before)]) == before
        return used
    def init(prepared, value, **kwargs):
        original_init(prepared, value, **kwargs)
        if value is actual_values["report"]:
            change(actual_values)
            if frontier == "constants": monkeypatch.setattr(packed, "PACK_TARGET", prior_target + 1)
            elif frontier == "named_cache":
                # A private cache entry replaced after the first role cannot
                # become an approved dependency solely through its identity.
                key = id(actual_values["report"]["engines"]["SCALPING"]["instruments"]["0"]["basis"])
                obj, capture = kwargs["cache"][key]
                kwargs["cache"][key] = obj, packed.Capture(capture.template, capture.literals)
    monkeypatch.setattr(packed.PreparedPackedStorage, "__init__", init)
    monkeypatch.setattr(publication._SectionScope, "append", append)
    actual, _ = publication.prepare_publication_storage(actual_values, mutable=MUTABLE, **LIMITS)
    assert replays and all(replays) is (frontier == "result_prefix")
    assert_roles(actual, prior, actual_values, decode=False)


def test_private_grants_and_builders_do_not_leave_frame_or_closure_cycles_after_preparation(monkeypatch):
    references, grants = [], []
    original_capture, original_grant = packed._CaptureBuilder._capture_original, packed._PublicationGrant.__init__
    def capture(builder, value, name=""):
        references.append(weakref.ref(builder))
        return original_capture(builder, value, name)
    def grant_init(grant, *args):
        original_grant(grant, *args); grants.append(grant)
    monkeypatch.setattr(packed._CaptureBuilder, "_capture_original", capture)
    monkeypatch.setattr(packed._PublicationGrant, "__init__", grant_init)
    actual, cache = publication.prepare_publication_storage(roles_for(), mutable=MUTABLE, **LIMITS)
    assert actual and type(cache) is dict
    assert references and all(reference() is None for reference in references)
    assert grants and all(grant.prepared is None and grant.value is None and grant.cache is None
                          and grant.scope is None and grant.builder is None for grant in grants)


def test_two_parallel_publications_do_not_share_graph_captures_callbacks_or_activated_scope(monkeypatch):
    barrier = threading.Barrier(2)
    original_init = packed.PreparedPackedStorage.__init__
    def observe(prepared, value, **kwargs):
        original_init(prepared, value, **kwargs)
        if "operational_funnel" in value: barrier.wait(timeout=5)
    monkeypatch.setattr(packed.PreparedPackedStorage, "__init__", observe)
    def run(marker):
        values = roles_for()
        values["report"]["engines"]["SCALPING"]["instruments"]["0"]["rank"] = marker
        actual, cache = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
        return actual["checkpoint"].metrics(values["checkpoint"]), cache
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.submit(run, 9001), executor.submit(run, 9002)
        (proof_a, cache_a), (proof_b, cache_b) = first.result(timeout=10), second.result(timeout=10)
    assert proof_a != proof_b and cache_a is not cache_b
    assert type(cache_a) is type(cache_b) is dict
    assert packed._PUBLICATION_CONSTRUCTION.get() is None


@pytest.mark.parametrize("attack", ["cycle", "depth", "nodes", "keys", "nan", "inf", "negative_inf"])
def test_full_shape_type_nonfinite_admission_guards_remain_equal_to_frozen_prior(monkeypatch, attack):
    values = roles_for()
    row = values["report"]["engines"]["SCALPING"]["instruments"]["0"]
    if attack == "cycle": row["cycle"] = row
    elif attack == "depth": monkeypatch.setattr(serialization, "MAX_DEPTH", 3)
    elif attack == "nodes": monkeypatch.setattr(serialization, "MAX_NODES", 20)
    elif attack == "keys": row[123] = "invalid string key"
    else: row["rank"] = {"nan": float("nan"), "inf": float("inf"), "negative_inf": float("-inf")}[attack]
    with pytest.raises((ValueError, TypeError)) as prior:
        prepare_prior(values)
    with pytest.raises(type(prior.value)) as actual:
        publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert str(actual.value) == str(prior.value)


def test_maximum_bindings_rejection_and_expansion_durable_limits_do_not_borrow_other_role_authority(monkeypatch):
    values = roles_for("volatile")
    monkeypatch.setattr(packed, "MAX_BINDINGS", 1)
    with pytest.raises(ValueError, match="SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED"):
        prepare_prior(values)
    with pytest.raises(ValueError, match="SHADOW_STORAGE_COMPLEXITY_CAPACITY_REACHED"):
        publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    monkeypatch.undo()
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    values = roles_for()
    actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    prior, _, _ = prepare_prior(values)
    actual["report"].metrics(values["report"])
    actual["checkpoint"].expansion_limit = prior["checkpoint"].expansion_limit = 20
    with pytest.raises(ValueError, match="SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED"):
        actual["checkpoint"].metrics(values["checkpoint"])
    with pytest.raises(ValueError, match="SHADOW_STORAGE_EXPANSION_CAPACITY_REACHED"):
        prior["checkpoint"].metrics(values["checkpoint"])
    actual["status"].durable_limit = prior["status"].durable_limit = 20
    with pytest.raises(ValueError) as actual_error: actual["status"].encode(values["status"])
    with pytest.raises(ValueError) as prior_error: prior["status"].encode(values["status"])
    assert str(actual_error.value) == str(prior_error.value)


def test_role_expansions_remain_original_plain_independent_caches_with_every_occurrence_hash(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    values = roles_for()
    actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    prior, _, _ = prepare_prior(values)
    proof_a = actual["report"].metrics(values["report"])
    proof_b = actual["checkpoint"].metrics(values["checkpoint"])
    assert proof_a == prior["report"].metrics(values["report"])
    assert proof_b == prior["checkpoint"].metrics(values["checkpoint"])
    assert type(actual["report"].expanded) is type(actual["checkpoint"].expanded) is dict
    assert actual["report"].expanded is not actual["checkpoint"].expanded
    assert set(actual["report"].expanded) & set(actual["checkpoint"].expanded)
    for role, value in values.items():
        expected = canonical(value)
        assert actual[role].metrics(value) == (hashlib.sha256(expected).hexdigest(), len(expected))


def test_native_worker_publisher_full_reader_and_projection_consume_the_same_exact_role_wire(tmp_path, monkeypatch):
    from tests.rc6_dashboard_native_fixture import native_fixture
    from rc6_shadow_runtime.persistence import read_committed_projection
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    observations, reuses, native_financial_groups = [], [], []
    financial_calls = {"prior": [], "actual": []}
    phase = [None]
    original_canonical = packed._canonical
    def canonical_observe(value, **kwargs):
        if phase[0] is not None and type(value) in (Counter, Decimal):
            financial_calls[phase[0]].append((type(value), id(value)))
        return original_canonical(value, **kwargs)
    original_factory, original_append = publication.prepare_publication_storage, publication._SectionScope.append
    def observe_append(scope, builder, value, name, buffer, *, root):
        existed = (id(value), name, root, scope.active_root) in scope.plans
        used = original_append(scope, builder, value, name, buffer, root=root)
        if existed and used: reuses.append(name)
        return used
    def observe(values, **kwargs):
        groups = values["report"].get("operational_funnel", {}).get("by_currency_channel", [])
        if groups:
            assert all(type(group["stages"]) is Counter for group in groups)
            assert all(type(group[field]) is Decimal for group in groups for field in ("net_pnl", "gross_pnl", "costs"))
            native_financial_groups.append(len(groups))
        financial_calls["prior"].clear(); financial_calls["actual"].clear()
        phase[0] = "prior"
        prior, prior_cache, _ = prepare_prior(values, mutable=kwargs["mutable"],
            limits={"durable_limit": kwargs["durable_limit"], "expansion_limit": kwargs["expansion_limit"]})
        phase[0] = "actual"
        actual, cache = original_factory(values, **kwargs)
        phase[0] = None
        assert financial_calls["actual"] == financial_calls["prior"]
        assert_cache(cache, prior_cache)
        for role, value in values.items():
            assert actual[role].sections == prior[role].sections
            assert actual[role].metrics(value) == prior[role].metrics(value)
            assert actual[role].encode(value) == prior[role].encode(value)
        observations.append(tuple(values))
        return actual, cache
    monkeypatch.setattr(publication, "prepare_publication_storage", observe)
    monkeypatch.setattr(publication._SectionScope, "append", observe_append)
    monkeypatch.setattr(packed, "_canonical", canonical_observe)
    fixture = native_fixture(tmp_path, count=64, with_future=False, with_spot=False)
    assert observations and all(roles == ("report", "checkpoint", "status") for roles in observations)
    assert native_financial_groups
    assert "instruments" in reuses
    projected = read_committed_projection(fixture.root, limit=10)
    assert fixture.cut["pointer"] == projected["pointer"]
    assert fixture.cut["report"]["mode"] == "SHADOW" and fixture.cut["report"]["real_orders_sent"] == 0
    assert fixture.cut["report"]["provider_requests"] == 0 and fixture.cut["report"]["real_routes"] == "NOT_CALLED"
    for role in ("report", "checkpoint", "status"):
        proof = fixture.cut["export_contract"]["verified_payloads"][role]
        assert serialization.canonical_metrics(fixture.cut[role], ensure_ascii=True) == (proof["payload_digest"], proof["logical_bytes"])
