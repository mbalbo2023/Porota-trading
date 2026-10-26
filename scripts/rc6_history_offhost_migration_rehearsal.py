#!/usr/bin/env python3
"""Rehearse the legacy migration on an exact private Actions runner copy.

The migrated database is destroyed after validation.  This entry point has no
host promotion, retention DELETE, provider call or timer control capability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from cu_history_store_v2_hf6 import migrate_copy
from scripts.rc6_history_offhost_preflight import census


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def rehearse(database: Path, capture: dict, *, runner_root: Path,
             currency_map=None, seconds: int = 1800) -> dict:
    """Return aggregate proof; keep neither the new DB nor a changed source."""
    if type(seconds) is not int or not 30 <= seconds <= 3600:
        raise ValueError('OFFHOST_MIGRATION_TIME_BUDGET_REQUIRED')
    started = time.monotonic()
    runner_root = Path(runner_root).resolve()
    database = Path(database).absolute()
    before = census(database, capture, runner_root=runner_root,
                    seconds=min(seconds, 600))
    source_bytes = before['copy_bytes']
    # A copy, a migration destination and index growth must coexist.  This is
    # runner space only; it does not claim that the Droplet has this capacity.
    available = os.statvfs(runner_root).f_bavail * os.statvfs(runner_root).f_frsize
    reserve = 2 * 1024**3
    required = 4 * source_bytes + reserve
    if available < required:
        raise ValueError('OFFHOST_MIGRATION_SCRATCH_CAPACITY_REQUIRED')
    remaining = seconds - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError('OFFHOST_MIGRATION_TIME_BUDGET_EXHAUSTED')
    with tempfile.TemporaryDirectory(prefix='rc6-migrate-', dir=runner_root) as tmp:
        private = Path(tmp)
        scratch = private / 'scratch'
        scratch.mkdir(mode=0o700)
        target = private / 'migrated.sqlite'
        result = migrate_copy(
            database, target, currency_map=currency_map,
            seconds=min(remaining, 3600), max_source_bytes=2 * 1024**3,
            max_rows=5_000_000, scratch_root=scratch,
            max_scratch_bytes=min(available - reserve, 2 * 1024**3))
        with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as connection:
            connection.execute('PRAGMA query_only=ON')
            if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('OFFHOST_MIGRATION_QUICK_CHECK_FAILED')
            if connection.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('OFFHOST_MIGRATION_FOREIGN_KEY_FAILED')
            old = connection.execute(
                'SELECT COUNT(*) FROM history_versions_v2_legacy').fetchone()[0]
            mapped = connection.execute(
                'SELECT COUNT(*) FROM history_migration_map_v2').fetchone()[0]
            held = connection.execute(
                'SELECT COUNT(*) FROM history_migration_quarantine_v2').fetchone()[0]
            if old != before['legacy_versions'] or mapped + held != old:
                raise ValueError('OFFHOST_MIGRATION_VERSION_ACCOUNTING_FAILED')
            # The original pointer tables must remain available until every
            # consumer and cross-database pin has a separately reviewed map.
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if 'history_canonical_v2_legacy' not in tables:
                raise ValueError('OFFHOST_MIGRATION_LEGACY_POINTERS_MISSING')
        target_bytes = target.stat().st_size
        target_sha = _hash(target)
    if _hash(database) != before['copy_sha256']:
        raise ValueError('OFFHOST_SOURCE_CHANGED_DURING_MIGRATION')
    return {
        'schema': 'RC6_OFFHOST_MIGRATION_REHEARSAL_V1',
        'source_sha256': before['copy_sha256'], 'source_bytes': source_bytes,
        'migrated_copy_sha256': target_sha, 'migrated_copy_bytes': target_bytes,
        'legacy_versions': old, 'mapped_versions': mapped,
        'quarantined_versions': held, 'close_only': result['close_only'],
        'currency_mapping_sha256': result['currency_mapping_sha256'],
        'original_source_mutated': False, 'temporary_copy_removed': True,
        'host_promotion_allowed': False, 'physical_purge_allowed': False,
        'cross_database_pins': 'NO_VERIFICADO',
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--capture-report', type=Path, required=True)
    parser.add_argument('--runner-root', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=1800)
    args = parser.parse_args()
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or
            not os.environ.get('RUNNER_TEMP') or
            args.runner_root.resolve() != Path(os.environ['RUNNER_TEMP']).resolve()):
        raise ValueError('OFFHOST_ACTION_RUNNER_REQUIRED')
    capture = json.loads(args.capture_report.read_text(encoding='utf-8'))
    print(json.dumps(rehearse(args.database, capture,
                             runner_root=args.runner_root, seconds=args.seconds),
                     sort_keys=True))


if __name__ == '__main__':
    main()
