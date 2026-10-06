"""Private type predicates versus the frozen pre-private 8764 encoder.

The historical helper is byte-exact e24 source, not runtime authority for the
fixed publisher. These guards do not establish BIG performance or acceptance.
"""
from collections import Counter
from decimal import Decimal
import hashlib
import inspect
import os
from pathlib import Path
import stat
from types import ModuleType

import pytest

from rc6_shadow_runtime import packed_storage as packed, publication_storage as publication, serialization
from tests.test_rc6_publication_storage import LIMITS, MUTABLE, PriorBuilder, canonical, prepare_prior, roles_for


E24_HELPER_SHA256 = "35ad9e2120d6d435798496568341d3426082e62c16425c9e0fa91f54833505be"


def historical_e24_helper():
    path = Path(__file__).parent / "fixtures" / "rc6_publication_storage_e24.py.source"
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        assert stat.S_ISREG(before.st_mode) and before.st_nlink == 1
        chunks = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
        after = os.fstat(fd)
        assert before == after
    finally:
        os.close(fd)
    raw = b"".join(chunks)
    assert hashlib.sha256(raw).hexdigest() == E24_HELPER_SHA256
    module = ModuleType("rc6_shadow_runtime._historical_e24_type_audit")
    module.__file__, module.__package__ = str(path), "rc6_shadow_runtime"
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def observed_roles(factory, values, trace, *, share_subtrees=False):
    prepared, cache = factory(values)
    result = {}
    for role, value in values.items():
        representation, logical = prepared[role].encode(value)
        metrics = prepared[role].metrics(value)
        restored = serialization.decode_storage(representation, share_subtrees=share_subtrees, **LIMITS)
        logical_bytes = canonical(restored)
        assert metrics == (logical, len(logical_bytes))
        assert hashlib.sha256(logical_bytes).hexdigest() == logical
        wire = b"".join(packed.envelope_components({"digest": logical, "payload": representation}))
        result[role] = (tuple(prepared[role].sections.items()), metrics,
                        representation, wire, logical_bytes, restored)
    captures = tuple(entry[1] for entry in cache.values())
    return result, tuple(trace), captures


def prior_factory(values):
    prepared, cache, _ = prepare_prior(values)
    return prepared, cache


def fixed_factory(values):
    return publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)


def marker_probe_roles():
    trace, values = [], {}
    for role in ("report", "checkpoint", "status"):
        root = {"marker": "BEFORE_TYPE_AUDIT", "padding": "x" * 300000}
        state = {"mutated": False}
        # Factory-local defaults bind each callback to its own fresh role.
        def make_probe(root, state, role):
            class ProbeMeta(type):
                def __eq__(cls, other):
                    trace.append((role, "eq", type.__getattribute__(other, "__name__")))
                    if not state["mutated"]:
                        state["mutated"] = True
                        root["marker"] = "MUTATED_BY_TYPE_COMPARISON"
                    return False

                def __ne__(cls, other):
                    trace.append((role, "ne"))
                    return True

                def __hash__(cls):
                    trace.append((role, "hash"))
                    return type.__hash__(cls)

            class Probe(metaclass=ProbeMeta):
                def __str__(self):
                    trace.append((role, "str"))
                    return "stable original default-string payload"

            return Probe()
        root["zz_probe"] = make_probe(root, state, role)
        values[role] = root
    return values, trace


@pytest.mark.parametrize("share_subtrees", (False, True))
def test_historical_e24_pre_audit_changes_full_role_wire_and_fix_keeps_original_8764_callbacks(share_subtrees):
    original_values, original_trace = marker_probe_roles()
    original = observed_roles(prior_factory, original_values, original_trace, share_subtrees=share_subtrees)
    bad_values, bad_trace = marker_probe_roles()
    historical = historical_e24_helper()
    bad = observed_roles(lambda values: historical.prepare_publication_storage(
        values, mutable=MUTABLE, **LIMITS), bad_values, bad_trace, share_subtrees=share_subtrees)
    fixed_values, fixed_trace = marker_probe_roles()
    fixed = observed_roles(fixed_factory, fixed_values, fixed_trace, share_subtrees=share_subtrees)

    assert fixed == original
    assert bad[0] != original[0] and bad[1] != original[1]
    for role in original_values:
        assert original[0][role][-1]["marker"] == "BEFORE_TYPE_AUDIT"
        assert fixed[0][role][-1]["marker"] == "BEFORE_TYPE_AUDIT"
        assert bad[0][role][-1]["marker"] == "MUTATED_BY_TYPE_COMPARISON"
    assert any(event[1] == "eq" for event in original[1])
    assert all(event[1] not in ("ne", "hash") for event in original[1])


@pytest.mark.parametrize("operation", ("candidate_construction", "snapshot_root", "snapshot_child", "eligible_child"))
def test_private_type_checks_add_no_metaclass_eq_ne_or_hash_callbacks(operation):
    values, trace = marker_probe_roles()
    root = values["report"]
    if operation == "candidate_construction":
        publication._SectionScope(values, frozenset())
    elif operation == "snapshot_root":
        builder = packed._CaptureBuilder(root)
        snapshot = publication._Snapshot(builder, root["zz_probe"], [0, 0, 0])
        assert not snapshot.valid
    elif operation == "snapshot_child":
        builder = packed._CaptureBuilder(root)
        snapshot = publication._Snapshot(builder, root, [0, 0, 0])
        assert not snapshot.valid
    else:
        shared = {"zz_probe": root["zz_probe"]}
        candidate_values = {"report": {"engines": shared}, "checkpoint": {"engines": shared}}
        scope = publication._SectionScope(candidate_values, frozenset())
        builder = packed._CaptureBuilder(candidate_values["report"])
        assert scope.candidates
        assert not scope.eligible_role(builder, candidate_values["report"])
    assert trace == []
    assert root["marker"] == "BEFORE_TYPE_AUDIT"


def test_private_append_hook_keeps_legacy_subclass_comparisons_and_never_adds_type_callbacks():
    def run(builder_class, private):
        trace = []
        class Meta(type):
            def __eq__(cls, other):
                trace.append(("eq", type.__getattribute__(other, "__name__")))
                return False
            def __hash__(cls):
                trace.append(("hash",))
                return type.__hash__(cls)
        class Mapping(dict, metaclass=Meta):
            pass
        value = Mapping(a=-0.0, b=False, c="Ñ/\0")
        builder = builder_class(value)
        class ForbiddenScope:
            minimum_items = 0
            def append(self, *args, **kwargs):
                raise AssertionError("unknown subclass must not enter private hook")
        if private:
            builder._publication_scope = ForbiddenScope()
        buffer = packed._CaptureBuffer()
        builder._append(value, "plain", buffer, root=True)
        buffer.flush()
        return tuple(buffer.result), trace
    original = run(PriorBuilder, False)
    fixed = run(packed._CaptureBuilder, True)
    assert fixed == original
    assert original[1]


def no_candidate_counter_roles():
    trace, values = [], {}
    for role in ("report", "checkpoint", "status"):
        prior_child = {"kind": "before count", "typed": [False, 0, -0.0, 0.0, "Ñ/\0"]}
        counter = Counter(native=1)
        def callback(counter=counter, prior_child=prior_child, role=role):
            trace.append((role, "Counter.values"))
            prior_child["kind"] = "after original count callback"
            del counter.values
            return dict.values(counter)
        counter.values = callback
        # Distinct root members ensure no root pair is shared across roles.
        values[role] = {"aa_visited": prior_child, "padding": "x" * 300000, "zz_counter": counter}
    return values, trace


def test_non_candidate_role_preserves_self_clearing_counter_count_mutation_and_full_wire(monkeypatch):
    original_values, original_trace = no_candidate_counter_roles()
    original = observed_roles(prior_factory, original_values, original_trace)
    fixed_values, fixed_trace = no_candidate_counter_roles()
    checks, eligible = [], publication._SectionScope.eligible_role
    def observe(scope, builder, value):
        assert builder._publication_scope is scope
        reference = PriorBuilder(value)
        assert builder.incoming == reference.incoming
        assert tuple(builder.objects) == tuple(reference.objects)
        assert all(builder.objects[key] is reference.objects[key] for key in builder.objects)
        result = eligible(scope, builder, value)
        checks.append((bool(scope.candidates), result, len(builder.objects)))
        return result
    monkeypatch.setattr(publication._SectionScope, "eligible_role", observe)
    fixed = observed_roles(fixed_factory, fixed_values, fixed_trace)
    assert fixed == original
    assert fixed[1] == tuple((role, "Counter.values") for role in fixed_values)
    assert checks and all(not candidates and not eligible and objects for candidates, eligible, objects in checks)


def test_candidate_role_still_checks_final_children_after_original_count_callback(monkeypatch):
    def fresh():
        values = roles_for(count=80)
        trace = []
        target = values["report"]["engines"]["SCALPING"]["instruments"]["0"]
        class ObserveScalarState:
            def __str__(self):
                frame = inspect.currentframe().f_back
                try:
                    while frame and frame.f_code.co_name != "_scalar":
                        frame = frame.f_back
                    count = len(frame.f_locals["self"].scalars) if frame is not None else None
                finally:
                    del frame
                trace.append(("original default=str scalar count", count))
                return str(count)
        counter = Counter(native=1)
        def callback():
            trace.append(("Counter.values",))
            target["zz_callback"] = ObserveScalarState()
            del counter.values
            return dict.values(counter)
        counter.values = callback
        values["report"]["zz_counter"] = counter
        return values, trace
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    original_values, original_trace = fresh()
    original = observed_roles(prior_factory, original_values, original_trace)
    fixed_values, fixed_trace = fresh()
    checks, eligible = [], publication._SectionScope.eligible_role
    def observe(scope, builder, value):
        assert builder._publication_scope is scope and scope.candidates
        result = eligible(scope, builder, value)
        checks.append(result)
        return result
    monkeypatch.setattr(publication._SectionScope, "eligible_role", observe)
    fixed = observed_roles(fixed_factory, fixed_values, fixed_trace)
    assert fixed == original
    assert checks and not any(checks)
    assert any(event[0] == "Counter.values" for event in fixed[1])
    assert any(event[0] == "original default=str scalar count" for event in fixed[1])


def test_candidate_builtin_roles_keep_financial_serialization_and_real_instruments_reuse(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    values = roles_for(count=80)
    values["report"]["financial"] = {"stages": Counter(native=2), "net_pnl": Decimal("-0.00")}
    prior, prior_cache, _ = prepare_prior(values)
    original, hits = publication._SectionScope.append, []
    def observe(scope, builder, value, name, buffer, *, root):
        existed = (id(value), name, root, scope.active_root) in scope.plans
        result = original(scope, builder, value, name, buffer, root=root)
        if existed and result:
            hits.append(name)
        return result
    monkeypatch.setattr(publication._SectionScope, "append", observe)
    actual, actual_cache = fixed_factory(values)
    assert "instruments" in hits
    for role, value in values.items():
        assert actual[role].sections == prior[role].sections
        assert actual[role].metrics(value) == prior[role].metrics(value)
        assert actual[role].encode(value) == prior[role].encode(value)
    assert tuple(actual_cache) == tuple(prior_cache)
    for key in actual_cache:
        assert actual_cache[key][0] is prior_cache[key][0]
        assert actual_cache[key][1] == prior_cache[key][1]
