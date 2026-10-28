#!/usr/bin/env python3
"""RC6 host-level general backup entrypoint.

The implementation is intentionally outside Docker and excludes secrets by
default. It delegates to the audited v17 backup implementation.
"""
import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import ak_byma_calendar as calendar
from co_market_sessions_hf6 import byma_paper_spot_phase

from scripts.v17_host_general_backup import create_backup, DEFAULT_PARTS, SKIP_NAMES, SECRET_FILENAMES
from rc6_backup_policy import backups_suspended

RESERVE_BYTES = 2 * 1024**3
ARCHIVE_OVERHEAD_BYTES = 64 * 1024**2
ART = ZoneInfo('America/Argentina/Buenos_Aires')
def backup_window_admitted(now: datetime) -> bool:
    """Persistent timer catch-up cannot start a heavy copy in live market."""
    if now.tzinfo is None:
        return False
    local = now.astimezone(ART)
    if local.year not in calendar.ANIOS_AUDITADOS:
        return False
    # Service TimeoutStartSec is 60 minutes. Leave more than an hour before
    # the first 09:45 preopen preparation for a delayed catch-up trigger.
    return local.hour < 8 and byma_paper_spot_phase(local) == 'CLOSED'


def source_upper_bound(root: Path) -> int:
    """Conservative bound for staging of files the archiver actually admits."""
    total = 0
    for name in DEFAULT_PARTS:
        part = root / name
        if not part.is_dir() or part.is_symlink():
            continue
        for base, dirs, files in os.walk(part, followlinks=False):
            dirs[:] = [d for d in dirs if d not in SKIP_NAMES and not (Path(base) / d).is_symlink()]
            for name in files:
                item = Path(base) / name
                if (name not in SECRET_FILENAMES and not name.endswith(("-wal", "-shm"))
                        and item.is_file() and not item.is_symlink()):
                    total += item.stat().st_size
    for name in ("_version.py", "docker-compose.yml"):
        item = root / name
        if item.is_file() and not item.is_symlink():
            total += item.stat().st_size
    return total


def capacity_required(source_bytes: int, same_device: bool) -> tuple[int, int]:
    """Staging plus worst-case archive size, retaining the host reserve."""
    if source_bytes < 0:
        raise ValueError("NEGATIVE_BACKUP_SOURCE")
    each = source_bytes + RESERVE_BYTES + ARCHIVE_OVERHEAD_BYTES
    return (2 * source_bytes + RESERVE_BYTES + ARCHIVE_OVERHEAD_BYTES, 0) if same_device else (each, each)


def backup_capacity(source: Path, destination: Path, staging: Path) -> dict:
    source_bytes = source_upper_bound(source)
    staging_dev = staging.stat().st_dev
    dest_dev = destination.stat().st_dev
    need_stage, need_dest = capacity_required(source_bytes, staging_dev == dest_dev)
    free_stage = shutil.disk_usage(staging).free
    free_dest = shutil.disk_usage(destination).free
    return {"status": "ADMITTED" if free_stage >= need_stage and free_dest >= need_dest
            else "DEFERRED_DISK_HEADROOM", "source_upper_bound_bytes": source_bytes,
            "staging_required_bytes": need_stage, "destination_required_bytes": need_dest,
            "staging_free_bytes": free_stage, "destination_free_bytes": free_dest,
            "same_device": staging_dev == dest_dev}


def main(*, now: datetime | None = None) -> int:
    if backups_suspended():
        print(json.dumps({'backup_capacity': {'status': 'SUSPENDED_BY_OPERATOR'}}), flush=True)
        return 0
    if not backup_window_admitted(now or datetime.now(ART)):
        print(json.dumps({'backup_capacity': {'status': 'DEFERRED_MARKET_WINDOW'}}), flush=True)
        return 75
    source = Path('/opt/porota-trading')
    destination = Path('/opt/porota-backups')
    # The timer cannot stage a multi-GB live data tree and then compress an
    # archive into the same near-full filesystem. Admission runs before any
    # backup directory or temporary copy is created.
    target = destination if destination.exists() else destination.parent
    admission = backup_capacity(source, target, Path(tempfile.gettempdir()))
    print(json.dumps({"backup_capacity": admission}, sort_keys=True), flush=True)
    if admission["status"] != "ADMITTED":
        return 75
    create_backup(source, destination, include_secrets=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
