"""Canonical evidence compression must scale by bytes, not JSON tokens."""
import gzip
import hashlib
import json

import pytest

from rc6_dynamic_universe.common import digest
from rc6_shadow_runtime.persistence import EvidenceFiles, EvidencePayloadLimit


@pytest.mark.parametrize('kind', ('envelope', 'audit'))
@pytest.mark.parametrize('shape', ('many_tokens', 'large_string'))
def test_large_evidence_uses_bounded_compressor_calls_and_preserves_bytes(tmp_path, monkeypatch, kind, shape):
    payload = ({'rows': [{'n': n, 'label': 'José 🚀', 'valid': True} for n in range(4096)]}
               if shape == 'many_tokens' else {'text': 'José 🚀' * 20000})
    sizes = []
    original = gzip.GzipFile.write
    def observed(self, data):
        sizes.append(len(data))
        return original(self, data)
    monkeypatch.setattr(gzip.GzipFile, 'write', observed)
    files = EvidenceFiles(tmp_path / 'shadow')
    with files:
        if kind == 'envelope':
            files.write('latest.json.gz', payload)
            assert files.read('latest.json.gz') == payload
        else:
            receipt = files.write_streamed_report(payload)
            raw = gzip.decompress((files.root / receipt['file']).read_bytes())
            assert json.loads(raw) == payload
            assert hashlib.sha256(raw).hexdigest() == digest(payload) == receipt['sha256']
    # Both sparse giant strings and many tiny fields need bounded chunks;
    # punctuation must not trigger a separate zlib operation per token.
    assert len(sizes) < 128
    assert max(sizes) <= 65536


def test_envelope_serializes_large_payload_only_once(tmp_path, monkeypatch):
    payload = {'rows': list(range(4096))}
    calls = []
    original = json.JSONEncoder.iterencode
    def observed(self, value, *args, **kwargs):
        if value is payload:
            calls.append(1)
        yield from original(self, value, *args, **kwargs)
    monkeypatch.setattr(json.JSONEncoder, 'iterencode', observed)
    files = EvidenceFiles(tmp_path / 'shadow')
    with files:
        files.write('checkpoint.json.gz', payload)
        assert files.read('checkpoint.json.gz') == payload
    assert len(calls) <= 1


def test_chunked_encoder_preserves_native_json_types_unicode_and_key_coercion(tmp_path):
    from datetime import datetime, timezone
    from decimal import Decimal
    payload = {'rows': [{str(n): [None, True, -0.0, 1.25, 'ñ🚀', Decimal('1.20'),
        datetime(2026, 10, 5, tzinfo=timezone.utc)]} for n in range(600)],
        'numeric_keys': {1: 'one', 2: 'two'}, 'tuples': [(1, 2), (3, 4)]}
    expected = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False).encode()
    files = EvidenceFiles(tmp_path / 'shadow')
    with files:
        receipt = files.write_streamed_report(payload)
        assert gzip.decompress((files.root / receipt['file']).read_bytes()) == expected
        assert receipt['sha256'] == hashlib.sha256(expected).hexdigest()


def test_circular_or_invalid_json_never_replaces_committed_evidence(tmp_path):
    files = EvidenceFiles(tmp_path / 'shadow')
    cycle = []; cycle.append(cycle)
    with files:
        files.write('status.json', {'valid': 'prior'})
        before = (files.root / 'status.json').read_bytes()
        for invalid in (cycle, {'value': float('nan')}, {'mixed_keys': {1: 1, 'a': 2}}):
            with pytest.raises((ValueError, TypeError)):
                files.write('status.json', invalid)
            assert (files.root / 'status.json').read_bytes() == before
            assert not list(files.root.glob('*.tmp'))


def test_structural_chunks_match_standard_encoder_across_nested_inputs(tmp_path):
    import random
    rng = random.Random(603)
    def value(depth):
        if depth == 0:
            return rng.choice([None, True, False, -2, 0, 1.25, 'ñ🚀\\"\n', '\ud800'])
        if rng.randrange(2):
            return tuple(value(depth - 1) for _ in range(rng.randrange(4)))
        return {str(n): value(depth - 1) for n in range(rng.randrange(4))}
    payload = {'rows': [value(3) for _ in range(600)],
               'bool_keys': {False: 1, True: 2}, 'null_key': {None: 'value'},
               'float_keys': {-0.0: 'zero', 1.25: 'fraction'}}
    expected = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False).encode()
    files = EvidenceFiles(tmp_path / 'shadow')
    with files:
        receipt = files.write_streamed_report(payload)
        assert gzip.decompress((files.root / receipt['file']).read_bytes()) == expected


def test_raw_envelope_limit_is_exact_at_the_boundary(tmp_path):
    payload = {'source': 'ñ🚀' * 50}
    size = len(json.dumps({'digest': digest(payload), 'payload': payload},
                          sort_keys=True, separators=(',', ':')).encode())
    files = EvidenceFiles(tmp_path / 'accepted', payload_limit=size)
    with files:
        files.write('status.json', payload)
        assert files.read('status.json') == payload
    files = EvidenceFiles(tmp_path / 'rejected', payload_limit=size - 1)
    with files, pytest.raises(EvidencePayloadLimit):
        files.write('status.json', payload)
    assert not (files.root / 'status.json').exists()


def test_legacy_envelope_and_immutable_freeze_remain_readable(tmp_path):
    files = EvidenceFiles(tmp_path / 'shadow')
    payload = {'source_at': '2026-10-16T10:30:00-03:00', 'value': 'ñ'}
    with files:
        raw = gzip.compress(json.dumps({'digest': digest(payload), 'payload': payload}).encode())
        path = files.root / 'preopen-2026-10-19.json.gz'
        path.write_bytes(raw)
        assert files.read(path.name) == payload
        files.write(path.name, payload, immutable=True)
        assert path.read_bytes() == raw


@pytest.mark.parametrize('suffix', ('.json', '.json.gz'))
def test_oversized_stream_keeps_old_target_and_removes_staging(tmp_path, suffix):
    files = EvidenceFiles(tmp_path / 'shadow', payload_limit=700)
    name = 'checkpoint' + suffix
    with files:
        files.write(name, {'valid': 'prior'})
        before = (files.root / name).read_bytes()
        with pytest.raises(EvidencePayloadLimit):
            files.write(name, {'rows': list(range(10000))})
        assert (files.root / name).read_bytes() == before
        assert not list(files.root.glob('*.tmp'))


def test_static_size_hint_never_substitutes_cached_bytes_for_live_payload(tmp_path):
    from rc6_shadow_runtime.persistence import FrozenJSONDict
    payload=FrozenJSONDict({'value':None,'candidates':[]})
    # Even deliberately bypassing the normal immutable API cannot make the
    # writer certify stale cached bytes instead of the actual dictionary.
    dict.__setitem__(payload,'value','bypassed base-class mutation')
    expected=json.dumps(payload,sort_keys=True,separators=(',',':')).encode()
    files=EvidenceFiles(tmp_path/'shadow')
    with files:
        receipt=files.write_streamed_report(payload)
    assert gzip.decompress((files.root/receipt['file']).read_bytes())==expected
