"""Cheap guards for lossless work elimination within one native publication."""
from concurrent.futures import ThreadPoolExecutor
import hashlib

import pytest

from rc6_shadow_runtime import packed_storage as packed, publication_storage as publication
from rc6_shadow_runtime import serialization
from tests.test_rc6_publication_storage import (
    LIMITS, MUTABLE, assert_roles, prepare_prior, roles_for)


@pytest.mark.parametrize("case", ["native", "volatile", "dense_aliases", "alias_mismatch", "parent_dag"])
def test_private_record_reuse_preserves_every_role_wire_and_two_mutable_header_packs(monkeypatch, case):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    values = roles_for(case)
    expected, _, _ = prepare_prior(values)
    actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    memo = actual["report"]._publication_records
    assert all(instance._publication_records is memo for instance in actual.values())
    for _ in range(2):
        assert_roles(actual, expected, values)
        for role in ("report", "status"):
            values[role]["evidence_retention"] = {"status": "RETENTION_OK", "bytes": 123456, "files": 7}
            values[role]["cross_payload_hashes"] = {"report": "a" * 64, "checkpoint": "b" * 64}
    assert memo.hits > 0
    assert 0 < memo.bytes <= publication.MAX_PUBLICATION_RECORD_BYTES


def test_mutating_encoded_member_never_changes_a_later_private_record_or_role(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    values = roles_for("volatile")
    actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    first, _ = actual["report"].encode(values["report"])
    second, _ = actual["report"].encode(values["report"])
    assert first == second
    first["packets"][0]["payload"] = "ATTACK"
    third, _ = actual["report"].encode(values["report"])
    assert third == second
    assert third["packets"][0] is not second["packets"][0]


@pytest.mark.parametrize("operation", ["_record", "_compress", "_sha"])
def test_replacing_native_compression_or_digest_operation_disables_private_reuse(monkeypatch, operation):
    memo = publication._PublicationRecords()
    raw = b"native publication record" * 100
    before = memo.record(raw)
    original = getattr(packed, operation)
    calls = []
    def tracked(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(packed, operation, tracked)
    assert memo.record(raw) == before
    assert memo.hits == 0 and len(calls) > 0


def test_replacing_native_function_code_disables_private_reuse(monkeypatch):
    memo = publication._PublicationRecords()
    raw = b"native" * 100
    expected = memo.record(raw)
    original_code = packed._sha.__code__
    def different_sha(raw):
        return hashlib.sha256(raw).hexdigest()
    try:
        packed._sha.__code__ = different_sha.__code__
        assert memo.record(raw) == expected
        assert memo.hits == 0
    finally:
        packed._sha.__code__ = original_code


def test_cache_capacity_exhaustion_and_foreign_thread_are_original_fallback(monkeypatch):
    monkeypatch.setattr(publication, "MAX_PUBLICATION_RECORD_BYTES", 32)
    memo = publication._PublicationRecords()
    raw = b"native record" * 100
    expected = packed._record(raw, count=7)
    assert memo.record(raw, count=7) == expected
    assert memo.bytes == 0 and not memo.entries
    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(memo.record, raw, count=7).result(timeout=5) == expected
    assert memo.hits == 0


def test_public_and_external_cache_constructors_have_no_private_record_cache():
    value = roles_for()["report"]
    prepared = packed.PreparedPackedStorage(value, mutable=MUTABLE, cache={}, **LIMITS)
    assert not hasattr(prepared, "_publication_records")


def test_identity_edge_matching_never_invokes_equality_or_hash_of_leaf():
    class Hostile(str):
        def __eq__(self, other):
            raise AssertionError("foreign equality")
        def __hash__(self):
            raise AssertionError("foreign hash")
    # A snapshot does not admit foreign leaves in the first place. The C
    # identity comparison itself remains identity-only even on a hostile leaf.
    value = Hostile("leaf")
    assert publication._native_all(publication._native_map(publication._native_is, [value], [value]))
    assert not publication._native_all(publication._native_map(publication._native_is, [value], ["leaf"]))


@pytest.mark.parametrize("operation", ["_room", "_root_volatile", "_small_plain"])
def test_native_buffer_and_name_reuse_disable_when_original_operation_is_replaced(monkeypatch, operation):
    owner = packed._CaptureBuffer if operation == "_room" else packed
    original = getattr(owner, operation)
    events = []
    def observed(*args, **kwargs):
        events.append(operation)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, operation, observed)
    monkeypatch.setattr(publication, "MIN_FRAME_ITEMS", 1000000)
    values = roles_for("volatile")
    expected, _, _ = prepare_prior(values)
    actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert events
    assert_roles(actual, expected, values)


@pytest.mark.parametrize("target", [1, 63, 64, 257, 65536])
def test_native_room_shortcut_keeps_every_original_capture_cut(monkeypatch, target):
    monkeypatch.setattr(packed, "PACK_TARGET", target)
    values = roles_for("volatile")
    expected, _, _ = prepare_prior(values)
    actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert_roles(actual, expected, values)


def test_replacing_builtin_global_disables_private_name_and_room_path(monkeypatch):
    calls = []
    def tracked_len(value):
        calls.append(True)
        return len(value)
    monkeypatch.setattr(packed, "len", tracked_len, raising=False)
    values = roles_for()
    expected, _, _ = prepare_prior(values)
    actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert calls
    assert_roles(actual, expected, values)


def test_replacing_inherited_builtin_binding_disables_native_path_and_restores_original_callbacks():
    import builtins
    import sys
    values = roles_for()
    expected, _, _ = prepare_prior(values)
    original = builtins.len
    calls = []
    flags = []
    def tracked_len(value):
        calls.append(True)
        return original(value)
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "ENTER":
            flags.append(sys._getframe(2).f_locals["builder"]._native_field_flags)
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    builtins.len = tracked_len
    try:
        actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    finally:
        builtins.len = original
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert calls and flags and all(flag is None for flag in flags)
    assert_roles(actual, expected, values)


@pytest.mark.parametrize("cap", ["MAX_ROLE_CONTAINERS", "MAX_ROLE_REFERENCES"])
def test_native_streaming_room_and_name_path_remain_active_without_replay_admission(monkeypatch, cap):
    import sys
    monkeypatch.setattr(publication, cap, 0)
    observations = []
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "ENTER":
            builder = sys._getframe(2).f_locals["builder"]
            observations.append((builder._publication_scope, builder._native_field_flags is not None))
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    values = roles_for("volatile")
    expected, _, _ = prepare_prior(values)
    try:
        actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert observations and all(scope is None and native for scope, native in observations)
    assert_roles(actual, expected, values)


def test_replay_cap_fallback_still_rejects_foreign_role_and_keeps_original_string_callbacks(monkeypatch):
    import sys
    monkeypatch.setattr(publication, "MAX_ROLE_CONTAINERS", 0)
    events = []
    class HostileMeta(type):
        def __eq__(cls, other):
            events.append("class_equality")
            return NotImplemented
        def __hash__(cls):
            events.append("class_hash")
            return type.__hash__(cls)
    class Foreign(metaclass=HostileMeta):
        def __str__(self):
            events.append("string")
            return "original foreign value"
    values = roles_for()
    values["report"]["foreign"] = Foreign()
    expected, _, _ = prepare_prior(values)
    prior_events = list(events)
    events.clear()
    observations = []
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "ENTER":
            frame = sys._getframe(2)
            if frame.f_locals["value"] is values["report"]:
                observations.append(frame.f_locals["builder"]._native_field_flags)
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    try:
        actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert observations == [None] and events == prior_events
    assert_roles(actual, expected, values)


def test_foreign_eligibility_boolean_cannot_authorize_native_room_or_name_path(monkeypatch):
    import sys
    monkeypatch.setattr(publication._SectionScope, "eligible_role", lambda *args: True)
    flags = []
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "ENTER":
            flags.append(sys._getframe(2).f_locals["builder"]._native_field_flags)
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    try:
        publication.prepare_publication_storage(roles_for(), mutable=MUTABLE, **LIMITS)
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert flags and all(value is None for value in flags)


@pytest.mark.parametrize("cap", ["_NATIVE_FIELD_TOKEN_ENTRIES", "_NATIVE_FIELD_TOKEN_BYTES"])
def test_native_field_prefix_cache_exhaustion_keeps_original_bytes_and_capture_cuts(monkeypatch, cap):
    import sys
    monkeypatch.setattr(packed, cap, 0)
    observations = []
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "RETURN":
            builder = sys._getframe(2).f_locals["builder"]
            observations.append((builder._native_field_tokens, builder._native_field_token_bytes))
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    values = roles_for("volatile")
    expected, _, _ = prepare_prior(values)
    try:
        actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert observations and all(cache == {} and size == 0 for cache, size in observations)
    assert_roles(actual, expected, values)


def test_native_field_prefix_cache_is_owned_bounded_and_immutable_ascii(monkeypatch):
    import sys
    observations = []
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "RETURN":
            builder = sys._getframe(2).f_locals["builder"]
            observations.append((builder._native_field_tokens, builder._native_field_token_bytes))
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    values = roles_for("native")
    values["report"]["\u00d1/\0\U0001f600"] = [-0.0, 0.0, False, 0]
    expected, _, _ = prepare_prior(values)
    try:
        actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert len({id(cache) for cache, _ in observations}) == len(observations)
    assert all(len(cache) <= packed._NATIVE_FIELD_TOKEN_ENTRIES and size <= packed._NATIVE_FIELD_TOKEN_BYTES
               for cache, size in observations)
    for cache, _ in observations:
        assert all(type(name) is str and type(raw) is bytes and raw == packed._canonical(name)+b":"
                   for name, raw in cache.items())
    assert_roles(actual, expected, values)


@pytest.mark.parametrize("cap", ["_NATIVE_KEY_SCHEMAS", "_NATIVE_KEY_SCHEMA_BYTES", "_NATIVE_KEY_SCHEMA_FIELDS"])
def test_native_key_schema_cache_exhaustion_keeps_original_bytes_and_capture_cuts(monkeypatch, cap):
    import sys
    monkeypatch.setattr(packed, cap, 0)
    observations = []
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "RETURN":
            builder = sys._getframe(2).f_locals["builder"]
            observations.append((builder._native_key_schemas, builder._native_key_schema_bytes))
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    values = roles_for("volatile")
    expected, _, _ = prepare_prior(values)
    try:
        actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert observations and all(cache == {} and size == 0 for cache, size in observations)
    assert_roles(actual, expected, values)


def test_native_string_encoder_preserves_canonical_ascii_for_every_unicode_codepoint():
    # Includes control characters, non-BMP characters and unpaired surrogates.
    # ASCII JSON escapes make their exact byte identity well defined.
    value = "".join(map(chr, range(0x110000)))
    assert packed._NATIVE_STRING_ENCODER(value).encode("ascii") == packed._canonical(value)


@pytest.mark.parametrize("target", [1, 63, 65536])
def test_authenticated_native_string_values_keep_scalar_types_and_every_capture_cut(monkeypatch, target):
    monkeypatch.setattr(packed, "PACK_TARGET", target)
    monkeypatch.setattr(publication, "MAX_ROLE_CONTAINERS", 0)
    values = roles_for("volatile", count=32)
    values["report"]["string_values"] = [
        "", "\0\n\r\t\b\f\"\\/", "Ñ/\U0001f600\ud800\udfff", "native" * 100,
        *["unique-string-" + str(index) for index in range(64)],
        -0.0, 0.0, False, 0, True, 1, None]
    values["report"]["large_source_at"] = "large-binding-Ñ\ud800" * 64
    expected, _, _ = prepare_prior(values)
    actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    assert_roles(actual, expected, values)


def test_native_string_serialization_eliminates_encoder_construction_even_without_replay(monkeypatch):
    import cProfile
    monkeypatch.setattr(publication, "MAX_ROLE_CONTAINERS", 0)
    values = {"report": {"values": ["unique-value-Ñ-" + str(index) for index in range(128)]},
              "checkpoint": {"values": [False, 0, -0.0, 0.0]},
              "status": {"status": "OBSERVING"}}
    original_profile, native_profile = cProfile.Profile(), cProfile.Profile()
    expected, _, _ = original_profile.runcall(prepare_prior, values)
    actual, _ = native_profile.runcall(publication.prepare_publication_storage, values,
                                     mutable=MUTABLE, **LIMITS)
    encoder_code = packed.json.JSONEncoder.__init__.__code__
    def calls(profile):
        return sum(entry.callcount for entry in profile.getstats() if entry.code is encoder_code)
    assert calls(original_profile) - calls(native_profile) >= 128
    assert_roles(actual, expected, values)


@pytest.mark.parametrize("operation", ["canonical", "dumps", "encoder_init", "encoder_encode",
                                      "encoder_iterencode", "string_encoder", "encoder_isinstance"])
def test_foreign_json_operation_restores_original_callbacks_and_wire(monkeypatch, operation):
    import sys
    monkeypatch.setattr(publication, "MAX_ROLE_CONTAINERS", 0)
    owners = {
        "canonical": (packed, "_canonical"), "dumps": (packed.json, "dumps"),
        "encoder_init": (packed.json.JSONEncoder, "__init__"),
        "encoder_encode": (packed.json.JSONEncoder, "encode"),
        "encoder_iterencode": (packed.json.JSONEncoder, "iterencode"),
        "string_encoder": (packed.json.encoder, "encode_basestring_ascii"),
        "encoder_isinstance": (packed.json.encoder, "isinstance")}
    owner, name = owners[operation]
    original = getattr(owner, name, isinstance if name == "isinstance" else None)
    events = []
    def observed(*args, **kwargs):
        events.append(operation)
        return original(*args, **kwargs)
    monkeypatch.setattr(owner, name, observed, raising=False)
    values = roles_for("volatile", count=32)
    values["report"]["strings"] = ["unique-Ñ-" + str(index) for index in range(32)]
    expected, _, _ = prepare_prior(values)
    prior_events = list(events)
    events.clear()
    flags = []
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "ENTER":
            flags.append(sys._getframe(2).f_locals["builder"]._native_field_flags)
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    try:
        actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert flags and all(flag is None for flag in flags)
    assert events == prior_events
    assert_roles(actual, expected, values)


@pytest.mark.parametrize("getter", ["gettrace", "getprofile"])
def test_foreign_debug_getter_is_not_called_to_authorize_native_serialization(monkeypatch, getter):
    import sys
    calls, flags = [], []
    original = getattr(sys, getter)
    def observed():
        calls.append(True)
        return original()
    monkeypatch.setattr(sys, getter, observed)
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "ENTER":
            flags.append(sys._getframe(2).f_locals["builder"]._native_field_flags)
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    values = roles_for(count=32)
    expected, _, _ = prepare_prior(values)
    try:
        actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert not calls and flags and all(flag is None for flag in flags)
    assert_roles(actual, expected, values)


@pytest.mark.parametrize("mutation", ["defaults", "descriptor", "private_boolean", "private_encoder", "private_check"])
def test_mutated_json_defaults_or_descriptors_cannot_authorize_native_string_bytes(monkeypatch, mutation):
    import sys
    if mutation == "defaults":
        changed = dict(packed._canonical.__kwdefaults__)
        # Truthy int keeps the public canonical bytes, but it is not the
        # original exact-bool default authenticated by the private path.
        changed["ascii"] = 1
        monkeypatch.setattr(packed._canonical, "__kwdefaults__", changed)
    elif mutation == "descriptor":
        # A benign new class attribute still invalidates the frozen namespace.
        monkeypatch.setattr(packed.json.JSONEncoder, "native_grant", True, raising=False)
    elif mutation == "private_boolean":
        monkeypatch.setattr(publication._SectionScope, "native_fast_path", lambda self: True)
    elif mutation == "private_encoder":
        monkeypatch.setattr(packed, "_NATIVE_STRING_ENCODER", lambda value: "FORGED")
    else:
        monkeypatch.setattr(packed, "_native_role_authorized", lambda scope: True)
    flags = []
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "ENTER":
            flags.append(sys._getframe(2).f_locals["builder"]._native_field_flags)
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    values = roles_for(count=32)
    expected, _, _ = prepare_prior(values)
    try:
        actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    finally:
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert flags and all(flag is None for flag in flags)
    assert_roles(actual, expected, values)


@pytest.mark.parametrize("hook", ["profile", "trace"])
def test_python_debug_callback_disables_native_string_serialization(hook):
    import sys
    flags = []
    setter, getter = getattr(sys, "set" + hook), getattr(sys, "get" + hook)
    previous = getter()
    def debug_callback(*args):
        return debug_callback
    def observe(phase, edge):
        if phase == "CAPTURE" and edge == "ENTER":
            flags.append(sys._getframe(2).f_locals["builder"]._native_field_flags)
    values = roles_for(count=16)
    expected, _, _ = prepare_prior(values)
    token = packed._STORAGE_PHASE_OBSERVER.set(observe)
    setter(debug_callback)
    try:
        actual, _ = publication.prepare_publication_storage(values, mutable=MUTABLE, **LIMITS)
    finally:
        setter(previous)
        packed._STORAGE_PHASE_OBSERVER.reset(token)
    assert flags and all(flag is None for flag in flags)
    assert_roles(actual, expected, values)
