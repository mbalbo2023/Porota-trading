"""Steady checkpoint decode must not multiply repeated immutable metadata."""
import json

import pytest

from rc6_shadow_runtime.persistence import EvidenceFiles, streamed_digest


def metadata_rows(count=256):
    return {'rows': [{'session': '2026-10-05',
                     'configuration_fingerprint': 'abcdef0123456789' * 4,
                     'registry_sha256': '1234567890abcdef' * 4,
                     'strategy_id': 'SCALPING_BASELINE', 'currency': 'ARS',
                     'identity': ['SYM' + str(i), 'ACCIONES', '24HS', 'ARS', 'BYMA'],
                     'stages': {'CATALOG_READY': '2026-10-05T13:20:00+00:00'},
                     'reasons': ['NO_RECENT_TRADES'],
                     'other': {'mutable': []}}
                    for i in range(count)]}


@pytest.mark.parametrize('name,compact', [
    ('checkpoint.json.gz', False), ('preopen-2026-10-05.json.gz', True)])
def test_repeated_checkpoint_values_have_bounded_retained_copies(tmp_path, name, compact):
    payload = metadata_rows()
    with EvidenceFiles(tmp_path/'private') as files:
        files.write(name, payload)
        actual = files.read(name, compact_preopen=compact)
    assert actual == payload
    assert streamed_digest(actual) == streamed_digest(payload)
    # These are the actual 64-character values duplicated in steady funnel
    # state, not object keys already memoized by the stdlib JSON decoder.
    for field in ('configuration_fingerprint', 'registry_sha256', 'session'):
        copies = {id(row[field]) for row in actual['rows']}
        assert len(copies) == 1, (field, len(copies))


def test_mutable_rows_and_lists_stay_independent_and_pool_does_not_survive_read(tmp_path):
    payload = metadata_rows(12)
    with EvidenceFiles(tmp_path/'private') as files:
        files.write('checkpoint.json.gz', payload)
        first = files.read('checkpoint.json.gz')
        second = files.read('checkpoint.json.gz')
    for field in ('identity', 'reasons', 'stages', 'other'):
        assert len({id(row[field]) for row in first['rows']}) == 12
    first['rows'][0]['reasons'].append('MUTATION')
    first['rows'][0]['identity'][3] = 'USD'
    assert first['rows'][1] == payload['rows'][1]
    assert second == payload
    assert (first['rows'][0]['configuration_fingerprint']
            is not second['rows'][0]['configuration_fingerprint'])


def test_compaction_keeps_exact_unicode_numeric_types_and_distinct_clocks(tmp_path):
    payload = {'rows': [{'session': 'é\x00\ud800', 'currency': 'ARS',
                         'source_at': '2026-10-05T13:20:00+00:00',
                         'received_at': '2026-10-05T13:20:01+00:00',
                         'value': value}
                        for value in (True, 1, 1.0, -0.0, '1', None)]}
    expected = json.dumps(payload, sort_keys=True, separators=(',', ':'), allow_nan=False)
    with EvidenceFiles(tmp_path/'private') as files:
        files.write('checkpoint.json.gz', payload)
        actual = files.read('checkpoint.json.gz')
    assert json.dumps(actual, sort_keys=True, separators=(',', ':'), allow_nan=False) == expected
    assert [type(r['value']) for r in actual['rows']] == [bool, int, float, float, str, type(None)]
    assert all(r['source_at'] != r['received_at'] for r in actual['rows'])


def test_new_owned_freeze_uses_same_immutable_metrics_without_changing_bytes():
    from rc6_shadow_runtime.persistence import compact_owned_preopen
    payload = {'rows': [{'identity': ['SYM'+str(i), 'ACCIONES', 'BYMA', 'ARS', '24HS'],
                         'components': {'volume': {'status': 'MISSING', 'value': None},
                                        'turnover': {'status': 'MISSING', 'value': None}}}
                        for i in range(128)]}
    before = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    result = compact_owned_preopen(payload)
    assert result is payload
    assert json.dumps(result, sort_keys=True, separators=(',', ':')) == before
    assert len({id(row['components']['volume']) for row in result['rows']}) == 1
    assert len({id(row['components']) for row in result['rows']}) == 128
    with pytest.raises(TypeError, match='IMMUTABLE_JSON_EVIDENCE'):
        result['rows'][0]['components']['volume']['value'] = 1


def test_value_pool_has_hard_entry_and_byte_bounds_without_losing_values():
    from rc6_shadow_runtime.persistence import _MetadataValuePool
    pool = _MetadataValuePool(maximum_entries=2, maximum_bytes=500)
    for i in range(1000):
        value = str(i).zfill(64)
        assert pool.share(value) == value
        assert len(pool.values) <= 2
        assert pool.charged_bytes <= 500
    oversized = 'é' * 2048
    assert pool.share(oversized) is oversized
    assert type(pool.share(True)) is bool
    assert type(pool.share(1)) is int
    assert type(pool.share(-0.0)) is float
    for kwargs in ({'maximum_entries': 4097}, {'maximum_bytes': 262145},
                   {'maximum_entries': True}, {'maximum_bytes': 0}):
        with pytest.raises(ValueError, match='BUDGET_REQUIRED'):
            _MetadataValuePool(**kwargs)


def test_different_metadata_values_and_tampered_digest_still_fail_closed(tmp_path):
    import gzip
    payload = metadata_rows(2)
    payload['rows'][1]['configuration_fingerprint'] = 'b' * 64
    with EvidenceFiles(tmp_path/'private') as files:
        files.write('checkpoint.json.gz', payload)
        actual = files.read('checkpoint.json.gz')
        assert actual == payload
        assert actual['rows'][0]['configuration_fingerprint'] != actual['rows'][1]['configuration_fingerprint']
        path = files.path('checkpoint.json.gz')
        envelope = json.loads(gzip.decompress(path.read_bytes()))
        envelope['payload']['rows'][1]['configuration_fingerprint'] = 'c' * 64
        path.write_bytes(gzip.compress(json.dumps(envelope).encode()))
        with pytest.raises(ValueError, match='SHADOW_EVIDENCE_DIGEST_MISMATCH'):
            files.read('checkpoint.json.gz')
