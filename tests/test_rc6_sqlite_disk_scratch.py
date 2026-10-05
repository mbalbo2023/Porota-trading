"""Native disk scratch, budget/custody failure and killed-reader recovery."""
import json
import os
from pathlib import Path
import select
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

from cg_paper_workspace import artifact_root
from rc6_audit_evidence.sqlite_snapshot import SnapshotError, readonly_copy, snapshot_peak_bytes
from rc6_audit_evidence import sqlite_scratch as scratch
from tests.test_rc6_history_snapshot_copy import inventory, wal_transport


@pytest.fixture
def disk_root():
    # pytest's standard /tmp can itself be tmpfs. The checkout's filesystem
    # must be the same disk-backed kind required by the runtime root.
    with tempfile.TemporaryDirectory(prefix=".rc6-scratch-test-", dir=Path.cwd()) as directory:
        root = Path(directory)/"private"
        root.mkdir(mode=0o700)
        yield root


@pytest.fixture(autouse=True)
def offline_configuration(monkeypatch):
    for key in list(os.environ):
        if key.startswith(scratch.ENV_PREFIX):
            monkeypatch.delenv(key)


def options(root, **overrides):
    return {"scratch_root": root, "max_scratch_bytes": 128*1024*1024,
            "reserve_bytes": 0, "min_free_inode_percent": 0, **overrides}


def footprint(root):
    return sum(max(item.stat().st_size, item.stat().st_blocks*512)
               for item in (root, *root.rglob("*")))


def test_NEW_disk_scratch_reads_main_wal_without_source_shm_and_cleans_only_own_copy(tmp_path, disk_root, record_property):
    folder, source = wal_transport(tmp_path)
    before = inventory(folder)
    with readonly_copy(source, **options(disk_root)) as copied:
        assert copied.execute("SELECT x FROM paper_fills").fetchone()[0] == "pending-wal-row"
        assert copied.execute("PRAGMA temp_store").fetchone()[0] == 2
        captured = Path(copied.execute("PRAGMA database_list").fetchone()[2])
        assert captured.parent.parent == disk_root
        assert scratch._storage_type(disk_root) not in {"tmpfs", "ramfs"}
        assert captured.stat().st_mode & 0o777 == 0o600
        assert captured.parent.stat().st_mode & 0o777 == 0o700
        assert footprint(disk_root) <= snapshot_peak_bytes(
            source.stat().st_size, Path(str(source)+"-wal").stat().st_size)
        record_property("scratch_occupied_bytes", footprint(disk_root))
    assert inventory(folder) == before
    assert sorted(item.name for item in disk_root.iterdir()) == [scratch.LOCK]
    assert not Path(str(source)+"-shm").exists()


def test_NEW_scratch_total_byte_quota_rejects_before_source_copy_or_sqlite(tmp_path, disk_root, monkeypatch):
    folder, source = wal_transport(tmp_path)
    before = inventory(folder)
    def forbidden(*args, **kwargs):
        raise AssertionError("QUOTA_REJECTION_MUST_PRECEDE_COPY_AND_SQLITE")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    import rc6_audit_evidence.sqlite_snapshot as snapshots
    monkeypatch.setattr(snapshots, "_read", forbidden)
    with pytest.raises(SnapshotError, match="SCRATCH_BYTE_BUDGET_EXHAUSTED"):
        with readonly_copy(source, **options(disk_root, max_scratch_bytes=32768)):
            pass
    assert inventory(folder) == before
    assert sorted(item.name for item in disk_root.iterdir()) == [scratch.LOCK]


@pytest.mark.parametrize("resource", ["bytes", "inodes"])
def test_NEW_disk_scratch_preserves_required_free_space_and_inode_reserves(tmp_path, disk_root, monkeypatch, resource):
    folder, source = wal_transport(tmp_path)
    before = inventory(folder)
    actual = os.statvfs(disk_root)
    fields = {name: getattr(actual, name) for name in ("f_bavail", "f_frsize", "f_files", "f_favail")}
    if resource == "bytes":
        fields["f_bavail"] = 0
    else:
        fields["f_favail"] = 0
    monkeypatch.setattr(os, "fstatvfs", lambda descriptor: SimpleNamespace(**fields))
    reason = "SCRATCH_FREE_SPACE_RESERVE_REQUIRED" if resource == "bytes" else "SCRATCH_FREE_INODES_RESERVE_REQUIRED"
    with pytest.raises(SnapshotError, match=reason):
        with readonly_copy(source, **options(disk_root, reserve_bytes=1024, min_free_inode_percent=10)):
            pass
    assert inventory(folder) == before
    assert sorted(item.name for item in disk_root.iterdir()) == [scratch.LOCK]


def test_NEW_runtime_scratch_root_is_primary_dataset_scoped_for_separate_history_source(tmp_path, disk_root, monkeypatch):
    folder, history_source = wal_transport(tmp_path)
    primary = disk_root.parent/"paper"/"observer_v17.db"
    root = artifact_root(primary)/"sqlite-read-scratch"
    root.mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(primary))
    for name, value in {"ROOT": root, "MAX_BYTES": scratch.MAX_BYTES,
                        "RESERVE_BYTES": scratch.RESERVE_BYTES, "MIN_FREE_INODE_PERCENT": 10}.items():
        monkeypatch.setenv(scratch.ENV_PREFIX+name, str(value))
    before = inventory(folder)
    with readonly_copy(history_source, validate=False) as copied:
        assert Path(copied.execute("PRAGMA database_list").fetchone()[2]).parent.parent == root
        assert copied.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
    with pytest.raises(SnapshotError, match="SCRATCH_RUNTIME_BUDGET_WEAKENING_FORBIDDEN"):
        with readonly_copy(history_source, reserve_bytes=0):
            pass
    monkeypatch.setenv(scratch.ENV_PREFIX+"ROOT", str(disk_root))
    with pytest.raises(SnapshotError, match="SCRATCH_CANONICAL_ROOT_REQUIRED"):
        with readonly_copy(history_source):
            pass
    assert inventory(folder) == before


def test_NEW_partial_runtime_scratch_configuration_cannot_fall_back_to_tmp(tmp_path, monkeypatch):
    folder, source = wal_transport(tmp_path)
    before = inventory(folder)
    monkeypatch.setenv(scratch.ENV_PREFIX+"MAX_BYTES", "536870912")
    with pytest.raises(SnapshotError, match="SCRATCH_RUNTIME_CONFIGURATION_REQUIRED"):
        with readonly_copy(source):
            pass
    assert inventory(folder) == before


def test_NEW_disk_scratch_rejects_volatile_tmpfs_instead_of_increasing_it(tmp_path):
    folder, source = wal_transport(tmp_path)
    before = inventory(folder)
    with tempfile.TemporaryDirectory(prefix="rc6-tmpfs-refusal-", dir="/dev/shm") as directory:
        root = Path(directory)
        with pytest.raises(SnapshotError, match="SCRATCH_DISK_BACKED_REQUIRED"):
            with readonly_copy(source, **options(root)):
                pass
        assert list(root.iterdir()) == []
    assert inventory(folder) == before


@pytest.mark.parametrize("kind", ["symlink", "public_root", "unknown_residue", "aliased_lease"])
def test_NEW_unverifiable_scratch_custody_fails_closed_and_preserves_evidence(tmp_path, disk_root, kind):
    folder, source = wal_transport(tmp_path)
    before = inventory(folder)
    root = disk_root
    evidence = None
    if kind == "symlink":
        root = disk_root.parent/"alias"
        root.symlink_to(disk_root, target_is_directory=True)
    elif kind == "public_root":
        root.chmod(0o755)
    elif kind == "unknown_residue":
        evidence = root/"retained-evidence"
        evidence.write_bytes(b"do-not-delete")
        evidence.chmod(0o600)
    else:
        evidence = root/scratch.LOCK
        evidence.write_bytes(b"retained-lock")
        evidence.chmod(0o600)
        os.link(evidence, disk_root.parent/"alias-lock")
    with pytest.raises(SnapshotError, match="SCRATCH_CUSTODY_UNVERIFIED"):
        with readonly_copy(source, **options(root)):
            pass
    if evidence is not None:
        assert evidence.read_bytes() in {b"do-not-delete", b"retained-lock"}
    assert inventory(folder) == before


def test_NEW_disk_scratch_cleans_owned_copy_after_consumer_error_and_expired_deadline(tmp_path, disk_root):
    folder, source = wal_transport(tmp_path)
    before = inventory(folder)
    with pytest.raises(RuntimeError, match="CALLER_FAILURE"):
        with readonly_copy(source, **options(disk_root)) as copied:
            assert copied.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
            raise RuntimeError("CALLER_FAILURE")
    assert sorted(item.name for item in disk_root.iterdir()) == [scratch.LOCK]
    deadline = time.monotonic()+.1
    with pytest.raises(SnapshotError, match="TIME_BUDGET_EXHAUSTED"):
        with readonly_copy(source, deadline=deadline, **options(disk_root)):
            while time.monotonic() <= deadline:
                pass
    assert sorted(item.name for item in disk_root.iterdir()) == [scratch.LOCK]
    assert inventory(folder) == before


@pytest.mark.parametrize('corruption',['hardlink','marker_replacement'])
def test_NEW_cleanup_preserves_current_copy_if_custody_changes_during_consumer_read(tmp_path,disk_root,corruption):
    folder,source=wal_transport(tmp_path)
    before=inventory(folder)
    captured=None
    with pytest.raises(SnapshotError,match='SCRATCH_CUSTODY_UNVERIFIED'):
        with readonly_copy(source,**options(disk_root)) as copied:
            captured=Path(copied.execute('PRAGMA database_list').fetchone()[2])
            if corruption=='hardlink':
                os.link(captured,disk_root.parent/'retained-copy-alias')
            else:
                marker=captured.parent/scratch.MARKER
                marker.write_bytes(b'unverifiable-custody-evidence')
    assert captured.exists()
    assert (captured.parent/scratch.MARKER).exists()
    if corruption=='marker_replacement':
        assert (captured.parent/scratch.MARKER).read_bytes()==b'unverifiable-custody-evidence'
    with pytest.raises(SnapshotError,match='SCRATCH_CUSTODY_UNVERIFIED'):
        with readonly_copy(source,**options(disk_root)):pass
    assert inventory(folder)==before


def test_NEW_killed_reader_residue_counts_toward_quota_but_does_not_block_affordable_restart(tmp_path, disk_root, record_property):
    folder, source = wal_transport(tmp_path)
    before = inventory(folder)
    code = """import signal, sys
from rc6_audit_evidence.sqlite_snapshot import readonly_copy
with readonly_copy(sys.argv[1], scratch_root=sys.argv[2], max_scratch_bytes=134217728,
                   reserve_bytes=0, min_free_inode_percent=0) as captured:
    assert captured.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0] == 1
    print('CAPTURED', flush=True)
    signal.pause()
"""
    environment = {key: value for key, value in os.environ.items() if not key.startswith(scratch.ENV_PREFIX)}
    child = subprocess.Popen([sys.executable, "-c", code, str(source), str(disk_root)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    try:
        assert select.select([child.stdout], [], [], 5)[0], "CHILD_CAPTURE_TIMEOUT"
        assert child.stdout.readline().strip() == "CAPTURED"
        with pytest.raises(SnapshotError, match="SCRATCH_LEASE_BUSY"):
            with readonly_copy(source, **options(disk_root)):
                pass
        child.kill()
        assert child.wait(timeout=5) == -signal.SIGKILL
        residual = sorted(item.name for item in disk_root.iterdir() if item.name != scratch.LOCK)
        assert len(residual) == 1
        occupied = footprint(disk_root)
        peak = snapshot_peak_bytes(source.stat().st_size, Path(str(source)+"-wal").stat().st_size)
        with pytest.raises(SnapshotError, match="SCRATCH_BYTE_BUDGET_EXHAUSTED"):
            with readonly_copy(source, **options(disk_root, max_scratch_bytes=occupied+peak-1)):
                pass
        with readonly_copy(source, **options(disk_root)) as copied:
            assert copied.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 1
        assert sorted(item.name for item in disk_root.iterdir() if item.name != scratch.LOCK) == residual
        inspected = scratch.inspect_scratch(disk_root, max_bytes=134217728, reserve_bytes=0,
                                            min_free_inode_percent=0)
        assert inspected["occupied_bytes"] == occupied
        assert inspected["residual_sessions"] == 1
        assert inspected["owner_uid"] == os.geteuid() and inspected["mode"] == "0700"
        assert inventory(folder) == before
        record_property("scratch_kill_restart", json.dumps({"child_exit": child.returncode,
            "residual_bytes": occupied, "next_reader_estimated_peak_bytes": peak,
            "total_estimated_bytes": occupied+peak, "quota_bytes": 134217728,
            "source_unchanged": True, "residual_preserved": True, "inspection": inspected}))
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
