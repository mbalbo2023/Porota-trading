#!/usr/bin/env python3
"""RC6 A330/A500: bounded private offhost retention diagnostic modes.

Host mode: fixed allowlist, read-only fds/SQLite and stdout tar; NO host files
created, no DB DML, no timers/services modified. Runner mode: an isolated tar
becomes SHA-verified private copies/projections and A319's copy-only rehearsal.
It never certifies external-universe completeness or historical provider PIT.

A500 HISTORY_COPY_ONLY is independent of A330: it never opens the live observer
database and therefore cannot attest runtime PAPER/0 or external PIN coverage.
It copies only a quiet historical database to a private offhost runner for A319
HOLD-only census. The original A330 mode and its same-session contract remain.

A502 HISTORY_AGGREGATE_ONLY is a separate opt-in diagnostic of the quiet history
file. It emits aggregate counts only, never raw dates, rows, symbols or the DB.
It does not attest provider PIT, external pins, runtime PAPER/0 or purge safety.

A503 HISTORY_CUTOFF_CENSUS_ONLY is a further opt-in COLD database census: it
counts cutoff repair states and canonical family/source/date coverage. It cannot
prove completeness, instrument readiness, session gaps, or authorization to purge.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo


SCHEMA = "RC6_A330_PRIVATE_SAME_SESSION_V1"
PIN_SCHEMA = "RC6_A330_PIN_CENSUS_V1"
HISTORY_SCHEMA = "RC6_A500_HISTORY_COPY_ONLY_V1"
AGGREGATE_SCHEMA = "RC6_A502_HISTORY_AGGREGATE_ONLY_V1"
CUTOFF_SCHEMA = "RC6_A503_HISTORY_CUTOFF_CENSUS_V1"
CUTOFF_DAY = "2026-09-21"
CUTOFF_MAX_ROWS = 1_000_000
CUTOFF_MAX_BUCKETS = 40
CUTOFF_FAMILIES = frozenset({
    "ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES",
    "OPCIONES", "FUTUROS", "CAUCIONES", "FCI", "OTHER_REDACTED"})
CUTOFF_SOURCES = frozenset({
    "PPI", "PPI_API", "PPI_PRODUCTION_HISTORY", "IOL", "BYMA", "BYMA_EOD",
    "A3_CEM_CLOSING", "DATA912", "DATA912_POROTA_BATCH", "YAHOO",
    "OTHER_REDACTED"})
CUTOFF_STATES = frozenset({
    "COMPLETE", "INCOMPLETE", "PENDING", "RUNNING", "FAILED", "ABSENT",
    "OTHER_REDACTED"})
CUTOFF_ERRORS = frozenset({
    "NONE", "PROVENANCE_MISSING", "NO_ROWS", "PROVIDER_ERROR",
    "PPI_HISTORY_ERROR", "OTHER_REDACTED"})
CUTOFF_SCHEMA_GUARDS = {
    "rc6_history_cutoff_repair_runs": (
        "A503_RUN_TABLE_UNAVAILABLE", "A503_RUN_COLUMNS_MISSING"),
    "rc6_history_cutoff_repair_exact_v2": (
        "A503_EXACT_TABLE_UNAVAILABLE", "A503_EXACT_COLUMNS_MISSING"),
    "history_canonical_v2": (
        "A503_CANONICAL_TABLE_UNAVAILABLE", "A503_CANONICAL_COLUMNS_MISSING"),
}
HISTORY_PIN_SCHEMA = "RC6_A500_UNAVAILABLE_PIN_GROUPS_V1"
MANIFEST_SCHEMA = "RC6_RETENTION_A319_MANIFEST_V1"
HOST_SOURCE = Path("/opt/porota-trading/data/market_history.db")
HOST_OBSERVER = Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
HOST_DATA = Path("/opt/porota-trading/data")
HOST_TIMERS = ("daily", "reconcile", "weekend")
SOURCE_MEMBER = "history-copy.sqlite"
PINS_MEMBER = "private-pin-census.json"
ALLOWED_MEMBERS = {SOURCE_MEMBER: 1024**3, PINS_MEMBER: 24 * 1024**2}
MAX_JSON_FILES = 64
MAX_JSON_BYTES = 32 * 1024**2
MAX_JSON_FILE_BYTES = 2 * 1024**2
MAX_SCAN_ROWS = 250_000
MAX_PIN_IDS = 1_000_000
MAX_TABLES = 256
HOST_SECONDS = 125
RUNNER_SECONDS = 360
AGGREGATE_HOST_SECONDS = 55
AGGREGATE_MAX_ROWS = 1_000_000
AGGREGATE_MAX_RECEIPT_BYTES = 8192
SIDECARS = ("-wal", "-shm", "-journal")
SHA = re.compile(r"[0-9a-f]{64}\Z")
# These are literal, reviewed host-export guard codes, never arbitrary stderr.
# The classifier below is runner-only and never makes a retention GO decision.
HOST_EXPORT_REJECTION_ALLOWLIST = frozenset({
    "A330_TIME_BUDGET", "A330_SOURCE_ALIAS", "A330_SOURCE_UNAVAILABLE",
    "A330_SOURCE_UNSAFE_FILE", "A330_SQLITE_SIDECAR_PRESENT",
    "A330_HOST_DISK_RESERVE", "A330_HOST_MEMORY_RESERVE",
    "A330_A3_TIMER_NOT_QUARANTINED", "A330_HISTORY_WRITER_ACTIVE",
    "A330_CLOSED_PAPER_ZERO_REQUIRED", "A330_HEARTBEAT_UNZONED",
    "A330_HEARTBEAT_INVALID", "A330_HEARTBEAT_STALE",
    "A330_VERSION_REFERENCE_AMBIGUOUS", "A330_JSON_NODE_BUDGET",
    "A330_PIN_BUDGET", "A330_JSON_DUPLICATE_KEY",
    "A330_JSON_NONFINITE", "A330_JSON_INVALID", "A330_JSON_DIRECTORY_UNSAFE",
    "A330_JSON_FILE_BUDGET", "A330_JSON_FILE_UNSAFE",
    "A330_JSON_FILE_BYTES", "A330_JSON_GROUP_BYTES",
    "A330_JSON_INODE_CHANGED", "A330_JSON_CHANGED_DURING_READ",
    "A330_JSON_PATH_CHANGED", "A330_OBSERVER_TABLE_BUDGET",
    "A330_OBSERVER_ROW_BUDGET", "A330_OBSERVER_JSON_TYPE",
    "A330_OBSERVER_JSON_VALUE_BUDGET", "A330_OBSERVER_CHANGED_DURING_SCAN",
    "A330_ARCHIVE_MEMBER_INVALID", "A330_SOURCE_CHANGED_SINCE_PAPER_GATE",
    "A330_PIN_ENVELOPE_BUDGET", "A330_SOURCE_CHANGED_BEFORE_STREAM",
    "A330_SOURCE_STREAM_SHORT", "A330_SOURCE_CHANGED_AFTER_STREAM",
    "A330_HOST_MODE_INPUTS_FORBIDDEN",
    "A500_HISTORY_WRITER_NOT_INACTIVE",
    "A500_SOURCE_CHANGED_SINCE_HISTORY_GATE",
    "A502_HISTORY_SIDECAR_PRESENT", "A502_SOURCE_CHANGED_SINCE_GATE",
    "A502_SOURCE_CHANGED_DURING_SCAN", "A502_SOURCE_FD_MISMATCH",
    "A502_LEGACY_DATE_SCHEMA", "A502_ROW_BUDGET", "A502_TIME_BUDGET",
    "A502_GATE_CLOCK_INVALID", "A502_SQLITE_UNAVAILABLE", "A502_EMPTY_HISTORY",
    "A502_HOST_CREDENTIALS_UNAVAILABLE",
    "A503_SCHEMA_MISSING", "A503_CUTOFF_COUNTERS_INVALID",
    "A503_RUN_TABLE_UNAVAILABLE", "A503_RUN_COLUMNS_MISSING",
    "A503_EXACT_TABLE_UNAVAILABLE", "A503_EXACT_COLUMNS_MISSING",
    "A503_CANONICAL_TABLE_UNAVAILABLE", "A503_CANONICAL_COLUMNS_MISSING",
    "A503_ROW_BUDGET", "A503_TIME_BUDGET",
    "A503_RECEIPT_BUDGET", "A503_HOST_CREDENTIALS_UNAVAILABLE",
})
HOST_EXPORT_GENERIC_EXCEPTIONS = frozenset({
    "OSError", "ValueError", "DatabaseError", "TarError",
})
PRIVATE_FAILURE_LOG_BUDGET = 16 * 1024
PRIVATE_REJECTION_LINE = re.compile(r"A330_REJECTED=([A-Za-z][A-Za-z0-9_]{0,90})\Z")


class GuardError(ValueError):
    """Classification returned without raw financial/credential content."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise GuardError(code)


def encoded(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def classify_capture_failure(capture_rc: int, private_stderr: bytes | None,
                             archive_size: int) -> str:
    """Pure, bounded, fail-closed classifier; no raw stderr enters receipts.

    A code 255 alone cannot prove network failure: remote commands may also
    exit 255. This class deliberately records that ambiguity.
    """
    if capture_rc == 124:
        return "SSH_TIMEOUT_124"
    if capture_rc == 255:
        return "SSH_EXIT_255_TRANSPORT_OR_REMOTE"
    if capture_rc == 0:
        return "ARTIFACT_EMPTY" if archive_size == 0 else "CLASSIFIER_NOT_APPLICABLE"
    if private_stderr is None:
        return "REMOTE_LOG_UNAVAILABLE"
    if len(private_stderr) > PRIVATE_FAILURE_LOG_BUDGET:
        return "REMOTE_LOG_OVERSIZED"
    try:
        message = private_stderr.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "REMOTE_LOG_NON_UTF8"
    if any(ord(char) < 32 and char not in "\t\r\n" for char in message):
        return "REMOTE_LOG_NON_TEXT"
    rejected: list[str] = []
    for line in message.splitlines():
        if "A330_REJECTED=" not in line:
            continue
        match = PRIVATE_REJECTION_LINE.fullmatch(line)
        if match is None:
            return "REMOTE_REJECTION_MALFORMED"
        rejected.append(match.group(1))
    if len(rejected) != 1:
        return "REMOTE_REJECTION_AMBIGUOUS" if rejected else "REMOTE_EXIT_UNCLASSIFIED"
    if capture_rc != 2:
        return "REMOTE_REJECTION_EXIT_MISMATCH"
    code = rejected[0]
    if code in HOST_EXPORT_REJECTION_ALLOWLIST:
        return "REMOTE_GUARD_" + code
    if code in HOST_EXPORT_GENERIC_EXCEPTIONS:
        return "REMOTE_EXCEPTION_" + code.upper()
    return "REMOTE_REJECTION_UNALLOWLISTED"


def bounded_private_failure_log(path: Path, private_root: Path, *,
                                expected_name: str = "a330-private.log") -> bytes | None:
    """Read at most budget+1 bytes from an isolated, non-symlink runner file."""
    require(private_root.is_absolute() and private_root.resolve() == private_root
            and private_root.is_dir() and not private_root.is_symlink()
            and path.parent == private_root and path.name == expected_name
            and expected_name in ("a330-private.log", "a502-private.log",
                                  "a503-private.log"),
            "A330_CLASSIFIER_PRIVATE_PATH_INVALID")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return None
        return stream.read(PRIVATE_FAILURE_LOG_BUDGET + 1)


def sanitized_capture_failure(capture_rc: int, private_stderr: bytes | None,
                              archive_size: int, *, history_only: bool = False) -> dict:
    return {
        "schema": HISTORY_SCHEMA if history_only else SCHEMA,
        "state": "NO_VERIFICADO",
        "failure_class": classify_capture_failure(capture_rc, private_stderr,
                                                  archive_size),
        "host_purge_allowed": False, "physical_host_delete_count": 0,
        "real_orders_sent_by_action": 0,
        **({"paper_runtime_state": "NO_VERIFICADO",
            "observer_pins_state": "UNAVAILABLE"} if history_only else {}),
    }


def sha_file(path: Path, deadline: float) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            require(time.monotonic() < deadline, "A330_TIME_BUDGET")
            block = stream.read(1024**2)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def check_quiet_file(path: Path, maximum: int, *, sqlite_file: bool = False) -> tuple:
    require(path.is_absolute() and path.resolve() == path and
            not any(directory.is_symlink() for directory in path.parents),
            "A330_SOURCE_ALIAS")
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise GuardError("A330_SOURCE_UNAVAILABLE") from exc
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
            4096 <= info.st_size <= maximum, "A330_SOURCE_UNSAFE_FILE")
    if sqlite_file:
        for suffix in SIDECARS:
            sidecar = Path(str(path) + suffix)
            require(not sidecar.exists(), "A330_SQLITE_SIDECAR_PRESENT")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns)


def available_memory() -> int:
    return next((int(line.split()[1]) * 1024
                 for line in Path("/proc/meminfo").read_text().splitlines()
                 if line.startswith("MemAvailable:")), 0)


def host_gate(source: Path = HOST_SOURCE, observer: Path = HOST_OBSERVER) -> dict:
    """A315-equivalent CLOSED/PAPER/0 idle precondition, no host writes."""
    require(shutil.disk_usage("/").free >= 4 * 1024**3,
            "A330_HOST_DISK_RESERVE")
    require(available_memory() >= 512 * 1024**2,
            "A330_HOST_MEMORY_RESERVE")
    for suffix in HOST_TIMERS:
        result = subprocess.run(
            ["systemctl", "show", "porota-a3-history-" + suffix + "-rc6.timer",
             "--property=ActiveState,UnitFileState"],
            capture_output=True, text=True, timeout=3)
        entries = dict(row.split("=", 1) for row in result.stdout.splitlines()
                       if "=" in row)
        require(result.returncode == 0 and
                entries == {"ActiveState": "inactive", "UnitFileState": "masked"},
                "A330_A3_TIMER_NOT_QUARANTINED")
    writer = subprocess.run(
        ["systemctl", "is-active", "porota-local-eod-history-rc6.service"],
        capture_output=True, text=True, timeout=3)
    require(writer.stdout.strip() in ("inactive", "failed"),
            "A330_HISTORY_WRITER_ACTIVE")
    source_stat = check_quiet_file(source, 1024**3, sqlite_file=True)
    # With immutable=1 and no sidecars, SQLite cannot create/alter host WAL-shm.
    # A live WAL observer is NO_VERIFICADO instead of making an unsafe snapshot.
    check_quiet_file(observer, 4 * 1024**3, sqlite_file=True)
    uri = "file:" + quote(str(observer), safe="/") + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True, timeout=.2) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute("SELECT mode,real_orders_sent,session_state,heartbeat_at "
                         "FROM observer_state WHERE id=1").fetchone()
    require(row is not None and row[:3] ==
            ("PRODUCTION_PAPER", 0, "MARKET_CLOSED"), "A330_CLOSED_PAPER_ZERO_REQUIRED")
    try:
        seen = datetime.fromisoformat(row[3].replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        require(seen.tzinfo is not None and seen.utcoffset() is not None,
                "A330_HEARTBEAT_UNZONED")
        age = (now - seen.astimezone(timezone.utc)).total_seconds()
    except (AttributeError, TypeError, ValueError) as exc:
        raise GuardError("A330_HEARTBEAT_INVALID") from exc
    require(0 <= age <= 180, "A330_HEARTBEAT_STALE")
    return {"source_stat": source_stat, "gated_at_utc": now.isoformat(),
            "paper_zero_closed": True, "a3_timers_masked": True,
            "history_writer_idle": True}


def host_history_gate(source: Path = HOST_SOURCE) -> dict:
    """A500: evidence of a quiet history file, never a PAPER/runtime gate.

    No observer path or SQLite connection is opened. This method does not
    authorize physical retention deletion or certify zero real runtime orders.
    """
    require(shutil.disk_usage("/").free >= 4 * 1024**3,
            "A330_HOST_DISK_RESERVE")
    require(available_memory() >= 512 * 1024**2,
            "A330_HOST_MEMORY_RESERVE")
    for suffix in HOST_TIMERS:
        result = subprocess.run(
            ["systemctl", "show", "porota-a3-history-" + suffix + "-rc6.timer",
             "--property=ActiveState,UnitFileState"],
            capture_output=True, text=True, timeout=3)
        rows = [row.split("=", 1) for row in result.stdout.splitlines()]
        entries = dict(rows) if all(len(row) == 2 for row in rows) else {}
        require(result.returncode == 0 and len(rows) == 2 and
                len(entries) == 2 and
                entries == {"ActiveState": "inactive", "UnitFileState": "masked"},
                "A330_A3_TIMER_NOT_QUARANTINED")
    for service in ("porota-local-eod-history-rc6.service",
                    "porota-history-postclose-rc6.service"):
        result = subprocess.run(["systemctl", "is-active", service],
                                capture_output=True, text=True, timeout=3)
        require(result.returncode in (0, 3) and
                result.stdout.strip() == "inactive",
                "A500_HISTORY_WRITER_NOT_INACTIVE")
    source_stat = check_quiet_file(source, 1024**3, sqlite_file=True)
    return {"source_stat": source_stat,
            "gated_at_utc": datetime.now(timezone.utc).isoformat(),
            "paper_runtime_state": "NO_VERIFICADO", "a3_timers_masked": True,
            "both_history_writers_inactive": True}


@contextmanager
def quiet_history_connection(source: Path, gate: dict | None,
                             deadline: float, *, census: bool = False):
    """A502/A503 shared immutable SQLite and race guards, with no sidecars.

    The /proc/self/fd binding prevents path-swap redirection. Immutable SQLite
    cannot replay WAL, so dangling symlinks, live writers and inode changes
    fail closed. Named writer gates cannot prove a global host-wide lock.
    """
    timeout_code = "A503_TIME_BUDGET" if census else "A502_TIME_BUDGET"
    require(time.monotonic() < deadline, timeout_code)
    if gate is None:
        gate = host_history_gate(source)
    require(gate.get("both_history_writers_inactive") is True and
            gate.get("a3_timers_masked") is True,
            "A500_HISTORY_WRITER_NOT_INACTIVE")
    expected = gate.get("source_stat")
    require(isinstance(expected, tuple) and len(expected) == 5,
            "A502_SOURCE_CHANGED_SINCE_GATE")

    def stable() -> tuple:
        # lexists also rejects dangling symlinks, unlike Path.exists().
        for suffix in SIDECARS:
            require(not os.path.lexists(str(source) + suffix),
                    "A502_HISTORY_SIDECAR_PRESENT")
        return check_quiet_file(source, 1024**3, sqlite_file=True)

    require(stable() == expected, "A502_SOURCE_CHANGED_SINCE_GATE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | \
        getattr(os, "O_NOATIME", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(source, flags)
    try:
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            current = (info.st_dev, info.st_ino, info.st_size,
                       info.st_mtime_ns, info.st_ctime_ns)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                    current == expected, "A502_SOURCE_FD_MISMATCH")
            require(time.monotonic() < deadline, timeout_code)
            uri = "file:" + quote("/proc/self/fd/" + str(stream.fileno()),
                                  safe="/") + "?mode=ro&immutable=1"
            try:
                db = sqlite3.connect(uri, uri=True, timeout=.2)
                try:
                    db.execute("PRAGMA query_only=ON")
                    db.execute("PRAGMA trusted_schema=OFF")
                    db.set_progress_handler(
                        lambda: int(time.monotonic() >= deadline), 1000)
                    yield db, gate
                finally:
                    db.close()
            except sqlite3.Error as exc:
                raise GuardError(timeout_code if
                                 time.monotonic() >= deadline else
                                 "A502_SQLITE_UNAVAILABLE") from exc
            after = os.fstat(stream.fileno())
            after_id = (after.st_dev, after.st_ino, after.st_size,
                        after.st_mtime_ns, after.st_ctime_ns)
            require(after_id == expected, "A502_SOURCE_CHANGED_DURING_SCAN")
    finally:
        # os.fdopen normally closes this descriptor. Also cover setup failure.
        try:
            os.close(fd)
        except OSError:
            pass
    require(time.monotonic() < deadline, timeout_code)
    require(stable() == expected, "A502_SOURCE_CHANGED_DURING_SCAN")


def history_aggregate(source: Path = HOST_SOURCE, *, gate: dict | None = None,
                      deadline: float | None = None) -> dict:
    """A502 date buckets from a quiet history inode; no raw rows or identities."""
    if deadline is None:
        deadline = time.monotonic() + AGGREGATE_HOST_SECONDS
    counters = {"total_versions": 0, "within_365d": 0,
                "older_than_365d": 0, "invalid_date": 0, "future_date": 0}
    with quiet_history_connection(source, gate, deadline) as (db, gate):
        kinds = [row[0] for row in db.execute(
            "SELECT type FROM sqlite_master WHERE name=?",
            ("history_versions_v2",))]
        columns = {row[1] for row in db.execute(
            'PRAGMA table_info("history_versions_v2")')}
        require(kinds == ["table"] and "date" in columns,
                "A502_LEGACY_DATE_SCHEMA")
        try:
            observed = datetime.fromisoformat(
                gate["gated_at_utc"].replace("Z", "+00:00"))
            require(observed.tzinfo is not None and
                    observed.utcoffset() is not None,
                    "A502_GATE_CLOCK_INVALID")
        except (KeyError, AttributeError, TypeError, ValueError) as exc:
            if isinstance(exc, GuardError):
                raise
            raise GuardError("A502_GATE_CLOCK_INVALID") from exc
        today_art = observed.astimezone(
            ZoneInfo("America/Argentina/Buenos_Aires")).date()
        cutoff = today_art - timedelta(days=364)
        # No ORDER/GROUP BY: avoid SQLite disk-backed temp tables.
        # Never materialize unbounded TEXT/BLOB in the Python runner.
        cursor = db.execute(
            "SELECT CASE WHEN typeof(\"date\")='text' "
            "AND length(\"date\")=10 THEN \"date\" ELSE NULL END "
            'FROM "history_versions_v2"')
        while True:
            require(time.monotonic() < deadline, "A502_TIME_BUDGET")
            chunk = cursor.fetchmany(1024)
            if not chunk:
                break
            require(counters["total_versions"] + len(chunk) <=
                    AGGREGATE_MAX_ROWS, "A502_ROW_BUDGET")
            for (value,) in chunk:
                counters["total_versions"] += 1
                if not isinstance(value, str) or len(value) != 10:
                    counters["invalid_date"] += 1
                    continue
                try:
                    day = date.fromisoformat(value)
                except ValueError:
                    counters["invalid_date"] += 1
                    continue
                if day.isoformat() != value:
                    counters["invalid_date"] += 1
                elif day > today_art:
                    counters["future_date"] += 1
                elif day >= cutoff:
                    counters["within_365d"] += 1
                else:
                    counters["older_than_365d"] += 1
    require(counters["total_versions"] > 0, "A502_EMPTY_HISTORY")
    return {"schema": AGGREGATE_SCHEMA, "state": "NO_VERIFICADO",
            "aggregate_status": "SOURCE_COUNTS_ONLY", **counters,
            "window_days": 365, "observer_database_opened": False,
            "provider_pit_authenticated": False,
            "external_consumer_universe_verified": False,
            "source_mutated_by_action": False,
            "host_purge_allowed": False, "physical_host_delete_count": 0,
            "real_orders_sent_by_action": 0}


def _cutoff_label(value: object, allowed: frozenset[str]) -> str:
    return value if type(value) is str and value in allowed else "OTHER_REDACTED"


def _cutoff_iso_date(value: object) -> str | None:
    if type(value) is not str or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _cutoff_columns(db: sqlite3.Connection, table: str,
                    columns: frozenset[str]) -> None:
    # Table names are hardcoded within this module, never user-supplied.
    unavailable, incomplete = CUTOFF_SCHEMA_GUARDS[table]
    kinds = [row[0] for row in db.execute(
        "SELECT type FROM sqlite_master WHERE name=?", (table,))]
    require(kinds == ["table"], unavailable)
    present = {row[1] for row in db.execute('PRAGMA table_info("' + table + '")')}
    require(columns <= present, incomplete)


def history_cutoff_census(source: Path = HOST_SOURCE, *,
                          gate: dict | None = None,
                          deadline: float | None = None) -> dict:
    """A503 optional bounded COLD census; never accesses HOT or provider APIs.

    Counts canonical daily rows, *not* true market-calendar missing candles.
    Repair state is a persisted marker, never a provider completeness proof.
    """
    if deadline is None:
        deadline = time.monotonic() + AGGREGATE_HOST_SECONDS
    with quiet_history_connection(source, gate, deadline, census=True) as (db, _):
        _cutoff_columns(db, "rc6_history_cutoff_repair_runs", frozenset({
            "cutoff", "state", "targets", "archive_imports", "ppi_queries",
            "complete", "failed", "updated_at"}))
        _cutoff_columns(db, "rc6_history_cutoff_repair_exact_v2", frozenset({
            "cutoff", "instrument_type", "source", "state", "error_class",
            "latest_date"}))
        _cutoff_columns(db, "history_canonical_v2", frozenset({
            "instrument_type", "source", "date"}))
        row = db.execute(
            "SELECT substr(state,1,32),targets,archive_imports,ppi_queries,"
            "complete,failed,substr(updated_at,1,10) "
            "FROM rc6_history_cutoff_repair_runs WHERE cutoff=? LIMIT 1",
            (CUTOFF_DAY,)).fetchone()
        if row is None:
            cutoff_run = {"state": "ABSENT", "targets": 0,
                          "archive_imports": 0, "ppi_queries": 0,
                          "complete": 0, "failed": 0,
                          "updated_date": None}
        else:
            state, targets, archive, queries, complete, failed, stamp = row
            values = (targets, archive, queries, complete, failed)
            require(all(type(n) is int and 0 <= n <= CUTOFF_MAX_ROWS for n in values)
                    and complete + failed <= targets, "A503_CUTOFF_COUNTERS_INVALID")
            cutoff_run = {"state": _cutoff_label(state, CUTOFF_STATES),
                          "targets": targets, "archive_imports": archive,
                          "ppi_queries": queries, "complete": complete,
                          "failed": failed, "updated_date": _cutoff_iso_date(stamp)}

        scanned = 0
        repair_family: dict[str, dict] = {}
        repair_source: dict[str, dict] = {}
        repair_error: Counter[str] = Counter()
        repair_cursor = db.execute(
            "SELECT CASE WHEN typeof(instrument_type)='text' "
            "AND length(instrument_type)<=32 THEN instrument_type ELSE NULL END,"
            "CASE WHEN typeof(source)='text' AND length(source)<=32 "
            "THEN source ELSE NULL END,"
            "CASE WHEN typeof(state)='text' AND length(state)<=32 "
            "THEN state ELSE NULL END,"
            "CASE WHEN typeof(error_class)='text' AND length(error_class)<=48 "
            "THEN error_class ELSE NULL END,"
            "CASE WHEN typeof(latest_date)='text' AND length(latest_date)=10 "
            "THEN latest_date ELSE NULL END "
            "FROM rc6_history_cutoff_repair_exact_v2 WHERE cutoff=?", (CUTOFF_DAY,))
        while True:
            require(time.monotonic() < deadline, "A503_TIME_BUDGET")
            rows = repair_cursor.fetchmany(1024)
            if not rows:
                break
            require(scanned + len(rows) <= CUTOFF_MAX_ROWS, "A503_ROW_BUDGET")
            scanned += len(rows)
            for family, source_name, state, error, last in rows:
                family = _cutoff_label(family, CUTOFF_FAMILIES)
                source_name = _cutoff_label(source_name, CUTOFF_SOURCES)
                state = _cutoff_label(state, CUTOFF_STATES)
                error = "NONE" if error is None else _cutoff_label(error, CUTOFF_ERRORS)
                last = _cutoff_iso_date(last)
                bucket = repair_family.setdefault(family, {
                    "family": family, "rows": 0, "complete": 0,
                    "failed": 0, "incomplete": 0, "latest_date": None})
                source_bucket = repair_source.setdefault(source_name, {
                    "source": source_name, "rows": 0, "complete": 0,
                    "failed": 0, "incomplete": 0, "latest_date": None})
                for part in (bucket, source_bucket):
                    part["rows"] += 1
                    part["complete" if state == "COMPLETE" else
                         "failed" if state == "FAILED" else "incomplete"] += 1
                    if last is not None and (part["latest_date"] is None or
                                             last > part["latest_date"]):
                        part["latest_date"] = last
                repair_error[error] += 1
        repair_total = scanned

        coverage: dict[tuple[str, str], dict] = {}
        invalid_dates = 0
        canonical_total = 0
        canonical_recent = 0
        canonical_cursor = db.execute(
            "SELECT CASE WHEN typeof(instrument_type)='text' "
            "AND length(instrument_type)<=32 THEN instrument_type ELSE NULL END,"
            "CASE WHEN typeof(source)='text' AND length(source)<=32 "
            "THEN source ELSE NULL END,"
            "CASE WHEN typeof(date)='text' AND length(date)=10 THEN date "
            "ELSE NULL END FROM history_canonical_v2")
        while True:
            require(time.monotonic() < deadline, "A503_TIME_BUDGET")
            rows = canonical_cursor.fetchmany(1024)
            if not rows:
                break
            require(scanned + len(rows) <= CUTOFF_MAX_ROWS, "A503_ROW_BUDGET")
            scanned += len(rows)
            for family, source_name, session in rows:
                canonical_total += 1
                family = _cutoff_label(family, CUTOFF_FAMILIES)
                source_name = _cutoff_label(source_name, CUTOFF_SOURCES)
                key = family, source_name
                bucket = coverage.setdefault(key, {
                    "family": family, "source": source_name, "rows": 0,
                    "since_cutoff": 0, "earliest_date": None,
                    "latest_date": None})
                bucket["rows"] += 1
                session = _cutoff_iso_date(session)
                if session is None:
                    invalid_dates += 1
                    continue
                if session >= CUTOFF_DAY:
                    bucket["since_cutoff"] += 1
                    canonical_recent += 1
                if bucket["earliest_date"] is None or session < bucket["earliest_date"]:
                    bucket["earliest_date"] = session
                if bucket["latest_date"] is None or session > bucket["latest_date"]:
                    bucket["latest_date"] = session
        require(len(coverage) <= CUTOFF_MAX_BUCKETS and
                len(repair_family) <= len(CUTOFF_FAMILIES) and
                len(repair_source) <= len(CUTOFF_SOURCES),
                "A503_RECEIPT_BUDGET")
        receipt = {
            "schema": CUTOFF_SCHEMA, "state": "NO_VERIFICADO",
            "census_status": "COLD_COUNTS_ONLY", "cutoff": CUTOFF_DAY,
            "cutoff_run": cutoff_run, "repair_total": repair_total,
            "repair_by_family": sorted(repair_family.values(),
                                       key=lambda item: item["family"]),
            "repair_by_source": sorted(repair_source.values(),
                                       key=lambda item: item["source"]),
            "repair_by_error": [{"error": key, "rows": repair_error[key]}
                                for key in sorted(repair_error)],
            "canonical_total": canonical_total,
            "canonical_on_or_after_cutoff": canonical_recent,
            "canonical_invalid_dates": invalid_dates,
            "coverage": [coverage[key] for key in sorted(coverage)],
            "resolution": "1D", "provider_requests_by_action": 0,
            "observer_database_opened": False,
            "provider_pit_authenticated": False,
            "external_consumer_universe_verified": False,
            "source_mutated_by_action": False, "host_purge_allowed": False,
            "physical_host_delete_count": 0, "real_orders_sent_by_action": 0}
        require(len(encoded(receipt)) <= AGGREGATE_MAX_RECEIPT_BYTES,
                "A503_RECEIPT_BUDGET")
    return receipt


def sanitize_history_aggregate(raw_path: Path, log_path: Path,
                               runner_root: Path, capture_rc: int) -> dict:
    """Never publish remote stdout/stderr: reconstruct from a fixed schema."""
    root = runner_root.resolve()
    require(root == runner_root and root.is_dir() and not root.is_symlink()
            and raw_path.parent == root and raw_path.name == "a502-private.json"
            and log_path.parent == root and log_path.name == "a502-private.log",
            "A330_CLASSIFIER_PRIVATE_PATH_INVALID")
    if capture_rc != 0:
        private = bounded_private_failure_log(log_path, root,
                                              expected_name="a502-private.log")
        return {"schema": AGGREGATE_SCHEMA, "state": "NO_VERIFICADO",
                "failure_class": classify_capture_failure(capture_rc, private, 0),
                "host_purge_allowed": False, "physical_host_delete_count": 0,
                "real_orders_sent_by_action": 0}
    try:
        descriptor = os.open(raw_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                    0 < info.st_size <= AGGREGATE_MAX_RECEIPT_BYTES,
                    "A502_RECEIPT_INVALID")
            body = decode_json(stream.read(AGGREGATE_MAX_RECEIPT_BYTES + 1))
        fixed = {"schema", "state", "aggregate_status", "total_versions",
                 "within_365d", "older_than_365d", "invalid_date",
                 "future_date", "window_days", "observer_database_opened",
                 "provider_pit_authenticated", "external_consumer_universe_verified",
                 "source_mutated_by_action", "host_purge_allowed",
                 "physical_host_delete_count", "real_orders_sent_by_action"}
        require(type(body) is dict and set(body) == fixed and
                body["schema"] == AGGREGATE_SCHEMA and
                body["state"] == "NO_VERIFICADO" and
                body["aggregate_status"] == "SOURCE_COUNTS_ONLY" and
                body["window_days"] == 365 and
                all(type(body[name]) is int and 0 <= body[name] <= AGGREGATE_MAX_ROWS
                    for name in ("total_versions", "within_365d",
                                 "older_than_365d", "invalid_date", "future_date")) and
                0 < body["total_versions"] == sum(body[name] for name in
                    ("within_365d", "older_than_365d", "invalid_date",
                     "future_date")) and
                all(body[name] is False for name in
                    ("observer_database_opened", "provider_pit_authenticated",
                     "external_consumer_universe_verified", "source_mutated_by_action",
                     "host_purge_allowed")) and
                body["physical_host_delete_count"] == 0 and
                body["real_orders_sent_by_action"] == 0,
                "A502_RECEIPT_INVALID")
        return {name: body[name] for name in sorted(fixed)}
    except (OSError, ValueError, TypeError, GuardError):
        return {"schema": AGGREGATE_SCHEMA, "state": "NO_VERIFICADO",
                "failure_class": "REMOTE_RECEIPT_INVALID",
                "host_purge_allowed": False, "physical_host_delete_count": 0,
                "real_orders_sent_by_action": 0}


def _validate_cutoff_receipt(body: object) -> dict:
    """Strict public allowlist; never forwards unknown host-supplied fields."""
    require(type(body) is dict and set(body) == {
        "schema", "state", "census_status", "cutoff", "cutoff_run",
        "repair_total", "repair_by_family", "repair_by_source",
        "repair_by_error",
        "canonical_total", "canonical_on_or_after_cutoff",
        "canonical_invalid_dates", "coverage", "resolution",
        "provider_requests_by_action", "observer_database_opened",
        "provider_pit_authenticated", "external_consumer_universe_verified",
        "source_mutated_by_action", "host_purge_allowed",
        "physical_host_delete_count", "real_orders_sent_by_action",
    }, "A503_RECEIPT_INVALID")
    require(body["schema"] == CUTOFF_SCHEMA and body["state"] == "NO_VERIFICADO"
            and body["census_status"] == "COLD_COUNTS_ONLY"
            and body["cutoff"] == CUTOFF_DAY and body["resolution"] == "1D"
            and all(body[key] is False for key in (
                "observer_database_opened", "provider_pit_authenticated",
                "external_consumer_universe_verified", "source_mutated_by_action",
                "host_purge_allowed"))
            and all(body[key] == 0 and type(body[key]) is int for key in (
                "provider_requests_by_action", "physical_host_delete_count",
                "real_orders_sent_by_action")), "A503_RECEIPT_INVALID")

    def count(value):
        require(type(value) is int and 0 <= value <= CUTOFF_MAX_ROWS,
                "A503_RECEIPT_INVALID")
        return value

    run = body["cutoff_run"]
    require(type(run) is dict and set(run) == {
        "state", "targets", "archive_imports", "ppi_queries", "complete",
        "failed", "updated_date"} and run["state"] in CUTOFF_STATES,
        "A503_RECEIPT_INVALID")
    for key in ("targets", "archive_imports", "ppi_queries", "complete", "failed"):
        count(run[key])
    require(run["complete"] + run["failed"] <= run["targets"] and
            (run["updated_date"] is None or
             _cutoff_iso_date(run["updated_date"]) == run["updated_date"]),
            "A503_RECEIPT_INVALID")
    for key in ("repair_total", "canonical_total", "canonical_invalid_dates",
                "canonical_on_or_after_cutoff"):
        count(body[key])

    families = body["repair_by_family"]
    sources = body["repair_by_source"]
    errors = body["repair_by_error"]
    coverage = body["coverage"]
    require(type(families) is list and len(families) <= len(CUTOFF_FAMILIES)
            and type(sources) is list and len(sources) <= len(CUTOFF_SOURCES)
            and type(errors) is list and len(errors) <= len(CUTOFF_ERRORS)
            and type(coverage) is list and len(coverage) <= CUTOFF_MAX_BUCKETS,
            "A503_RECEIPT_INVALID")
    for group_name, items, allowed in (
            ("family", families, CUTOFF_FAMILIES),
            ("source", sources, CUTOFF_SOURCES)):
        last_group = ""
        group_sum = 0
        for item in items:
            require(type(item) is dict and set(item) == {
                group_name, "rows", "complete", "failed", "incomplete",
                "latest_date"} and type(item[group_name]) is str
                and item[group_name] in allowed and item[group_name] > last_group,
                "A503_RECEIPT_INVALID")
            last_group = item[group_name]
            require(count(item["rows"]) > 0 and item["rows"] == sum(
                count(item[key]) for key in ("complete", "failed", "incomplete"))
                and (item["latest_date"] is None or
                     _cutoff_iso_date(item["latest_date"]) == item["latest_date"]),
                "A503_RECEIPT_INVALID")
            group_sum += item["rows"]
        require(group_sum == body["repair_total"], "A503_RECEIPT_INVALID")
    repair_sum = body["repair_total"]
    previous_error = ""
    error_sum = 0
    for item in errors:
        require(type(item) is dict and set(item) == {"error", "rows"}
                and type(item["error"]) is str and item["error"] in CUTOFF_ERRORS
                and item["error"] > previous_error, "A503_RECEIPT_INVALID")
        previous_error = item["error"]
        error_sum += count(item["rows"])
    require(error_sum == repair_sum, "A503_RECEIPT_INVALID")
    previous_key = ("", "")
    canonical_sum = 0
    since_sum = 0
    invalid_dates = body["canonical_invalid_dates"]
    for item in coverage:
        require(type(item) is dict and set(item) == {
            "family", "source", "rows", "since_cutoff", "earliest_date",
            "latest_date"} and type(item["family"]) is str
            and type(item["source"]) is str
            and item["family"] in CUTOFF_FAMILIES
            and item["source"] in CUTOFF_SOURCES,
            "A503_RECEIPT_INVALID")
        key = item["family"], item["source"]
        require(key > previous_key, "A503_RECEIPT_INVALID")
        previous_key = key
        rows = count(item["rows"])
        since = count(item["since_cutoff"])
        earliest = item["earliest_date"]
        latest = item["latest_date"]
        require(0 < rows and since <= rows and
                ((earliest is None and latest is None and since == 0) or
                 (type(earliest) is str and type(latest) is str and
                  _cutoff_iso_date(earliest) == earliest and
                  _cutoff_iso_date(latest) == latest and earliest <= latest)),
                "A503_RECEIPT_INVALID")
        canonical_sum += rows
        since_sum += since
    require(canonical_sum == body["canonical_total"] and
            since_sum == body["canonical_on_or_after_cutoff"] and
            invalid_dates <= canonical_sum and since_sum + invalid_dates <= canonical_sum,
            "A503_RECEIPT_INVALID")
    return {key: body[key] for key in sorted(body)}


def sanitize_history_cutoff_census(raw_path: Path, log_path: Path,
                                   runner_root: Path, capture_rc: int) -> dict:
    """A503 runner-only allowlist, independently capped at 8KiB."""
    root = runner_root.resolve()
    require(root == runner_root and root.is_dir() and not root.is_symlink()
            and raw_path.parent == root and raw_path.name == "a503-private.json"
            and log_path.parent == root and log_path.name == "a503-private.log",
            "A330_CLASSIFIER_PRIVATE_PATH_INVALID")
    if capture_rc != 0:
        private = bounded_private_failure_log(log_path, root,
                                              expected_name="a503-private.log")
        return {"schema": CUTOFF_SCHEMA, "state": "NO_VERIFICADO",
                "failure_class": classify_capture_failure(capture_rc, private, 0),
                "host_purge_allowed": False, "physical_host_delete_count": 0,
                "real_orders_sent_by_action": 0}
    try:
        descriptor = os.open(raw_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                    0 < info.st_size <= AGGREGATE_MAX_RECEIPT_BYTES,
                    "A503_RECEIPT_INVALID")
            body = decode_json(stream.read(AGGREGATE_MAX_RECEIPT_BYTES + 1))
        return _validate_cutoff_receipt(body)
    except (OSError, ValueError, TypeError, GuardError):
        return {"schema": CUTOFF_SCHEMA, "state": "NO_VERIFICADO",
                "failure_class": "REMOTE_RECEIPT_INVALID",
                "host_purge_allowed": False, "physical_host_delete_count": 0,
                "real_orders_sent_by_action": 0}


def version_id(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is int and 0 <= value <= 2**63 - 1:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 19:
        parsed = int(value)
        require(parsed <= 2**63 - 1, "A330_VERSION_REFERENCE_AMBIGUOUS")
        return parsed
    raise GuardError("A330_VERSION_REFERENCE_AMBIGUOUS")


def json_pin_ids(obj: object, deadline: float) -> set[int]:
    found: set[int] = set()
    stack = [(obj, "", 0)]
    visited = 0
    while stack:
        require(time.monotonic() < deadline, "A330_TIME_BUDGET")
        node, context, depth = stack.pop()
        visited += 1
        require(visited <= MAX_SCAN_ROWS and depth <= 64,
                "A330_JSON_NODE_BUDGET")
        if isinstance(node, dict):
            stack.extend((v, context + "." + str(k).lower(), depth + 1)
                         for k, v in node.items())
        elif isinstance(node, list):
            stack.extend((v, context, depth + 1) for v in node)
        elif ("version" in context and
              (any(s in context for s in ("id", "pk", "ref"))
               or context.endswith(("version", "versions")))):
            value = version_id(node)
            if value is not None:
                found.add(value)
    require(len(found) <= MAX_PIN_IDS, "A330_PIN_BUDGET")
    return found


def decode_json(payload: bytes) -> object:
    def unique(pairs):
        doc = {}
        for key, val in pairs:
            require(key not in doc, "A330_JSON_DUPLICATE_KEY")
            doc[key] = val
        return doc

    try:
        return json.loads(payload, object_pairs_hook=unique,
                          parse_constant=lambda _: require(False, "A330_JSON_NONFINITE"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, GuardError):
            raise
        raise GuardError("A330_JSON_INVALID") from exc


def scan_json_group(directory: Path, deadline: float) -> dict:
    if not directory.exists():
        return {"state": "UNAVAILABLE", "files": 0, "pins": []}
    require(directory.is_dir() and not directory.is_symlink(),
            "A330_JSON_DIRECTORY_UNSAFE")
    paths = [p for p in directory.iterdir()
             if p.suffix.lower() in (".json", ".jsonl", ".ndjson")]
    require(len(paths) <= MAX_JSON_FILES, "A330_JSON_FILE_BUDGET")
    total = 0
    ids: set[int] = set()
    for path in sorted(paths):
        require(time.monotonic() < deadline, "A330_TIME_BUDGET")
        require(not path.is_symlink() and path.is_file() and
                stat.S_ISREG(path.stat().st_mode) and path.stat().st_nlink == 1,
                "A330_JSON_FILE_UNSAFE")
        before = path.stat()
        size = before.st_size
        require(0 < size <= MAX_JSON_FILE_BYTES, "A330_JSON_FILE_BYTES")
        total += size
        require(total <= MAX_JSON_BYTES, "A330_JSON_GROUP_BYTES")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME |
                     os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            entered = os.fstat(handle.fileno())
            require((entered.st_dev, entered.st_ino, entered.st_size,
                     entered.st_mtime_ns) ==
                    (before.st_dev, before.st_ino, before.st_size,
                     before.st_mtime_ns), "A330_JSON_INODE_CHANGED")
            contents = handle.read(MAX_JSON_FILE_BYTES + 1)
            require(len(contents) == size and
                    os.fstat(handle.fileno()).st_mtime_ns == before.st_mtime_ns,
                    "A330_JSON_CHANGED_DURING_READ")
        after = path.stat()
        require((after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                 after.st_ctime_ns) ==
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns), "A330_JSON_PATH_CHANGED")
        if path.suffix.lower() in (".jsonl", ".ndjson"):
            for line in contents.splitlines():
                if line.strip():
                    ids |= json_pin_ids(decode_json(line), deadline)
        else:
            ids |= json_pin_ids(decode_json(contents), deadline)
    return {"state": "SCANNED_BOUNDED_NOT_UNIVERSE_PROOF", "files": len(paths),
            "pins": sorted(ids)} if paths else {"state": "UNAVAILABLE", "files": 0, "pins": []}


def scan_observer_refs(path: Path, deadline: float) -> dict:
    """Projection only: no live observer DB copy and no write transaction."""
    first_stat = check_quiet_file(path, 4 * 1024**3, sqlite_file=True)
    ids: set[int] = set()
    rows = 0
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True, timeout=.2) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA trusted_schema=OFF")
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        db.execute("BEGIN")
        table_names = [name for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        require(len(table_names) <= MAX_TABLES, "A330_OBSERVER_TABLE_BUDGET")
        for name in sorted(table_names):
            quoted = '"' + name.replace('"', '""') + '"'
            columns = [r[1] for r in db.execute(f"PRAGMA table_info({quoted})")]
            relevant = [column for column in columns if
                        ("version" in column.lower() and
                         any(part in column.lower() for part in ("id", "pk", "ref")))
                        or column.lower().endswith(("json", "_payload", "_receipt"))]
            if not relevant:
                continue
            projection = ','.join('"' + c.replace('"', '""') + '"' for c in relevant)
            for values in db.execute(f"SELECT {projection} FROM {quoted}"):
                rows += 1
                require(time.monotonic() < deadline and rows <= MAX_SCAN_ROWS,
                        "A330_OBSERVER_ROW_BUDGET")
                for column, value in zip(relevant, values):
                    if value is None:
                        continue
                    if "version" in column.lower() and any(
                            k in column.lower() for k in ("id", "pk", "ref")):
                        number = version_id(value)
                        if number is not None:
                            ids.add(number)
                    else:
                        require(type(value) in (str, bytes),
                                "A330_OBSERVER_JSON_TYPE")
                        require(len(value) <= MAX_JSON_FILE_BYTES,
                                "A330_OBSERVER_JSON_VALUE_BUDGET")
                        ids |= json_pin_ids(decode_json(value), deadline)
                require(len(ids) <= MAX_PIN_IDS, "A330_PIN_BUDGET")
        db.rollback()  # closes a READ transaction, not a host write
    require(check_quiet_file(path, 4 * 1024**3, sqlite_file=True) == first_stat,
            "A330_OBSERVER_CHANGED_DURING_SCAN")
    return {"state": "SCANNED_PROJECTION_NOT_FULL_DB_SNAPSHOT",
            "rows_examined": rows, "tables": len(table_names),
            "pins": sorted(ids), "full_db_hash_verified": False}


class DigestReader:
    def __init__(self, stream):
        self.stream = stream
        self.sha = hashlib.sha256()
        self.read_bytes = 0

    def read(self, size=-1):
        result = self.stream.read(size)
        self.sha.update(result)
        self.read_bytes += len(result)
        return result


def add_member(archive, name: str, size: int, reader) -> None:
    require(name in ALLOWED_MEMBERS and 0 < size <= ALLOWED_MEMBERS[name],
            "A330_ARCHIVE_MEMBER_INVALID")
    entry = tarfile.TarInfo(name=name)
    entry.size = size
    entry.mode = 0o600
    entry.mtime = 0
    archive.addfile(entry, reader)


def export_bundle(source: Path, observer: Path, data_root: Path,
                  output, deadline: float, gate: dict) -> dict:
    """Only reads a pinned host FD; private tar is emitted to stdout."""
    require(time.monotonic() < deadline, "A330_TIME_BUDGET")
    source_before = check_quiet_file(source, 1024**3, sqlite_file=True)
    require(source_before == gate.get("source_stat"),
            "A330_SOURCE_CHANGED_SINCE_PAPER_GATE")
    pins = {
        "schema": PIN_SCHEMA,
        "observed_at_utc": gate["gated_at_utc"],
        "sqlite": scan_observer_refs(observer, deadline),
        "json": scan_json_group(data_root / "paper_v17" / "receipts", deadline),
        "artifact": scan_json_group(data_root / "paper_v17" / "artifacts", deadline),
        "replay": scan_json_group(data_root / "paper_v17" / "replays", deadline),
        "provider_capture_pit_authenticated": False,
        "external_consumer_universe_verified": False,
        "host_purge_allowed": False,
    }
    payload = encoded(pins)
    require(len(payload) <= ALLOWED_MEMBERS[PINS_MEMBER], "A330_PIN_ENVELOPE_BUDGET")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME |
                 os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        actual = os.fstat(stream.fileno())
        require((actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns,
                 actual.st_ctime_ns) == source_before,
                "A330_SOURCE_CHANGED_BEFORE_STREAM")
        hash_reader = DigestReader(stream)
        with tarfile.open(mode="w|", fileobj=output, format=tarfile.PAX_FORMAT) as tar:
            add_member(tar, SOURCE_MEMBER, source_before[2], hash_reader)
            add_member(tar, PINS_MEMBER, len(payload), io.BytesIO(payload))
        require(hash_reader.read_bytes == source_before[2],
                "A330_SOURCE_STREAM_SHORT")
        first = hash_reader.sha.hexdigest()
        stream.seek(0)
        second = hashlib.sha256()
        while True:
            require(time.monotonic() < deadline, "A330_TIME_BUDGET")
            chunk = stream.read(1024**2)
            if not chunk:
                break
            second.update(chunk)
        require(first == second.hexdigest() and
                check_quiet_file(source, 1024**3, sqlite_file=True) == source_before,
                "A330_SOURCE_CHANGED_AFTER_STREAM")
    return {"schema": SCHEMA, "state": "HOST_READ_ONLY_CAPTURE",
            "source_sha256": first, "source_bytes": source_before[2],
            "pin_envelope_sha256": hashlib.sha256(payload).hexdigest(),
            "pin_envelope_bytes": len(payload),
            "captured_at_utc": gate["gated_at_utc"],
            "second_full_hash_equal": True,
            "external_universe_verified": False,
            "source_mutated": False, "host_purge_allowed": False,
            "real_orders_sent_by_action": 0}


def export_history_bundle(source: Path, output, deadline: float, gate: dict) -> dict:
    """A500: no observer read, no host copy, no misleading PAPER/0 attestation.

    A compact unavailable-PIN envelope satisfies the existing A319 runner
    format but explicitly prevents claiming cross-store coverage. The archive
    never includes provider secrets, observer data or live sidecars.
    """
    require(time.monotonic() < deadline, "A330_TIME_BUDGET")
    source_before = check_quiet_file(source, 1024**3, sqlite_file=True)
    require(source_before == gate.get("source_stat"),
            "A500_SOURCE_CHANGED_SINCE_HISTORY_GATE")
    unavailable = {"state": "UNAVAILABLE", "pins": []}
    pins = {"schema": HISTORY_PIN_SCHEMA,
            "observed_at_utc": gate["gated_at_utc"],
            **{kind: dict(unavailable)
               for kind in ("sqlite", "json", "artifact", "replay")},
            "provider_capture_pit_authenticated": False,
            "external_consumer_universe_verified": False,
            "host_purge_allowed": False}
    payload = encoded(pins)
    require(len(payload) <= ALLOWED_MEMBERS[PINS_MEMBER],
            "A330_PIN_ENVELOPE_BUDGET")
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME |
                 os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        actual = os.fstat(stream.fileno())
        require((actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns,
                 actual.st_ctime_ns) == source_before,
                "A330_SOURCE_CHANGED_BEFORE_STREAM")
        hash_reader = DigestReader(stream)
        with tarfile.open(mode="w|", fileobj=output, format=tarfile.PAX_FORMAT) as tar:
            add_member(tar, SOURCE_MEMBER, source_before[2], hash_reader)
            add_member(tar, PINS_MEMBER, len(payload), io.BytesIO(payload))
        require(hash_reader.read_bytes == source_before[2],
                "A330_SOURCE_STREAM_SHORT")
        first = hash_reader.sha.hexdigest()
        stream.seek(0)
        second = hashlib.sha256()
        while True:
            require(time.monotonic() < deadline, "A330_TIME_BUDGET")
            chunk = stream.read(1024**2)
            if not chunk:
                break
            second.update(chunk)
        require(first == second.hexdigest() and
                check_quiet_file(source, 1024**3, sqlite_file=True) == source_before,
                "A330_SOURCE_CHANGED_AFTER_STREAM")
    return {"schema": HISTORY_SCHEMA,
            "state": "HISTORY_COPY_ONLY_HOST_READONLY_CAPTURE",
            "source_sha256": first, "source_bytes": source_before[2],
            "pin_envelope_sha256": hashlib.sha256(payload).hexdigest(),
            "pin_envelope_bytes": len(payload),
            "captured_at_utc": gate["gated_at_utc"],
            "second_full_hash_equal": True,
            "paper_runtime_state": "NO_VERIFICADO",
            "observer_pins_state": "UNAVAILABLE",
            "provider_capture_pit_authenticated": False,
            "external_universe_verified": False,
            "observer_database_opened": False,
            "source_mutated": False, "host_purge_allowed": False,
            "real_orders_sent_by_action": 0}


def write_exclusive(root: Path, name: str, source) -> Path:
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name),
            "A330_RUNNER_FILENAME_INVALID")
    path = root / name
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                         getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as output:
        shutil.copyfileobj(source, output, length=1024**2)
        output.flush()
        os.fsync(output.fileno())
    return path


def extract_bundle(bundle: Path, runner_root: Path, deadline: float) -> dict[str, Path]:
    require(bundle.is_file() and not bundle.is_symlink() and
            stat.S_ISREG(bundle.stat().st_mode) and bundle.stat().st_nlink == 1,
            "A330_RUNNER_BUNDLE_UNSAFE")
    # At least 2x payload scratch free: tar, extracted DB and two A319 clones.
    require(shutil.disk_usage(runner_root).free > 3 * 1024**3,
            "A330_RUNNER_SCRATCH_CAPACITY")
    unpacked: dict[str, Path] = {}
    with tarfile.open(bundle, mode="r:") as archive:
        for member in archive:
            require(time.monotonic() < deadline, "A330_TIME_BUDGET")
            require(member.name in ALLOWED_MEMBERS and member.name not in unpacked
                    and member.isfile() and not member.issym() and not member.islnk()
                    and 0 < member.size <= ALLOWED_MEMBERS[member.name]
                    and member.uid == 0 and member.gid == 0,
                    "A330_TAR_UNEXPECTED_MEMBER")
            source = archive.extractfile(member)
            require(source is not None, "A330_TAR_MEMBER_UNREADABLE")
            with source:
                destination = write_exclusive(runner_root, member.name, source)
            require(destination.stat().st_size == member.size,
                    "A330_TAR_MEMBER_SIZE_MISMATCH")
            unpacked[member.name] = destination
    require(set(unpacked) == set(ALLOWED_MEMBERS), "A330_TAR_MISSING_MEMBER")
    return unpacked


def read_receipt(path: Path, *, history_only: bool = False) -> dict:
    require(path.is_file() and path.stat().st_size <= 128 * 1024,
            "A330_CAPTURE_LOG_BUDGET")
    text = path.read_text(encoding="utf-8", errors="replace")
    receipts = [line.removeprefix("A330_CAPTURE_RECEIPT=") for line in text.splitlines()
                if line.startswith("A330_CAPTURE_RECEIPT=")]
    require(len(receipts) == 1, "A330_CAPTURE_RECEIPT_MISSING")
    result = decode_json(receipts[0].encode())
    expected_schema = HISTORY_SCHEMA if history_only else SCHEMA
    expected_state = ("HISTORY_COPY_ONLY_HOST_READONLY_CAPTURE" if history_only
                      else "HOST_READ_ONLY_CAPTURE")
    require(isinstance(result, dict) and
            result.get("schema") == expected_schema and
            result.get("state") == expected_state and
            result.get("source_mutated") is False and
            result.get("host_purge_allowed") is False and
            result.get("real_orders_sent_by_action") == 0 and
            type(result.get("source_bytes")) is int and
            4096 <= result["source_bytes"] <= ALLOWED_MEMBERS[SOURCE_MEMBER] and
            isinstance(result.get("source_sha256"), str) and
            SHA.fullmatch(result["source_sha256"]) and
            isinstance(result.get("pin_envelope_sha256"), str) and
            SHA.fullmatch(result["pin_envelope_sha256"]) and
            type(result.get("pin_envelope_bytes")) is int and
            1 <= result["pin_envelope_bytes"] <= ALLOWED_MEMBERS[PINS_MEMBER] and
            isinstance(result.get("captured_at_utc"), str) and
            result.get("second_full_hash_equal") is True,
            "A330_CAPTURE_RECEIPT_INVALID")
    if history_only:
        require(result.get("paper_runtime_state") == "NO_VERIFICADO" and
                result.get("observer_pins_state") == "UNAVAILABLE" and
                result.get("observer_database_opened") is False and
                result.get("provider_capture_pit_authenticated") is False and
                result.get("external_universe_verified") is False,
                "A330_CAPTURE_RECEIPT_INVALID")
    return result


def projection_db(path: Path, ids: list[int]) -> None:
    require(len(ids) <= MAX_PIN_IDS and all(type(item) is int and 0 <= item <= 2**63 - 1
                                            for item in ids), "A330_PIN_IDS_INVALID")
    require(ids == sorted(set(ids)), "A330_PIN_IDS_UNSORTED_OR_DUPLICATE")
    require(not path.exists(), "A330_PROJECTION_COLLISION")
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE external_reference_projection (history_version_id INTEGER)")
        db.executemany("INSERT INTO external_reference_projection VALUES (?)",
                       ((version_id,) for version_id in ids))
        db.commit()


def a319_module(repo_root: Path):
    import importlib.util
    filename = repo_root / "scripts" / "rc6_retention_a319_offhost.py"
    require(filename.is_file(), "A319_NOT_REGISTERED_IN_MAIN")
    spec = importlib.util.spec_from_file_location("a330_imported_a319", filename)
    require(spec is not None and spec.loader is not None,
            "A319_IMPORT_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runner_rehearsal(bundle: Path, log: Path, runner_root: Path,
                     repo_root: Path, *, deadline: float,
                     history_only: bool = False) -> dict:
    runner_root = Path(runner_root).resolve()
    require(runner_root.is_dir() and not runner_root.is_symlink() and
            not runner_root.is_relative_to(Path("/opt/porota-trading")),
            "A330_RUNNER_ROOT_UNSAFE")
    receipt = read_receipt(log, history_only=history_only)
    members = extract_bundle(bundle, runner_root, deadline)
    require(sha_file(members[SOURCE_MEMBER], deadline) == receipt["source_sha256"]
            and members[SOURCE_MEMBER].stat().st_size == receipt["source_bytes"],
            "A330_HOST_SOURCE_SHA_NOT_REPRODUCED")
    require(sha_file(members[PINS_MEMBER], deadline) == receipt["pin_envelope_sha256"]
            and members[PINS_MEMBER].stat().st_size == receipt["pin_envelope_bytes"],
            "A330_PIN_ENVELOPE_SHA_NOT_REPRODUCED")
    census = decode_json(members[PINS_MEMBER].read_bytes())
    expected_pin_schema = HISTORY_PIN_SCHEMA if history_only else PIN_SCHEMA
    require(isinstance(census, dict) and
            census.get("schema") == expected_pin_schema and
            census.get("host_purge_allowed") is False and
            census.get("provider_capture_pit_authenticated") is False and
            census.get("external_consumer_universe_verified") is False,
            "A330_PIN_CENSUS_INVALID")
    require(receipt["captured_at_utc"] == census.get("observed_at_utc"),
            "A330_CROSSSTORE_CLOCK_MISMATCH")
    a319 = a319_module(repo_root)
    specs = []
    for kind in ("sqlite", "json", "artifact", "replay"):
        entry = census.get(kind)
        require(isinstance(entry, dict) and isinstance(entry.get("pins"), list),
                "A330_PIN_GROUP_INVALID")
        if history_only:
            require(entry.get("state") == "UNAVAILABLE" and not entry["pins"],
                    "A330_PIN_GROUP_INVALID")
        if entry.get("state") == "UNAVAILABLE":
            require(not entry["pins"], "A330_UNAVAILABLE_WITH_PINS")
            continue
        require(entry.get("state") in
                ("SCANNED_PROJECTION_NOT_FULL_DB_SNAPSHOT",
                 "SCANNED_BOUNDED_NOT_UNIVERSE_PROOF"),
                "A330_PIN_GROUP_STATUS_INVALID")
        pins = entry["pins"]
        require(len(pins) <= MAX_PIN_IDS and pins == sorted(set(pins))
                and all(type(i) is int and 0 <= i <= 2**63 - 1 for i in pins),
                "A330_PIN_GROUP_IDS_INVALID")
        path = runner_root / ("a330-" + kind + (".sqlite" if kind == "sqlite" else ".json"))
        if kind == "sqlite":
            projection_db(path, pins)
        else:
            write_exclusive(runner_root, path.name,
                            io.BytesIO(encoded({"history_version_ids": pins})))
        specs.append({"kind": kind, "path": path.name,
                      "sha256": sha_file(path, deadline)})
    manifest = {"schema": MANIFEST_SCHEMA, "snapshots": specs,
                "provenance": [], "host_exported_pit_authenticity": "NO_VERIFICADO",
                "external_universe": "NO_VERIFICADO"}
    manifest_path = write_exclusive(runner_root, "a330-private-manifest.json",
                                    io.BytesIO(encoded(manifest)))
    report = a319.audit(runner_root, SOURCE_MEMBER, receipt["source_sha256"],
                        manifest_path.name, sha_file(manifest_path, deadline),
                        receipt["captured_at_utc"], simulate=True,
                        budget_seconds=min(240, max(1, int(deadline - time.monotonic()))),
                        index_output="a330-per-row-index.private.jsonl")
    require(report.get("host_purge_allowed") is False and
            report.get("physical_host_delete_count") == 0,
            "A330_A319_UNSAFE_RECEIPT")
    return {"schema": HISTORY_SCHEMA if history_only else SCHEMA,
            "state": "NO_VERIFICADO",
            "failure_class": (
                "OBSERVER_PINS_PIT_AND_EXTERNAL_UNIVERSE_UNVERIFIED"
                if history_only else
                "AUTHENTIC_PROVIDER_PIT_AND_EXTERNAL_UNIVERSE_UNVERIFIED"),
            "source_sha256": receipt["source_sha256"],
            "source_bytes": receipt["source_bytes"],
            "private_index_sha256": report.get("per_row_index", {}).get("sha256"),
            "indexed_versions": report.get("per_row_index", {}).get("rows"),
            "a319_copy_only_state": report.get("state"),
            "a319_decision_counts": report.get("decision_counts"),
            "crossstore_scanned_kinds": sorted(spec["kind"] for spec in specs),
            "real_orders_sent_by_action": 0, "source_mutated": False,
            "host_purge_allowed": False, "physical_host_delete_count": 0,
            "physical_host_bytes_reclaimed": 0,
            "provenance_pit_authenticated": False,
            "external_consumer_universe_verified": False,
            **({"paper_runtime_state": "NO_VERIFICADO",
                "observer_pins_state": "UNAVAILABLE",
                "observer_database_opened": False}
               if history_only else {})}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--host-export", action="store_true")
    group.add_argument("--runner-rehearsal", action="store_true")
    group.add_argument("--classify-capture-failure", action="store_true")
    group.add_argument("--host-history-export", action="store_true")
    group.add_argument("--runner-history-rehearsal", action="store_true")
    group.add_argument("--classify-history-capture-failure", action="store_true")
    group.add_argument("--host-history-aggregate", action="store_true")
    group.add_argument("--sanitize-history-aggregate", action="store_true")
    group.add_argument("--host-history-cutoff-census", action="store_true")
    group.add_argument("--sanitize-history-cutoff-census", action="store_true")
    parser.add_argument("--capture-exit-code", type=int)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--capture-log", type=Path)
    parser.add_argument("--runner-root", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--sanitized-output", type=Path)
    parser.add_argument("--raw", type=Path)
    args = parser.parse_args()
    try:
        if args.host_export:
            require(not any((args.bundle, args.capture_log, args.runner_root,
                             args.sanitized_output, args.raw)) and
                    args.capture_exit_code is None,
                    "A330_HOST_MODE_INPUTS_FORBIDDEN")
            gate = host_gate()
            receipt = export_bundle(HOST_SOURCE, HOST_OBSERVER, HOST_DATA,
                                    sys.stdout.buffer,
                                    time.monotonic() + HOST_SECONDS, gate)
            print("A330_CAPTURE_RECEIPT=" + encoded(receipt).decode(), file=sys.stderr)
        elif args.host_history_export:
            require(not any((args.bundle, args.capture_log, args.runner_root,
                             args.sanitized_output, args.raw)) and
                    args.capture_exit_code is None,
                    "A330_HOST_MODE_INPUTS_FORBIDDEN")
            gate = host_history_gate()
            receipt = export_history_bundle(HOST_SOURCE, sys.stdout.buffer,
                                            time.monotonic() + HOST_SECONDS, gate)
            print("A330_CAPTURE_RECEIPT=" + encoded(receipt).decode(), file=sys.stderr)
        elif args.host_history_aggregate:
            require(not any((args.bundle, args.capture_log, args.runner_root,
                             args.sanitized_output, args.raw)) and
                    args.capture_exit_code is None,
                    "A330_HOST_MODE_INPUTS_FORBIDDEN")
            # Unlike A500 this writes only a fixed, aggregate-only JSON to stdout.
            receipt = history_aggregate(HOST_SOURCE, deadline=time.monotonic() +
                                       AGGREGATE_HOST_SECONDS)
            print(encoded(receipt).decode(), flush=True)
        elif args.host_history_cutoff_census:
            require(not any((args.bundle, args.capture_log, args.runner_root,
                             args.sanitized_output, args.raw)) and
                    args.capture_exit_code is None,
                    "A330_HOST_MODE_INPUTS_FORBIDDEN")
            receipt = history_cutoff_census(
                HOST_SOURCE, deadline=time.monotonic() + AGGREGATE_HOST_SECONDS)
            print(encoded(receipt).decode(), flush=True)
        elif args.sanitize_history_aggregate:
            require(args.raw is not None and args.capture_log is not None and
                    args.runner_root is not None and args.sanitized_output is not None
                    and args.capture_exit_code is not None and
                    args.bundle is None and
                    args.sanitized_output.parent == args.runner_root and
                    args.sanitized_output.name == "a502-sanitized.json",
                    "A330_CLASSIFIER_INPUTS_REQUIRED")
            receipt = sanitize_history_aggregate(args.raw, args.capture_log,
                                                 args.runner_root,
                                                 args.capture_exit_code)
            write_exclusive(args.runner_root, args.sanitized_output.name,
                            io.BytesIO(encoded(receipt) + b"\n"))
            print("A502_AGGREGATE_STATUS=" +
                  ("SOURCE_COUNTS_ONLY" if receipt.get("aggregate_status") ==
                   "SOURCE_COUNTS_ONLY" else "NO_VERIFICADO"))
        elif args.sanitize_history_cutoff_census:
            require(args.raw is not None and args.capture_log is not None and
                    args.runner_root is not None and args.sanitized_output is not None
                    and args.capture_exit_code is not None and
                    args.bundle is None and
                    args.sanitized_output.parent == args.runner_root and
                    args.sanitized_output.name == "a503-sanitized.json",
                    "A330_CLASSIFIER_INPUTS_REQUIRED")
            receipt = sanitize_history_cutoff_census(
                args.raw, args.capture_log, args.runner_root, args.capture_exit_code)
            write_exclusive(args.runner_root, args.sanitized_output.name,
                            io.BytesIO(encoded(receipt) + b"\n"))
            print("A503_CENSUS_STATUS=" +
                  ("COLD_COUNTS_ONLY" if receipt.get("census_status") ==
                   "COLD_COUNTS_ONLY" else "NO_VERIFICADO"))
        elif args.runner_rehearsal or args.runner_history_rehearsal:
            require(all((args.bundle, args.capture_log, args.runner_root,
                         args.sanitized_output)) and args.capture_exit_code is None
                    and args.raw is None,
                    "A330_RUNNER_INPUTS_REQUIRED")
            result = runner_rehearsal(args.bundle, args.capture_log,
                                      args.runner_root, args.repo_root,
                                      deadline=time.monotonic() + RUNNER_SECONDS,
                                      history_only=args.runner_history_rehearsal)
            output = Path(args.sanitized_output)
            require(output.parent.resolve() == args.runner_root.resolve(),
                    "A330_SANITIZED_OUTPUT_OUTSIDE_RUNNER")
            write_exclusive(output.parent, output.name, io.BytesIO(encoded(result) + b"\n"))
            print("A330_SANITIZED_RECEIPT=NO_VERIFICADO|host_purge_allowed=false")
        else:
            root = args.runner_root
            require(root is not None and args.capture_log is not None and
                    args.bundle is not None and args.sanitized_output is not None and
                    args.capture_exit_code is not None and args.raw is None,
                    "A330_CLASSIFIER_INPUTS_REQUIRED")
            require(args.capture_exit_code >= 0 and
                    args.bundle.parent == root and
                    args.bundle.name == "a330-private.tar" and
                    args.sanitized_output.parent == root and
                    args.sanitized_output.name == "a330-sanitized.json",
                    "A330_CLASSIFIER_PRIVATE_PATH_INVALID")
            stderr = bounded_private_failure_log(args.capture_log, root)
            try:
                info = args.bundle.lstat()
                archive_size = (info.st_size if stat.S_ISREG(info.st_mode)
                                and info.st_nlink == 1 else 0)
            except OSError:
                archive_size = 0
            receipt = sanitized_capture_failure(args.capture_exit_code,
                                                stderr, archive_size,
                                                history_only=args.classify_history_capture_failure)
            write_exclusive(root, args.sanitized_output.name,
                            io.BytesIO(encoded(receipt) + b"\n"))
            print("A330_CAPTURE_FAILURE_CLASS=" + receipt["failure_class"])
    except (GuardError, OSError, ValueError, sqlite3.DatabaseError, tarfile.TarError) as exc:
        code = str(exc) if isinstance(exc, GuardError) else type(exc).__name__
        if (args.runner_rehearsal or args.classify_capture_failure or
            args.runner_history_rehearsal or args.classify_history_capture_failure) and \
                args.sanitized_output and args.runner_root:
            try:
                output = Path(args.sanitized_output)
                if output.parent.resolve() == args.runner_root.resolve():
                    write_exclusive(output.parent, output.name, io.BytesIO(encoded({
                        "schema": (HISTORY_SCHEMA if
                                   (args.runner_history_rehearsal or
                                    args.classify_history_capture_failure)
                                   else SCHEMA),
                        "state": "NO_VERIFICADO",
                        "failure_class": code, "host_purge_allowed": False,
                        "physical_host_delete_count": 0,
                        "real_orders_sent_by_action": 0,
                        **({"paper_runtime_state": "NO_VERIFICADO",
                            "observer_pins_state": "UNAVAILABLE"}
                           if (args.runner_history_rehearsal or
                               args.classify_history_capture_failure) else {})
                    }) + b"\n"))
            except OSError:
                pass
        print("A330_REJECTED=" + code, file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
