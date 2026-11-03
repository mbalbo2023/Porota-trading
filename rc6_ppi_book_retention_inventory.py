"""A574: bounded OFFHOST census of a *verified copy* of native PPI Book PIT.

This is a separate, non-destructive inventory of the A556 private journal.
It does not prove provider authenticity, consumer coverage, runtime state or
365-day physical retention, and it cannot authorize deletion/rotation.
Legacy history_versions_v2 IDs are a different graph: do not backfill their
missing lineage/PIT with newer intraday Book evidence.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from rc6_data_retention import ART, daily_start
from rc6_ppi_scanner_provenance import (
    PIT_EVENT_MAX_BYTES, PIT_JOURNAL_MAX_BYTES, PIT_JOURNAL_MAX_RECORDS,
    PIT_SESSION_MAX_BYTES, PIT_SESSION_MAX_RECORDS, _aware, _pit_bytes,
    verified_native_book_journal,
)


SCHEMA = "rc6.ppi-native-book-retention-offhost-inventory.v1"
RETENTION_DAYS = 365
# Safety contract: no opaque claim of pin completeness may change this state.
PIN_SCOPE = "PIN_SCOPE_UNKNOWN"
MAX_PRIVATE_COPY_BYTES = PIT_JOURNAL_MAX_BYTES


def _hold(reason: str) -> dict[str, Any]:
    return {"schema": SCHEMA, "state": "HOLD", "reason": reason,
            "pin_scope": PIN_SCOPE, "source_mutated": False,
            "host_purge_allowed": False, "physical_host_delete_count": 0,
            "physical_bytes_reclaimed": 0, "provider_calls": 0,
            "real_orders_sent": 0, "predeploy_executed": False,
            "provider_authenticity_verified": False,
            "legacy_596196_pit_backfill_authorized": False}


def _private_copy_under_root(path: Path, root: Path) -> bool:
    """Reject any production path, aliases or root-wide filesystem inspection."""
    if (not path.is_absolute() or not root.is_absolute() or root.is_symlink()
            or not root.is_dir() or path.is_symlink() or path == root
            or root == Path("/") or root.is_relative_to(Path("/opt/porota-trading"))
            or path.is_relative_to(Path("/opt/porota-trading"))):
        return False
    try:
        if root.stat().st_mode & 0o077:
            # Only a dedicated private runner directory is an acceptable root.
            return False
        if not path.resolve(strict=True).is_relative_to(root.resolve(strict=True)):
            return False
        # O_NOFOLLOW protects the final file. Also forbid aliases through any
        # parent component between the trusted runner root and the file.
        for parent in path.parents:
            if parent == root:
                break
            if parent.is_symlink():
                return False
        return True
    except (OSError, RuntimeError):
        return False


def _copy_fingerprint(path: Path) -> tuple[int, str]:
    """Independent bounded byte hash after the journal's SHA-chain check."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_mode & 0o077
                or not 0 < before.st_size <= MAX_PRIVATE_COPY_BYTES):
            raise ValueError("PRIVATE_JOURNAL_PERMISSION_OR_CAPACITY_HOLD")
        digest = hashlib.sha256()
        received = 0
        while True:
            chunk = os.read(fd, 64 * 1024)
            if not chunk:
                break
            received += len(chunk)
            if received > MAX_PRIVATE_COPY_BYTES:
                raise ValueError("PRIVATE_JOURNAL_CAPACITY_HOLD")
            digest.update(chunk)
        after = os.fstat(fd)
        if (received != before.st_size or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise ValueError("PRIVATE_JOURNAL_CHANGED_DURING_HASH_HOLD")
        return received, digest.hexdigest()
    finally:
        os.close(fd)


def inventory_native_book_retention(journal_path: Path, *, runner_root: Path,
                                    as_of_utc: str,
                                    pin_claims: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Count native-clock cohorts, budget and *unknown* pins, never DELETE.

    `pin_claims` are intentionally untrusted hints. They cannot certify the
    external universe of replay, artifacts, JSON, positions and backups.
    """
    at = _aware(as_of_utc)
    if not at:
        return _hold("RETENTION_CLOCK_UNVERIFIED")
    if (not Path(journal_path).is_absolute() or not Path(runner_root).is_absolute()
            or ".." in Path(journal_path).parts or ".." in Path(runner_root).parts):
        return _hold("PRIVATE_OFFHOST_COPY_REQUIRED")
    path = Path(journal_path).absolute()
    root = Path(runner_root).absolute()
    if not _private_copy_under_root(path, root):
        return _hold("PRIVATE_OFFHOST_COPY_REQUIRED")
    try:
        file_before = _copy_fingerprint(path)
    except (OSError, ValueError):
        return _hold("PRIVATE_JOURNAL_FINGERPRINT_HOLD")
    rows, chain = verified_native_book_journal(path)
    if chain.get("state") != "HASH_CHAIN_VERIFIED":
        return _hold("PPI_BOOK_JOURNAL_" + str(chain.get("state") or "UNKNOWN"))
    try:
        file_bytes, file_digest = _copy_fingerprint(path)
    except (OSError, ValueError):
        return _hold("PRIVATE_JOURNAL_FINGERPRINT_HOLD")
    if (file_bytes, file_digest) != file_before:
        return _hold("PRIVATE_JOURNAL_CHANGED_DURING_CENSUS_HOLD")

    cutoff = at - timedelta(days=RETENTION_DAYS)
    counts: Counter[str] = Counter()
    session_rows: Counter[str] = Counter()
    session_bytes: Counter[str] = Counter()
    for row in rows:
        native_at = _aware(row.get("native_book_at"))
        known_at = _aware(row.get("known_at"))
        session = row.get("session_art")
        if not native_at or not known_at or type(session) is not str:
            return _hold("PRIVATE_JOURNAL_NATIVE_CLOCK_OR_SESSION_HOLD")
        session_rows[session] += 1
        session_bytes[session] += len(_pit_bytes(row))
        if native_at > at or known_at > at:
            counts["FUTURE_AT_AS_OF_HOLD"] += 1
        elif native_at < cutoff:
            # Correcting/copying/observing now never extends the economic TTL.
            counts["OLDER_THAN_365D_PROTECTED_UNKNOWN_PINS"] += 1
        else:
            counts["WITHIN_365D_NATIVE_BOOK"] += 1
    today = at.astimezone(ART).date().isoformat()
    active_rows = session_rows[today]
    active_bytes = session_bytes[today]
    total_remaining_rows = PIT_JOURNAL_MAX_RECORDS - len(rows)
    total_remaining_bytes = PIT_JOURNAL_MAX_BYTES - file_bytes
    session_remaining_rows = PIT_SESSION_MAX_RECORDS - active_rows
    session_remaining_bytes = PIT_SESSION_MAX_BYTES - active_bytes
    exhausted = (total_remaining_rows <= 0 or total_remaining_bytes <= 0
                 or session_remaining_rows <= 0 or session_remaining_bytes <= 0)
    # A future entry may cost up to 32 KiB plus document overhead: a positive
    # byte balance is NOT proof that the next whole event will fit.
    event_budget_unproven = (total_remaining_bytes < PIT_EVENT_MAX_BYTES
                             or session_remaining_bytes < PIT_EVENT_MAX_BYTES)
    return {
        "schema": SCHEMA,
        "state": "OFFHOST_CHAIN_VERIFIED_RETENTION_AND_PIN_HOLD",
        "journal_copy_sha256": file_digest,
        "journal_copy_bytes": file_bytes,
        "journal_record_count": len(rows),
        "journal_chain_last_event_sha256": chain.get("latest_sha256"),
        "native_clock_basis": "PPI_BOOK_DATE_UTC_ECONOMIC_EVENT",
        "known_at_role": "PIT_AVAILABILITY_NOT_RETENTION_REJUVENATION",
        "cutoff_utc_inclusive": cutoff.isoformat(),
        "daily_date_only_cutoff_art_inclusive": daily_start(at).isoformat(),
        "as_of_utc": at.isoformat(),
        "cohorts": {key: counts[key] for key in (
            "WITHIN_365D_NATIVE_BOOK", "OLDER_THAN_365D_PROTECTED_UNKNOWN_PINS",
            "FUTURE_AT_AS_OF_HOLD")},
        "budgets": {
            "global_max_records": PIT_JOURNAL_MAX_RECORDS,
            "global_max_bytes": PIT_JOURNAL_MAX_BYTES,
            "global_remaining_records": total_remaining_rows,
            "global_remaining_bytes": total_remaining_bytes,
            "session_art": today,
            "session_max_records": PIT_SESSION_MAX_RECORDS,
            "session_max_bytes": PIT_SESSION_MAX_BYTES,
            "session_observed_records": active_rows,
            "session_observed_record_bytes": active_bytes,
            "session_remaining_records": session_remaining_rows,
            "session_remaining_bytes": session_remaining_bytes,
            "max_record_bytes": PIT_EVENT_MAX_BYTES,
            "capture_capacity_state": "CAPTURE_CAPACITY_HOLD" if exhausted else
                "NEXT_EVENT_SIZE_UNVERIFIED" if event_budget_unproven else
                "HEADROOM_OBSERVED_NOT_GUARANTEED",
        },
        "pin_scope": PIN_SCOPE,
        "pin_claims_accepted_as_proof": False,
        "pin_claims_supplied": pin_claims is not None,
        "external_consumer_universe_verified": False,
        "legacy_history_version_ids_reconciled": False,
        "legacy_596196_pit_backfill_authorized": False,
        "provider_authenticity_verified": False,
        "external_clock_attested": False,
        "host_purge_allowed": False,
        "physical_host_delete_count": 0,
        "physical_bytes_reclaimed": 0,
        "source_mutated": False,
        "provider_calls": 0,
        "real_orders_sent": 0,
        "predeploy_executed": False,
        "next_gate": "INDEPENDENT_EXTERNAL_PIN_GRAPH_AND_AUTHENTIC_SOURCE_RECEIPTS",
    }
