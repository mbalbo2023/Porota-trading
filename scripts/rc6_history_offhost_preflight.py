#!/usr/bin/env python3
"""Full legacy lineage census on an independently captured offhost SQLite image.

This program has no migration, retention or service control path. The live
history database is too large for the bounded OPEN probe; this census runs
only on an exact hashed image under a dedicated runner scratch directory.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote


REQUIRED_IDENTITY = {'id', 'symbol', 'instrument_type', 'market', 'settlement',
                     'date', 'source', 'metadata_json'}
POINTERS = ('history_canonical_v2', 'history_canonical_v2_quarantine_rc6')
MAX_BYTES = 2 * 1024**3


def _sha256(path: Path, check):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            check()
            digest.update(chunk)
    return digest.hexdigest()


def census(database: Path, capture: dict, *, runner_root: Path, seconds=180):
    """Verify the copy before/after the scan; never infer a legacy currency."""
    database = Path(database).absolute()
    runner_root = Path(runner_root).resolve()
    if (not runner_root.is_dir() or not database.resolve().is_relative_to(runner_root)
            or database.resolve().is_relative_to(Path('/opt/porota-trading'))):
        raise ValueError('OFFHOST_RUNNER_ROOT_REQUIRED')
    if database.is_symlink() or not database.is_file() or database.stat().st_nlink != 1:
        raise ValueError('OFFHOST_REGULAR_COPY_REQUIRED')
    if any(Path(str(database) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')):
        raise ValueError('OFFHOST_QUIESCENT_COPY_REQUIRED')
    evidence = capture.get('source_capture', capture)
    expected_bytes = evidence.get('source_bytes')
    expected_sha = evidence.get('source_sha256')
    if (type(expected_bytes) is not int or not 4096 <= expected_bytes <= MAX_BYTES
            or database.stat().st_size != expected_bytes
            or not isinstance(expected_sha, str) or len(expected_sha) != 64
            or any(ch not in '0123456789abcdef' for ch in expected_sha)):
        raise ValueError('OFFHOST_CAPTURE_IDENTITY_INVALID')
    if not isinstance(seconds, (int, float)) or not 0 < seconds <= 600:
        raise ValueError('OFFHOST_TIME_BUDGET_REQUIRED')
    deadline = time.monotonic() + seconds

    def check():
        if time.monotonic() >= deadline:
            raise TimeoutError('OFFHOST_CENSUS_TIME_BUDGET_EXHAUSTED')

    source_stat = database.stat()
    if _sha256(database, check) != expected_sha:
        raise ValueError('OFFHOST_COPY_HASH_MISMATCH')
    uri = 'file:' + quote(str(database), safe='/') + '?mode=ro&immutable=1'
    with sqlite3.connect(uri, uri=True, timeout=.2) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if 'history_versions_v2' not in tables:
            raise ValueError('OFFHOST_LEGACY_VERSIONS_MISSING')

        def columns(table):
            # All names are constants, never user supplied SQL identifiers.
            return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}

        legacy_columns = columns('history_versions_v2')
        if not REQUIRED_IDENTITY <= legacy_columns or 'currency' in legacy_columns:
            raise ValueError('OFFHOST_LEGACY_SCHEMA_MISMATCH')
        counts = Counter()
        metadata_bad = Counter()
        sources = Counter()
        family = Counter()
        for row in connection.execute(
                'SELECT metadata_json,instrument_type,source FROM history_versions_v2'):
            check()
            counts['legacy_versions'] += 1
            family[str(row[1] or 'UNKNOWN')] += 1
            sources[str(row[2] or 'UNKNOWN')] += 1
            try:
                metadata = json.loads(row[0] or '{}')
                if not isinstance(metadata, dict):
                    raise ValueError('not object')
                if metadata.get('currency'):
                    counts['currency_in_legacy_metadata_unverified'] += 1
            except (ValueError, TypeError):
                metadata_bad['invalid_json_or_object'] += 1

        pointers = {}
        for table in POINTERS:
            if table not in tables or 'version_id' not in columns(table):
                pointers[table] = {'state': 'MISSING'}
                continue
            total = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            dangling = int(connection.execute(f'''SELECT COUNT(*) FROM "{table}" AS p
                WHERE p.version_id IS NOT NULL AND NOT EXISTS
                (SELECT 1 FROM history_versions_v2 AS v WHERE v.id=p.version_id)''').fetchone()[0])
            null = int(connection.execute(f'SELECT COUNT(*) FROM "{table}" WHERE version_id IS NULL').fetchone()[0])
            pointers[table] = {'state': 'COUNTED', 'rows': total,
                               'dangling_legacy_version_ids': dangling, 'null_version_ids': null}
        close_count = None
        if 'history_close_versions_v1' in tables:
            close_count = int(connection.execute(
                'SELECT COUNT(*) FROM history_close_versions_v1').fetchone()[0])
        # This proves only the two known SQLite pointer tables; artifacts,
        # PAPER decisions, snapshots and cross-DB consumers are not certified.
        outcome = {'schema': 'RC6_OFFHOST_HISTORY_PREFLIGHT_V1',
                   'copy_sha256': expected_sha, 'copy_bytes': expected_bytes,
                   'legacy_versions': counts['legacy_versions'],
                   'legacy_metadata_currency_candidates_unverified':
                       counts['currency_in_legacy_metadata_unverified'],
                   'invalid_legacy_metadata': dict(metadata_bad),
                   'families': dict(sorted(family.items())),
                   'sources': dict(sorted(sources.items())),
                   'pointers': pointers, 'close_legacy_versions': close_count,
                   'cross_database_and_artifact_pins': 'NO_VERIFICADO',
                   'per_row_currency_provenance': 'NO_VERIFICADO',
                   'migration_allowed': False, 'retention_allowed': False,
                   'a3_timer_unmask_allowed': False, 'source_mutated': False,
                   'blockers': ['PER_ROW_CURRENCY_AND_PRICE_BASIS_PROVENANCE',
                                'PIT_VERSION_CLOCKS', 'COMPLETE_PIN_MANIFEST',
                                'INDEPENDENT_COPY_REPROJECTION_AND_RUNTIME_CHECK']}
    check()
    after_stat = database.stat()
    if ((source_stat.st_dev, source_stat.st_ino, source_stat.st_size,
         source_stat.st_mtime_ns) !=
        (after_stat.st_dev, after_stat.st_ino, after_stat.st_size,
         after_stat.st_mtime_ns) or _sha256(database, check) != expected_sha):
        raise ValueError('OFFHOST_COPY_CHANGED_DURING_CENSUS')
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--capture-report', type=Path, required=True)
    parser.add_argument('--runner-root', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=180)
    args = parser.parse_args()
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or not os.environ.get('RUNNER_TEMP')
            or args.runner_root.resolve() != Path(os.environ['RUNNER_TEMP']).resolve()):
        raise ValueError('OFFHOST_ACTION_RUNNER_REQUIRED')
    capture = json.loads(args.capture_report.read_text(encoding='utf-8'))
    print(json.dumps(census(args.database, capture, runner_root=args.runner_root,
                            seconds=args.seconds), sort_keys=True))


if __name__ == '__main__':
    main()
