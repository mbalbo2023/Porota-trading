#!/usr/bin/env python3
"""RC6 A330: bounded private same-session read/export and offhost rehearsal.

Host mode: fixed allowlist, read-only fds/SQLite and stdout tar; NO host files
created, no DB DML, no timers/services modified. Runner mode: an isolated tar
becomes SHA-verified private copies/projections and A319's copy-only rehearsal.
It never certifies external-universe completeness or historical provider PIT.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
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


SCHEMA = "RC6_A330_PRIVATE_SAME_SESSION_V1"
PIN_SCHEMA = "RC6_A330_PIN_CENSUS_V1"
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


def bounded_private_failure_log(path: Path, private_root: Path) -> bytes | None:
    """Read at most budget+1 bytes from an isolated, non-symlink runner file."""
    require(private_root.is_absolute() and private_root.resolve() == private_root
            and private_root.is_dir() and not private_root.is_symlink()
            and path.parent == private_root and path.name == "a330-private.log",
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
                              archive_size: int) -> dict:
    return {
        "schema": SCHEMA, "state": "NO_VERIFICADO",
        "failure_class": classify_capture_failure(capture_rc, private_stderr,
                                                  archive_size),
        "host_purge_allowed": False, "physical_host_delete_count": 0,
        "real_orders_sent_by_action": 0,
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


def read_receipt(path: Path) -> dict:
    require(path.is_file() and path.stat().st_size <= 128 * 1024,
            "A330_CAPTURE_LOG_BUDGET")
    text = path.read_text(encoding="utf-8", errors="replace")
    receipts = [line.removeprefix("A330_CAPTURE_RECEIPT=") for line in text.splitlines()
                if line.startswith("A330_CAPTURE_RECEIPT=")]
    require(len(receipts) == 1, "A330_CAPTURE_RECEIPT_MISSING")
    result = decode_json(receipts[0].encode())
    require(isinstance(result, dict) and result.get("schema") == SCHEMA and
            result.get("state") == "HOST_READ_ONLY_CAPTURE" and
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
                     repo_root: Path, *, deadline: float) -> dict:
    runner_root = Path(runner_root).resolve()
    require(runner_root.is_dir() and not runner_root.is_symlink() and
            not runner_root.is_relative_to(Path("/opt/porota-trading")),
            "A330_RUNNER_ROOT_UNSAFE")
    receipt = read_receipt(log)
    members = extract_bundle(bundle, runner_root, deadline)
    require(sha_file(members[SOURCE_MEMBER], deadline) == receipt["source_sha256"]
            and members[SOURCE_MEMBER].stat().st_size == receipt["source_bytes"],
            "A330_HOST_SOURCE_SHA_NOT_REPRODUCED")
    require(sha_file(members[PINS_MEMBER], deadline) == receipt["pin_envelope_sha256"]
            and members[PINS_MEMBER].stat().st_size == receipt["pin_envelope_bytes"],
            "A330_PIN_ENVELOPE_SHA_NOT_REPRODUCED")
    census = decode_json(members[PINS_MEMBER].read_bytes())
    require(isinstance(census, dict) and census.get("schema") == PIN_SCHEMA and
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
    return {"schema": SCHEMA, "state": "NO_VERIFICADO",
            "failure_class": "AUTHENTIC_PROVIDER_PIT_AND_EXTERNAL_UNIVERSE_UNVERIFIED",
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
            "external_consumer_universe_verified": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--host-export", action="store_true")
    group.add_argument("--runner-rehearsal", action="store_true")
    group.add_argument("--classify-capture-failure", action="store_true")
    parser.add_argument("--capture-exit-code", type=int)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--capture-log", type=Path)
    parser.add_argument("--runner-root", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--sanitized-output", type=Path)
    args = parser.parse_args()
    try:
        if args.host_export:
            require(not any((args.bundle, args.capture_log, args.runner_root,
                             args.sanitized_output)) and args.capture_exit_code is None,
                    "A330_HOST_MODE_INPUTS_FORBIDDEN")
            gate = host_gate()
            receipt = export_bundle(HOST_SOURCE, HOST_OBSERVER, HOST_DATA,
                                    sys.stdout.buffer,
                                    time.monotonic() + HOST_SECONDS, gate)
            print("A330_CAPTURE_RECEIPT=" + encoded(receipt).decode(), file=sys.stderr)
        elif args.runner_rehearsal:
            require(all((args.bundle, args.capture_log, args.runner_root,
                         args.sanitized_output)) and args.capture_exit_code is None,
                    "A330_RUNNER_INPUTS_REQUIRED")
            result = runner_rehearsal(args.bundle, args.capture_log,
                                      args.runner_root, args.repo_root,
                                      deadline=time.monotonic() + RUNNER_SECONDS)
            output = Path(args.sanitized_output)
            require(output.parent.resolve() == args.runner_root.resolve(),
                    "A330_SANITIZED_OUTPUT_OUTSIDE_RUNNER")
            write_exclusive(output.parent, output.name, io.BytesIO(encoded(result) + b"\n"))
            print("A330_SANITIZED_RECEIPT=NO_VERIFICADO|host_purge_allowed=false")
        else:
            root = args.runner_root
            require(root is not None and args.capture_log is not None and
                    args.bundle is not None and args.sanitized_output is not None and
                    args.capture_exit_code is not None,
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
                                                stderr, archive_size)
            write_exclusive(root, args.sanitized_output.name,
                            io.BytesIO(encoded(receipt) + b"\n"))
            print("A330_CAPTURE_FAILURE_CLASS=" + receipt["failure_class"])
    except (GuardError, OSError, ValueError, sqlite3.DatabaseError, tarfile.TarError) as exc:
        code = str(exc) if isinstance(exc, GuardError) else type(exc).__name__
        if (args.runner_rehearsal or args.classify_capture_failure) and \
                args.sanitized_output and args.runner_root:
            try:
                output = Path(args.sanitized_output)
                if output.parent.resolve() == args.runner_root.resolve():
                    write_exclusive(output.parent, output.name, io.BytesIO(encoded({
                        "schema": SCHEMA, "state": "NO_VERIFICADO",
                        "failure_class": code, "host_purge_allowed": False,
                        "physical_host_delete_count": 0,
                        "real_orders_sent_by_action": 0}) + b"\n"))
            except OSError:
                pass
        print("A330_REJECTED=" + code, file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
