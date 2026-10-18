"""Actual byte equivalence, role integrity, atomicity and bounded staging."""
from datetime import timedelta
from decimal import Decimal
import gzip
import hashlib
import io
import json
import os
import zlib

import pytest

from rc6_shadow_runtime.audit_spool import AuditSpool, RESERVE
from rc6_shadow_runtime.checkpoint_parts import CheckpointParts
from rc6_shadow_runtime.persistence import (EvidenceFiles, canonical_ascii_chunks,
                                          streamed_digest, _canonical_fragments)


def _payload():
    return {'as_of': '2026-10-05T13:20:00+00:00', 'session': '2026-10-05',
            'engines': {'SCALPING': {'instruments': {'a': [True, 1, -0.0, None, 'é\x00', Decimal('1.01')]}}},
            'lab': [{'data': 'ñ'*100000}], 'entry_signals': {},
            'funnel': {'reaches': {str(i): {'session': '2026-10-05', 'strategy_id': 'SCALPING',
                'identity': ['X'+str(i), 'ACCIONES', 'BYMA', 'ARS', '24HS'],
                'stages': {'CATALOG_READY': '2026-10-05T13:20:00+00:00'}} for i in range(400)}}}


def test_legacy_and_indexed_checkpoint_have_identical_standard_gzip_bytes(tmp_path):
    with EvidenceFiles(tmp_path) as files:
        files.write('checkpoint.json.gz', _payload())
        expected = gzip.decompress(files.path('checkpoint.json.gz').read_bytes())
        ordinary = json.loads(expected)['payload']
        for legacy in (True, False):
            if not legacy:
                files.write_checkpoint(_payload())
                blob = files.path('checkpoint.json.gz').read_bytes()
                assert gzip.decompress(blob) == zlib.decompress(blob, 31) == expected
            with CheckpointParts(files, canonical_ascii_chunks) as parts:
                assert set(parts.head) == {'session', 'as_of'}
                loaded = {**parts.head, **{role: parts.load(role) for role in parts.roles}}
                assert loaded == ordinary
                assert files.read('checkpoint.json.gz') == ordinary
                reaches = loaded['funnel']['reaches']
                assert reaches['0']['session'] is reaches['1']['session']
                reaches['0']['identity'][0] = 'MUTATED'
                assert reaches['1']['identity'][0] == 'X1'


@pytest.mark.parametrize('corruption', ['index', 'deflate', 'crc', 'truncate', 'role_binding'])
def test_corrupt_checkpoint_cannot_become_lazy_authority(tmp_path, corruption):
    with EvidenceFiles(tmp_path) as files:
        files.write_checkpoint(_payload())
        path = files.path('checkpoint.json.gz')
        blob = bytearray(path.read_bytes())
        if corruption == 'index':
            blob[16] = 0
        elif corruption == 'deflate':
            blob[4200] ^= 255
        elif corruption == 'crc':
            blob[-8] ^= 1
        elif corruption == 'truncate':
            del blob[-12:]
        else:
            index = json.loads(blob[16:4108].rstrip(b'\0'))
            index['roles']['lab'], index['roles']['funnel'] = index['roles']['funnel'], index['roles']['lab']
            changed = json.dumps(index, sort_keys=True, separators=(',', ':')).encode()
            blob[16:4108] = changed+b'\0'*(4092-len(changed))
        path.write_bytes(blob)
        with pytest.raises((ValueError, OSError, EOFError, zlib.error)):
            with CheckpointParts(files, canonical_ascii_chunks):
                pass


def test_changed_source_closed_reader_and_failed_atomic_write(tmp_path):
    with EvidenceFiles(tmp_path) as files:
        files.write_checkpoint(_payload())
        path = files.path('checkpoint.json.gz')
        original = path.read_bytes()
        with CheckpointParts(files, canonical_ascii_chunks) as parts:
            node = parts.roles['funnel']
            path.write_bytes(original+b'changed')
            with pytest.raises(ValueError, match='CHANGED_DURING_TICK'):
                parts.load('funnel')
        with pytest.raises(ValueError, match='PARTS_CLOSED'):
            list(node.iter_bytes())
        path.write_bytes(original)
        files.payload_limit = 100
        with pytest.raises(ValueError, match='PAYLOAD_LIMIT'):
            files.write_checkpoint(_payload())
        assert path.read_bytes() == original
        assert not list(tmp_path.glob('*.tmp'))


def test_anonymous_staging_replays_actual_bytes_and_fails_closed(tmp_path):
    with EvidenceFiles(tmp_path) as files:
        before = set(tmp_path.iterdir())
        with AuditSpool(files, maximum_bytes=1024*1024) as spool:
            payload = {'actual': ['é', -0.0, True, Decimal('1.23')]}
            node = spool.stage('actual', canonical_ascii_chunks(payload))
            assert os.fstat(spool.stream.fileno()).st_nlink == 0
            assert set(tmp_path.iterdir()) == before
            assert streamed_digest({'value': node}) == streamed_digest({'value': payload})
            with pytest.raises(ValueError, match='CANONICAL_ENCODER'):
                list(_canonical_fragments(json.JSONEncoder(ensure_ascii=False), node))
            os.pwrite(spool.stream.fileno(), b'!', node.offset)
            with pytest.raises(ValueError, match='DIGEST_MISMATCH'):
                list(node.iter_bytes())
        assert set(tmp_path.iterdir()) == before
        with pytest.raises(ValueError, match='SPOOL_CLOSED'):
            list(node.iter_bytes())


def test_staging_budget_and_disk_floor_cleanup(tmp_path, monkeypatch):
    from rc6_shadow_runtime import audit_spool
    with EvidenceFiles(tmp_path) as files:
        with pytest.raises(RuntimeError, match='producer'):
            with AuditSpool(files, maximum_bytes=100) as spool:
                spool.stage('first', [b'0'])
                raise RuntimeError('producer')
        with AuditSpool(files, maximum_bytes=100) as spool:
            with pytest.raises(ValueError, match='CAPACITY_REACHED'):
                spool.stage('too_large', [b'0'*101])
        assert list(tmp_path.iterdir()) == [tmp_path/'writer.lock']
        class LowDisk:
            free = RESERVE-1
        monkeypatch.setattr(audit_spool.shutil, 'disk_usage', lambda path: LowDisk())
        with pytest.raises(ValueError, match='DISK_ADMISSION'):
            with AuditSpool(files):
                pass


def test_four_cuts_preserve_full_audit_latest_checkpoint_and_session(tmp_path):
    from tests.test_rc6_shadow_runtime_wiring import PRE, make_store
    from rc6_shadow_runtime.worker import ShadowRuntime
    store, _ = make_store(tmp_path, count=18)
    old = ShadowRuntime(store.path, evidence_root=tmp_path/'public', source_roots=[])
    new = ShadowRuntime(store.path, evidence_root=tmp_path/'private', source_roots=[], spool_audit=True)
    for offset in (0, 30, 86400, 86430):
        ordinary, hot = old.tick(PRE+timedelta(seconds=offset)), new.tick(PRE+timedelta(seconds=offset))
        assert type(ordinary['engines']['SCALPING']['telemetry'][0]) is dict
        assert type(hot['engines']['SCALPING']['telemetry'][0]) is dict
        with old.files as a, new.files as b:
            before, after = a.read('latest.json.gz'), b.read('latest.json.gz')
            assert before == after
            assert gzip.decompress(a.path('checkpoint.json.gz').read_bytes()) == gzip.decompress(b.path('checkpoint.json.gz').read_bytes())
            full_before = gzip.decompress(a.path(before['computed_report']['file']).read_bytes())
            full_after = gzip.decompress(b.path(after['computed_report']['file']).read_bytes())
            assert full_before == full_after
            assert hashlib.sha256(full_after).hexdigest() == after['computed_report']['sha256']
        assert hot['operational_funnel'] == ordinary['operational_funnel']
        assert hot['real_orders_sent'] == 0 and hot['real_routes'] == 'NOT_CALLED'


@pytest.mark.parametrize('block_size', [1, 2, 7, 31, 65536])
def test_incremental_json_matches_stdlib_across_all_token_boundaries(block_size):
    from rc6_shadow_runtime.checkpoint_parts import _JSONBlocks, funnel_metadata_decoder
    payload = {'keys_order': {'b': 1, 'a': 2, 'unknown': 3},
               'values': [True, False, None, -0.0, -12.3e20, 1234567890123456789,
                          'é\x00\\"\ud800', ['nested', {'content': 'large'*20000}]],
               'items': [{'session': 's', 'strategy_id': 't', 'extra': i} for i in range(3)]}
    raw = json.dumps(payload, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode()
    chunks = (raw[i:i+block_size] for i in range(0, len(raw), block_size))
    parsed = _JSONBlocks(chunks, funnel_metadata_decoder()).read()
    assert parsed == payload
    assert json.dumps(parsed, ensure_ascii=True, separators=(',', ':')) == raw.decode()


@pytest.mark.parametrize('raw', [b'{"a":1,}', b'[1,]', b'{"a":01}', b'12x', b'"unterminated', b'{"a":truefalse}'])
def test_incremental_json_rejects_invalid_or_trailing_tokens(raw):
    from rc6_shadow_runtime.checkpoint_parts import _JSONBlocks, funnel_metadata_decoder
    with pytest.raises(ValueError):
        _JSONBlocks((raw[i:i+2] for i in range(0, len(raw), 2)), funnel_metadata_decoder()).read()


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-8-sig', 'utf-16', 'utf-32'])
def test_preopen_incremental_preserves_legacy_encoding_values_and_digest(tmp_path, encoding):
    payload = {'rows': [{'identity': ['É', 'ACCIONES', 'BYMA', 'ARS', '24HS'],
                        'metric': {'status': 'AVAILABLE', 'value': -0.0},
                        'text': 'ñ'*20000}]*4}
    envelope = {'payload': payload, 'digest': streamed_digest(payload)}
    raw = json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).encode(encoding)
    with EvidenceFiles(tmp_path) as files:
        path = files.path('preopen-2026-10-05.json.gz')
        path.write_bytes(gzip.compress(raw))
        normal = files.read(path.name)
        compact = files.read(path.name, compact_preopen=True)
        assert normal == compact and streamed_digest(compact) == envelope['digest']
        assert compact['rows'][0]['metric'] is compact['rows'][1]['metric']
        envelope['payload']['rows'][0]['identity'][0] = 'ALTERED'
        path.write_bytes(gzip.compress(json.dumps(envelope, ensure_ascii=False).encode(encoding)))
        with pytest.raises(ValueError, match='DIGEST_MISMATCH'):
            files.read(path.name, compact_preopen=True)


def test_stream_references_and_index_bindings_are_immutable_and_complete(tmp_path):
    from dataclasses import FrozenInstanceError
    with EvidenceFiles(tmp_path) as files:
        with AuditSpool(files, maximum_bytes=100) as spool:
            node = spool.stage('sample', [b'{}'])
            with pytest.raises(FrozenInstanceError):
                node.sha256 = '0'*64
        files.write_checkpoint(_payload())
        with CheckpointParts(files, canonical_ascii_chunks) as parts:
            with pytest.raises(TypeError):
                parts.roles['funnel'].info['raw_bytes'] = 0
        path = files.path('checkpoint.json.gz')
        raw = bytearray(path.read_bytes())
        index = json.loads(raw[16:4108].rstrip(b'\0'))
        del index['roles']['funnel']
        changed = json.dumps(index, sort_keys=True, separators=(',', ':')).encode()
        raw[16:4108] = changed+b'\0'*(4092-len(changed))
        path.write_bytes(raw)
        with pytest.raises(ValueError, match='ROLE_INDEX_INCOMPLETE'):
            with CheckpointParts(files, canonical_ascii_chunks):
                pass


def test_funnel_projection_cannot_silently_drop_a_new_consumed_field():
    import ast
    import inspect
    from rc6_shadow_runtime import funnel, worker
    tree = ast.parse(inspect.getsource(funnel._planner_stages)+inspect.getsource(funnel._strategy))
    for variable, available in [('row', worker.FUNNEL_TELEMETRY_FIELDS),
                                ('instrument', worker.FAMILY_FIELDS), ('catalog', worker.CATALOG_FIELDS)]:
        required = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == variable
                    and node.func.attr == 'get' and node.args and isinstance(node.args[0], ast.Constant)):
                required.add(node.args[0].value)
            if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                    and node.value.id == variable and isinstance(node.slice, ast.Constant)):
                required.add(node.slice.value)
        assert required <= available, (variable, required-available)


def test_legacy_normalization_checks_original_digest_before_exposing_roles(tmp_path, monkeypatch):
    from rc6_shadow_runtime import checkpoint_parts
    opened = []
    original = checkpoint_parts.tempfile.TemporaryFile
    def observed(*args, **kwargs):
        stream = original(*args, **kwargs)
        opened.append(stream)
        return stream
    monkeypatch.setattr(checkpoint_parts.tempfile, 'TemporaryFile', observed)
    with EvidenceFiles(tmp_path) as files:
        files.write('CURRENT.json', {'prior': 'safe'})
        files.write('checkpoint.json.gz', _payload())
        path = files.path('checkpoint.json.gz')
        envelope = json.loads(gzip.decompress(path.read_bytes()))
        envelope['digest'] = '0'*64
        path.write_bytes(gzip.compress(json.dumps(envelope).encode()))
        before = set(tmp_path.iterdir())
        with pytest.raises(ValueError, match='EVIDENCE_DIGEST_MISMATCH'):
            with CheckpointParts(files, canonical_ascii_chunks):
                pytest.fail('unverified legacy roles exposed')
        assert opened and all(stream.closed for stream in opened)
        assert files.read('CURRENT.json') == {'prior': 'safe'}
        assert set(tmp_path.iterdir()) == before


def test_direct_byte_replay_checks_ascii_and_joint_temporary_budget(tmp_path):
    with EvidenceFiles(tmp_path) as files:
        with AuditSpool(files, maximum_bytes=100) as spool:
            spool.external_bytes = 90
            spool.stage('valid', [b'0'*10])
            with pytest.raises(ValueError, match='CAPACITY_REACHED'):
                spool.stage('over_joint_budget', [b'0'])
        with AuditSpool(files, maximum_bytes=100) as spool:
            invalid = spool.stage('non_ascii', [b'\xff'])
            with pytest.raises(ValueError, match='NOT_ASCII'):
                files.write_streamed_report({'invalid': invalid})
        assert not list(tmp_path.glob('computed-*')) and not list(tmp_path.glob('*.tmp'))


def test_indexed_checkpoint_preserves_bounded_failure_context_and_old_target(tmp_path):
    from rc6_shadow_runtime.persistence import EvidencePayloadLimit
    with EvidenceFiles(tmp_path) as files:
        files.write_checkpoint({'old': 'verified'})
        original = files.path('checkpoint.json.gz').read_bytes()
        files.payload_limit = 100
        with pytest.raises(EvidencePayloadLimit) as failure:
            files.write_checkpoint({'large': 'x'*1000})
        assert failure.value.evidence_file == 'checkpoint.json.gz'
        assert failure.value.limit_bytes == 100 < failure.value.measured_bytes
        assert files.path('checkpoint.json.gz').read_bytes() == original
