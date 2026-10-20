#!/usr/bin/env python3
"""RC6 host-level general backup entrypoint.

The implementation is intentionally outside Docker and excludes secrets by
default. It delegates to the audited v17 backup implementation.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

from scripts.v17_host_general_backup import create_backup

RESERVE_BYTES = 2 * 1024**3
ARCHIVE_OVERHEAD_BYTES = 64 * 1024**2


def source_upper_bound(root: Path) -> int:
    """Conservative bound for staging, including files the archiver excludes."""
    total = 0
    for name in ("data", "sre_vector_db"):
        part = root / name
        if not part.is_dir() or part.is_symlink():
            continue
        for base, dirs, files in os.walk(part, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
            for name in files:
                item = Path(base) / name
                if item.is_file() and not item.is_symlink():
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


def main() -> int:
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
