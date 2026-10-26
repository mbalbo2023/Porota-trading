"""The offhost rehearsal accounts for every legacy revision without a host apply."""
import hashlib
import sqlite3

import pytest

from scripts.rc6_history_offhost_migration_rehearsal import rehearse


def _legacy(tmp_path):
    source = tmp_path / 'source.sqlite'
    with sqlite3.connect(source) as connection:
        connection.executescript('''
            CREATE TABLE history_versions_v2(
              id INTEGER PRIMARY KEY,symbol,instrument_type,market,settlement,date,
              open,high,low,close,volume,source,adjusted,observed_at,
              payload_hash,metadata_json);
            CREATE TABLE history_canonical_v2(
              symbol,instrument_type,market,settlement,date,close,version_id);
            CREATE TABLE history_canonical_v2_quarantine_rc6(version_id INTEGER);
            INSERT INTO history_versions_v2 VALUES
              (1,'AAA','ACCIONES','BYMA','A-24HS','2026-09-30',100,102,98,
               100,1000,'PPI_API',0,'2026-10-01T16:00:00Z','h1',
               '{"currency":"ARS"}'),
              (2,'BBB','ACCIONES','BYMA','A-24HS','2026-09-30',100,102,98,
               100,1000,'PPI_API',0,'2026-10-01T16:00:00Z','h2','{}');
            INSERT INTO history_canonical_v2 VALUES
              ('AAA','ACCIONES','BYMA','A-24HS','2026-09-30',100,1);
            INSERT INTO history_canonical_v2_quarantine_rc6 VALUES(2);
        ''')
    return source, {'source_bytes': source.stat().st_size,
                    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}


def test_rehearsal_preserves_source_and_quarantines_unproved_currency(tmp_path):
    source, capture = _legacy(tmp_path)
    report = rehearse(source, capture, runner_root=tmp_path, seconds=60)
    assert report['legacy_versions'] == 2
    assert report['mapped_versions'] == 1
    assert report['quarantined_versions'] == 1
    assert report['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report['temporary_copy_removed']
    assert not report['host_promotion_allowed'] and not report['physical_purge_allowed']
    assert not list(tmp_path.glob('rc6-migrate-*'))


def test_rehearsal_rejects_changed_source_before_migration(tmp_path):
    source, capture = _legacy(tmp_path)
    capture['source_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='OFFHOST_COPY_HASH_MISMATCH'):
        rehearse(source, capture, runner_root=tmp_path, seconds=60)
    assert not list(tmp_path.glob('rc6-migrate-*'))
