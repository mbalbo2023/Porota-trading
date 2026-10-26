"""A disposable copy may measure deletion; source and local pins remain intact."""
from datetime import datetime, timezone
import hashlib
import sqlite3

import pytest

from scripts.rc6_history_offhost_retention_rehearsal import rehearse


def _source(tmp_path, *, bad_date=False):
    source = tmp_path / 'history.sqlite'
    with sqlite3.connect(source) as connection:
        connection.executescript('''
            CREATE TABLE history_versions_v2(
              id INTEGER PRIMARY KEY,symbol,instrument_type,market,settlement,date,
              source,metadata_json);
            CREATE TABLE history_canonical_v2(version_id INTEGER);
            CREATE TABLE history_canonical_v2_quarantine_rc6(version_id INTEGER);
            CREATE TABLE history_close_versions_v1(id INTEGER PRIMARY KEY,date TEXT);
            CREATE TABLE market_historical_ohlcv(
              symbol TEXT,date TEXT,close REAL,PRIMARY KEY(symbol,date));
            INSERT INTO history_versions_v2 VALUES
              (1,'AAA','ACCIONES','BYMA','A-24HS','2025-01-02','PPI','{}'),
              (2,'BBB','ACCIONES','BYMA','A-24HS','2026-10-22','PPI','{}');
            INSERT INTO history_canonical_v2 VALUES(1);
            INSERT INTO history_canonical_v2_quarantine_rc6 VALUES(2);
            INSERT INTO history_close_versions_v1 VALUES(1,'2025-01-02');
            INSERT INTO market_historical_ohlcv VALUES
              ('AAA','2025-01-02',10),('AAA','2026-10-22',12);
        ''')
        if bad_date:
            connection.execute("INSERT INTO market_historical_ohlcv VALUES('BBB','unknown',13)")
    return source, {'source_bytes': source.stat().st_size,
                    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}


def test_disposable_purge_reconciles_rows_and_preserves_original(tmp_path):
    source, capture = _source(tmp_path)
    report = rehearse(source, capture, runner_root=tmp_path,
                      now=datetime.now(timezone.utc), seconds=120)
    assert report['market_rows_expired'] == 1
    assert report['market_rows_retained'] == 1
    assert report['market_rows_deleted_on_disposable_copy'] == 1
    assert report['legacy_versions_expired'] == 1
    assert report['legacy_versions_expired_with_known_local_pins'] == 1
    assert report['close_versions_expired'] == 1
    assert report['disposable_copy_freelist_bytes'] >= 0
    assert report['disposable_copy_removed'] and not report['host_purge_allowed']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == capture['source_sha256']
    with sqlite3.connect(source) as c:
        assert c.execute('SELECT COUNT(*) FROM market_historical_ohlcv').fetchone()[0] == 2
    assert not list(tmp_path.glob('rc6-retention-*'))


def test_unclassifiable_date_blocks_even_disposable_delete(tmp_path):
    source, capture = _source(tmp_path, bad_date=True)
    with pytest.raises(ValueError, match='OFFHOST_MARKET_DATE_UNCLASSIFIABLE'):
        rehearse(source, capture, runner_root=tmp_path,
                 now=datetime.now(timezone.utc), seconds=120)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == capture['source_sha256']
    assert not list(tmp_path.glob('rc6-retention-*'))


def test_wrong_capture_hash_rejects_before_disposable_copy(tmp_path):
    source, capture = _source(tmp_path)
    capture['source_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='OFFHOST_COPY_HASH_MISMATCH'):
        rehearse(source, capture, runner_root=tmp_path,
                 now=datetime.now(timezone.utc), seconds=120)
    assert not list(tmp_path.glob('rc6-retention-*'))
