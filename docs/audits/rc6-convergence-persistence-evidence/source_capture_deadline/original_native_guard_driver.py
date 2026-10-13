"""Source capture includes closing/cleanup and never waits on a swapped FIFO.

All inputs are synthetic and private. Faults occur at real cleanup/open
boundaries; the absolute consumer deadline remains unchanged.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

import pytest

from rc6_audit_evidence import sqlite_scratch as scratch
from rc6_audit_evidence import sqlite_snapshot as snapshots
from rc6_shadow_runtime.persistence import failure_reason


@pytest.fixture
def private_inputs(monkeypatch):
    for name in tuple(os.environ):
        if name.startswith(scratch.ENV_PREFIX):
            monkeypatch.delenv(name)
    # The source and canonical disk scratch remain outside the code snapshot.
    with tempfile.TemporaryDirectory(prefix="rc6-capture-boundaries-", dir="/workspace") as directory:
        folder = Path(directory)
        source = folder / "observer.sqlite"
        with sqlite3.connect(source) as writer:
            writer.execute("CREATE TABLE observer_state(mode, real_orders_sent)")
            writer.execute("INSERT INTO observer_state VALUES('PRODUCTION_PAPER', 0)")
        writer.close()
        root = folder / "scratch"
        root.mkdir(mode=0o700)
        yield source, root


def source_custody(path):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        member = Path(str(path) + suffix)
        try:
            before = member.lstat()
        except FileNotFoundError:
            continue
        descriptor = os.open(member, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME | os.O_NONBLOCK)
        try:
            digest = hashlib.sha256()
            while block := os.read(descriptor, 65536):
                digest.update(block)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        fields = lambda info: (info.st_dev, info.st_ino, info.st_uid, info.st_gid,
            info.st_mode, info.st_nlink, info.st_size, info.st_atime_ns,
            info.st_mtime_ns, info.st_ctime_ns)
        assert fields(before) == fields(after) == fields(member.lstat())
        result[suffix] = (fields(before), digest.hexdigest())
    return result


def disk_options(root):
    return {"scratch_root": root, "max_scratch_bytes": 128 * 1024 * 1024,
            "reserve_bytes": 0, "min_free_inode_percent": 0}


def delay_until_expired(deadline):
    # Delay only the chosen exit boundary, after its real close/cleanup.
    # This is bounded to .27 seconds from capture entry, not a new deadline.
    time.sleep(max(0, deadline + .02 - time.monotonic()))


@pytest.mark.parametrize("exit_boundary", ("connection_close", "disk_cleanup", "offline_cleanup"))
def test_capture_rejects_success_after_complete_close_and_cleanup(private_inputs, monkeypatch, exit_boundary):
    source, root = private_inputs
    before = source_custody(source)
    events = {}
    deadline = time.monotonic() + .25
    options = {} if exit_boundary == "offline_cleanup" else disk_options(root)
    if exit_boundary == "connection_close":
        original_connect = sqlite3.connect
        class DelayedClose(sqlite3.Connection):
            def close(self):
                super().close()
                events["connection_closed"] = True
                delay_until_expired(deadline)
        def connect(*args, **kwargs):
            return original_connect(*args, factory=DelayedClose, **kwargs)
        monkeypatch.setattr(sqlite3, "connect", connect)
    else:
        original_scratch = snapshots.private_scratch
        @contextmanager
        def delayed_cleanup(*args, **kwargs):
            with original_scratch(*args, **kwargs) as captured:
                events["captured_root"] = captured[0]
                yield captured
            events["cleanup_completed"] = True
            assert not events["captured_root"].exists()
            delay_until_expired(deadline)
        monkeypatch.setattr(snapshots, "private_scratch", delayed_cleanup)
    with pytest.raises(snapshots.SnapshotError, match="^TIME_BUDGET_EXHAUSTED$"):
        with snapshots.readonly_copy(source, deadline=deadline, validate=False, **options) as connection:
            events["private_path"] = Path(connection.execute("PRAGMA database_list").fetchone()[2])
            assert tuple(connection.execute("SELECT * FROM observer_state").fetchone()) == ("PRODUCTION_PAPER", 0)
            assert time.monotonic() < deadline
    assert time.monotonic() >= deadline
    assert events.get("connection_closed") or events.get("cleanup_completed")
    assert not events["private_path"].exists()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    assert source_custody(source) == before
    assert {path.name for path in root.iterdir()} == (set() if not options else {scratch.LOCK})


def test_late_cleanup_preserves_original_consumer_failure_and_still_closes_everything(private_inputs, monkeypatch):
    source, root = private_inputs
    before = source_custody(source)
    original_scratch = snapshots.private_scratch
    deadline = time.monotonic() + .25
    completed = []
    @contextmanager
    def delayed_cleanup(*args, **kwargs):
        try:
            with original_scratch(*args, **kwargs) as captured:
                yield captured
        finally:
            assert not captured[0].exists()
            completed.append(True)
            delay_until_expired(deadline)
    monkeypatch.setattr(snapshots, "private_scratch", delayed_cleanup)
    with pytest.raises(RuntimeError, match="^SYNTHETIC_CONSUMER_FAILURE$"):
        with snapshots.readonly_copy(source, deadline=deadline, validate=False, **disk_options(root)) as connection:
            assert connection.execute("SELECT COUNT(*) FROM observer_state").fetchone()[0] == 1
            raise RuntimeError("SYNTHETIC_CONSUMER_FAILURE")
    assert completed and time.monotonic() >= deadline
    assert source_custody(source) == before
    assert {path.name for path in root.iterdir()} == {scratch.LOCK}
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")


FIFO_CHILD = r'''
import json, os, sys, time
from pathlib import Path
from rc6_audit_evidence import sqlite_snapshot as snapshots
source, root, suffix = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
member = Path(str(source) + suffix)
actual_open = os.open
swapped = []
def swap_at_open(path, flags, *args, **kwargs):
    if Path(path) == member and flags & os.O_NOATIME and not swapped:
        member.rename(Path(str(member) + '.saved'))
        os.mkfifo(member, 0o600)
        swapped.append(True)
        print(json.dumps({'event':'SWAPPED_BEFORE_SOURCE_OPEN', 'flags':flags}), flush=True)
    return actual_open(path, flags, *args, **kwargs)
snapshots.os.open = swap_at_open
started = time.monotonic()
deadline = started + .25
try:
    with snapshots.readonly_copy(source, deadline=deadline, validate=False,
            scratch_root=root, max_scratch_bytes=134217728,
            reserve_bytes=0, min_free_inode_percent=0):
        raise AssertionError('SWAPPED_FIFO_WAS_ACCEPTED')
except snapshots.SnapshotError as error:
    print(json.dumps({'event':'REJECTED', 'reason':str(error),
        'elapsed_seconds':time.monotonic()-started,
        'residue':sorted(p.name for p in root.iterdir())}), flush=True)
'''


@pytest.mark.parametrize("suffix", ("", "-wal"))
def test_source_open_race_to_fifo_is_bounded_and_rejected_before_read(private_inputs, suffix):
    source, root = private_inputs
    if suffix:
        # Actual committed SQLite WAL bytes, copied before its writer closes.
        writer_path = source.parent / "wal-writer.sqlite"
        writer = sqlite3.connect(writer_path)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE observer_state(mode, real_orders_sent)")
            writer.execute("INSERT INTO observer_state VALUES('PRODUCTION_PAPER', 0)")
            writer.commit()
            source.write_bytes(writer_path.read_bytes())
            Path(str(source) + "-wal").write_bytes(Path(str(writer_path) + "-wal").read_bytes())
        finally:
            writer.close()
    environment = {name: value for name, value in os.environ.items() if not name.startswith(scratch.ENV_PREFIX)}
    child = subprocess.Popen([sys.executable, "-B", "-u", "-c", FIFO_CHILD, str(source), str(root), suffix],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    try:
        try:
            output, errors = child.communicate(timeout=1.5)
        except subprocess.TimeoutExpired:
            child.kill()
            output, errors = child.communicate(timeout=2)
            pytest.fail("SOURCE_OPEN_FIFO_EXCEEDED_ABSOLUTE_DEADLINE: " + output.strip())
        assert child.returncode == 0, errors
        events = [json.loads(line) for line in output.splitlines()]
        assert [event["event"] for event in events] == ["SWAPPED_BEFORE_SOURCE_OPEN", "REJECTED"]
        assert events[0]["flags"] & os.O_NONBLOCK
        assert events[1]["reason"] == "SOURCE_SNAPSHOT_BUSY"
        assert events[1]["elapsed_seconds"] < .25
        assert events[1]["residue"] == [scratch.LOCK]
        saved = Path(str(source) + suffix + ".saved")
        assert saved.is_file() and saved.stat().st_size > 0
        assert not Path(str(source) + "-shm").exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=2)


def test_missing_nonblocking_open_support_fails_closed_without_opening_source(private_inputs, monkeypatch):
    source, _ = private_inputs
    expected = snapshots._metadata(source.lstat())
    opened = []
    with monkeypatch.context() as absent:
        absent.delattr(snapshots.os, "O_NONBLOCK")
        absent.setattr(snapshots.os, "open", lambda *a, **k: opened.append((a, k)))
        with pytest.raises(snapshots.SnapshotError, match="^SOURCE_OWNER_CAPTURE_REQUIRED$"):
            snapshots._read(source, expected, deadline=time.monotonic() + .25)
    assert not opened


@pytest.mark.parametrize("token", ("TIME_BUDGET_EXHAUSTED",))
def test_capture_timeout_reason_is_the_exact_nonsecret_contract(token):
    assert failure_reason(snapshots.SnapshotError(token)) == token


@pytest.mark.parametrize("token", ("TIME_PROVIDER_SECRET_SYNTHETIC", "TIME_BUDGET_EXHAUSTED_EXTRA",
    "TIME_BUDGET_EXHAUSTED\nsynthetic-secret", "TIME_BUDGET_EXHAUSTED " ))
def test_unknown_time_message_never_extends_the_safe_taxonomy(token):
    assert failure_reason(snapshots.SnapshotError(token)) == "SnapshotError"
