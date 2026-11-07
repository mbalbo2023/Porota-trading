"""RC6 idempotent report retention planner.

Daily reports are never deleted unless a verified weekly/monthly consolidated
artifact exists and REPORT_RETENTION_APPLY is explicitly enabled.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import stat
from datetime import date, datetime
from pathlib import Path


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory(root):
    root = Path(root).resolve()
    rows = []
    for path in sorted(root.glob("*.json")) + sorted(root.glob("*.html")) + sorted(root.glob("*.log")):
        if path.is_file() and not path.is_symlink():
            stat = path.stat()
            rows.append({"path": str(path), "name": path.name,
                         "bytes": stat.st_size, "sha256": _sha(path)})
    return rows


def plan(root, *, today=None):
    root = Path(root).resolve()
    today = today or date.today()
    items = inventory(root)
    return {
        "schema": 1,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "root": str(root),
        "policy": "daily_weekdays_to_weekly; four_weeks_to_monthly",
        "apply": str(os.getenv("REPORT_RETENTION_APPLY", "false")).lower() in {"1", "true", "yes"},
        "today": today.isoformat(),
        "items": items,
        "deletion": "DISABLED_UNTIL_CONSOLIDATED_ARTIFACT_VERIFIED",
    }


def _has_symlink_parent(path):
    """Reject aliases in ancestors before resolve() erases their identity."""
    return any(parent.is_symlink() for parent in path.parents)


def compress_verified(source, destination):
    # resolve() dereferences symlinks. Reject aliases *before* resolving paths;
    # otherwise an unrelated file can be read or overwritten as a report.
    source_arg = Path(source)
    destination_arg = Path(destination)
    if source_arg.is_symlink() or _has_symlink_parent(source_arg):
        raise ValueError("REPORT_SOURCE_ALIAS")
    if destination_arg.is_symlink() or _has_symlink_parent(destination_arg):
        raise ValueError("REPORT_DESTINATION_ALIAS")
    source = source_arg.resolve()
    destination = destination_arg.resolve()
    if source == destination:
        raise ValueError("REPORT_DESTINATION_SOURCE_ALIAS")
    if not source.is_file():
        raise ValueError("REPORT_SOURCE_NOT_REGULAR")
    if source.stat().st_nlink != 1:
        raise ValueError("REPORT_SOURCE_ALIAS")
    if destination.exists():
        raise ValueError("REPORT_DESTINATION_EXISTS")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_hash = hashlib.sha256()
    # O_NOFOLLOW protects against replacement of the source between validation
    # and open. gzip 'xb' creates the archive exclusively, never truncating one.
    with os.fdopen(os.open(source, os.O_RDONLY | os.O_NOFOLLOW), "rb") as src:
        metadata = os.fstat(src.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("REPORT_SOURCE_NOT_REGULAR")
        if metadata.st_nlink != 1:
            raise ValueError("REPORT_SOURCE_ALIAS")
        try:
            with gzip.open(destination, "xb", compresslevel=6) as dst:
                while chunk := src.read(1024 * 1024):
                    source_hash.update(chunk)
                    dst.write(chunk)
        except FileExistsError as exc:
            raise ValueError("REPORT_DESTINATION_EXISTS") from exc
    source_sha256 = source_hash.hexdigest()
    # A gzip file necessarily has a different hash. Verify the *restored*
    # stream instead, without loading the whole report into memory.
    restored_hash = hashlib.sha256()
    with gzip.open(destination, "rb") as check:
        while chunk := check.read(1024 * 1024):
            restored_hash.update(chunk)
    if restored_hash.hexdigest() != source_sha256:
        raise ValueError("REPORT_COMPRESSION_VERIFY_FAILED")
    return {"source": str(source), "destination": str(destination),
            "source_sha256": source_sha256,
            "restored_sha256": restored_hash.hexdigest(),
            "verified": True,
            "compressed_bytes": destination.stat().st_size}


def apply_plan(plan_data):
    if not plan_data.get("apply"):
        return {"state": "DRY_RUN", "deleted": [], "compressed": []}
    # Consolidation is intentionally delegated to the report generator. This
    # function only permits compression after that generator creates artifacts.
    return {"state": "NO_DELETE_WITHOUT_VERIFIED_CONSOLIDATED_ARTIFACT",
            "deleted": [], "compressed": []}
