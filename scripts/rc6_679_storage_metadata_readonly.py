#!/usr/bin/env python3
"""Bounded, metadata-only RC6 storage inventory; no content reads or mutations."""
import collections
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/opt/porota-trading')
EXPECTED = 'bc661130968f2b85b8b2146f76923ddd26eb7837'
DEADLINE = time.monotonic() + 7
MAX_FILES = 30000
SKIP_NAMES = {'.git', '.venv', '__pycache__', '.pytest_cache', '.hypothesis',
              'backups', 'restore_test'}


def inventory(paths):
    groups = collections.defaultdict(lambda: {'files': 0, 'logical': 0,
                                               'allocated': 0, 'oldest': None,
                                               'newest': None})
    exclusions = collections.defaultdict(lambda: {'files': 0, 'bytes': 0})
    large = []
    same_size = collections.defaultdict(list)
    inodes = set()
    hardlink_bytes = 0
    files_seen = 0
    truncated = False
    errors = 0
    todo = list(reversed(paths))
    while todo:
        if time.monotonic() >= DEADLINE or files_seen >= MAX_FILES:
            truncated = True
            break
        root, base = todo.pop()
        try:
            with os.scandir(root) as entries:
                children = sorted(entries, key=lambda e: e.name)
        except (OSError, PermissionError):
            errors += 1
            continue
        for entry in children:
            if time.monotonic() >= DEADLINE or files_seen >= MAX_FILES:
                truncated = True
                break
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    todo.append((Path(entry.path), base))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                stat = entry.stat(follow_symlinks=False)
            except OSError:
                errors += 1
                continue
            files_seen += 1
            item = Path(entry.path)
            rel = item.relative_to(base)
            key = ('/opt/porota-backups/' if base.name == 'porota-backups' else
                   '/opt/porota-trading/') + '/'.join(rel.parts[:min(6, len(rel.parts)-1)]
                                                 if len(rel.parts) > 1 else rel.parts)
            row = groups[key]
            row['files'] += 1
            row['logical'] += stat.st_size
            row['allocated'] += stat.st_blocks * 512
            stamp = int(stat.st_mtime)
            row['oldest'] = stamp if row['oldest'] is None else min(row['oldest'], stamp)
            row['newest'] = stamp if row['newest'] is None else max(row['newest'], stamp)
            if base == ROOT:
                reason = ('skip_subtree' if any(p in SKIP_NAMES for p in rel.parts)
                          else 'sqlite_wal_shm' if item.name.endswith(('-wal', '-shm'))
                          else 'secret' if item.name == '.env' else None)
                if reason:
                    exclusions[reason]['files'] += 1
                    exclusions[reason]['bytes'] += stat.st_size
            inode = (stat.st_dev, stat.st_ino)
            if inode in inodes:
                hardlink_bytes += stat.st_size
            inodes.add(inode)
            if stat.st_size >= 64 * 1024 * 1024:
                large.append({'path': str(item), 'bytes': stat.st_size,
                              'allocated': stat.st_blocks * 512, 'mtime': stamp})
            if stat.st_size >= 1024 * 1024 and len(same_size[stat.st_size]) < 8:
                same_size[stat.st_size].append(str(item))
        if truncated:
            break
    return {'groups': groups, 'exclusions': exclusions, 'files_seen': files_seen,
            'truncated': truncated, 'errors': errors, 'hardlink_repeat_bytes': hardlink_bytes,
            'large': sorted(large, key=lambda r: r['bytes'], reverse=True)[:40],
            'same_size_candidates_not_proven_duplicates':
                {str(size): names for size, names in sorted(same_size.items(), reverse=True)
                 if len(names) > 1 and size >= 64 * 1024 * 1024}}


def main():
    state = json.loads((ROOT / 'data/deploy/CURRENT_STATE_V2.json').read_text())
    if state.get('deploy_sha') != EXPECTED or state.get('mode') != 'PRODUCTION_PAPER' or \
       type(state.get('real_orders_sent')) is not int or state['real_orders_sent'] != 0 or \
       state.get('real_order_routes') != 'NOT_CALLED':
        raise SystemExit('RC6_STORAGE_AUDIT_DEFERRED_IDENTITY_OR_PAPER')
    start = time.monotonic()
    payload = inventory([(ROOT / 'data', ROOT), (ROOT / 'sre_vector_db', ROOT),
                         (Path('/opt/porota-backups'), Path('/opt/porota-backups'))])
    st = os.statvfs('/')
    payload.update({'schema': 'RC6_STORAGE_METADATA_RO_V1',
                    'at_utc': datetime.now(timezone.utc).isoformat(),
                    'source_sha': EXPECTED, 'free_bytes': st.f_bavail * st.f_frsize,
                    'filesystem_bytes': st.f_blocks * st.f_frsize,
                    'elapsed_seconds': round(time.monotonic() - start, 3),
                    'ppi_watch': 'NOT_ACCESSED', 'file_contents': 'NOT_READ'})
    print('RC6_STORAGE_METADATA=' + json.dumps(payload, sort_keys=True))


if __name__ == '__main__':
    main()
