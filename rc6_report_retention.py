"""RC6 idempotent report retention planner.

Daily reports are never deleted unless a verified weekly/monthly consolidated
artifact exists and REPORT_RETENTION_APPLY is explicitly enabled.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
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


def compress_verified(source, destination):
    source = Path(source).resolve()
    destination = Path(destination).resolve()
    if not source.is_file() or source.is_symlink():
        raise ValueError("REPORT_SOURCE_NOT_REGULAR")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, gzip.open(destination, "wb", compresslevel=6) as dst:
        while chunk := src.read(1024 * 1024):
            dst.write(chunk)
    source_sha256 = _sha(source)
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
