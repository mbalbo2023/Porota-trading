"""Exact V2 producer layout and bounded token-allocation regressions.

The reference capture loop is frozen from product a6d622d784c03707001859d87688ddb9ba5fc4b6,
packed_storage.py Git blob 212b839c443b37bf5a8f9a860ff3736e81ed6f1b.
It keeps the prior generator/token algorithm, not the append implementation.
Scalar/named capture helpers are unchanged by this optimization. Independent
canonical JSON and public full readers also check the resulting semantics.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from rc6_shadow_runtime import packed_storage as packed, serialization


LIMITS = dict(durable_limit=32 * 1024**2, expansion_limit=64 * 1024**2)


class LegacyCaptureBuilder(packed._CaptureBuilder):
    """Prior capture loop, retained only as a byte-layout regression oracle."""
    def _parts(self, value, name="", *, root=False):
        if isinstance(value, (dict, list, tuple)) and not root and (
                self.incoming.get(id(value), 0) >= 2 or name in packed._FIELDS):
            cached = self.cache.get(id(value))
            if cached is None or cached[0] is not value:
                capture = self._named(value)
                self.cache[id(value)] = value, capture
            else:
                capture = cached[1]
            if len(capture.template) >= 64:
                yield True, capture
                return
        if isinstance(value, dict):
            yield False, packed.Capture(b"{", ())
            for ordinal, key in enumerate(sorted(value)):
                yield False, packed.Capture((b"," if ordinal else b"")+self._scalar(key)+b":", ())
                child = value[key]
                if packed._root_volatile(key):
                    yield False, packed.Capture(b"\0"+b"\0"*4, (packed._canonical(child),))
                else:
                    yield from self._parts(child, key)
            yield False, packed.Capture(b"}", ())
        elif isinstance(value, (list, tuple)):
            yield False, packed.Capture(b"[", ())
            for ordinal, child in enumerate(value):
                if ordinal:
                    yield False, packed.Capture(b",", ())
                yield from self._parts(child, name)
            yield False, packed.Capture(b"]", ())
        else:
            yield False, packed.Capture(self._scalar(value), ())

    def capture(self, value, name=""):
        if packed._root_volatile(name):
            return (packed.Capture(b"\0"+b"\0"*4, (packed._canonical(value),)),)
        result, pending, size = [], [], 0
        for boundary, capture in self._parts(value, name, root=True):
            if boundary:
                if pending:
                    result.append(packed._combined(pending)); pending.clear(); size = 0
                result.append(capture)
            else:
                if size+len(capture.template) > packed.PACK_TARGET and pending:
                    result.append(packed._combined(pending)); pending.clear(); size = 0
                pending.append(capture); size += len(capture.template)
        if pending:
            result.append(packed._combined(pending))
        return tuple(result)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      default=str, allow_nan=False).encode()


def payload(case):
    typed = [False, 0, -0.0, 0.0, None, "observación/~", "\0", "\\u0000", '"\\\n']
    if case == "typed":
        return {"rows": [{"typed": list(typed), "quantity": index} for index in range(50)]}
    if case == "volatile":
        return {"rows": [{"as_of": "2026-10-05T16:00:00.000001+00:00", "known_at": None,
            "effective_at": "2026-10-05T15:59:59.999999+00:00", "elapsed_seconds": -0.0,
            "sequence": index, "payload": {"typed": list(typed)}} for index in range(50)]}
    if case == "named":
        common = {"typed": typed, "as_of": "2026-10-05T16:00:00+00:00",
                  "rank_components": {"score": 1.25, "useful_at": None}}
        return {"rows": [common, {"distinct": True, "pipeline": deepcopy(common)}, common]}
    if case == "large_token":
        return {"rows": ["Ñ" * (packed.PACK_TARGET+5), "small", {"known_at": None}]}
    raise AssertionError(case)


@pytest.mark.parametrize("target", [5, 17, 64, 65536])
@pytest.mark.parametrize("case", ["typed", "volatile", "named", "large_token"])
def test_direct_capture_preserves_legacy_chunks_markers_and_compressed_wire(monkeypatch, target, case):
    monkeypatch.setattr(packed, "PACK_TARGET", target)
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    value = payload(case)
    expected = LegacyCaptureBuilder(value).capture(value)
    actual = packed._CaptureBuilder(value).capture(value)
    assert actual == expected  # Exact chunk, marker ordinal and literal bytes.
    raw = b"".join(packed._expanded(item, expansion_limit=LIMITS["expansion_limit"]) for item in actual)
    assert raw == canonical(value)
    native_wire = packed.encode_packed_storage(value, **LIMITS)
    with monkeypatch.context() as legacy:
        legacy.setattr(packed, "_CaptureBuilder", LegacyCaptureBuilder)
        legacy_wire = packed.encode_packed_storage(value, **LIMITS)
    assert canonical(native_wire) == canonical(legacy_wire)
    assert native_wire["logical_sha256"] == hashlib.sha256(raw).hexdigest()
    restored = serialization.decode_storage(native_wire, **LIMITS)
    assert canonical(restored) == raw


@pytest.mark.parametrize("name", ["as_of", "payload", "elapsed_seconds", "known_at"])
def test_volatile_root_sections_remain_single_exact_literal_capture(name):
    value = {"typed": [False, 0, -0.0, 0.0, "observación"], "clock": None}
    expected = LegacyCaptureBuilder(value).capture(value, name)
    actual = packed._CaptureBuilder(value).capture(value, name)
    assert actual == expected == (packed.Capture(b"\0"*5, (canonical(value),)),)


def test_plain_token_allocations_are_bounded_by_output_chunks_not_json_tokens(monkeypatch):
    rows = [{"label": f"identity-{index}", "quantity": index,
             "typed": [False, 0, -0.0, 0.0, None]} for index in range(2000)]
    value = {"rows": rows}
    constructor, calls = packed.Capture, []
    def tracked(template, literals):
        calls.append(len(template))
        return constructor(template, literals)
    monkeypatch.setattr(packed, "Capture", tracked)
    expected = LegacyCaptureBuilder(value).capture(value)
    prior_count = len(calls)
    calls.clear()
    actual = packed._CaptureBuilder(value).capture(value)
    assert actual == expected
    assert len(calls) == len(actual) < 20
    assert prior_count > 2000 * 10
    assert max(calls) <= packed.PACK_TARGET
    assert b"".join(item.template for item in actual) == canonical(value)


def test_prepared_capture_freezes_static_bytes_and_recaptures_mutable_headers(monkeypatch):
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    value = {"rows": [{"typed": [-0.0, 0.0, False, 0], "clock": None}] * 20,
             "status": "OBSERVING", "cross_payload_hashes": {"report": "old"}}
    frozen = deepcopy(value)
    prepared = packed.PreparedPackedStorage(value, mutable={"status", "cross_payload_hashes"}, **LIMITS)
    first, _ = prepared.encode(value)
    value["rows"][0]["typed"][0] = 4.0
    value["rows"].append({"injected": True})
    value.update(status="RETENTION_PRESSURE", cross_payload_hashes={"report": "new"})
    second, _ = prepared.encode(value)
    frozen.update(status=value["status"], cross_payload_hashes=value["cross_payload_hashes"])
    restored = serialization.decode_storage(second, **LIMITS)
    assert canonical(restored) == canonical(frozen)
    assert serialization.decode_storage(first, **LIMITS)["status"] == "OBSERVING"
    assert prepared.metrics(value) == (hashlib.sha256(canonical(frozen)).hexdigest(), len(canonical(frozen)))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_direct_capture_preserves_nonfinite_rejection(value):
    with pytest.raises(ValueError):
        packed.PreparedPackedStorage({"rows": [value]}, **LIMITS)


@pytest.mark.parametrize("attack", ["cycle", "depth", "nodes", "key"])
def test_direct_capture_keeps_shape_limits_before_recursion(monkeypatch, attack):
    value = {"rows": []}
    if attack == "cycle":
        value["rows"].append(value)
    elif attack == "depth":
        for _ in range(serialization.MAX_DEPTH+1):
            value = {"rows": [value]}
    elif attack == "nodes":
        monkeypatch.setattr(serialization, "MAX_NODES", 4)
        value["rows"].extend(range(5))
    elif attack == "key":
        value["rows"].append({1: "invalid"})
    with pytest.raises(ValueError, match="SHADOW_STORAGE_"):
        packed.PreparedPackedStorage(value, **LIMITS)


def test_native_worker_publication_full_reader_and_projection_keep_the_same_cut(tmp_path, monkeypatch):
    from rc6_shadow_runtime.persistence import read_committed_generation, read_committed_projection
    from tests.rc6_dashboard_native_fixture import native_fixture
    monkeypatch.setattr(serialization, "THRESHOLD", 1)
    fixture = native_fixture(tmp_path, count=25, with_future=False)
    cut = read_committed_generation(fixture.root)
    projected = read_committed_projection(fixture.root, limit=10)
    assert cut["pointer"] == fixture.cut["pointer"] == projected["pointer"]
    assert cut["report"]["phase"] == "OPEN"
    for role in ("report", "checkpoint", "status"):
        proof = cut["export_contract"]["verified_payloads"][role]
        assert proof["storage_schema"] == serialization.PACKED_SCHEMA
        raw = canonical(cut[role])
        assert proof["logical_bytes"] == len(raw)
        assert proof["payload_digest"] == hashlib.sha256(raw).hexdigest()
        assert proof["payload_digest"] == projected["export_contract"]["verified_payloads"][role]["payload_digest"]
    page = projected["dataset_pages"]["opportunities"]
    assert page["total"] == 50 and len(page["rows"]) == 10
    assert len(cut["report"]["economic_exit_lab"]["entries"]) == 1
