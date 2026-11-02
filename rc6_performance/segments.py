"""Crash-safe, append-only evidence segments; never prune historical SQLite files.

The manifest is a small atomic pointer.  A rollover copies only capture cursors
and the last-capture state into a new database.  The previous database (and its
WAL) remains an immutable *logical* part of the evidence collection: reports
must read every manifest-listed segment, including late optional exit probes.
"""
import fcntl
import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path


SCHEMA = "rc6.performance-segments.v1"
TABLES = frozenset({"events", "cursors", "state", "exit_probes"})
CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
  source_table TEXT NOT NULL, source_rowid INTEGER NOT NULL,
  recorded_at TEXT NOT NULL, payload_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cursors(
  source_key TEXT NOT NULL, source_table TEXT NOT NULL, last_rowid INTEGER NOT NULL,
  PRIMARY KEY(source_key,source_table));
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS exit_probes(
  paper_id TEXT NOT NULL, stage TEXT NOT NULL, at TEXT NOT NULL,
  detail_json TEXT NOT NULL, PRIMARY KEY(paper_id,stage));
"""


def manifest_path(base):
    base = Path(base).resolve()
    return Path(str(base) + ".segments.json")


def evidence_paths(base):
    """Return all segments in order; malformed/missing entries fail closed."""
    base = Path(base).resolve()
    manifest = manifest_path(base)
    if manifest.is_symlink():
        raise ValueError("EVIDENCE_SEGMENT_MANIFEST_SYMLINK")
    if not manifest.exists():
        return (base,)
    try:
        metadata = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("EVIDENCE_SEGMENT_MANIFEST_INVALID") from exc
    if not isinstance(metadata, dict):
        raise ValueError("EVIDENCE_SEGMENT_MANIFEST_INVALID")
    names = metadata.get("segments")
    if (metadata.get("schema") != SCHEMA or not isinstance(names, list) or
            not names or not all(isinstance(name, str) for name in names) or
            names[0] != base.name or
            metadata.get("active") != names[-1] or len(names) != len(set(names))):
        raise ValueError("EVIDENCE_SEGMENT_MANIFEST_INVALID")
    result = []
    for index, name in enumerate(names):
        if not isinstance(name, str) or (
                name != base.name and
                not (index > 0 and re.fullmatch(
                    re.escape(base.name) + r"\.segment\.[0-9]{6,}\.sqlite", name))):
            raise ValueError("EVIDENCE_SEGMENT_MANIFEST_INVALID")
        file_path = base.parent / name
        if file_path.is_symlink() or not file_path.is_file():
            raise ValueError("EVIDENCE_SEGMENT_MISSING_OR_SYMLINK")
        result.append(file_path)
    return tuple(result)


def evidence_bytes(path):
    """Physical SQLite + WAL + SHM bytes, without changing journal state."""
    return sum(p.stat().st_size for p in
               (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"))
               if p.exists())


def _prepare_next(path, predecessor):
    """Create an empty shard, copying capture offsets in one committed txn."""
    with closing(sqlite3.connect(path, timeout=.005)) as dest:
        dest.execute("PRAGMA journal_mode=WAL")
        dest.executescript(CREATE_TABLES)
        with closing(sqlite3.connect(predecessor.as_uri() + "?mode=ro", uri=True,
                                     timeout=.005)) as old:
            old.row_factory = sqlite3.Row
            old.execute("PRAGMA query_only=ON")
            old.execute("BEGIN")
            with dest:
                dest.executemany("INSERT INTO cursors VALUES(?,?,?)",
                                 ((r["source_key"], r["source_table"], r["last_rowid"])
                                  for r in old.execute("SELECT * FROM cursors")))
                dest.executemany("INSERT INTO state VALUES(?,?)",
                                 ((r["key"], r["value_json"])
                                  for r in old.execute("SELECT * FROM state")))
            old.rollback()


@contextmanager
def capture_write_guard(base):
    """Serialize capture commits with manifest publication.

    Optional exit telemetry may finish on an old shard, but only this guarded
    capture commit can advance source cursors and add capture events.
    Contention is nonblocking: the next bounded capture loop retries.
    """
    lock_path = Path(str(Path(base).resolve()) + ".segments.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(lock_path, flags, 0o600), "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def rollover_when_full(base, maximum_bytes):
    """Switch the active segment only after its successor is durable.

    A failed or interrupted creation leaves the previous manifest unchanged;
    abandoned empty successor files are never reused or deleted implicitly.
    """
    base = Path(base).resolve()
    current = evidence_paths(base)[-1]
    if evidence_bytes(current) < maximum_bytes:
        return True
    # A new SQLite WAL shard cannot fit an artificially tiny quota.
    if maximum_bytes < 65536:
        return False
    lock_path = Path(str(base) + ".segments.lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(lock_path, flags, 0o600), "a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        try:
            segments = evidence_paths(base)
            if evidence_bytes(segments[-1]) < maximum_bytes:
                return True
            configured = os.environ.get("RC6_PERFORMANCE_DISK_RESERVE_MIB", "512")
            if not configured.isdecimal() or int(configured) < 512:
                raise ValueError("EVIDENCE_DISK_RESERVE_CONFIG_INVALID")
            reserve = int(configured) * 1024 * 1024
            # Leave this *additional* headroom after one whole shard.  The
            # operational >8-GiB target is a monitor, not a hard deploy gate.
            if shutil.disk_usage(base.parent).free < reserve + maximum_bytes:
                raise ValueError("EVIDENCE_DISK_RESERVE_REQUIRED")
            # An orphan from a previous failed publication is not overwritten.
            ordinal = len(segments)
            while True:
                successor = base.with_name(base.name + ".segment." + f"{ordinal:06d}" + ".sqlite")
                if not successor.exists() and not Path(str(successor) + "-wal").exists():
                    break
                ordinal += 1
            _prepare_next(successor, segments[-1])
            if evidence_bytes(successor) >= maximum_bytes:
                return False
            names = [p.name for p in segments] + [successor.name]
            manifest = manifest_path(base)
            staging = manifest.with_name(manifest.name + "." + uuid.uuid4().hex + ".tmp")
            payload = json.dumps({"schema": SCHEMA, "segments": names, "active": names[-1]},
                                 separators=(",", ":"), sort_keys=True) + "\n"
            with open(staging, "x", encoding="utf-8") as out:
                out.write(payload)
                out.flush()
                os.fsync(out.fileno())
            os.replace(staging, manifest)
            directory = os.open(base.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
