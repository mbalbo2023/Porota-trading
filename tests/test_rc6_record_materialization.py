"""Private row layout must preserve every canonical value and causal update."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import gc
import gzip
import hashlib
import json
import sys
import weakref

import pytest

from rc6_dynamic_universe.common import digest
from rc6_performance.common import canonical
from rc6_shadow_runtime.persistence import EvidenceFiles, streamed_digest, _canonical_chunks
from rc6_shadow_runtime.records import (RecordMap, RecordRegistry, json_default,
                                      record, record_context)


def test_cells_preserve_mutations_deletions_extra_fields_and_exact_json():
    ordinary = {'a': True, 'b': 1, 'c': -0.0, 'd': Decimal('1.001'), 'e': 'é\x00', 'f': None}
    packed = RecordRegistry().pack(ordinary)
    assert type(packed) is RecordMap
    for target in (ordinary, packed):
        target['a'] = False
        target['g'] = ['actual', 'values']
        del target['b']
        target.setdefault('b', 3)
        target.setdefault('f', 'must-not-replace-None')
    assert dict(packed) == ordinary and len(packed) == len(ordinary)
    expected = json.dumps(ordinary, sort_keys=True, separators=(',', ':'), default=str, allow_nan=False)
    assert streamed_digest(packed) == hashlib.sha256(expected.encode()).hexdigest()
    assert canonical(packed) == canonical(ordinary)
    assert digest(packed) == digest(ordinary)
    copied = deepcopy(packed)
    copied['g'].append('independent')
    assert copied['g'] != packed['g']
    before = digest(packed)
    packed['d'] = Decimal('1.002')
    assert digest(packed) != before
    with pytest.raises(KeyError):
        del packed['absent']


def test_shared_schema_reduces_headers_without_sharing_values_or_retaining_data():
    registry = RecordRegistry()
    ordinary = [{f'field{i}': i for i in range(50)} for _ in range(1000)]
    packed = [registry.pack(row) for row in ordinary]
    assert len(registry.schemas) == 1
    assert len({id(row._schema) for row in packed}) == 1
    assert len({id(row._cells) for row in packed}) == 1000
    packed[0]['field1'] = 'changed'
    assert packed[1]['field1'] == 1 and ordinary[0]['field1'] == 1
    with pytest.raises(TypeError):
        packed[0]._schema.index['other'] = 1
    headers_before = sum(sys.getsizeof(row) for row in ordinary)
    headers_after = sum(sys.getsizeof(row)+sys.getsizeof(row._cells) for row in packed)
    assert headers_after + registry.charged_bytes < .5 * headers_before
    class Value:
        pass
    value = Value()
    reference = weakref.ref(value)
    payload = {'a': value, 'b': None, 'c': None, 'd': None}
    row = registry.pack(payload)
    del row, payload, value
    gc.collect()
    assert reference() is None


def test_registry_stays_bounded_and_falls_back_without_omitting_unknown_shapes():
    registry = RecordRegistry()
    for index in range(1000):
        row = {f'key{index}_{field}': index for field in range(4)}
        packed = registry.pack(row)
        assert dict(packed) == row
        assert len(registry.schemas) <= registry.MAX_SCHEMAS
        assert registry.charged_bytes <= registry.MAX_BYTES
    assert type(registry.pack({'x'*129: 1, 'b': 2, 'c': 3, 'd': 4})) is dict
    assert type(registry.pack({str(i): i for i in range(129)})) is dict


def test_record_context_never_leaks_into_public_calls_or_after_failure():
    payload = {'a': 1, 'b': 2, 'c': 3, 'd': 4}
    with pytest.raises(RuntimeError):
        with record_context():
            assert type(record(payload)) is RecordMap
            with record_context(False):
                assert type(record(payload)) is dict
            assert type(record(payload)) is RecordMap
            raise RuntimeError('injected producer failure')
    assert type(record(payload)) is dict


def test_large_record_streams_actual_values_and_detects_cycles_nonfinite_values():
    registry = RecordRegistry()
    row = registry.pack({'a': list(range(20000)), 'b': 'é', 'c': 1, 'd': -0.0})
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), default=json_default, allow_nan=False)
    chunks = list(_canonical_chunks(encoder, row))
    assert max(map(len, chunks)) <= 65536
    assert b''.join(chunks) == json.dumps(row.as_dict(), sort_keys=True, separators=(',', ':')).encode()
    row['a'] = row
    with pytest.raises(ValueError, match='Circular'):
        streamed_digest(row)
    row['a'] = float('nan')
    with pytest.raises(ValueError):
        streamed_digest(row)


def test_checkpoint_decoder_and_writer_keep_original_envelope_and_detect_tamper(tmp_path):
    payload = {'reaches': {str(index): {'identity': ['X', 'ACCIONES', '24HS', 'ARS', 'BYMA'],
                'stages': {'CATALOG_READY': '2026-10-05T13:20:00+00:00'},
                'session': '2026-10-05', 'strategy_id': 'BASELINE', 'reasons': []}
                for index in range(100)}}
    with EvidenceFiles(tmp_path/'evidence') as files:
        files.write('checkpoint.json.gz', payload)
        public = files.read('checkpoint.json.gz')
        private = files.read('checkpoint.json.gz', compact_records=True)
        assert type(public['reaches']['0']) is dict
        assert type(private['reaches']['0']) is RecordMap
        assert canonical(private) == canonical(public)
        files.write('checkpoint.json.gz', private)
        assert files.read('checkpoint.json.gz') == public
        path = files.path('checkpoint.json.gz')
        envelope = json.loads(gzip.decompress(path.read_bytes()))
        envelope['payload']['reaches']['0']['session'] = '2026-10-06'
        path.write_bytes(gzip.compress(json.dumps(envelope).encode()))
        with pytest.raises(ValueError, match='SHADOW_EVIDENCE_DIGEST_MISMATCH'):
            files.read('checkpoint.json.gz', compact_records=True)
        with pytest.raises(ValueError, match='SHADOW_COMPACT_CHECKPOINT_ROLE_REQUIRED'):
            files.read('checkpoint.json.gz', compact_records='truthy')


def test_four_worker_cuts_match_public_api_full_hash_checkpoint_and_sessions(tmp_path):
    from tests.test_rc6_shadow_runtime_wiring import PRE, make_store
    from rc6_shadow_runtime.worker import ShadowRuntime
    from rc6_shadow_runtime.persistence import read_committed_generation
    store, _ = make_store(tmp_path, count=18)
    before = ShadowRuntime(store.path, evidence_root=tmp_path/'public', source_roots=[])
    after = ShadowRuntime(store.path, evidence_root=tmp_path/'private', source_roots=[], compact_records=True)
    for offset in (0, 30, 86400, 86430):
        at = PRE + timedelta(seconds=offset)
        ordinary, compact = before.tick(at), after.tick(at)
        assert type(ordinary['engines']['SCALPING']['telemetry'][0]) is dict
        assert type(compact['engines']['SCALPING']['telemetry'][0]) is RecordMap
        assert canonical(compact) == canonical(ordinary)
        a, b = read_committed_generation(before.root), read_committed_generation(after.root)
        assert a['report']['computed_report']['sha256'] == b['report']['computed_report']['sha256']
        with before.files as files:
            old_state = files.read('checkpoint.json.gz')
        with after.files as files:
            new_state = files.read('checkpoint.json.gz')
        assert canonical(new_state) == canonical(old_state)
        assert compact['real_routes'] == 'NOT_CALLED' and compact['real_orders_sent'] == 0
