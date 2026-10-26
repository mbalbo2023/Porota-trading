"""A captured legacy image cannot silently become migration/purge authority."""
import hashlib
import json
import sqlite3

import pytest

from scripts.rc6_history_offhost_preflight import census


def _copy(tmp_path):
    path = tmp_path / 'history.sqlite'
    with sqlite3.connect(path) as db:
        db.executescript('''
            CREATE TABLE history_versions_v2 (
              id INTEGER PRIMARY KEY, symbol TEXT, instrument_type TEXT,
              market TEXT, settlement TEXT, date TEXT, source TEXT,
              metadata_json TEXT);
            CREATE TABLE history_canonical_v2 (version_id INTEGER);
            CREATE TABLE history_canonical_v2_quarantine_rc6 (version_id INTEGER);
            CREATE TABLE history_close_versions_v1 (id INTEGER PRIMARY KEY);
            INSERT INTO history_versions_v2 VALUES
              (1,'GGAL','ACCIONES','BYMA','A-24HS','2025-01-02','PPI','{}'),
              (2,'GGAL','ACCIONES','BYMA','A-24HS','2025-01-03','PPI',
                 '{"currency":"ARS"}');
            INSERT INTO history_canonical_v2 VALUES (1),(999);
            INSERT INTO history_canonical_v2_quarantine_rc6 VALUES (2);
            INSERT INTO history_close_versions_v1 VALUES (1);
        ''')
    return path, {'source_capture': {'source_bytes': path.stat().st_size,
        'source_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}


def test_full_copy_counts_refs_but_never_certifies_ambiguous_currency(tmp_path):
    path, capture = _copy(tmp_path)
    report = census(path, capture, runner_root=tmp_path)
    assert report['legacy_versions'] == 2
    assert report['legacy_metadata_currency_candidates_unverified'] == 1
    assert report['pointers']['history_canonical_v2']['dangling_legacy_version_ids'] == 1
    assert report['pointers']['history_canonical_v2_quarantine_rc6']['rows'] == 1
    assert report['close_legacy_versions'] == 1
    assert report['cross_database_and_artifact_pins'] == 'NO_VERIFICADO'
    assert not report['migration_allowed'] and not report['retention_allowed']
    assert not report['a3_timer_unmask_allowed']


def test_capture_must_match_exact_offhost_bytes_and_root(tmp_path):
    path, capture = _copy(tmp_path)
    capture['source_capture']['source_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='OFFHOST_COPY_HASH_MISMATCH'):
        census(path, capture, runner_root=tmp_path)
    capture['source_capture']['source_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='OFFHOST_RUNNER_ROOT_REQUIRED'):
        census(path, capture, runner_root=tmp_path / 'other')
    path.with_name(path.name + '-wal').write_bytes(b'not a quiescent copy')
    with pytest.raises(ValueError, match='OFFHOST_QUIESCENT_COPY_REQUIRED'):
        census(path, capture, runner_root=tmp_path)
