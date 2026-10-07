"""Native publication observation controls; no material/kernel PASS receipts."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import multiprocessing as mp
import time

import pytest

from rc6_shadow_runtime import packed_storage as packed, publication_storage as publication, serialization
from scripts.rc6_issue465_stress import (
    StressResourceLimit, _publication_replay_transport_healthy,
    _require_stress_resource_gates, _storage_constructor_observation)

LIMITS = dict(durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)


class _Collector:
    def __init__(self):
        self.messages = []
        self.expected_context = (None, None, None)
        self.replay_put_attempts = 0

    def put(self, message):
        if message.get("_probe_event") == "PUBLICATION_REPLAY_COUNTERS":
            self.replay_put_attempts += 1
            assert (publication._PUBLICATION_REPLAY_OBSERVATION.get(),
                    publication._PUBLICATION_REPLAY_HEALTH.get(),
                    packed._STORAGE_PHASE_OBSERVER.get()) == self.expected_context
        self.messages.append(message)


def _shared():
    return {"healthy": True, "constructor_closes": 0, "report_puts_returned": 0}


def _shadow(sink, shared):
    # This reproduces only native observer event transport, never kernel/GREEN.
    return {"storage_publication_replay_observation_requested": True,
        "storage_publication_replay_observation_healthy": shared["healthy"] is True,
        "storage_publication_replay_constructor_closes": shared["constructor_closes"],
        "storage_publication_replay_reports_put_returned": shared["report_puts_returned"],
        "probe_event_receipts": [{"event": row["_probe_event"],
            "constructor_ordinal": row["constructor_ordinal"],
            "publication_replay_counters": row["publication_replay_counters"]}
            for row in sink.messages if row.get("_probe_event") == "PUBLICATION_REPLAY_COUNTERS"]}


def _assert_unit_veto(shadow, allow_fail_closed):
    # Boolean inputs isolate the Source predicate only. No database, custody,
    # child, artifact, image, runtime or BIG qualification is claimed here.
    receipt = {"schema": "rc6.unit-publication-observation-veto.NOT_GATE_ACCEPTANCE",
        "completion_required": not allow_fail_closed, "shadow": shadow,
        "resource_gates": {"source_database_unchanged": True, "evidence_within_quota": True,
            "rss_within_two_gib": True, "child_cleanup_completed": True,
            "complete_committed_cycle": allow_fail_closed or True,
            "actual_slow_fsync_exit_isolation": True}}
    with pytest.raises(StressResourceLimit) as failed:
        _require_stress_resource_gates(receipt, None)
    assert failed.value.evidence is receipt
    assert receipt["resource_gates"]["storage_phase_observation_healthy"] is False
    assert receipt["business_resource_complete"] is False
    assert receipt["import_proof_complete"] is False
    return receipt


def _values():
    shared = [{"identity": "row/" + str(i), "typed": [False, 0, -0.0, "Ñ/~"],
               "long_native_value": ("payload_is_not_observation_metadata/" + str(i)) * 40}
              for i in range(512)]
    return {"report": {"rows": shared, "status": "OBSERVING"},
            "checkpoint": {"rows": shared, "status": "OBSERVING"},
            "status": {"status": "OBSERVING"}}


def test_real_publication_frames_replay_and_keep_wire_cuts_aliases_order_callbacks(monkeypatch):
    values = _values()
    before = deepcopy(values)
    plain, plain_cache = publication.prepare_publication_storage(values, mutable={"status"}, **LIMITS)
    baseline = {role: plain[role].encode(value) for role, value in values.items()}
    assert all(serialization.decode_storage(wire, **LIMITS) == values[role]
               for role, (wire, _) in baseline.items())
    sink, state, errors, ordinals = _Collector(), _shared(), [], []
    actual = packed.PreparedPackedStorage.__init__
    def observed(instance, *args, **kwargs):
        ordinal = len(ordinals) + 1
        ordinals.append(ordinal)
        with _storage_constructor_observation(sink, ordinal, time.process_time(), errors,
                                              replay_observation=state):
            actual(instance, *args, **kwargs)
    monkeypatch.setattr(packed.PreparedPackedStorage, "__init__", observed)
    prepared, cache = publication.prepare_publication_storage(values, mutable={"status"}, **LIMITS)
    assert ordinals == [1, 2, 3] and errors == [] and sink.replay_put_attempts == 3
    assert list(prepared) == list(values) == ["report", "checkpoint", "status"]
    shadow = _shadow(sink, state)
    assert _publication_replay_transport_healthy(shadow)
    counts = [row["publication_replay_counters"]["counters"] for row in shadow["probe_event_receipts"]]
    assert sum(row["ROLE_ATTEMPT"] for row in counts) == 3
    assert sum(row["PLAN_STORED"] for row in counts) > 0
    assert sum(row["REPLAY_USED"] for row in counts) > 0
    for role, value in values.items():
        wire, digest = prepared[role].encode(value)
        assert (wire, digest) == baseline[role]
        assert list(wire) == list(baseline[role][0])
        assert prepared[role].sections == plain[role].sections
        assert prepared[role].metrics(value) == plain[role].metrics(value)
    assert values == before and values["report"]["rows"] is values["checkpoint"]["rows"]
    prior_events = list(sink.messages)
    values["report"]["status"] = "RETENTION_PRESSURE"
    wire, _ = prepared["report"].encode(values["report"])
    assert serialization.decode_storage(wire, **LIMITS) == values["report"]
    assert sink.messages == prior_events  # No observers are left active during encode/metrics.
    assert set(plain_cache) == set(cache)
    assert all(cache[key][0] is plain_cache[key][0] and cache[key][1] == plain_cache[key][1]
               for key in plain_cache)


def test_default_str_callback_unknown_role_and_enclosing_context_are_preserved():
    calls = []
    class Scalar:
        def __str__(self):
            calls.append("ORIGINAL_DEFAULT_STR")
            return "not_exported_to_numeric_observations"
    scalar = Scalar()
    rows = [{"n": i, "callback": scalar} for i in range(80)]
    values = {"report": {"rows": rows}, "checkpoint": {"rows": rows}}
    baseline, _ = publication.prepare_publication_storage(values, mutable=(), **LIMITS)
    baseline_wire = {role: baseline[role].encode(value) for role, value in values.items()}
    baseline_calls = list(calls)
    calls.clear()
    sink, state, errors = _Collector(), _shared(), []
    parent_observation, parent_health = object(), {"healthy": True}
    parent_phase = lambda *_: None
    tokens = (publication._PUBLICATION_REPLAY_OBSERVATION.set(parent_observation),
              publication._PUBLICATION_REPLAY_HEALTH.set(parent_health),
              packed._STORAGE_PHASE_OBSERVER.set(parent_phase))
    sink.expected_context = (parent_observation, parent_health, parent_phase)
    try:
        with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                              replay_observation=state):
            prepared, _ = publication.prepare_publication_storage(values, mutable=(), **LIMITS)
        assert publication._PUBLICATION_REPLAY_OBSERVATION.get() is parent_observation
        assert publication._PUBLICATION_REPLAY_HEALTH.get() is parent_health
        assert packed._STORAGE_PHASE_OBSERVER.get() is parent_phase
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(tokens[2])
        publication._PUBLICATION_REPLAY_HEALTH.reset(tokens[1])
        publication._PUBLICATION_REPLAY_OBSERVATION.reset(tokens[0])
    assert calls == baseline_calls and errors == [] and parent_health == {"healthy": True}
    for role, value in values.items():
        assert prepared[role].encode(value) == baseline_wire[role]
    report = sink.messages[-1]["publication_replay_counters"]
    assert report["context_reset_returned"] and report["counters"]["ROLE_CHILD_KIND"] > 0
    assert "not_exported_to_numeric_observations" not in json.dumps(report)
    assert rows[0]["callback"] is rows[1]["callback"] is scalar


def test_plain_counter_admission_and_snapshot_rejection_remain_original():
    counter = Counter({"NATIVE_COUNTER": 3})
    rows = [{"n": i, "counts": counter} for i in range(80)]
    values = {"report": {"rows": rows}, "checkpoint": {"rows": rows}}
    plain, _ = publication.prepare_publication_storage(values, mutable=(), **LIMITS)
    expected = {role: plain[role].encode(value) for role, value in values.items()}
    sink, state, errors = _Collector(), _shared(), []
    with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                          replay_observation=state):
        observed, _ = publication.prepare_publication_storage(values, mutable=(), **LIMITS)
    counts = sink.messages[-1]["publication_replay_counters"]["counters"]
    assert counts["ROLE_ACCEPTED_COUNTER_PRESENT"] > 0
    assert counts["SNAPSHOT_CHILD_KIND"] > 0 and counts["FRAME_SNAPSHOT_REJECTED"] > 0
    assert counts["REPLAY_USED"] == 0
    assert errors == [] and _publication_replay_transport_healthy(_shadow(sink, state))
    for role, value in values.items():
        assert observed[role].encode(value) == expected[role]
    assert counter == Counter({"NATIVE_COUNTER": 3}) and not counter.__dict__


def test_actual_snapshot_identity_and_original_capacity_predicates_emit_only_numeric_reasons():
    rows = [{"n": 300}, {"n": 301}]
    body = {"rows": rows}
    sink, state, errors = _Collector(), _shared(), []
    with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                          replay_observation=state):
        builder = packed._CaptureBuilder(body)
        snapshot = publication._Snapshot(builder, rows, [0, 0, 0])
        assert snapshot.valid and snapshot.matches(builder)
        rows[0]["n"] = 302
        assert not snapshot.matches(builder)
        denied = publication._Snapshot(builder, rows, [publication.MAX_SNAPSHOT_CONTAINERS, 0, 0])
        assert not denied.valid
    report = sink.messages[-1]["publication_replay_counters"]
    counts = report["counters"]
    assert counts["SNAPSHOT_ATTEMPT"] == 2 and counts["SNAPSHOT_VALID"] == 1
    assert counts["SNAPSHOT_PREFLIGHT_CAPS"] == 1 and counts["MATCH_CHILD_IDENTITY"] == 1
    assert counts["MATCH_VALID"] == 1 and errors == []
    assert set(counts) == set(publication._REPLAY_REASON_CODES)
    assert all(type(value) is int and 0 <= value <= publication._REPLAY_MAX_COUNTER for value in counts.values())
    assert report["admission_or_replay_authority"] is False
    assert report["exclusive_cost_or_material_GREEN_inferred"] is False


@pytest.mark.parametrize("allow_fail_closed", [False, True])
def test_original_business_exception_no_return_is_preserved_and_never_authorizes_green(allow_fail_closed):
    original = RuntimeError("ORIGINAL_UNIT_CAPTURE_FAILURE")
    class Scalar:
        def __str__(self):
            raise original
    sink, state, errors = _Collector(), _shared(), []
    with pytest.raises(RuntimeError) as failure:
        with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                              replay_observation=state):
            packed.PreparedPackedStorage({"callback": Scalar()}, **LIMITS)
    assert failure.value is original and errors == []
    report = sink.messages[-1]["publication_replay_counters"]
    assert report["constructor_completed"] is False and report["context_reset_returned"] is True
    assert report["observational_health"] is True  # Observation health is not business completion.
    assert publication._PUBLICATION_REPLAY_OBSERVATION.get() is None
    assert publication._PUBLICATION_REPLAY_HEALTH.get() is None
    assert packed._STORAGE_PHASE_OBSERVER.get() is None
    _assert_unit_veto(_shadow(sink, state), allow_fail_closed)


@pytest.mark.parametrize("allow_fail_closed", [False, True])
def test_real_closed_queue_report_put_fault_preserves_original_six_gates(allow_fail_closed):
    queue = mp.get_context("spawn").Queue()
    queue.close()
    class ActualQueueReportSink(_Collector):
        def put(self, message):
            if message.get("_probe_event") == "PUBLICATION_REPLAY_COUNTERS":
                self.replay_put_attempts += 1
                queue.put(message)  # Actual stdlib closed-queue ValueError.
            else:
                super().put(message)
    sink, state, errors = ActualQueueReportSink(), _shared(), []
    try:
        with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                              replay_observation=state):
            prepared = packed.PreparedPackedStorage({"n": 1}, **LIMITS)
        assert prepared.encode({"n": 1})[0] == {"n": 1}
    finally:
        queue.join_thread()
    assert sink.replay_put_attempts == 1 and errors == []
    assert state == {"healthy": False, "constructor_closes": 1, "report_puts_returned": 0}
    receipt = _assert_unit_veto(_shadow(sink, state), allow_fail_closed)
    assert list(receipt["resource_gates"]) == ["source_database_unchanged", "evidence_within_quota",
        "rss_within_two_gib", "child_cleanup_completed", "complete_committed_cycle",
        "actual_slow_fsync_exit_isolation", "storage_phase_observation_healthy"]


@pytest.mark.parametrize("allow_fail_closed", [False, True])
def test_actual_put_return_is_not_delivery_and_missing_observed_health_is_red(allow_fail_closed):
    class DroppingReportSink(_Collector):
        def put(self, message):
            if message.get("_probe_event") == "PUBLICATION_REPLAY_COUNTERS":
                self.replay_put_attempts += 1
                return  # Genuine Source emission attempt deliberately not delivered.
            super().put(message)
    sink, state, errors = DroppingReportSink(), _shared(), []
    with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                          replay_observation=state):
        packed.PreparedPackedStorage({"n": 1}, **LIMITS)
    assert sink.replay_put_attempts == state["report_puts_returned"] == 1
    assert state["healthy"] is True and errors == []
    _assert_unit_veto(_shadow(sink, state), allow_fail_closed)
    observed_missing = {"storage_publication_replay_observation_requested": True}
    _assert_unit_veto(observed_missing, allow_fail_closed)
    _assert_unit_veto({"storage_publication_replay_observation_requested": False}, allow_fail_closed)
    assert _publication_replay_transport_healthy({})  # Original unobserved unit defaults.


@pytest.mark.parametrize("allow_fail_closed", [False, True])
def test_malicious_report_metadata_failure_cannot_hide_the_independent_fault(allow_fail_closed):
    metadata_callbacks = []
    class UnsafeMetadata(type):
        @property
        def __name__(cls):
            metadata_callbacks.append("FORBIDDEN_EXCEPTION_METADATA")
            raise AssertionError("METADATA_MUST_NOT_BE_TOUCHED")
    class ObservationError(RuntimeError, metaclass=UnsafeMetadata):
        pass
    fault = ObservationError("NOT_A_BUSINESS_ERROR")
    class BrokenCounts(dict):
        def __getitem__(self, name):
            raise fault
    business = ValueError("ORIGINAL_BUSINESS_SENTINEL")
    sink, state, errors = _Collector(), _shared(), []
    with pytest.raises(ValueError) as failure:
        with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                              replay_observation=state):
            observation = publication._PUBLICATION_REPLAY_OBSERVATION.get()
            observation.counts = BrokenCounts(observation.counts)
            publication._note_replay("ROLE_ATTEMPT")
            assert observation.fault is True and state["healthy"] is False
            raise business
    assert failure.value is business and metadata_callbacks == [] and errors == []
    assert sink.replay_put_attempts == 0 and state["constructor_closes"] == 1
    assert state["report_puts_returned"] == 0
    assert publication._PUBLICATION_REPLAY_OBSERVATION.get() is None
    assert publication._PUBLICATION_REPLAY_HEALTH.get() is None
    assert packed._STORAGE_PHASE_OBSERVER.get() is None
    _assert_unit_veto(_shadow(sink, state), allow_fail_closed)


@pytest.mark.parametrize("attack", ["noncontiguous", "duplicate", "drop", "boolean_counter", "unknown_counter", "missing_health"])
def test_transport_rejects_changes_to_real_source_reports(attack):
    sink, state, errors = _Collector(), _shared(), []
    for ordinal in (1, 2):
        with _storage_constructor_observation(sink, ordinal, time.process_time(), errors,
                                              replay_observation=state):
            packed.PreparedPackedStorage({"n": ordinal}, **LIMITS)
    actual = _shadow(sink, state)
    assert errors == [] and _publication_replay_transport_healthy(actual)
    attacked = deepcopy(actual)
    rows = attacked["probe_event_receipts"]
    if attack == "noncontiguous":
        rows[1]["constructor_ordinal"] = 3
    elif attack == "duplicate":
        rows[1]["constructor_ordinal"] = 1
    elif attack == "drop":
        rows.pop()
    elif attack == "boolean_counter":
        rows[0]["publication_replay_counters"]["counters"]["ROLE_ATTEMPT"] = True
    elif attack == "unknown_counter":
        rows[0]["publication_replay_counters"]["counters"]["UNDECLARED"] = 0
    else:
        del attacked["storage_publication_replay_observation_healthy"]
    assert not _publication_replay_transport_healthy(attacked)


def test_counter_saturation_is_sticky_and_does_not_change_an_original_business_result():
    sink, state, errors = _Collector(), _shared(), []
    with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                          replay_observation=state):
        observation = publication._PUBLICATION_REPLAY_OBSERVATION.get()
        observation.counts["ROLE_ATTEMPT"] = publication._REPLAY_MAX_COUNTER
        publication._note_replay("ROLE_ATTEMPT")
        publication._note_replay("ROLE_ACCEPTED")
        prepared = packed.PreparedPackedStorage({"n": 1}, **LIMITS)
    assert prepared.encode({"n": 1})[0] == {"n": 1} and errors == []
    assert observation.fault and state["healthy"] is False
    report = sink.messages[-1]["publication_replay_counters"]
    assert report["observational_fault"] and not report["observational_health"]
    assert not report["counters_complete"] and report["counters"]["ROLE_ACCEPTED"] == 0
    assert not _publication_replay_transport_healthy(_shadow(sink, state))


@pytest.mark.parametrize("allow_fail_closed", [False, True])
@pytest.mark.parametrize("corrupt", ["string", "object"])
def test_corrupt_private_counter_never_formats_or_transports_a_value(corrupt, allow_fail_closed):
    callbacks = []
    class NoMetadataValue:
        def __str__(self):
            callbacks.append("FORBIDDEN_STR")
            raise AssertionError("OBSERVER_VALUES_MUST_NOT_BE_FORMATTED")
        def __repr__(self):
            callbacks.append("FORBIDDEN_REPR")
            raise AssertionError("OBSERVER_VALUES_MUST_NOT_BE_FORMATTED")
    value = "PRIVATE_VALUE_MUST_NOT_BE_TRANSPORTED" if corrupt == "string" else NoMetadataValue()
    sink, state, errors = _Collector(), _shared(), []
    with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                          replay_observation=state):
        observation = publication._PUBLICATION_REPLAY_OBSERVATION.get()
        observation.counts["ROLE_ATTEMPT"] = value
        prepared = packed.PreparedPackedStorage({"n": 1}, **LIMITS)
    assert prepared.encode({"n": 1})[0] == {"n": 1}
    assert observation.fault is True and errors == [] and callbacks == []
    assert state == {"healthy": False, "constructor_closes": 1, "report_puts_returned": 0}
    assert sink.replay_put_attempts == 0
    assert not any(row.get("_probe_event") == "PUBLICATION_REPLAY_COUNTERS" for row in sink.messages)
    assert publication._PUBLICATION_REPLAY_OBSERVATION.get() is None
    assert publication._PUBLICATION_REPLAY_HEALTH.get() is None
    assert packed._STORAGE_PHASE_OBSERVER.get() is None
    _assert_unit_veto(_shadow(sink, state), allow_fail_closed)


@pytest.mark.parametrize("allow_fail_closed", [False, True])
@pytest.mark.parametrize("corrupt", ["string", "object"])
def test_real_default_str_cannot_export_a_corrupt_private_fault(corrupt, allow_fail_closed):
    metadata_callbacks, business_callbacks = [], []
    class NoMetadataValue:
        def __bool__(self):
            metadata_callbacks.append("FORBIDDEN_BOOL")
            raise AssertionError("PRIVATE_METADATA_MUST_NOT_BE_COERCED")
        def __str__(self):
            metadata_callbacks.append("FORBIDDEN_STR")
            raise AssertionError("PRIVATE_METADATA_MUST_NOT_BE_COERCED")
        def __repr__(self):
            metadata_callbacks.append("FORBIDDEN_REPR")
            raise AssertionError("PRIVATE_METADATA_MUST_NOT_BE_COERCED")
        def __reduce__(self):
            metadata_callbacks.append("FORBIDDEN_REDUCE")
            raise AssertionError("PRIVATE_METADATA_MUST_NOT_BE_TRANSPORTED")
    value = "PRIVATE_FAULT_MUST_NOT_BE_TRANSPORTED" if corrupt == "string" else NoMetadataValue()
    class Scalar:
        def __str__(self):
            business_callbacks.append("ORIGINAL_DEFAULT_STR")
            publication._PUBLICATION_REPLAY_OBSERVATION.get().fault = value
            return "ORIGINAL_CALLBACK_RESULT"
    sink, state, errors = _Collector(), _shared(), []
    with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                          replay_observation=state):
        prepared = packed.PreparedPackedStorage({"callback": Scalar()}, **LIMITS)
    # No second encode of the custom input: its frozen capture is the real result.
    raw = b'{"callback":"ORIGINAL_CALLBACK_RESULT"}'
    assert prepared.metrics({"callback": None}) == (hashlib.sha256(raw).hexdigest(), len(raw))
    assert business_callbacks == ["ORIGINAL_DEFAULT_STR"] and metadata_callbacks == [] and errors == []
    assert state == {"healthy": False, "constructor_closes": 1, "report_puts_returned": 0}
    assert sink.replay_put_attempts == 0
    _assert_unit_veto(_shadow(sink, state), allow_fail_closed)


@pytest.mark.parametrize("allow_fail_closed", [False, True])
def test_private_shared_foreign_getter_is_never_called_or_used_for_report(allow_fail_closed):
    callbacks, holder = [], []
    class ForeignShared(dict):
        def __getitem__(self, key):
            callbacks.append("FORBIDDEN_SHARED_GETTER")
            holder[0].fault = "PRIVATE_VALUE_MUST_NOT_BE_TRANSPORTED"
            return True
        def __setitem__(self, key, value):
            callbacks.append("FORBIDDEN_SHARED_SETTER")
            raise AssertionError("FOREIGN_METADATA_MUST_NOT_BE_USED")
    sink, state, errors = _Collector(), _shared(), []
    with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                          replay_observation=state):
        observation = publication._PUBLICATION_REPLAY_OBSERVATION.get()
        holder.append(observation)
        observation.shared = ForeignShared(healthy=True)
        prepared = packed.PreparedPackedStorage({"n": 1}, **LIMITS)
    assert prepared.encode({"n": 1})[0] == {"n": 1}
    assert callbacks == [] and errors == [] and state["healthy"] is False
    assert sink.replay_put_attempts == 0
    _assert_unit_veto(_shadow(sink, state), allow_fail_closed)


@pytest.mark.parametrize("field", ["constructor_closes", "report_puts_returned", "healthy"])
@pytest.mark.parametrize("allow_fail_closed", [False, True])
def test_real_callback_corrupted_final_fields_export_null_and_false_only(field, allow_fail_closed):
    from scripts.rc6_issue465_stress import _publication_replay_summary
    callbacks, business_callbacks = [], []
    class NoMetadataValue:
        def __iadd__(self, other):
            callbacks.append("FORBIDDEN_COUNTER_ARITHMETIC")
            raise AssertionError("PRIVATE_METADATA_MUST_NOT_BE_COERCED")
        def __str__(self):
            callbacks.append("FORBIDDEN_STR")
            raise AssertionError("PRIVATE_METADATA_MUST_NOT_BE_COERCED")
        def __bool__(self):
            callbacks.append("FORBIDDEN_BOOL")
            raise AssertionError("PRIVATE_METADATA_MUST_NOT_BE_COERCED")
        def __reduce__(self):
            callbacks.append("FORBIDDEN_REDUCE")
            raise AssertionError("PRIVATE_METADATA_MUST_NOT_BE_TRANSPORTED")
    value = NoMetadataValue()
    sink, state, errors = _Collector(), _shared(), []
    class Scalar:
        def __str__(self):
            business_callbacks.append("ORIGINAL_DEFAULT_STR")
            publication._PUBLICATION_REPLAY_HEALTH.get()[field] = value
            return "ORIGINAL_CALLBACK_RESULT"
    with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                          replay_observation=state):
        prepared = packed.PreparedPackedStorage({"callback": Scalar()}, **LIMITS)
    summary = _publication_replay_summary(state)
    assert type(summary["healthy"]) is bool and summary["healthy"] is False
    assert all(summary[name] is None or type(summary[name]) is int for name in
               ("constructor_closes", "report_puts_returned"))
    assert callbacks == [] and business_callbacks == ["ORIGINAL_DEFAULT_STR"] and errors == []
    assert sink.replay_put_attempts == 0 and state["healthy"] is False
    assert prepared.metrics({"callback": None}) == (
        hashlib.sha256(b'{"callback":"ORIGINAL_CALLBACK_RESULT"}').hexdigest(),
        len(b'{"callback":"ORIGINAL_CALLBACK_RESULT"}'))
    shadow = {"storage_publication_replay_observation_requested": True,
        "storage_publication_replay_observation_healthy": summary["healthy"],
        "storage_publication_replay_constructor_closes": summary["constructor_closes"],
        "storage_publication_replay_reports_put_returned": summary["report_puts_returned"],
        "probe_event_receipts": []}
    assert "PRIVATE" not in json.dumps(summary)
    _assert_unit_veto(shadow, allow_fail_closed)


@pytest.mark.parametrize("kind", ["counts", "shared"])
def test_oversized_private_native_dictionary_is_rejected_before_native_copy(kind):
    import sys
    from types import BuiltinFunctionType
    from scripts.rc6_issue465_stress import _publication_replay_summary
    # Genuine native metadata maps, not mirrored results or altered business caps.
    # This control records C-level copy invocations; it claims no CPU/time saving.
    huge = {"EXTRA_PRIVATE_" + str(i): i for i in range(128)}
    if kind == "counts":
        huge.update(dict.fromkeys(publication._REPLAY_REASON_CODES, 0))
    else:
        huge.update(_shared())
    copies = []
    def native_copy_observer(frame, event, function):
        if (event == "c_call" and type(function) is BuiltinFunctionType
                and function.__self__ is huge and function.__name__ == "copy"):
            copies.append("ACTUAL_NATIVE_COPY")
    previous = sys.getprofile()
    sink, state, errors = _Collector(), _shared(), []
    sys.setprofile(native_copy_observer)
    try:
        if kind == "counts":
            with _storage_constructor_observation(sink, 1, time.process_time(), errors,
                                                  replay_observation=state):
                observation = publication._PUBLICATION_REPLAY_OBSERVATION.get()
                observation.counts = huge
                packed.PreparedPackedStorage({"n": 1}, **LIMITS)
            assert observation.fault and state["healthy"] is False and sink.replay_put_attempts == 0
        else:
            assert publication._replay_shared_snapshot(huge) is None
            publication._fail_replay_shared(huge)
            summary = _publication_replay_summary(huge)
            assert summary == {"healthy": False, "constructor_closes": None, "report_puts_returned": None}
    finally:
        sys.setprofile(previous)
    assert sys.getprofile() is previous and copies == [] and errors == []
    assert len(huge) > 41  # Full private map remains intact; no data pruning for PASS.
