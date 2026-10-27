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


def _external_manifest_census(database: Path, manifest: Path, receipt: dict,
                              runner_root: Path, check):
    """Count exact archive references, never convert wrapper hints into currency."""
    manifest = Path(manifest).absolute()
    if (not manifest.resolve().is_relative_to(runner_root) or manifest == database
            or manifest.is_symlink() or not manifest.is_file()
            or manifest.stat().st_nlink != 1
            or any(Path(str(manifest) + suffix).exists()
                   for suffix in ('-wal', '-shm', '-journal'))):
        raise ValueError('OFFHOST_EXTERNAL_MANIFEST_COPY_REQUIRED')
    evidence = receipt.get('source_capture', receipt)
    size, digest = evidence.get('source_bytes'), evidence.get('source_sha256')
    if (type(size) is not int or not 4096 <= size <= 256 * 1024**2
            or manifest.stat().st_size != size or not isinstance(digest, str)
            or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('OFFHOST_EXTERNAL_MANIFEST_IDENTITY_INVALID')
    before = manifest.stat()
    if _sha256(manifest, check) != digest:
        raise ValueError('OFFHOST_EXTERNAL_MANIFEST_HASH_MISMATCH')
    uri = 'file:' + quote(str(manifest), safe='/') + '?mode=ro&immutable=1'
    counts = Counter()
    legacy_ids = set()
    digests = set()
    with sqlite3.connect(uri, uri=True, timeout=.2) as connection:
        connection.execute('PRAGMA query_only=ON')
        cols = {row[1] for row in connection.execute(
            'PRAGMA table_info(exact_attempts_v1)')}
        if not {'legacy_id', 'quality', 'inner_sha256', 'wrapper_json'} <= cols:
            raise ValueError('OFFHOST_EXTERNAL_MANIFEST_SCHEMA_MISMATCH')
        for legacy_id, quality, inner_sha, wrapper_json in connection.execute(
                'SELECT legacy_id,quality,inner_sha256,wrapper_json FROM exact_attempts_v1'):
            check()
            counts['attempts'] += 1
            if counts['attempts'] > 250000:
                raise ValueError('OFFHOST_EXTERNAL_MANIFEST_ROW_BUDGET')
            counts['quality_' + str(quality or 'UNKNOWN')] += 1
            if legacy_id is not None:
                legacy_ids.add(legacy_id)
                counts['legacy_id_references'] += 1
            if (isinstance(inner_sha, str) and len(inner_sha) == 64
                    and all(ch in '0123456789abcdef' for ch in inner_sha)):
                digests.add(inner_sha)
            else:
                counts['invalid_object_digest'] += 1
            try:
                wrapper = json.loads(wrapper_json)
                if not isinstance(wrapper, dict):
                    raise ValueError('not an object')
                if wrapper.get('currency') or (
                        isinstance(wrapper.get('metadata'), dict)
                        and wrapper['metadata'].get('currency')):
                    counts['application_currency_hints_unverified'] += 1
            except (TypeError, ValueError):
                counts['invalid_wrapper'] += 1
    # The legacy_id belongs to historical_raw_archive, never directly to a
    # history_versions_v2 revision.  Even a matching ID proves no row currency.
    history_uri = 'file:' + quote(str(database), safe='/') + '?mode=ro&immutable=1'
    matched = 0
    with sqlite3.connect(history_uri, uri=True, timeout=.2) as history:
        history.execute('PRAGMA query_only=ON')
        if history.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                           "AND name='historical_raw_archive'").fetchone():
            for (row_id,) in history.execute('SELECT id FROM historical_raw_archive'):
                check()
                matched += row_id in legacy_ids
    after = manifest.stat()
    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
        (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or _sha256(manifest, check) != digest):
        raise ValueError('OFFHOST_EXTERNAL_MANIFEST_CHANGED')
    return {'manifest_sha256': digest, 'manifest_bytes': size,
            'attempts': counts['attempts'],
            'qualities': {name[8:]: count for name, count in sorted(counts.items())
                          if name.startswith('quality_')},
            'legacy_id_references': counts['legacy_id_references'],
            'distinct_legacy_ids': len(legacy_ids),
            'matched_legacy_raw_archive_ids': matched,
            'distinct_object_digests': len(digests),
            'invalid_object_digests': counts['invalid_object_digest'],
            'invalid_wrappers': counts['invalid_wrapper'],
            'application_currency_hints_unverified':
                counts['application_currency_hints_unverified'],
            'full_object_hash_verification': 'NO_VERIFICADO',
            'direct_history_version_linkage': 'NO_VERIFICADO',
            'per_row_provider_currency': 'NO_VERIFICADO'}


def census(database: Path, capture: dict, *, runner_root: Path, seconds=180,
           external_manifest: Path | None = None, external_capture: dict | None = None):
    """Verify the copy before/after the scan; never infer a legacy currency."""
    database = Path(database).absolute()
    runner_root = Path(runner_root).resolve()
    if (external_manifest is None) != (external_capture is None):
        raise ValueError('OFFHOST_EXTERNAL_CAPTURE_PAIR_REQUIRED')
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
    if external_manifest is not None:
        outcome['external_raw_manifest'] = _external_manifest_census(
            database, external_manifest, external_capture, runner_root, check)
        outcome['blockers'].append('EXTERNAL_OBJECTS_AND_VERSION_LINKAGE')
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
    parser.add_argument('--external-manifest', type=Path)
    parser.add_argument('--external-capture-report', type=Path)
    args = parser.parse_args()
    if (os.environ.get('GITHUB_ACTIONS') != 'true' or not os.environ.get('RUNNER_TEMP')
            or args.runner_root.resolve() != Path(os.environ['RUNNER_TEMP']).resolve()):
        raise ValueError('OFFHOST_ACTION_RUNNER_REQUIRED')
    capture = json.loads(args.capture_report.read_text(encoding='utf-8'))
    external_capture = (json.loads(args.external_capture_report.read_text(encoding='utf-8'))
                        if args.external_capture_report else None)
    print(json.dumps(census(args.database, capture, runner_root=args.runner_root,
                            seconds=args.seconds, external_manifest=args.external_manifest,
                            external_capture=external_capture), sort_keys=True))


if __name__ == '__main__':
    main()
