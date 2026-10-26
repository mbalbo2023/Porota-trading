#!/usr/bin/env python3
"""Stream one bounded SQLite online snapshot to a private Actions runner.

This is sent over SSH to ``docker exec python -``.  Only the copied database
is written to stdout; no payload or credential is printed in a job log.
"""
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

SOURCE = Path('/app/data/market_history.db')
OBSERVER = Path('/app/data/paper_v17/observer_v17.db')
RECEIPT = Path('/app/data/deploy/paper_scalping_preopen_gate_v1.json')
MAX_BYTES = 2 * 1024**3
DEADLINE = time.monotonic() + 240


def _timeout(*_):
    raise TimeoutError('RC6_CAPTURE_TIMEOUT')


signal.signal(signal.SIGTERM, _timeout)


def _admission() -> None:
    with sqlite3.connect('file:' + str(OBSERVER) + '?mode=ro', uri=True, timeout=.3) as db:
        row = db.execute(
            'SELECT mode,session_state,real_orders_sent FROM observer_state WHERE id=1'
        ).fetchone()
    if not row or row[0] != 'PRODUCTION_PAPER' or row[2] != 0:
        raise RuntimeError('RC6_CAPTURE_PAPER_SAFETY_BLOCKED')
    if row[1] != 'MARKET_CLOSED':
        try:
            receipt = json.loads(RECEIPT.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            raise RuntimeError('RC6_CAPTURE_OPEN_ADMISSION_UNKNOWN') from None
        today = datetime.now(ZoneInfo('America/Argentina/Buenos_Aires')).date().isoformat()
        if (receipt.get('status') != 'NO_GO_PAPER_SCALPING'
                or receipt.get('trade_day_ar') != today):
            raise RuntimeError('RC6_CAPTURE_OPEN_ADMISSION_NOT_BLOCKED')
    try:
        with Path('/proc/meminfo').open() as handle:
            available = next(int(line.split()[1]) * 1024 for line in handle
                             if line.startswith('MemAvailable:'))
    except (OSError, StopIteration, ValueError, IndexError):
        raise RuntimeError('RC6_CAPTURE_MEMORY_UNKNOWN') from None
    if available < 256 * 1024**2:
        raise RuntimeError('RC6_CAPTURE_MEMORY_FLOOR')


def main() -> None:
    _admission()
    source_size = SOURCE.stat().st_size
    if not 4096 <= source_size <= MAX_BYTES or SOURCE.is_symlink():
        raise RuntimeError('RC6_CAPTURE_SOURCE_INVALID')
    vfs = os.statvfs(SOURCE.parent)
    if vfs.f_bavail * vfs.f_frsize < source_size + 2 * 1024**3:
        raise RuntimeError('RC6_CAPTURE_DISK_FLOOR')
    fd, name = tempfile.mkstemp(prefix='.rc6-a102-capture-', suffix='.sqlite',
                                dir=SOURCE.parent)
    os.close(fd)
    target = Path(name)
    try:
        with sqlite3.connect('file:' + str(SOURCE) + '?mode=ro', uri=True, timeout=.3) as src:
            with sqlite3.connect(target) as dst:
                def progress(*_):
                    if time.monotonic() >= DEADLINE:
                        raise TimeoutError('RC6_CAPTURE_BACKUP_DEADLINE')
                    time.sleep(.005)
                src.backup(dst, pages=256, progress=progress, sleep=.05)
        with target.open('rb') as copied:
            while chunk := copied.read(1024 * 1024):
                if time.monotonic() >= DEADLINE:
                    raise TimeoutError('RC6_CAPTURE_STREAM_DEADLINE')
                sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        print('RC6_HISTORY_CAPTURE_STREAMED', file=sys.stderr)
    finally:
        target.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
