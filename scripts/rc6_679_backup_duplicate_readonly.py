#!/usr/bin/env python3
"""Bounded read-only proof of logical equality for exact history gzip archives."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time


ROOT = Path('/opt/porota-trading')
ARCHIVES = ROOT / 'data/paper_v17/artifacts/observer_v17.db/backups/history'
EXPECTED_SHA = 'bc661130968f2b85b8b2146f76923ddd26eb7837'
NAMES = tuple('market_history_2026-10-' + day + '.db.gz'
              for day in ('17', '18', '19', '21'))
MAX_SIZE = 128 * 1024**2
DEADLINE = time.monotonic() + 40


def payload_start(fd, size):
    header = os.pread(fd, 10, 0)
    if len(header) != 10 or header[:3] != b'\x1f\x8b\x08' or header[3] & 0xe0:
        raise RuntimeError('GZIP_HEADER_UNSUPPORTED')
    flags = header[3]
    offset = 10
    if flags & 4:
        extra_len = os.pread(fd, 2, offset)
        if len(extra_len) != 2:
            raise RuntimeError('GZIP_EXTRA_TRUNCATED')
        offset += 2 + int.from_bytes(extra_len, 'little')
    for flag in (8, 16):
        if flags & flag:
            while offset < min(size - 8, 8192):
                b = os.pread(fd, 1, offset)
                if len(b) != 1:
                    raise RuntimeError('GZIP_NAME_TRUNCATED')
                offset += 1
                if b == b'\x00':
                    break
            else:
                raise RuntimeError('GZIP_HEADER_BOUND_RED')
    if flags & 2:
        offset += 2
    if offset >= size - 8 or offset > 8192:
        raise RuntimeError('GZIP_HEADER_BOUND_RED')
    return header, offset


def main():
    state = json.loads((ROOT / 'data/deploy/CURRENT_STATE_V2.json').read_text())
    if (state.get('deploy_sha') != EXPECTED_SHA or
            state.get('mode') != 'PRODUCTION_PAPER' or
            type(state.get('real_orders_sent')) is not int or
            state['real_orders_sent'] != 0 or
            state.get('real_order_routes') != 'NOT_CALLED'):
        raise RuntimeError('PAPER_IDENTITY_GUARD_RED')
    if ARCHIVES.is_symlink() or not ARCHIVES.is_dir():
        raise RuntimeError('ARCHIVE_ROOT_INVALID')
    if sorted(p.name for p in ARCHIVES.iterdir()) != sorted(NAMES):
        raise RuntimeError('ARCHIVE_SET_DRIFT')
    rows = []
    for name in NAMES:
        if not re.fullmatch(r'market_history_2026-10-\d\d\.db\.gz', name):
            raise RuntimeError('ARCHIVE_NAME_INVALID')
        fd = os.open(ARCHIVES / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                    before.st_size < 18 or before.st_size > MAX_SIZE):
                raise RuntimeError('ARCHIVE_TYPE_OR_BOUND_RED')
            header, start = payload_start(fd, before.st_size)
            digest = hashlib.sha256()
            position = start
            end = before.st_size - 8
            while position < end:
                if time.monotonic() >= DEADLINE:
                    raise RuntimeError('ARCHIVE_READ_BUDGET_RED')
                chunk = os.pread(fd, min(1024 * 1024, end - position), position)
                if not chunk:
                    raise RuntimeError('ARCHIVE_EARLY_EOF')
                digest.update(chunk)
                position += len(chunk)
            trailer = os.pread(fd, 8, end)
            after = os.fstat(fd)
            if (len(trailer) != 8 or
                    (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                    (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
                raise RuntimeError('ARCHIVE_CHANGED_DURING_READ')
            rows.append({'date': name[15:25], 'size': before.st_size,
                         'gzip_mtime': int.from_bytes(header[4:8], 'little'),
                         'deflate_sha256': digest.hexdigest(),
                         'trailer_hex': trailer.hex()})
        finally:
            os.close(fd)
    same = len({(r['deflate_sha256'], r['trailer_hex']) for r in rows}) == 1
    print('RC6_HISTORY_GZIP_LOGICAL_EQUALITY_RO=' + json.dumps({
        'schema': 'RC6_679_GZIP_EQUALITY_RO_V1', 'candidate': EXPECTED_SHA,
        'files': rows, 'same_logical_bytes': same if same else 'NO_VERIFICADO',
        'method': 'identical_deflate_stream_and_crc_isize_trailer',
        'elapsed_seconds': round(time.monotonic() - (DEADLINE - 40), 3),
        'paper_mode': 'PRODUCTION_PAPER', 'real_orders_sent': 0,
        'ppi_watch': 'NOT_ACCESSED', 'mutations': 0}, sort_keys=True))


if __name__ == '__main__':
    main()
