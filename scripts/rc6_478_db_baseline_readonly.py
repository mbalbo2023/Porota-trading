#!/usr/bin/env python3
"""One bounded metadata sample for #478; no row scans or host writes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DB = Path('/opt/porota-trading/data/paper_v17/observer_v17.db')
BACKUPS = {
    'general': Path('/opt/porota-backups'),
    'paper': Path('/opt/porota-trading/data/paper_v17/artifacts/observer_v17.db/backups/paper'),
    'history': Path('/opt/porota-trading/data/paper_v17/artifacts/observer_v17.db/backups/history'),
}
PATTERNS = {
    'general': re.compile(r'^porota-general-.*\.tar\.gz$'),
    'paper': re.compile(r'^observer_.*\.db\.gz$'),
    'history': re.compile(r'^market_history_.*\.db\.gz$'),
}


def sample(path: Path) -> dict:
    try:
        s = os.lstat(path)
    except FileNotFoundError:
        return {'exists': False}
    if not stat.S_ISREG(s.st_mode):
        return {'exists': True, 'regular_file': False}
    return {'exists': True, 'regular_file': True, 'apparent_bytes': s.st_size,
            'allocated_bytes': s.st_blocks * 512, 'inode': s.st_ino,
            'device': s.st_dev, 'links': s.st_nlink,
            'mtime_utc': datetime.fromtimestamp(s.st_mtime, timezone.utc).isoformat()}


def backup_inventory(label: str, directory: Path) -> dict:
    try:
        with os.scandir(directory) as it:
            entries = []
            seen = 0
            for item in it:
                seen += 1
                if seen > 200:
                    return {'coverage': 'TRUNCATED_AT_200', 'count_at_least': 200}
                if not PATTERNS[label].fullmatch(item.name):
                    continue
                s = item.stat(follow_symlinks=False)
                if stat.S_ISREG(s.st_mode):
                    entries.append((s.st_size, s.st_mtime_ns, s.st_blocks * 512))
    except FileNotFoundError:
        return {'coverage': 'DIRECTORY_ABSENT', 'count': 0}
    sizes = sorted((size for size, _, _ in entries), reverse=True)
    now = time.time_ns()
    return {'coverage': 'KNOWN_PATTERN_ONE_DIRECTORY', 'count': len(entries),
            'apparent_bytes': sum(row[0] for row in entries),
            'allocated_bytes': sum(row[2] for row in entries),
            'newest_mtime_utc': (datetime.fromtimestamp(max(x[1] for x in entries) / 1e9,
                                   timezone.utc).isoformat() if entries else None),
            'count_mtime_older_7d': sum(now - x[1] >= 7 * 86400 * 10**9 for x in entries),
            'count_mtime_older_14d': sum(now - x[1] >= 14 * 86400 * 10**9 for x in entries),
            'count_mtime_older_30d': sum(now - x[1] >= 30 * 86400 * 10**9 for x in entries),
            'largest_three_apparent_bytes': sizes[:3]}


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r'[0-9a-f]{40}', sys.argv[1]):
        raise ValueError('PRODUCT_SHA_INVALID')
    if os.geteuid() != 0:
        raise ValueError('READONLY_PRIVILEGE_REQUIRED')
    begin = time.monotonic()
    now = datetime.now(timezone.utc)
    fs = os.statvfs('/')
    out = {'schema': 'RC6_DB478_METADATA_V1', 'product_sha_expected': sys.argv[1],
           'utc': now.isoformat(), 'art': now.astimezone(ZoneInfo('America/Argentina/Buenos_Aires')).isoformat(),
           'filesystem_root': {'available_bytes': fs.f_bavail * fs.f_frsize,
                               'free_inodes': fs.f_ffree, 'total_inodes': fs.f_files},
           'files_before': {suffix or 'main': sample(Path(str(DB) + suffix))
                            for suffix in ('', '-wal', '-shm', '-journal')}}
    uri = 'file:' + str(DB) + '?mode=ro'
    conn = sqlite3.connect(uri, uri=True, timeout=0.5)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA busy_timeout=500')
        p = {key: conn.execute('PRAGMA ' + key).fetchone()[0]
             for key in ('page_size', 'page_count', 'freelist_count', 'user_version', 'schema_version')}
        rows = conn.execute("SELECT type,name,tbl_name,coalesce(sql,'') FROM sqlite_master WHERE type IN ('table','index','view','trigger')").fetchall()
        kinds = {kind: sum(row[0] == kind for row in rows)
                 for kind in ('table','index','view','trigger')}
        relevant = sorted(row[1] for row in rows if row[0] == 'table' and
                          any(token in row[1].lower() for token in ('candle','history','raw','snapshot','intraday')))
        out['sqlite_metadata'] = {'pragmas': p, 'schema_objects': kinds,
            'schema_sql_sha256': hashlib.sha256('\n'.join(sorted(str(x[3]) for x in rows)).encode()).hexdigest(),
            'history_table_names': relevant[:40],
            'history_name_truncated': len(relevant) > 40,
            'page_bytes': p['page_size'] * p['page_count'],
            'freelist_reusable_bytes': p['page_size'] * p['freelist_count']}
    finally:
        conn.close()
    out['files_after'] = {suffix or 'main': sample(Path(str(DB) + suffix))
                          for suffix in ('', '-wal', '-shm', '-journal')}
    out['backup_pattern_inventory'] = {k: backup_inventory(k, v) for k, v in BACKUPS.items()}
    state = Path('/opt/porota-trading/data/deploy/CURRENT_STATE_V2.json')
    if state.is_file() and state.stat().st_size < 65536:
        receipt = json.loads(state.read_text())
        out['installed_receipt'] = {
            k: receipt.get(k) for k in ('candidate_sha','image_id','validation_status','mode','real_orders_sent')
        }
    else:
        out['installed_receipt'] = 'UNAVAILABLE'
    out['elapsed_ms'] = round((time.monotonic() - begin) * 1000)
    out['limits'] = 'metadata only; live files non-atomic; no row counts, full tree, dbstat, 7/14/30 series or deletion'
    out['side_effects'] = 'NO_INTENTIONAL_WRITE'
    print('RC6_DB478_PASSIVE_BASELINE=' + json.dumps(out, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
