#!/usr/bin/env python3
"""Measure expired history and exercise a bounded DELETE on a disposable copy.

This is a runner-only rehearsal. Other databases, raw objects, artifacts and
backups can pin these rows, so this program never emits host purge authority.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from rc6_data_retention import ART, daily_start
from scripts.rc6_history_offhost_preflight import census


def _digest_rows(connection: sqlite3.Connection, cutoff: str) -> str:
    digest = hashlib.sha256()
    for row in connection.execute(
            'SELECT * FROM market_historical_ohlcv WHERE date >= ? ORDER BY rowid',
            (cutoff,)):
        digest.update(json.dumps(row, ensure_ascii=False, separators=(',', ':'),
                                 allow_nan=False).encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()


def _market_dates(connection: sqlite3.Connection, cutoff: date,
                  today: date) -> tuple[int, int]:
    expired = retained = 0
    for (raw,) in connection.execute('SELECT date FROM market_historical_ohlcv'):
        try:
            day = date.fromisoformat(raw)
        except (TypeError, ValueError):
            raise ValueError('OFFHOST_MARKET_DATE_UNCLASSIFIABLE') from None
        if day.isoformat() != raw:
            raise ValueError('OFFHOST_MARKET_DATE_UNCLASSIFIABLE')
        if day > today:
            raise ValueError('OFFHOST_MARKET_DATE_IN_FUTURE')
        if day > cutoff:
            retained += 1
        else:
            expired += int(day < cutoff)
            retained += int(day == cutoff)
    return expired, retained


def rehearse(database: Path, capture: dict, *, runner_root: Path,
             now: datetime, seconds: int = 1200, batch: int = 1000) -> dict:
    if type(seconds) is not int or not 60 <= seconds <= 3600:
        raise ValueError('OFFHOST_RETENTION_TIME_BUDGET_REQUIRED')
    if type(batch) is not int or not 1 <= batch <= 5000:
        raise ValueError('OFFHOST_RETENTION_BATCH_BUDGET_REQUIRED')
    start = time.monotonic()
    runner_root = Path(runner_root).resolve()
    database = Path(database).absolute()
    before = census(database, capture, runner_root=runner_root,
                    seconds=min(seconds, 600))
    cutoff = daily_start(now)
    cutoff_text = cutoff.isoformat()
    if abs((datetime.now(timezone.utc) - now.astimezone(timezone.utc)).total_seconds()) > 300:
        raise ValueError('OFFHOST_RETENTION_CLOCK_UNVERIFIED')
    remaining = seconds - (time.monotonic() - start)
    if remaining <= 0:
        raise TimeoutError('OFFHOST_RETENTION_TIME_BUDGET_EXHAUSTED')
    available = os.statvfs(runner_root).f_bavail * os.statvfs(runner_root).f_frsize
    if available < before['copy_bytes'] * 2 + 2 * 1024**3:
        raise ValueError('OFFHOST_RETENTION_SCRATCH_CAPACITY_REQUIRED')
    deadline = time.monotonic() + remaining
    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('OFFHOST_RETENTION_TIME_BUDGET_EXHAUSTED')

    with tempfile.TemporaryDirectory(prefix='rc6-retention-', dir=runner_root) as tmp:
        target = Path(tmp) / 'disposable.sqlite'
        with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as source:
            source.execute('PRAGMA query_only=ON')
            source.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
            names = {row[0] for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            required = {'history_versions_v2', 'history_canonical_v2',
                        'history_canonical_v2_quarantine_rc6',
                        'market_historical_ohlcv', 'history_close_versions_v1'}
            if not required <= names:
                raise ValueError('OFFHOST_RETENTION_SCHEMA_UNVERIFIED')
            old_market, retained = _market_dates(source, cutoff, now.astimezone(ART).date())
            old_versions = source.execute(
                'SELECT COUNT(*) FROM history_versions_v2 WHERE date < ?',
                (cutoff_text,)).fetchone()[0]
            old_close = source.execute(
                'SELECT COUNT(*) FROM history_close_versions_v1 WHERE date < ?',
                (cutoff_text,)).fetchone()[0]
            pinned = source.execute('''SELECT COUNT(*) FROM history_versions_v2 AS v
                WHERE v.date < ? AND v.id IN (
                    SELECT version_id FROM history_canonical_v2 UNION
                    SELECT version_id FROM history_canonical_v2_quarantine_rc6)''',
                (cutoff_text,)).fetchone()[0]
            retained_digest = _digest_rows(source, cutoff_text)
            with sqlite3.connect(target) as copy:
                source.backup(copy, pages=256, progress=lambda *_: check())
        deleted = 0
        last_rowid = 0
        with sqlite3.connect(target) as copy:
            copy.execute('PRAGMA foreign_keys=ON')
            copy.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
            while True:
                check()
                ids = [row[0] for row in copy.execute('''SELECT rowid FROM market_historical_ohlcv
                    WHERE rowid>? AND date<? ORDER BY rowid LIMIT ?''',
                    (last_rowid, cutoff_text, batch))]
                if not ids:
                    break
                last_rowid = ids[-1]
                copy.executemany('DELETE FROM market_historical_ohlcv WHERE rowid=?',
                                 ((item,) for item in ids))
                copy.commit()
                deleted += len(ids)
            if deleted != old_market:
                raise ValueError('OFFHOST_RETENTION_DELETE_ACCOUNTING_FAILED')
            if copy.execute('SELECT COUNT(*) FROM market_historical_ohlcv').fetchone()[0] != retained:
                raise ValueError('OFFHOST_RETENTION_RETAINED_COUNT_CHANGED')
            if _digest_rows(copy, cutoff_text) != retained_digest:
                raise ValueError('OFFHOST_RETENTION_RETAINED_ROWS_CHANGED')
            if copy.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('OFFHOST_RETENTION_QUICK_CHECK_FAILED')
            if copy.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('OFFHOST_RETENTION_FOREIGN_KEY_FAILED')
            free_pages = copy.execute('PRAGMA freelist_count').fetchone()[0]
            page_size = copy.execute('PRAGMA page_size').fetchone()[0]
        target_bytes = target.stat().st_size
    # census() verified the source on entry. Rehash after every operation too.
    digest = hashlib.sha256()
    with database.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != before['copy_sha256']:
        raise ValueError('OFFHOST_RETENTION_SOURCE_CHANGED')
    return {'schema': 'RC6_OFFHOST_RETENTION_REHEARSAL_V1',
            'source_sha256': before['copy_sha256'], 'source_bytes': before['copy_bytes'],
            'cutoff_inclusive_art': cutoff_text,
            'legacy_versions_expired': old_versions,
            'legacy_versions_expired_with_known_local_pins': pinned,
            'close_versions_expired': old_close,
            'market_rows_expired': old_market, 'market_rows_retained': retained,
            'market_rows_deleted_on_disposable_copy': deleted,
            'disposable_copy_file_bytes_after_delete': target_bytes,
            'disposable_copy_freelist_bytes': free_pages * page_size,
            'disposable_copy_removed': True, 'source_mutated': False,
            'cross_database_raw_backup_and_artifact_pins': 'NO_VERIFICADO',
            'host_purge_allowed': False, 'host_migration_allowed': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--capture-report', type=Path, required=True)
    parser.add_argument('--runner-root', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=1200)
    args = parser.parse_args()
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or not os.environ.get('RUNNER_TEMP') or
            args.runner_root.resolve() != Path(os.environ['RUNNER_TEMP']).resolve()):
        raise ValueError('OFFHOST_ACTION_RUNNER_REQUIRED')
    capture = json.loads(args.capture_report.read_text(encoding='utf-8'))
    print(json.dumps(rehearse(args.database, capture, runner_root=args.runner_root,
                             now=datetime.now(timezone.utc), seconds=args.seconds), sort_keys=True))


if __name__ == '__main__':
    main()
