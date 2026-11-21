#!/usr/bin/env python3
"""A729: bounded, immutable SQLite schema and Porota-only backup inventory.

This is deliberately NOT a deletion utility. Historical/PIT pointers and
cross-database pins remain unproven; even a row older than 365 ART dates is
NOT a deletable row. Production DB/WAL, backups and other apps are untouched.

Host execution has no caller-supplied paths or SQL. A private runner collects
the raw JSON and publishes only the strict allowlisted sanitized receipt.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo
import zlib


SCHEMA = "rc6.a729.db-backup-inventory-readonly.v1"
ARCHIVE_SCHEMA = "rc6.a729.archive-provenance-ro.v1"
LOCATOR_SCHEMA = "rc6.a729.historical-source-locator-ro.v1"
ART = ZoneInfo("America/Argentina/Buenos_Aires")
DATA = Path("/opt/porota-trading/data")
DB_TARGETS = (
    ("HISTORY", DATA / "market_history.db", 2 * 1024**3),
    ("OBSERVER", DATA / "paper_v17/observer_v17.db", 5 * 1024**3),
)
BACKUP_TARGETS = (
    ("GENERAL", Path("/opt/porota-backups"), re.compile(r"porota-general-[A-Za-z0-9_.-]{1,80}\.tar\.gz\Z")),
    ("PAPER", DATA / "paper_v17/artifacts/observer_v17.db/backups/paper", re.compile(r"observer_[A-Za-z0-9_.-]{1,80}\.db\.gz\Z")),
    ("HISTORY", DATA / "paper_v17/artifacts/observer_v17.db/backups/history", re.compile(r"market_history_[A-Za-z0-9_.-]{1,80}\.db\.gz\Z")),
)
DATED_TABLES = frozenset({"history_versions_v2", "market_historical_ohlcv"})
BOOK_CONTRACT = {
    # Exact A726 SQL contract, publicly defined in the main-branch probe.
    "observer_state": ("id", "mode", "real_orders_sent", "process_state", "session_state"),
    "paper_positions": ("status",),
    "paper_future_positions": ("status",),
    "paper_cauciones": ("status",),
    "paper_family_lifecycle": ("family", "state"),
}
PUBLIC_TABLES = frozenset(BOOK_CONTRACT) | frozenset({
    "history_versions_v2", "market_historical_ohlcv", "historical_raw_archive",
})
MAX_TABLES = 96
MAX_BACKUP_ENTRIES = 1000
MAX_RAW_BYTES = 65536
DB_SCAN_SECONDS = 4.0
ALLOWED_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")
DB_STATUSES = frozenset({"SCANNED", "SQL_PARTIAL_BOUNDED", "SCHEMA_UNAVAILABLE", "MISSING",
                         "PATH_UNSAFE", "FILE_UNSAFE", "SIZE_LIMIT", "WAL_BASE_ONLY",
                         "ROLLBACK_JOURNAL_ACTIVE", "CHANGED_DURING_SCAN", "UNAVAILABLE"})
BACKUP_STATUSES = frozenset({"SCANNED", "MISSING", "PATH_UNSAFE", "UNAVAILABLE",
                             "BOUNDED", "CHANGED_DURING_SCAN"})
TABLE_STATUSES = frozenset({"METADATA_ONLY", "TIME_BUDGET", "SQL_UNAVAILABLE"})
SIDECAR_STATES = frozenset({"REGULAR", "MISSING", "FILE_UNSAFE", "UNAVAILABLE"})
JOURNAL_STATES = frozenset({"WAL_HEADER", "ROLLBACK_HEADER", "UNKNOWN"})
CONTRACT_STATES = frozenset({"PRESENT", "MISSING_COLUMNS", "TABLE_ABSENT", "NO_VERIFICADO"})
SCHEMA_SCOPES = frozenset({"NO_VERIFICADO", "IMMUTABLE_BASE_STABLE",
                           "BASE_FILE_ONLY_WAL_UNRECONCILED"})
PRAGMA_MODES = frozenset({"wal", "delete", "truncate", "persist", "memory", "off"})
MAX_NUMBER = 2**60
ARCHIVE_MAX_ROWS = 64
ARCHIVE_SCAN_SECONDS = 2.0
ARCHIVE_MAX_BODY_BYTES = 128 * 1024
ARCHIVE_MAX_TOTAL_BODY_BYTES = 2 * 1024 * 1024
ARCHIVE_COLUMNS = ("id", "origin", "row_key", "body_hash", "recorded_at",
                   "quality", "body_json")
ARCHIVE_STATUSES = frozenset({
    "OBSERVED_BOUNDED", "RO_MOUNT_UNVERIFIED", "MISSING", "PATH_UNSAFE",
    "FILE_UNSAFE", "SIZE_LIMIT", "SQLITE_UNAVAILABLE", "TABLE_ABSENT",
    "SCHEMA_INVALID", "TIME_BUDGET", "CHANGED_DURING_SCAN",
    "SOURCE_BINDING_UNVERIFIED",
})
ARCHIVE_WAL_SCOPES = frozenset({"WAL_SNAPSHOT_CONSISTENT", "BASE_NO_WAL",
                                "NO_VERIFICADO"})
LOCATOR_TABLES = {
    "HISTORY": ("history_versions_v2", "history_canonical_v2",
                "market_historical_ohlcv"),
    "OBSERVER": ("production_history", "production_history_attempts"),
    "EXTERNAL_EXACT": ("exact_attempts_v1",),
}
LOCATOR_REQUIRED_COLUMNS = {
    "history_versions_v2": frozenset({"id", "instrument_type", "date", "source"}),
    "history_canonical_v2": frozenset({"instrument_type", "date", "source"}),
    "market_historical_ohlcv": frozenset({"date", "source"}),
    "production_history": frozenset({"instrument_type", "settlement", "date_to"}),
    "production_history_attempts": frozenset({"instrument_type", "state", "attempted_at"}),
    "exact_attempts_v1": frozenset({"origin", "quality", "inner_sha256", "wrapper_json"}),
}
LOCATOR_TARGETS = (
    ("HISTORY", DATA / "market_history.db", 2 * 1024**3),
    ("OBSERVER", DATA / "paper_v17/observer_v17.db", 5 * 1024**3),
    ("EXTERNAL_EXACT", DATA / "evidence/ppi_history_exact_v1/manifest_exact_v1.db",
     256 * 1024**2),
)
LOCATOR_OBJECTS_ROOT = DATA / "evidence/ppi_history_exact_v1/objects/sha256"
LOCATOR_STATUSES = ARCHIVE_STATUSES | {"UNAVAILABLE"}
LOCATOR_TABLE_STATUSES = frozenset({"NO_VERIFICADO", "TABLE_ABSENT",
                                  "SCHEMA_INVALID", "COLUMNS_MISSING",
                                  "PRESENT_COLUMNS_OK"})
LOCATOR_SAMPLE_ROWS = 64
EXACT_INTEGRITY_MODE = "EXTERNAL_EXACT_INTEGRITY_ONLY"
EXACT_INTEGRITY_COLUMNS = frozenset({"body_hash", "body_sha256", "body_bytes"})
# Match the existing cutoff reader's per-body and total external archive limits.
EXACT_MAX_BODY_BYTES = 2 * 1024**2
EXACT_MAX_TOTAL_BODY_BYTES = 128 * 1024**2
EXACT_INTEGRITY_RESULTS = (
    "matched", "invalid_manifest", "unreadable_object", "inner_hash_mismatch",
    "body_hash_mismatch", "byte_budget_exceeded",
)
EXACT_PAYLOAD_FIELDS = ("nonempty_bar_lists", "all_bars_have_currency",
                        "all_bars_have_raw_basis", "all_bars_have_both")
HEX_SHA256 = re.compile(rb"[0-9a-f]{64}\Z")
LOCATOR_SQLITE_PHASES = frozenset({
    "OPEN_CONNECTION", "SET_READ_LIMITS", "READ_JOURNAL_MODE", "BEGIN_READ",
    "VERIFY_SOURCE_BINDING", "SCAN_TABLES", "RECHECK_SOURCE_BINDING", "END_READ",
})
LOCATOR_WAL_FILE_STATES = frozenset({"MISSING", "EMPTY", "NONEMPTY"})


class InventoryError(Exception):
    """Expected fail-closed inventory condition (never published verbatim)."""


def archive_readonly_mount_attested(path: Path) -> bool:
    """The caller must have isolated the *host data* in a private OS RO mount.

    SQLite URI mode=ro alone can still write a live WAL -shm sidecar.  Do not
    open a connection when the kernel has not marked the data mount read-only.
    """
    try:
        return bool(path.is_absolute() and path.is_relative_to(DATA) and
                    os.statvfs(DATA).f_flag & os.ST_RDONLY and
                    os.statvfs(path.parent).f_flag & os.ST_RDONLY)
    except (OSError, ValueError, AttributeError):
        return False


def _anchored_directory(path: Path) -> int:
    """Anchor every absolute parent component with NOFOLLOW; reject symlinks."""
    if not path.is_absolute() or ".." in path.parts or not hasattr(os, "O_PATH"):
        raise InventoryError("PATH_UNSAFE")
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open("/", flags)
        for part in path.parts[1:]:
            next_fd = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_fd
        return descriptor
    except (OSError, ValueError) as exc:
        if "descriptor" in locals():
            os.close(descriptor)
        raise InventoryError("MISSING" if isinstance(exc, FileNotFoundError)
                             else "PATH_UNSAFE") from exc


def _file_metadata(parent_fd: int, name: str) -> tuple[str, tuple | None]:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return "MISSING", None
    except (OSError, ValueError):
        return "UNAVAILABLE", None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return "FILE_UNSAFE", None
    return "REGULAR", (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                       info.st_ctime_ns, info.st_nlink, info.st_mode,
                       info.st_uid, info.st_gid)


def _stable_file(parent_fd: int, name: str, before: tuple | None) -> bool:
    status, after = _file_metadata(parent_fd, name)
    return status == "REGULAR" and after == before


def _archive_empty(role: str, status: str = "SQLITE_UNAVAILABLE") -> dict:
    return {"role": role, "status": status, "wal_scope": "NO_VERIFICADO",
            "schema_exact": False, "sample_rows": 0, "truncated": False,
            "rows_total": None, "origin_ppi_history_labeled": 0,
            "origin_other_labeled": 0, "quality_valid_payload_labeled": 0,
            "quality_unvalidated_labeled": 0, "quality_other_labeled": 0,
            "hash_matched": 0, "hash_mismatched": 0, "hash_unchecked": 0,
            "oversize_bodies": 0, "body_bytes_examined": 0,
            "db_bytes": None, "wal_bytes": None, "shm_bytes": None,
            "sqlite_ro": False}


def _archive_same_identity(old: tuple | None, current: tuple | None) -> bool:
    """Content may advance within an ACID read snapshot; inode/owner cannot."""
    if old is None or current is None:
        return old is None and current is None
    return (old[0], old[1], *old[5:]) == (current[0], current[1], *current[5:])


def _archive_fd_map() -> dict[int, tuple[int, int]]:
    """Observe our own open regular-file inodes; no /proc paths leave host."""
    try:
        names = os.listdir("/proc/self/fd")
    except OSError as exc:
        raise InventoryError("SOURCE_BINDING_UNVERIFIED") from exc
    found = {}
    for name in names:
        if not name.isdecimal():
            continue
        handle = int(name)
        try:
            info = os.fstat(handle)
        except OSError:
            continue  # The directory listing's transient fd may have closed.
        if stat.S_ISREG(info.st_mode):
            found[handle] = (info.st_dev, info.st_ino)
    return found


def _archive_sqlite_binding(db: sqlite3.Connection, path: Path,
                            pinned: tuple[int, int],
                            fds_before: dict[int, tuple[int, int]]) -> None:
    """Require SQLite to hold its OWN fd to the exact pinned source inode.

    SQLite's WAL VFS cannot use `file:/proc/self/fd/N?mode=ro` (it needs
    sibling -wal/-shm paths), so compare the newly opened physical fd in
    /proc rather than trusting the pathname returned by database_list alone.
    """
    rows = db.execute("PRAGMA database_list").fetchall()
    if (len(rows) != 1 or rows[0][1] != "main" or rows[0][2] != str(path)):
        raise InventoryError("SOURCE_BINDING_UNVERIFIED")
    after = _archive_fd_map()
    if not any(fd not in fds_before and inode == pinned
               for fd, inode in after.items()):
        raise InventoryError("SOURCE_BINDING_UNVERIFIED")


def _archive_blob(db: sqlite3.Connection, column: str, row_id: int,
                  limit: int, table: str = "historical_raw_archive") -> tuple[bytes | None, int]:
    """Read at most the allowance, even for a malicious multi-GiB TEXT value.

    SQLite's incremental blob handle works on TEXT as well.  The SQL SELECT
    projects only the integer rowid; it never materializes user text in Python
    or evaluates length(CAST(body_json AS BLOB)) over the whole value.
    """
    if not callable(getattr(db, "blobopen", None)):
        raise InventoryError("SQLITE_UNAVAILABLE")
    with db.blobopen(table, column, row_id, readonly=True) as blob:
        size = len(blob)
        if size > limit:
            return None, size
        return blob.read(size), size


def _archive_sample(db: sqlite3.Connection, deadline: float, receipt: dict) -> None:
    # `sqlite_schema.sql` may include multi-MiB comments or column names.
    # A bounded type-only SELECT differentiates truly absent from a SQLite
    # build too old to implement PRAGMA table_list (which returns no rows).
    known = db.execute("""SELECT type FROM sqlite_schema
        WHERE name='historical_raw_archive' LIMIT 2""").fetchall()
    if not known:
        receipt["status"] = "TABLE_ABSENT"
        return
    if len(known) != 1 or known[0][0] != "table":
        receipt["status"] = "SCHEMA_INVALID"
        return
    # PRAGMA table_list returns bounded type metadata without DDL contents.
    objects = db.execute("PRAGMA table_list('historical_raw_archive')").fetchall()
    if (len(objects) != 1 or objects[0][0:3] !=
            ("main", "historical_raw_archive", "table") or objects[0][4] != 0):
        receipt["status"] = "SCHEMA_INVALID"
        return
    columns = db.execute("PRAGMA table_info('historical_raw_archive')").fetchall()
    if (tuple(col[1] for col in columns) != ARCHIVE_COLUMNS or
            type(columns[0][5]) is not int or columns[0][5] != 1):
        receipt["status"] = "SCHEMA_INVALID"
        return
    receipt["schema_exact"] = True
    rows = db.execute("""SELECT id FROM historical_raw_archive
        ORDER BY id DESC LIMIT ?""", (ARCHIVE_MAX_ROWS + 1,)).fetchall()
    receipt["truncated"] = len(rows) > ARCHIVE_MAX_ROWS
    body_budget = ARCHIVE_MAX_TOTAL_BODY_BYTES
    for (row_id,) in rows[:ARCHIVE_MAX_ROWS]:
        if time.monotonic() >= deadline:
            raise InventoryError("TIME_BUDGET")
        if type(row_id) is not int or row_id < 1:
            raise InventoryError("SCHEMA_INVALID")
        origin, _ = _archive_blob(db, "origin", row_id, 64)
        quality, _ = _archive_blob(db, "quality", row_id, 64)
        body_hash, _ = _archive_blob(db, "body_hash", row_id, 64)
        receipt["sample_rows"] += 1
        receipt["origin_ppi_history_labeled" if origin == b"PPI_HISTORY"
                else "origin_other_labeled"] += 1
        if quality == b"VALID_PAYLOAD":
            receipt["quality_valid_payload_labeled"] += 1
        elif quality == b"UNVALIDATED":
            receipt["quality_unvalidated_labeled"] += 1
        else:
            receipt["quality_other_labeled"] += 1
        body, body_bytes = _archive_blob(db, "body_json", row_id,
                                         min(ARCHIVE_MAX_BODY_BYTES, body_budget))
        if body is None:
            if body_bytes > ARCHIVE_MAX_BODY_BYTES:
                receipt["oversize_bodies"] += 1
            receipt["hash_unchecked"] += 1
            continue
        body_budget -= len(body)
        receipt["body_bytes_examined"] += len(body)
        if (body_hash is not None and re.fullmatch(rb"[0-9a-f]{64}", body_hash)
                and hashlib.sha256(body).hexdigest().encode() == body_hash):
            receipt["hash_matched"] += 1
        else:
            receipt["hash_mismatched"] += 1
    if time.monotonic() >= deadline:
        raise InventoryError("TIME_BUDGET")
    receipt["status"] = "OBSERVED_BOUNDED"


def archive_provenance_db(role: str, path: Path, maximum: int) -> dict:
    """Bounded SQL sample of a live WAL, permitted only on OS-RO data mounts.

    Labels and stored-body SHA are NOT independent economic PPI provenance.
    No COUNT(*), full-text export, PPI call, or database write is attempted.
    """
    receipt = _archive_empty(role)
    if not archive_readonly_mount_attested(path):
        receipt["status"] = "RO_MOUNT_UNVERIFIED"
        return receipt
    deadline = time.monotonic() + ARCHIVE_SCAN_SECONDS
    try:
        parent_fd = _anchored_directory(path.parent)
    except InventoryError as exc:
        receipt["status"] = "MISSING" if str(exc) == "MISSING" else "PATH_UNSAFE"
        return receipt
    try:
        status, before = _file_metadata(parent_fd, path.name)
        if status != "REGULAR":
            receipt["status"] = status if status == "MISSING" else "FILE_UNSAFE"
            return receipt
        assert before is not None
        if not 4096 <= before[2] <= maximum:
            receipt["status"] = "SIZE_LIMIT"
            return receipt
        sides = {}
        for suffix in ("-wal", "-shm", "-journal"):
            side_status, info = _file_metadata(parent_fd, path.name + suffix)
            if side_status not in ("MISSING", "REGULAR"):
                receipt["status"] = "FILE_UNSAFE"
                return receipt
            sides[suffix] = info
        if sides["-journal"] is not None:
            receipt["status"] = "FILE_UNSAFE"
            return receipt
        receipt["db_bytes"] = before[2]
        receipt["wal_bytes"] = sides["-wal"][2] if sides["-wal"] else 0
        receipt["shm_bytes"] = sides["-shm"][2] if sides["-shm"] else 0
        opened_fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=parent_fd)
        try:
            fd_stat = os.fstat(opened_fd)
            if (fd_stat.st_dev, fd_stat.st_ino) != (before[0], before[1]):
                receipt["status"] = "CHANGED_DURING_SCAN"
                return receipt
            opened_fds = _archive_fd_map()
            uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=.2,
                                         isolation_level=None)) as db:
                receipt["sqlite_ro"] = True
                # Per-connection SQLite limits stop giant schema/text parsing
                # BEFORE the first SQL statement; they never alter the DB.
                if (not callable(getattr(db, "setlimit", None)) or
                        not hasattr(sqlite3, "SQLITE_LIMIT_LENGTH") or
                        not hasattr(sqlite3, "SQLITE_LIMIT_SQL_LENGTH")):
                    raise InventoryError("SQLITE_UNAVAILABLE")
                db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 256 * 1024)
                db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 256 * 1024)
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA temp_store=MEMORY")
                db.execute("PRAGMA trusted_schema=OFF")
                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 500)
                try:
                    mode = db.execute("PRAGMA journal_mode").fetchone()
                    if not mode or mode[0] != "wal":
                        raise InventoryError("SCHEMA_INVALID")
                    db.execute("BEGIN")
                    _archive_sqlite_binding(db, path, (before[0], before[1]),
                                            opened_fds)
                    _archive_sample(db, deadline, receipt)
                    _archive_sqlite_binding(db, path, (before[0], before[1]),
                                            opened_fds)
                    db.rollback()
                    receipt["wal_scope"] = ("WAL_SNAPSHOT_CONSISTENT"
                                            if receipt["wal_bytes"] else "BASE_NO_WAL")
                finally:
                    db.set_progress_handler(None, 0)
            after_status, after = _file_metadata(parent_fd, path.name)
            if after_status != "REGULAR" or not _archive_same_identity(before, after):
                receipt = _archive_empty(role, "CHANGED_DURING_SCAN")
            for suffix, old in sides.items():
                side_status, now = _file_metadata(parent_fd, path.name + suffix)
                if (side_status not in ("REGULAR", "MISSING") or
                        not _archive_same_identity(old, now)):
                    receipt = _archive_empty(role, "CHANGED_DURING_SCAN")
                    break
            return receipt
        finally:
            os.close(opened_fd)
    except InventoryError as exc:
        status = str(exc)
        return _archive_empty(role, status if status in ARCHIVE_STATUSES else "SQLITE_UNAVAILABLE")
    except (OSError, sqlite3.Error, ValueError, TypeError, RecursionError) as exc:
        return _archive_empty(role, "TIME_BUDGET" if time.monotonic() >= deadline
                              else "SQLITE_UNAVAILABLE")
    finally:
        os.close(parent_fd)


def _locator_empty(role: str, status: str = "SQLITE_UNAVAILABLE") -> dict:
    return {"role": role, "status": status, "wal_scope": "NO_VERIFICADO",
            "db_bytes": None, "wal_bytes": None, "shm_bytes": None,
            "sqlite_ro": False,
            "tables": [{"name": name, "status": "NO_VERIFICADO",
                        "sample_rows": 0, "truncated": False,
                        "rows_total": None, "origin_ppi_history_labeled": 0,
                        "origin_other_labeled": 0,
                        "quality_valid_payload_labeled": 0,
                        "quality_unvalidated_labeled": 0,
                        "quality_other_labeled": 0}
                       for name in LOCATOR_TABLES[role]]}


def _exact_gzip_bytes(objects_root: Path, digest: str, allowance: int) -> bytes:
    """Read only a fixed digest path on the private read-only mount."""
    parent_fd = _anchored_directory(objects_root / digest[:2])
    try:
        name = f"{digest}.json.gz"
        status, before = _file_metadata(parent_fd, name)
        if status != "REGULAR" or not before or not 0 < before[2] <= EXACT_MAX_BODY_BYTES:
            raise InventoryError("FILE_UNSAFE")
        if not os.fstatvfs(parent_fd).f_flag & os.ST_RDONLY:
            raise InventoryError("RO_MOUNT_UNVERIFIED")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                     dir_fd=parent_fd)
        with os.fdopen(fd, "rb") as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    (info.st_dev, info.st_ino, info.st_size,
                     info.st_mtime_ns, info.st_ctime_ns) != before[:5]):
                raise InventoryError("CHANGED_DURING_SCAN")
            if not os.fstatvfs(source.fileno()).f_flag & os.ST_RDONLY:
                raise InventoryError("RO_MOUNT_UNVERIFIED")
            compressed = source.read(before[2] + 1)
            if len(compressed) != before[2] or not _stable_file(parent_fd, name, before):
                raise InventoryError("CHANGED_DURING_SCAN")
        with gzip.GzipFile(fileobj=BytesIO(compressed)) as packed:
            return packed.read(allowance + 1)
    finally:
        os.close(parent_fd)


def _count_exact_payload_fields(inner: bytes, counts: dict) -> None:
    """Report provider fields from matched objects, never from the local wrapper."""
    try:
        bars = json.loads(inner)
    except (UnicodeError, ValueError, RecursionError):
        return
    if (not isinstance(bars, list) or not 1 <= len(bars) <= 4096
            or not all(isinstance(row, dict) for row in bars)):
        return
    currency = all(any(isinstance(row.get(name), str) and row[name].strip()
                       for name in ("currency", "quoteCurrency", "moneda"))
                   for row in bars)
    raw_basis = all(row.get("priceBasis") == "RAW"
                    and row.get("adjustmentBasis") == "RAW_NO_ADJUSTMENT"
                    and row.get("adjusted") is False for row in bars)
    counts["nonempty_bar_lists"] += 1
    counts["all_bars_have_currency"] += int(currency)
    counts["all_bars_have_raw_basis"] += int(raw_basis)
    counts["all_bars_have_both"] += int(currency and raw_basis)


def _exact_integrity(db: sqlite3.Connection, row_id: int, objects_root: Path,
                     budget: int, fields: dict) -> tuple[str, int]:
    table = "exact_attempts_v1"
    row = db.execute("""SELECT CASE WHEN typeof(body_bytes)='integer' THEN body_bytes END
                        FROM exact_attempts_v1 WHERE rowid=?""", (row_id,)).fetchone()
    if not row or type(row[0]) is not int or row[0] <= 0:
        return "invalid_manifest", 0
    expected_bytes = row[0]
    if expected_bytes > min(EXACT_MAX_BODY_BYTES, budget):
        return "byte_budget_exceeded", 0
    # Reuse A729 incremental reads on the pinned WAL snapshot; do not project
    # the wrapper or digest columns as unbounded SQLite TEXT fields.
    try:
        inner_hash, _ = _archive_blob(db, "inner_sha256", row_id, 64, table)
        body_hash, _ = _archive_blob(db, "body_hash", row_id, 64, table)
        body_sha, _ = _archive_blob(db, "body_sha256", row_id, 64, table)
        wrapper, wrapper_bytes = _archive_blob(db, "wrapper_json", row_id, expected_bytes, table)
    except sqlite3.OperationalError as exc:
        if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_ERROR:
            return "invalid_manifest", expected_bytes
        raise
    if (wrapper is None or wrapper_bytes >= expected_bytes or
            any(digest is None or not HEX_SHA256.fullmatch(digest)
                for digest in (inner_hash, body_hash, body_sha))):
        return "invalid_manifest", expected_bytes
    try:
        inner = _exact_gzip_bytes(objects_root, inner_hash.decode("ascii"),
                                  expected_bytes - wrapper_bytes)
    except (InventoryError, OSError, EOFError, zlib.error):
        return "unreadable_object", expected_bytes
    if (len(inner) > expected_bytes - wrapper_bytes or
            hashlib.sha256(inner).hexdigest().encode("ascii") != inner_hash):
        return "inner_hash_mismatch", expected_bytes
    try:
        body = json.loads(wrapper.decode("utf-8", "surrogatepass"))
        if not isinstance(body, dict) or "payload_json" in body:
            return "invalid_manifest", expected_bytes
        body["payload_json"] = inner.decode("utf-8", "surrogatepass")
        # The reviewed fb_raw_evidence_exact_v1 writer hashes this format.
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False).encode("utf-8", "surrogatepass")
    except (UnicodeError, ValueError, TypeError, RecursionError):
        return "invalid_manifest", expected_bytes
    observed = hashlib.sha256(encoded).hexdigest().encode("ascii")
    if len(encoded) != expected_bytes or observed != body_hash or observed != body_sha:
        return "body_hash_mismatch", expected_bytes
    _count_exact_payload_fields(inner, fields)
    return "matched", expected_bytes


def _locator_scan(db: sqlite3.Connection, role: str, deadline: float,
                  receipt: dict, objects_root: Path | None = None) -> None:
    """Inspect fixed tables and optionally verify bounded EXTERNAL_EXACT objects."""
    for part in receipt["tables"]:
        if time.monotonic() >= deadline:
            raise InventoryError("TIME_BUDGET")
        name = part["name"]
        if name not in LOCATOR_TABLES[role]:
            raise InventoryError("SCHEMA_INVALID")
        objects = db.execute("SELECT type FROM sqlite_schema WHERE name=? LIMIT 2",
                             (name,)).fetchall()
        if not objects:
            part["status"] = "TABLE_ABSENT"
            continue
        if len(objects) != 1 or objects[0][0] != "table":
            part["status"] = "SCHEMA_INVALID"
            continue
        schema = db.execute(f"PRAGMA table_list('{name}')").fetchall()
        if (len(schema) != 1 or schema[0][0:3] != ("main", name, "table")
                or type(schema[0][3]) is not int or not 1 <= schema[0][3] <= 64
                or schema[0][4] != 0):
            part["status"] = "SCHEMA_INVALID"
            continue
        # table_info omits generated/hidden columns, which can shadow SQLite
        # rowid. table_xinfo must expose the full physical column catalog.
        cols = db.execute(f"PRAGMA table_xinfo('{name}')").fetchall()
        if (len(cols) != schema[0][3] or
                any(len(c) < 7 or not isinstance(c[1], str) for c in cols)):
            part["status"] = "COLUMNS_MISSING"
            continue
        # An explicit or generated `rowid` column would change the query into
        # an unindexed ORDER BY scan, potentially sorting a multi-GiB table.
        column_names = {c[1].casefold() for c in cols}
        if column_names & {"rowid", "_rowid_", "oid"}:
            part["status"] = "SCHEMA_INVALID"
            continue
        required = LOCATOR_REQUIRED_COLUMNS[name]
        if objects_root is not None:
            required |= EXACT_INTEGRITY_COLUMNS
        if (any(type(c[6]) is not int or c[6] != 0 for c in cols) or
                not required.issubset(column_names)):
            part["status"] = "COLUMNS_MISSING"
            continue
        part["status"] = "PRESENT_COLUMNS_OK"
        # An ordinary rowid table gives indexed latest-row probes. No full
        # count or potentially unbounded date scan is used.
        rows = db.execute(f'SELECT rowid FROM "{name}" '
                          'ORDER BY rowid DESC LIMIT ?',
                          (LOCATOR_SAMPLE_ROWS + 1,)).fetchall()
        part["sample_rows"] = min(len(rows), LOCATOR_SAMPLE_ROWS)
        part["truncated"] = len(rows) > LOCATOR_SAMPLE_ROWS
        if role == "EXTERNAL_EXACT":
            if objects_root is not None:
                integrity = dict.fromkeys(EXACT_INTEGRITY_RESULTS, 0)
                integrity["eligible_rows"] = 0
                part["exact_integrity"] = integrity
                fields = dict.fromkeys(EXACT_PAYLOAD_FIELDS, 0)
                part["exact_payload_fields"] = fields
            budget = EXACT_MAX_TOTAL_BODY_BYTES
            for (row_id,) in rows[:LOCATOR_SAMPLE_ROWS]:
                if time.monotonic() >= deadline:
                    raise InventoryError("TIME_BUDGET")
                if type(row_id) is not int or row_id < 1:
                    raise InventoryError("SCHEMA_INVALID")
                origin, _ = _archive_blob(db, "origin", row_id, 64, name)
                quality, _ = _archive_blob(db, "quality", row_id, 64, name)
                part["origin_ppi_history_labeled" if origin == b"PPI_HISTORY"
                     else "origin_other_labeled"] += 1
                if quality == b"VALID_PAYLOAD":
                    part["quality_valid_payload_labeled"] += 1
                elif quality == b"UNVALIDATED":
                    part["quality_unvalidated_labeled"] += 1
                else:
                    part["quality_other_labeled"] += 1
                if (objects_root is not None and origin == b"PPI_HISTORY" and
                        quality == b"VALID_PAYLOAD"):
                    outcome, reserved = _exact_integrity(db, row_id, objects_root, budget, fields)
                    budget -= reserved
                    integrity["eligible_rows"] += 1
                    integrity[outcome] += 1


def _locator_readonly_mount_attested(path: Path) -> bool:
    """A missing descendant is safe to label MISSING only under pinned RO ancestors.

    The older archive guard requires the immediate parent to exist, so it
    cannot distinguish an absent exact manifest directory from a RW mount.
    Walk only anchored, non-symlink ancestor directories and check each real
    mount with fstatvfs. Never create the missing component.
    """
    if archive_readonly_mount_attested(path):
        return True
    if not path.is_absolute() or ".." in path.parts or not path.is_relative_to(DATA):
        return False
    try:
        current = _anchored_directory(DATA)
    except InventoryError:
        return False
    try:
        if not os.fstatvfs(current).f_flag & os.ST_RDONLY:
            return False
        for component in path.parent.relative_to(DATA).parts:
            try:
                next_fd = os.open(component, os.O_PATH | os.O_DIRECTORY |
                                  os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=current)
            except FileNotFoundError:
                return True  # First absent ancestor, pinned under the RO mount.
            except OSError:
                return False
            os.close(current)
            current = next_fd
            if not os.fstatvfs(current).f_flag & os.ST_RDONLY:
                return False
        return True
    except (OSError, ValueError):
        return False
    finally:
        os.close(current)


def source_locator_db(role: str, path: Path, maximum: int,
                      table_names: tuple[str, ...], *, verify_external_exact: bool = False) -> dict:
    """WAL-consistent OS-ro table presence, never source promotion.

    External manifest labels are local assertions, not PPI economic proof.
    """
    if role not in LOCATOR_TABLES or tuple(table_names) != LOCATOR_TABLES[role]:
        raise InventoryError("SCHEMA_INVALID")
    if verify_external_exact and role != "EXTERNAL_EXACT":
        raise InventoryError("SCHEMA_INVALID")
    objects_root = path.parent / "objects" / "sha256" if verify_external_exact else None
    receipt = _locator_empty(role)
    if not _locator_readonly_mount_attested(path):
        return _locator_empty(role, "RO_MOUNT_UNVERIFIED")
    deadline = time.monotonic() + ARCHIVE_SCAN_SECONDS
    try:
        parent_fd = _anchored_directory(path.parent)
    except InventoryError as exc:
        return _locator_empty(role, "MISSING" if str(exc) == "MISSING" else "PATH_UNSAFE")
    phase = "OPEN_CONNECTION"
    sides = {}
    try:
        status, before = _file_metadata(parent_fd, path.name)
        if status != "REGULAR":
            return _locator_empty(role, status if status == "MISSING" else "FILE_UNSAFE")
        assert before is not None
        if not 4096 <= before[2] <= maximum:
            return _locator_empty(role, "SIZE_LIMIT")
        for suffix in ("-wal", "-shm", "-journal"):
            side_status, info = _file_metadata(parent_fd, path.name + suffix)
            if side_status not in ("MISSING", "REGULAR"):
                return _locator_empty(role, "FILE_UNSAFE")
            sides[suffix] = info
        if sides["-journal"] is not None:
            return _locator_empty(role, "FILE_UNSAFE")
        receipt.update(db_bytes=before[2],
                       wal_bytes=sides["-wal"][2] if sides["-wal"] else 0,
                       shm_bytes=sides["-shm"][2] if sides["-shm"] else 0)
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=parent_fd)
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (before[0], before[1]):
                return _locator_empty(role, "CHANGED_DURING_SCAN")
            previous = _archive_fd_map()
            uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True, timeout=.2,
                                         isolation_level=None)) as db:
                phase = "SET_READ_LIMITS"
                receipt["sqlite_ro"] = True
                if (not callable(getattr(db, "setlimit", None)) or
                        not hasattr(sqlite3, "SQLITE_LIMIT_LENGTH") or
                        not hasattr(sqlite3, "SQLITE_LIMIT_SQL_LENGTH")):
                    raise InventoryError("SQLITE_UNAVAILABLE")
                db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 256 * 1024)
                db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 256 * 1024)
                db.execute("PRAGMA query_only=ON")
                db.execute("PRAGMA temp_store=MEMORY")
                db.execute("PRAGMA trusted_schema=OFF")
                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 500)
                try:
                    phase = "READ_JOURNAL_MODE"
                    mode = db.execute("PRAGMA journal_mode").fetchone()
                    if not mode or mode[0] != "wal":
                        raise InventoryError("SCHEMA_INVALID")
                    phase = "BEGIN_READ"
                    db.execute("BEGIN")
                    phase = "VERIFY_SOURCE_BINDING"
                    _archive_sqlite_binding(db, path, (before[0], before[1]), previous)
                    phase = "SCAN_TABLES"
                    _locator_scan(db, role, deadline, receipt, objects_root)
                    phase = "RECHECK_SOURCE_BINDING"
                    _archive_sqlite_binding(db, path, (before[0], before[1]), previous)
                    phase = "END_READ"
                    db.rollback()
                    receipt["wal_scope"] = ("WAL_SNAPSHOT_CONSISTENT" if
                                            receipt["wal_bytes"] else "BASE_NO_WAL")
                finally:
                    db.set_progress_handler(None, 0)
            receipt["status"] = "OBSERVED_BOUNDED"
            after_status, after = _file_metadata(parent_fd, path.name)
            if after_status != "REGULAR" or not _archive_same_identity(before, after):
                return _locator_empty(role, "CHANGED_DURING_SCAN")
            for suffix, old in sides.items():
                side_status, now = _file_metadata(parent_fd, path.name + suffix)
                if (side_status not in ("MISSING", "REGULAR") or
                        not _archive_same_identity(old, now)):
                    return _locator_empty(role, "CHANGED_DURING_SCAN")
            if time.monotonic() >= deadline:
                return _locator_empty(role, "TIME_BUDGET")
            return receipt
        finally:
            os.close(fd)
    except InventoryError as exc:
        status = str(exc)
        return _locator_empty(role, status if status in LOCATOR_STATUSES
                              else "SQLITE_UNAVAILABLE")
    except sqlite3.Error as exc:
        failure = _locator_empty(role, "TIME_BUDGET" if time.monotonic() >= deadline
                                 else "SQLITE_UNAVAILABLE")
        wal = sides.get("-wal")
        observation = {"phase": phase,
                       "wal_file": "MISSING" if wal is None else ("EMPTY" if wal[2] == 0 else "NONEMPTY"),
                       "shm_present": sides.get("-shm") is not None}
        error_code = getattr(exc, "sqlite_errorcode", None)
        error_name = getattr(exc, "sqlite_errorname", None)
        if _known_sqlite_error(error_name, error_code):
            observation.update(error_code=error_code, error_name=error_name)
        failure["sqlite_failure"] = observation
        return failure
    except (OSError, ValueError, TypeError, RecursionError):
        return _locator_empty(role, "TIME_BUDGET" if time.monotonic() >= deadline
                              else "SQLITE_UNAVAILABLE")
    finally:
        os.close(parent_fd)


def _known_sqlite_error(name, code):
    return (type(code) is int and 0 < code <= 65535 and isinstance(name, str)
            and re.fullmatch(r"SQLITE_[A-Z0-9_]{1,40}", name) is not None
            and getattr(sqlite3, name, None) == code)


def _db_public(role: str, status: str, size: int | None = None) -> dict:
    return {"role": role, "status": status, "size_bytes": size,
            "wal_bytes": None, "shm_bytes": None, "journal_bytes": None,
            "wal_state": "UNAVAILABLE", "shm_state": "UNAVAILABLE",
            "journal_state": "UNAVAILABLE", "wal_owner_matches_db": None,
            "shm_owner_matches_db": None, "wal_same_group_as_db": None,
            "shm_same_group_as_db": None, "shm_owner_readable": None,
            "wal_owner_readable": None, "sqlite_header_mode": "UNKNOWN",
            "pragma_immutable_journal_mode": None,
            "schema_scope": "NO_VERIFICADO", "page_size": None,
            "page_count": None, "freelist_count": None, "tables": [],
            "tables_seen": 0, "tables_inspected": 0,
            "book_contract": [{"name": name, "state": "NO_VERIFICADO",
                               "missing_required_columns": []} for name in BOOK_CONTRACT],
            "retention_delete_allowed": False, "physical_reclaim_estimate_bytes": None}


def _integer_pragma(connection: sqlite3.Connection, name: str) -> int | None:
    value = connection.execute(f"PRAGMA {name}").fetchone()
    result = value[0] if value else None
    return result if type(result) is int and 0 <= result <= MAX_NUMBER else None


def _table_inventory(connection: sqlite3.Connection) -> tuple[list[dict], int, bool, list[dict]]:
    """Read only sqlite_schema and PRAGMA metadata; never scan table records.

    Even SELECT COUNT(*) can scan a full index on a large host DB. Since no
    current CLOSED/flat/write-quiescence proof exists, counting live records
    violates the source policy; keep exact counts and 365d rows NO_VERIFICADO.
    """
    start = time.monotonic()
    overall_end = start + DB_SCAN_SECONDS
    names = connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name LIMIT ?",
        (MAX_TABLES + 1,)).fetchall()
    overflow = len(names) > MAX_TABLES
    result = []
    contracts = {name: {"name": name, "state": "TABLE_ABSENT",
                        "missing_required_columns": []} for name in BOOK_CONTRACT}
    for ordinal, row in enumerate(names[:MAX_TABLES], 1):
        actual_name = row[0]
        if not isinstance(actual_name, str) or not ALLOWED_NAME.fullmatch(actual_name):
            # Never emit uncontrolled names from a private database.
            name = f"OTHER_TABLE_{ordinal}"
            result.append({"name": name, "status": "SQL_UNAVAILABLE",
                           "rows": None, "older_than_365_art_dates": None,
                           "columns_count": None, "indexes_count": None,
                           "has_date_column": None})
            continue
        name = actual_name if actual_name in PUBLIC_TABLES else f"OTHER_TABLE_{ordinal}"
        table = {"name": name, "status": "TIME_BUDGET", "rows": None,
                 "older_than_365_art_dates": None, "columns_count": None,
                 "indexes_count": None, "has_date_column": None}
        if time.monotonic() >= overall_end:
            result.append(table)
            continue
        connection.set_progress_handler(lambda: int(time.monotonic() > overall_end), 2000)
        try:
            quoted = '"' + actual_name.replace('"', '""') + '"'
            columns = [item[1] for item in connection.execute(f"PRAGMA table_info({quoted})")]
            indexes = [item[1] for item in connection.execute(f"PRAGMA index_list({quoted})")]
            table.update(status="METADATA_ONLY", columns_count=len(columns),
                         indexes_count=len(indexes), has_date_column="date" in columns)
            if name in contracts:
                missing = sorted(set(BOOK_CONTRACT[name]) - set(columns))
                contracts[name] = {"name": name,
                                   "state": "MISSING_COLUMNS" if missing else "PRESENT",
                                   "missing_required_columns": missing}
        except sqlite3.OperationalError as exc:
            if "interrupted" not in str(exc).lower():
                table["status"] = "SQL_UNAVAILABLE"
            else:
                table["status"] = "TIME_BUDGET"
        except sqlite3.DatabaseError:
            table["status"] = "SQL_UNAVAILABLE"
        finally:
            connection.set_progress_handler(None, 0)
        result.append(table)
    if overflow:
        # A truncated table listing cannot prove that a missing expected table
        # is absent. Keep only explicitly seen table contracts.
        for item in contracts.values():
            if item["state"] == "TABLE_ABSENT":
                item["state"] = "NO_VERIFICADO"
    return result, len(names), overflow, list(contracts.values())


def inventory_db(role: str, path: Path, maximum: int, cutoff: str) -> dict:
    receipt = _db_public(role, "UNAVAILABLE")
    try:
        parent_fd = _anchored_directory(path.parent)
    except InventoryError as exc:
        receipt["status"] = "MISSING" if str(exc) == "MISSING" else "PATH_UNSAFE"
        return receipt
    try:
        status, before = _file_metadata(parent_fd, path.name)
        if status != "REGULAR":
            receipt["status"] = status if status in ("MISSING", "UNAVAILABLE") else "FILE_UNSAFE"
            return receipt
        assert before is not None
        size = before[2]
        receipt["size_bytes"] = size
        if not 4096 <= size <= maximum:
            receipt["status"] = "SIZE_LIMIT"
            return receipt
        sidecars_before = {}
        for suffix, key, statekey in (("-wal", "wal_bytes", "wal_state"),
                                      ("-shm", "shm_bytes", "shm_state"),
                                      ("-journal", "journal_bytes", "journal_state")):
            scstatus, scmarker = _file_metadata(parent_fd, path.name + suffix)
            receipt[statekey] = scstatus
            if scstatus not in ("MISSING", "REGULAR"):
                receipt["status"] = "FILE_UNSAFE"
                return receipt
            sidecars_before[suffix] = (scstatus, scmarker)
            receipt[key] = scmarker[2] if scmarker is not None else 0
            if suffix in ("-wal", "-shm") and scmarker is not None:
                prefix = "wal" if suffix == "-wal" else "shm"
                receipt[f"{prefix}_owner_matches_db"] = scmarker[7] == before[7]
                receipt[f"{prefix}_same_group_as_db"] = scmarker[8] == before[8]
                receipt[f"{prefix}_owner_readable"] = bool(scmarker[6] & stat.S_IRUSR)
        if receipt["journal_bytes"]:
            receipt["status"] = "ROLLBACK_JOURNAL_ACTIVE"
            return receipt
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=parent_fd)
        except OSError:
            receipt["status"] = "CHANGED_DURING_SCAN"
            return receipt
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_ino != before[1]:
                receipt["status"] = "CHANGED_DURING_SCAN"
                return receipt
            header = os.pread(fd, 20, 0)
            if header[:16] == b"SQLite format 3\x00":
                receipt["sqlite_header_mode"] = (
                    "WAL_HEADER" if header[18:20] == b"\x02\x02" else
                    "ROLLBACK_HEADER" if header[18:20] == b"\x01\x01" else "UNKNOWN")
            # /proc/self/fd binds SQLite to the already-opened, no-follow inode;
            # immutable=1 cannot create journal/WAL, even if source is read-only.
            uri = f"file:/proc/self/fd/{fd}?mode=ro&immutable=1"
            with closing(sqlite3.connect(uri, uri=True, timeout=0.2)) as database:
                database.execute("PRAGMA query_only=ON")
                database.execute("PRAGMA temp_store=MEMORY")
                mode = database.execute("PRAGMA journal_mode").fetchone()
                if mode and mode[0] in ("wal", "delete", "truncate", "persist", "memory", "off"):
                    receipt["pragma_immutable_journal_mode"] = mode[0]
                receipt["page_size"] = _integer_pragma(database, "page_size")
                receipt["page_count"] = _integer_pragma(database, "page_count")
                receipt["freelist_count"] = _integer_pragma(database, "freelist_count")
                tables, seen, overflow, contracts = _table_inventory(database)
                receipt["tables"] = tables
                receipt["book_contract"] = contracts
                receipt["tables_seen"] = seen
                receipt["tables_inspected"] = sum(t["status"] == "METADATA_ONLY" for t in tables)
                receipt["schema_scope"] = ("BASE_FILE_ONLY_WAL_UNRECONCILED"
                                           if receipt["wal_bytes"] else "IMMUTABLE_BASE_STABLE")
                receipt["status"] = ("WAL_BASE_ONLY" if receipt["wal_bytes"] else
                    "SQL_PARTIAL_BOUNDED" if overflow or any(
                        t["status"] != "METADATA_ONLY" for t in tables) else "SCANNED")
        except (OSError, sqlite3.DatabaseError, sqlite3.OperationalError):
            receipt["status"] = "SCHEMA_UNAVAILABLE"
        finally:
            os.close(fd)
        if not _stable_file(parent_fd, path.name, before):
            receipt = _db_public(role, "CHANGED_DURING_SCAN")
        for suffix, (old_status, old_marker) in sidecars_before.items():
            new_status, new_marker = _file_metadata(parent_fd, path.name + suffix)
            if (old_status, old_marker) != (new_status, new_marker):
                receipt = _db_public(role, "CHANGED_DURING_SCAN")
                break
        return receipt
    finally:
        os.close(parent_fd)


def inventory_backups(role: str, root: Path, name_re: re.Pattern, cutoff: datetime) -> dict:
    receipt = {"role": role, "status": "UNAVAILABLE", "matched_files": 0,
               "matched_bytes": 0, "older_365_file_mtime_count": 0,
               "older_365_file_mtime_bytes": 0, "unexpected_entries": 0,
               "unsafe_entries": 0, "files_seen": 0, "delete_allowed": False}
    try:
        directory_fd = _anchored_directory(root)
    except InventoryError as exc:
        receipt["status"] = "MISSING" if str(exc) == "MISSING" else "PATH_UNSAFE"
        return receipt
    try:
        before = os.fstat(directory_fd)
        # The path walk uses O_PATH to reject symlinks; scandir requires an
        # independently opened read-only directory descriptor on that inode.
        read_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                          dir_fd=directory_fd)
        try:
            with os.scandir(read_fd) as entries:
                for entry in entries:
                    receipt["files_seen"] += 1
                    if receipt["files_seen"] > MAX_BACKUP_ENTRIES:
                        receipt["status"] = "BOUNDED"
                        break
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        receipt["unsafe_entries"] += 1
                        continue
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        receipt["unsafe_entries"] += 1
                        continue
                    if not name_re.fullmatch(entry.name) or not 0 <= info.st_size <= MAX_NUMBER:
                        receipt["unexpected_entries"] += 1
                        continue
                    receipt["matched_files"] += 1
                    receipt["matched_bytes"] += info.st_size
                    if datetime.fromtimestamp(info.st_mtime, tz=timezone.utc) < cutoff:
                        receipt["older_365_file_mtime_count"] += 1
                        receipt["older_365_file_mtime_bytes"] += info.st_size
        finally:
            os.close(read_fd)
        if receipt["status"] != "BOUNDED":
            receipt["status"] = "SCANNED"
        after = os.fstat(directory_fd)
        if (before.st_ino, before.st_dev, before.st_mtime_ns) != (
                after.st_ino, after.st_dev, after.st_mtime_ns):
            receipt["status"] = "CHANGED_DURING_SCAN"
    except (OSError, OverflowError):
        receipt["status"] = "UNAVAILABLE"
    finally:
        os.close(directory_fd)
    return receipt


def host_inventory() -> dict:
    now = datetime.now(timezone.utc)
    cutoff_day = now.astimezone(ART).date() - timedelta(days=364)
    cutoff = datetime.combine(cutoff_day, datetime.min.time(), tzinfo=ART).astimezone(timezone.utc)
    databases = [inventory_db(role, path, maximum, cutoff_day.isoformat())
                 for role, path, maximum in DB_TARGETS]
    backups = [inventory_backups(role, path, pattern, cutoff)
               for role, path, pattern in BACKUP_TARGETS]
    return {"schema": SCHEMA, "state": "INVENTORY_ONLY", "generated_at_utc": now.isoformat(),
            "retention_cutoff_art_inclusive": cutoff_day.isoformat(),
            "db_inventory": databases, "backup_inventory": backups,
            "read_only": True, "db_immutable_ro": True,
            "host_mutations": 0, "db_rows_deleted": 0, "backup_files_deleted": 0,
            "ppi_watch_touched": False, "provider_calls": 0, "real_orders_sent_by_action": 0,
            "purge_allowed": False, "db_delete_authorized": False,
            "backup_delete_authorized": False,
            "limitations": ["PINS_AND_365D_LINEAGE_NOT_PROVEN", "NO_HOST_PAPER_LEDGER_PROOF",
                            "BACKUP_MTIME_NOT_DATA_RETENTION", "NO_HOST_FULL_TABLE_SCANS",
                            "ROW_COUNTS_NO_VERIFICADO"]}


def host_archive_provenance() -> dict:
    """Two fixed physical locations; never choose a historical repair source."""
    now = datetime.now(timezone.utc)
    targets = DB_TARGETS
    return {"schema": ARCHIVE_SCHEMA, "state": "ARCHIVE_PROVENANCE_ONLY",
            "generated_at_utc": now.isoformat(),
            "db_inventory": [archive_provenance_db(role, path, maximum)
                             for role, path, maximum in targets],
            "read_only": True,
            "os_ro_mount": all(archive_readonly_mount_attested(path)
                               for _, path, _ in targets),
            "host_mutations": 0, "provider_calls": 0,
            "ppi_watch_touched": False, "real_orders_sent_by_action": 0,
            "archive_source_selected": False,
            "ppi_provider_contract_verified": False}


def _locator_objects_root_state(path: Path) -> str:
    """Only directory existence; never enumerate or decompress raw objects."""
    if not _locator_readonly_mount_attested(path):
        return "RO_MOUNT_UNVERIFIED"
    try:
        fd = _anchored_directory(path)
    except InventoryError as exc:
        return "MISSING" if str(exc) == "MISSING" else "PATH_UNSAFE"
    try:
        return "PRESENT" if stat.S_ISDIR(os.fstat(fd).st_mode) else "PATH_UNSAFE"
    except OSError:
        return "PATH_UNSAFE"
    finally:
        os.close(fd)


def host_source_locator(*, verify_external_exact: bool = False) -> dict:
    """Locate the three sources or verify EXTERNAL_EXACT alone in RO."""
    now = datetime.now(timezone.utc)
    targets = LOCATOR_TARGETS[-1:] if verify_external_exact else LOCATOR_TARGETS
    state = EXACT_INTEGRITY_MODE if verify_external_exact else "SOURCE_LOCATOR_ONLY"
    return {"schema": LOCATOR_SCHEMA, "state": state,
            "generated_at_utc": now.isoformat(),
            "source_inventory": [source_locator_db(role, path, maximum,
                                                   LOCATOR_TABLES[role],
                                                   verify_external_exact=verify_external_exact)
                                 for role, path, maximum in targets],
            "objects_root_state": _locator_objects_root_state(LOCATOR_OBJECTS_ROOT),
            "read_only": True,
            "os_ro_mount": all(_locator_readonly_mount_attested(path)
                               for _, path, _ in targets),
            "host_mutations": 0, "provider_calls": 0,
            "ppi_watch_touched": False, "real_orders_sent_by_action": 0,
            "history_source_selected": False,
            "ppi_provider_contract_verified": False}


def _int(value: object, maximum: int = MAX_NUMBER) -> int | None:
    return value if type(value) is int and 0 <= value <= maximum else None


def public_receipt(raw: object, remote_rc: int) -> dict:
    failure = {"schema": SCHEMA, "state": "NO_VERIFICADO", "failure_class": "HOST_UNAVAILABLE",
               "read_only": True, "host_mutations": 0, "db_rows_deleted": 0,
               "backup_files_deleted": 0, "purge_allowed": False, "ppi_watch_touched": False,
               "real_orders_sent_by_action": 0}
    if remote_rc != 0 or not isinstance(raw, dict):
        return failure
    require = {"schema": SCHEMA, "state": "INVENTORY_ONLY", "read_only": True,
               "db_immutable_ro": True, "host_mutations": 0, "db_rows_deleted": 0,
               "backup_files_deleted": 0, "ppi_watch_touched": False, "provider_calls": 0,
               "real_orders_sent_by_action": 0, "purge_allowed": False,
               "db_delete_authorized": False, "backup_delete_authorized": False}
    if any(raw.get(key) != value or type(raw.get(key)) is not type(value)
           for key, value in require.items()):
        return dict(failure, failure_class="INVARIANT_NOT_PROVEN")
    try:
        timestamp = datetime.fromisoformat(raw["generated_at_utc"])
        assert timestamp.tzinfo is not None
        cutoff = raw["retention_cutoff_art_inclusive"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", cutoff)
        assert isinstance(raw["db_inventory"], list) and len(raw["db_inventory"]) == 2
        assert isinstance(raw["backup_inventory"], list) and len(raw["backup_inventory"]) == 3
        clean_dbs = []
        for value, role in zip(raw["db_inventory"], ("HISTORY", "OBSERVER")):
            assert isinstance(value, dict) and value.get("role") == role
            assert value["status"] in DB_STATUSES
            fields = ("size_bytes", "wal_bytes", "shm_bytes", "journal_bytes", "page_size",
                      "page_count", "freelist_count", "tables_seen", "tables_inspected")
            ints = {key: _int(value.get(key)) for key in fields}
            assert all(value.get(key) is None or ints[key] is not None for key in fields)
            details = {}
            for part in ("wal", "shm"):
                state = value.get(f"{part}_state")
                assert state in SIDECAR_STATES
                details[f"{part}_state"] = state
                for key in ("owner_matches_db", "same_group_as_db", "owner_readable"):
                    result = value.get(f"{part}_{key}")
                    assert result is None or type(result) is bool
                    details[f"{part}_{key}"] = result
            assert value.get("journal_state") in SIDECAR_STATES
            assert value.get("sqlite_header_mode") in JOURNAL_STATES
            assert value.get("schema_scope") in SCHEMA_SCOPES
            pragma = value.get("pragma_immutable_journal_mode")
            assert pragma is None or pragma in PRAGMA_MODES
            details.update(journal_state=value["journal_state"],
                           sqlite_header_mode=value["sqlite_header_mode"],
                           pragma_immutable_journal_mode=pragma,
                           schema_scope=value["schema_scope"])
            assert isinstance(value["tables"], list) and len(value["tables"]) <= MAX_TABLES
            tables = []
            for table in value["tables"]:
                assert isinstance(table, dict) and isinstance(table.get("name"), str)
                assert table["name"] in PUBLIC_TABLES or re.fullmatch(
                    r"OTHER_TABLE_\d{1,2}", table["name"])
                assert table.get("status") in TABLE_STATUSES
                rows = _int(table.get("rows"))
                old = _int(table.get("older_than_365_art_dates"))
                # No host full-table scans: row/age counts MUST be unknown.
                assert table.get("rows") is None and table.get("older_than_365_art_dates") is None
                cols = _int(table.get("columns_count"), 1024)
                indexes = _int(table.get("indexes_count"), 1024)
                has_date = table.get("has_date_column")
                assert has_date is None or type(has_date) is bool
                assert table["status"] != "METADATA_ONLY" or (
                    cols is not None and indexes is not None and type(has_date) is bool)
                tables.append({"name": table["name"], "status": table["status"],
                               "rows": rows, "older_than_365_art_dates": old,
                               "columns_count": cols, "indexes_count": indexes,
                               "has_date_column": has_date})
            contracts = []
            assert isinstance(value.get("book_contract"), list)
            assert len(value["book_contract"]) == len(BOOK_CONTRACT)
            for contract, name in zip(value["book_contract"], BOOK_CONTRACT):
                assert isinstance(contract, dict) and contract.get("name") == name
                assert contract.get("state") in CONTRACT_STATES
                missing = contract.get("missing_required_columns")
                assert isinstance(missing, list)
                assert len(missing) <= len(BOOK_CONTRACT[name])
                assert len(set(missing)) == len(missing)
                assert all(item in BOOK_CONTRACT[name] for item in missing)
                contracts.append({"name": name, "state": contract["state"],
                                  "missing_required_columns": missing})
            clean_dbs.append({"role": role, "status": value["status"],
                              **ints, **details, "tables": tables,
                              "book_contract": contracts, "retention_delete_allowed": False,
                              "physical_reclaim_estimate_bytes": None})
        clean_backups = []
        for value, role in zip(raw["backup_inventory"], ("GENERAL", "PAPER", "HISTORY")):
            assert isinstance(value, dict) and value.get("role") == role
            assert value.get("status") in BACKUP_STATUSES
            keys = ("matched_files", "matched_bytes", "older_365_file_mtime_count",
                    "older_365_file_mtime_bytes", "unexpected_entries", "unsafe_entries",
                    "files_seen")
            vals = {key: _int(value.get(key)) for key in keys}
            assert all(num is not None for num in vals.values())
            clean_backups.append({"role": role, "status": value["status"],
                                  **vals, "delete_allowed": False})
        return {"schema": SCHEMA, "state": "INVENTORY_ONLY",
                "generated_at_utc": timestamp.isoformat(),
                "retention_cutoff_art_inclusive": cutoff,
                "db_inventory": clean_dbs, "backup_inventory": clean_backups,
                "read_only": True, "db_immutable_ro": True, "host_mutations": 0,
                "db_rows_deleted": 0, "backup_files_deleted": 0,
                "purge_allowed": False, "ppi_watch_touched": False,
                "real_orders_sent_by_action": 0,
                "db_delete_authorized": False, "backup_delete_authorized": False,
                "limitations": ["PINS_AND_365D_LINEAGE_NOT_PROVEN",
                                "NO_HOST_PAPER_LEDGER_PROOF", "BACKUP_MTIME_NOT_DATA_RETENTION",
                                "NO_HOST_FULL_TABLE_SCANS", "ROW_COUNTS_NO_VERIFICADO"]}
    except (AssertionError, KeyError, TypeError, ValueError, OverflowError):
        return dict(failure, failure_class="MALFORMED_HOST_RECEIPT")


def archive_public_receipt(raw: object, remote_rc: int) -> dict:
    """Publish only known counters; never echo instrument or raw archive body."""
    failure = {"schema": ARCHIVE_SCHEMA, "state": "NO_VERIFICADO",
               "failure_class": "HOST_UNAVAILABLE", "read_only": True,
               "host_mutations": 0, "ppi_watch_touched": False,
               "provider_calls": 0, "real_orders_sent_by_action": 0,
               "archive_source_selected": False,
               "ppi_provider_contract_verified": False}
    if remote_rc != 0 or not isinstance(raw, dict):
        return failure
    require = {"schema": ARCHIVE_SCHEMA, "state": "ARCHIVE_PROVENANCE_ONLY",
               "read_only": True, "os_ro_mount": True, "host_mutations": 0,
               "ppi_watch_touched": False, "provider_calls": 0,
               "real_orders_sent_by_action": 0, "archive_source_selected": False,
               "ppi_provider_contract_verified": False}
    if any(raw.get(key) != value or type(raw.get(key)) is not type(value)
           for key, value in require.items()):
        return dict(failure, failure_class="INVARIANT_NOT_PROVEN")
    try:
        stamp = datetime.fromisoformat(raw["generated_at_utc"])
        assert stamp.tzinfo is not None
        dbs = raw["db_inventory"]
        assert isinstance(dbs, list) and len(dbs) == 2
        cleaned = []
        for item, role in zip(dbs, ("HISTORY", "OBSERVER")):
            assert isinstance(item, dict) and item.get("role") == role
            status, scope = item.get("status"), item.get("wal_scope")
            assert status in ARCHIVE_STATUSES and scope in ARCHIVE_WAL_SCOPES
            booleans = ("schema_exact", "truncated", "sqlite_ro")
            assert all(type(item.get(name)) is bool for name in booleans)
            ints = ("sample_rows", "origin_ppi_history_labeled",
                    "origin_other_labeled", "quality_valid_payload_labeled",
                    "quality_unvalidated_labeled", "quality_other_labeled",
                    "hash_matched", "hash_mismatched", "hash_unchecked",
                    "oversize_bodies", "body_bytes_examined")
            counts = {k: _int(item.get(k), ARCHIVE_MAX_TOTAL_BODY_BYTES)
                      for k in ints}
            assert all(x is not None for x in counts.values())
            assert counts["sample_rows"] <= ARCHIVE_MAX_ROWS
            assert counts["origin_ppi_history_labeled"] + counts[
                "origin_other_labeled"] == counts["sample_rows"]
            assert (counts["quality_valid_payload_labeled"] + counts[
                "quality_unvalidated_labeled"] + counts["quality_other_labeled"]
                == counts["sample_rows"])
            assert (counts["hash_matched"] + counts["hash_mismatched"] +
                    counts["hash_unchecked"] == counts["sample_rows"])
            assert counts["oversize_bodies"] <= counts["hash_unchecked"]
            assert item.get("rows_total") is None
            sizes = {key: _int(item.get(key)) for key in
                     ("db_bytes", "wal_bytes", "shm_bytes")}
            assert all(item.get(k) is None or sizes[k] is not None for k in sizes)
            if status == "OBSERVED_BOUNDED":
                assert item["schema_exact"] is True and item["sqlite_ro"] is True
                assert scope != "NO_VERIFICADO"
            else:
                assert (not item["schema_exact"] or status == "SCHEMA_INVALID")
                assert all(counts[k] == 0 for k in counts)
            cleaned.append({"role": role, "status": status,
                            "wal_scope": scope,
                            **{k: item[k] for k in booleans},
                            **counts, **sizes, "rows_total": None})
        whole_state = ("ARCHIVE_PROVENANCE_ONLY" if all(
            x["status"] in ("OBSERVED_BOUNDED", "TABLE_ABSENT") for x in cleaned)
            else "NO_VERIFICADO")
        return {"schema": ARCHIVE_SCHEMA, "state": whole_state,
                "generated_at_utc": stamp.isoformat(),
                "db_inventory": cleaned, "read_only": True,
                "os_ro_mount": True, "host_mutations": 0, "provider_calls": 0,
                "real_orders_sent_by_action": 0, "ppi_watch_touched": False,
                "archive_source_selected": False,
                "ppi_provider_contract_verified": False,
                "limitations": ["LATEST_64_ROWS_ONLY", "FULL_COUNTS_NOT_SCANNED",
                                "LABELS_AND_BODY_SHA_NOT_PPI_PROVIDER_PROVENANCE",
                                "NO_HISTORY_SOURCE_SELECTION", "NO_DB_RESTORE"]}
    except (AssertionError, KeyError, TypeError, ValueError, OverflowError):
        return dict(failure, failure_class="MALFORMED_HOST_RECEIPT")


def source_locator_public_receipt(raw: object, remote_rc: int, *,
                                 verify_external_exact: bool = False) -> dict:
    """Allowlisted diagnostic only; no row-level material or source promotion."""
    failure = {"schema": LOCATOR_SCHEMA, "state": "NO_VERIFICADO",
               "failure_class": "HOST_UNAVAILABLE", "read_only": True,
               "host_mutations": 0, "ppi_watch_touched": False,
               "provider_calls": 0, "real_orders_sent_by_action": 0,
               "history_source_selected": False,
               "ppi_provider_contract_verified": False}
    if remote_rc != 0 or not isinstance(raw, dict):
        return failure
    expected = EXACT_INTEGRITY_MODE if verify_external_exact else "SOURCE_LOCATOR_ONLY"
    targets = LOCATOR_TARGETS[-1:] if verify_external_exact else LOCATOR_TARGETS
    require = {"schema": LOCATOR_SCHEMA, "state": expected,
               "read_only": True, "os_ro_mount": True,
               "host_mutations": 0, "ppi_watch_touched": False,
               "provider_calls": 0, "real_orders_sent_by_action": 0,
               "history_source_selected": False,
               "ppi_provider_contract_verified": False}
    if any(raw.get(key) != value or type(raw.get(key)) is not type(value)
           for key, value in require.items()):
        return dict(failure, failure_class="INVARIANT_NOT_PROVEN")
    try:
        stamp = datetime.fromisoformat(raw["generated_at_utc"])
        assert stamp.tzinfo is not None
        objects_state = raw["objects_root_state"]
        assert objects_state in {"PRESENT", "MISSING", "PATH_UNSAFE",
                                 "RO_MOUNT_UNVERIFIED"}
        sources = raw["source_inventory"]
        assert isinstance(sources, list) and len(sources) == len(targets)
        cleaned = []
        integrity = None
        for source, (role, _, _) in zip(sources, targets):
            assert isinstance(source, dict) and source.get("role") == role
            status, scope = source.get("status"), source.get("wal_scope")
            assert status in LOCATOR_STATUSES and scope in ARCHIVE_WAL_SCOPES
            assert type(source.get("sqlite_ro")) is bool
            if status == "OBSERVED_BOUNDED":
                assert source["sqlite_ro"] and scope != "NO_VERIFICADO"
            else:
                assert scope == "NO_VERIFICADO" and not source["sqlite_ro"]
            sizes = {name: _int(source.get(name)) for name in
                     ("db_bytes", "wal_bytes", "shm_bytes")}
            assert all(source.get(k) is None or sizes[k] is not None for k in sizes)
            if status != "OBSERVED_BOUNDED":
                assert all(value is None for value in sizes.values())
            tables = source["tables"]
            assert isinstance(tables, list) and len(tables) == len(LOCATOR_TABLES[role])
            clean_tables = []
            for part, name in zip(tables, LOCATOR_TABLES[role]):
                assert isinstance(part, dict) and part.get("name") == name
                assert part.get("status") in LOCATOR_TABLE_STATUSES
                assert type(part.get("truncated")) is bool
                assert part.get("rows_total") is None
                sample = _int(part.get("sample_rows"), LOCATOR_SAMPLE_ROWS)
                labels = ("origin_ppi_history_labeled", "origin_other_labeled",
                          "quality_valid_payload_labeled", "quality_unvalidated_labeled",
                          "quality_other_labeled")
                counters = {key: _int(part.get(key), LOCATOR_SAMPLE_ROWS)
                            for key in labels}
                assert sample is not None and all(n is not None for n in counters.values())
                if part["status"] != "PRESENT_COLUMNS_OK" or status != "OBSERVED_BOUNDED":
                    assert sample == 0 and not part["truncated"]
                    assert all(n == 0 for n in counters.values())
                if role == "EXTERNAL_EXACT" and part["status"] == "PRESENT_COLUMNS_OK":
                    assert (counters["origin_ppi_history_labeled"] +
                            counters["origin_other_labeled"] == sample)
                    assert (counters["quality_valid_payload_labeled"] +
                            counters["quality_unvalidated_labeled"] +
                            counters["quality_other_labeled"] == sample)
                else:
                    assert all(n == 0 for n in counters.values())
                clean_tables.append({"name": name, "status": part["status"],
                                     "sample_rows": sample,
                                     "truncated": part["truncated"],
                                     "rows_total": None, **counters})
                if verify_external_exact and part["status"] == "PRESENT_COLUMNS_OK":
                    results = part["exact_integrity"]
                    names = ("eligible_rows", *EXACT_INTEGRITY_RESULTS)
                    assert isinstance(results, dict)
                    integrity = {key: _int(results.get(key), LOCATOR_SAMPLE_ROWS) for key in names}
                    assert all(n is not None for n in integrity.values())
                    eligible = integrity["eligible_rows"]
                    ppi_labels = counters["origin_ppi_history_labeled"]
                    valid_labels = counters["quality_valid_payload_labeled"]
                    assert max(0, ppi_labels + valid_labels - sample) <= eligible <= min(
                        ppi_labels, valid_labels)
                    assert eligible == sum(integrity[k] for k in EXACT_INTEGRITY_RESULTS)
                    clean_tables[-1]["exact_integrity"] = integrity
                    reported_fields = part["exact_payload_fields"]
                    assert isinstance(reported_fields, dict)
                    fields = {key: _int(reported_fields.get(key), integrity["matched"])
                              for key in EXACT_PAYLOAD_FIELDS}
                    assert all(n is not None for n in fields.values())
                    total, currency, raw_basis, both = (fields[key] for key in EXACT_PAYLOAD_FIELDS)
                    assert max(0, currency + raw_basis - total) <= both <= min(currency, raw_basis)
                    assert max(currency, raw_basis) <= total
                    clean_tables[-1]["exact_payload_fields"] = fields
            cleaned.append({"role": role, "status": status,
                            "wal_scope": scope, "sqlite_ro": source["sqlite_ro"],
                            **sizes, "tables": clean_tables})
            if "sqlite_failure" in source:
                error = source["sqlite_failure"]
                assert status in {"SQLITE_UNAVAILABLE", "TIME_BUDGET"} and isinstance(error, dict)
                assert (error.get("phase") in LOCATOR_SQLITE_PHASES
                        and error.get("wal_file") in LOCATOR_WAL_FILE_STATES
                        and type(error.get("shm_present")) is bool)
                clean_error = {key: error[key] for key in ("phase", "wal_file", "shm_present")}
                if _known_sqlite_error(error.get("error_name"), error.get("error_code")):
                    clean_error.update(error_code=error["error_code"], error_name=error["error_name"])
                cleaned[-1]["sqlite_failure"] = clean_error
        okay = (all(x["status"] in ("OBSERVED_BOUNDED", "MISSING") for x in cleaned)
                and objects_state in ("PRESENT", "MISSING"))
        if verify_external_exact:
            okay = (objects_state == "PRESENT" and cleaned[0]["status"] == "OBSERVED_BOUNDED"
                    and integrity is not None and integrity["eligible_rows"] > 0)
        objects_limit = ("EXTERNAL_BYTES_CHECKED_ONLY_FOR_ELIGIBLE_ROWS" if verify_external_exact
                         else "EXTERNAL_OBJECTS_NOT_OPENED_OR_HASH_VERIFIED")
        return {"schema": LOCATOR_SCHEMA,
                "state": expected if okay else "NO_VERIFICADO",
                "generated_at_utc": stamp.isoformat(),
                "source_inventory": cleaned, "objects_root_state": objects_state,
                "read_only": True, "os_ro_mount": True, "host_mutations": 0,
                "provider_calls": 0, "ppi_watch_touched": False,
                "real_orders_sent_by_action": 0,
                "history_source_selected": False,
                "ppi_provider_contract_verified": False,
                "limitations": ["LATEST_64_ROWIDS_ONLY", "FULL_COUNTS_NOT_SCANNED",
                                "EXTERNAL_LABELS_NOT_PROVIDER_CONTRACT",
                                objects_limit,
                                "NO_BACKUP_EXPANSION_OR_DB_RESTORE",
                                "NO_HISTORY_SOURCE_SELECTION"]}
    except (AssertionError, KeyError, TypeError, ValueError, OverflowError):
        return dict(failure, failure_class="MALFORMED_HOST_RECEIPT")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("host-inventory", "sanitize",
                                         "host-archive-provenance", "sanitize-archive",
                                         "host-source-locator", "sanitize-source",
                                         "host-exact-integrity", "sanitize-exact-integrity"))
    parser.add_argument("--raw", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--remote-rc", type=int, default=255)
    args = parser.parse_args()
    if args.mode == "host-inventory":
        print(json.dumps(host_inventory(), separators=(",", ":"), sort_keys=True))
        return 0
    if args.mode == "host-archive-provenance":
        print(json.dumps(host_archive_provenance(), separators=(",", ":"), sort_keys=True))
        return 0
    if args.mode in ("host-source-locator", "host-exact-integrity"):
        result = host_source_locator(verify_external_exact=args.mode == "host-exact-integrity")
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0
    if args.out is None or args.raw is None:
        parser.error("sanitize requires --raw and --out")
    try:
        if args.raw.stat().st_size > MAX_RAW_BYTES:
            raise InventoryError("RAW_LIMIT")
        raw = json.loads(args.raw.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, InventoryError):
        raw = None
    clean = (source_locator_public_receipt(raw, args.remote_rc,
                                          verify_external_exact=args.mode == "sanitize-exact-integrity")
             if args.mode in ("sanitize-source", "sanitize-exact-integrity") else
             archive_public_receipt(raw, args.remote_rc)
             if args.mode == "sanitize-archive" else public_receipt(raw, args.remote_rc))
    args.out.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    expected = (EXACT_INTEGRITY_MODE if args.mode == "sanitize-exact-integrity" else
                "SOURCE_LOCATOR_ONLY" if args.mode == "sanitize-source" else
                "ARCHIVE_PROVENANCE_ONLY" if args.mode == "sanitize-archive" else
                "INVENTORY_ONLY")
    return 0 if clean["state"] == expected else 2


if __name__ == "__main__":
    sys.exit(main())
