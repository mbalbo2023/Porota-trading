"""Native private-file custody oracles; no worker/horizon/provider execution."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3

from scripts.rc6_issue465_stress import source_custody_snapshot, source_file_custody


STAT_ATTRIBUTES = ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
                   "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")


def oracle(path: Path):
    """Independent physical stat/hash observation without opening SQLite."""
    physical = path.stat()
    before = {name: getattr(physical, name) for name in STAT_ATTRIBUTES}
    descriptor = os.open(path, os.O_RDONLY | os.O_NOATIME | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            hashed = hashlib.file_digest(stream, "sha256").hexdigest()
    finally:
        os.close(descriptor)
    physical = path.stat()
    after = {name: getattr(physical, name) for name in STAT_ATTRIBUTES}
    assert before == after
    return before, hashed


def test_real_primary_wal_shm_snapshots_bind_eleven_stats_and_sha_without_source_effect(tmp_path, record_property):
    record_property("evidence_scope", "NATIVE_PRIVATE_SQLITE_WAL_STAT_ORACLE_NOT_NATIVE_WORKER_OR_HORIZON")
    database = tmp_path / "source.sqlite"
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE native_fixture (value BLOB NOT NULL)")
        connection.execute("INSERT INTO native_fixture VALUES (?)", (b"synthetic private fixture" * 1024,))
        connection.commit()
        paths = {suffix: Path(str(database) + suffix) for suffix in ("", "-wal", "-shm")}
        assert all(path.is_file() for path in paths.values())
        before = {suffix: oracle(path) for suffix, path in paths.items()}
        snapshot = source_custody_snapshot(database)
        after = {suffix: oracle(path) for suffix, path in paths.items()}
        assert before == after
        assert set(snapshot) == set(paths)
        for suffix, (physical, hashed) in before.items():
            actual = snapshot[suffix]
            assert set(actual) == {"st_dev", "st_ino", "st_uid", "st_gid", "st_mode", "st_nlink", "st_size",
                                   "st_blocks", "st_atime_ns", "st_mtime_ns", "st_ctime_ns", "sha256"}
            assert actual["st_blocks"] == physical["st_blocks"]
            assert actual["st_size"] == physical["st_size"]
            assert actual["sha256"] == hashed
            assert {name: actual[name] for name in STAT_ATTRIBUTES} == physical
    finally:
        connection.close()


def test_same_sparse_bytes_and_logical_size_keep_actual_allocated_blocks_distinct(tmp_path, record_property):
    record_property("evidence_scope", "NATIVE_PRIVATE_SPARSE_ALLOCATION_ORACLE_NOT_INVISIBLE_MUTATION_OR_HORIZON")
    source = tmp_path / "physical-allocation.source"
    descriptor = os.open(source, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.ftruncate(descriptor, 128 * 1024)
        os.fsync(descriptor)
        physical_before, digest_before = oracle(source)
        snapshot_before = source_file_custody(source)
        os.posix_fallocate(descriptor, 0, 128 * 1024)
        os.fsync(descriptor)
        physical_after, digest_after = oracle(source)
        snapshot_after = source_file_custody(source)
        assert physical_after["st_blocks"] > physical_before["st_blocks"]
        assert digest_before == digest_after
        assert physical_before["st_size"] == physical_after["st_size"] == 128 * 1024
        assert snapshot_before["sha256"] == snapshot_after["sha256"] == digest_before
        assert snapshot_before["st_size"] == snapshot_after["st_size"]
        assert snapshot_before["st_blocks"] == physical_before["st_blocks"]
        assert snapshot_after["st_blocks"] == physical_after["st_blocks"]
        # Allocation can also change ctime; no prior ten-field bypass is claimed.
        assert snapshot_before != snapshot_after
    finally:
        os.close(descriptor)
