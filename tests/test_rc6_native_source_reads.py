"""Native readers must preserve the ORIGINAL main/WAL/SHM bytes and custody."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

import pytest

from cg_paper_workspace import artifact_root
from rc6_audit_evidence.sqlite_snapshot import SnapshotError
from rc6_dynamic_universe.runtime import read_runtime
from rc6_performance.common import canonical, digest
from rc6_shadow_runtime import entry_signals, families, lab, source_reads, stages
from rc6_shadow_runtime.worker import ShadowRuntime
from tests.rc6_dashboard_native_fixture import native_fixture


def source_inventory(database):
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(str(database) + suffix)
        if not path.exists():
            continue
        before = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
        try:
            hashed = hashlib.sha256()
            while block := os.read(descriptor, 65536):
                hashed.update(block)
            assert os.fstat(descriptor) == before == path.lstat()
        finally:
            os.close(descriptor)
        result[suffix] = (before.st_dev, before.st_ino, before.st_uid, before.st_gid,
            before.st_mode, before.st_nlink, before.st_size, before.st_atime_ns,
            before.st_mtime_ns, before.st_ctime_ns, hashed.hexdigest())
    return result


@pytest.fixture(scope="module")
def native():
    # /tmp can be tmpfs. The canonical scratch policy requires actual disk.
    with tempfile.TemporaryDirectory(prefix=".rc6-native-source-", dir=Path.cwd()) as directory:
        fixture = native_fixture(Path(directory), count=5)
        # Keep a native, quiescent source connection alive so this guard also
        # covers real WAL/SHM files. It performs no operation after baseline.
        anchor = fixture.store.connect()
        anchor.execute("SELECT count(*) FROM paper_positions").fetchone()
        try:
            yield fixture
        finally:
            anchor.close()


def native_call(name, native):
    database, at = native.database, native.as_of
    info = database.lstat()
    key = digest([str(database), info.st_dev, info.st_ino])
    if name == "runtime":
        value = read_runtime(database, as_of=at)
        assert {row["ticker"] for row in value["catalog"]} >= {f"T{i:03d}" for i in range(5)}
        assert value["source_database_effect"] == "READ_ONLY"
    elif name == "entry_signals":
        boot = entry_signals._read_rows(database, as_of=at, tables=entry_signals.READ_COLUMNS)
        assert boot["bootstrap"] and boot["source_key"] == key
        value = entry_signals._read_rows(database, as_of=at, tables=entry_signals.READ_COLUMNS,
            source_key=key, cursors={table: 0 for table in entry_signals.READ_COLUMNS}, join_positions=True)
        assert not value["bootstrap"] and value["positions"] and value["futures"]
        rows = value["rows"]["decision_evidence_snapshots"]
        assert {json.loads(row["payload_json"]).get("capture_phase") for row in rows} >= {
            "NATIVE_DECISION", "ATOMIC_PAPER_ADMISSION"}
        assert all(hashlib.sha256(row["payload_json"].encode()).hexdigest() == row["payload_sha256"] for row in rows)
    elif name == "lab":
        boot = lab._read(database, at, None, 200)
        assert boot[0] == key and boot[3] == 1
        value = lab._read(database, at, {"source_key": key, "cursors": {table: 0 for table in lab.TABLES}}, 200)
        assert value[0] == key and len(value[2]["decision_evidence_snapshots"]) >= 2
    elif name == "families":
        value = families._read(database, at)
        assert len(value["metadata"]) >= 5 and value["quotes"]
    elif name == "stages":
        original = deepcopy(native.cut["report"]["engines"])
        value = stages.enrich_pipeline(database, {"engines": deepcopy(original)}, as_of=at)
        assert canonical(value["engines"]) == canonical(original)
    elif name == "metadata":
        value = native.worker._metadata(at, at - timedelta(days=1))
        with source_reads.source_connection(database, deadline=time.monotonic() + .15) as (connection, source):
            dataset = connection.execute("SELECT dataset_id FROM paper_workspace WHERE id=1").fetchone()[0]
        assert value[0] == digest({"dataset": dataset, "path": str(database),
            "inode": source.st_ino, "device": source.st_dev})
    else:
        raise AssertionError(name)
    return value


@pytest.mark.parametrize("reader", ("runtime", "entry_signals", "lab", "families", "stages", "metadata"))
def test_all_native_consumers_open_only_private_sqlite_and_preserve_original_custody(native, monkeypatch, reader):
    before = source_inventory(native.database)
    assert "-shm" in before, "The guard must cover an actual native WAL SHM"
    original = sqlite3.connect
    captures = []
    def guarded(database, *args, **kwargs):
        path = str(database).split("?", 1)[0].removeprefix("file:")
        assert Path(path).absolute() != native.database, "SOURCE_SQLITE_OPEN_FORBIDDEN"
        captures.append(Path(path))
        return original(database, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", guarded)
    native_call(reader, native)
    assert captures and all(not path.exists() for path in captures)
    assert source_inventory(native.database) == before


@pytest.mark.parametrize("alias", ("symlink", "parent_symlink", "hardlink"))
def test_native_source_guard_rejects_alias_before_capture_or_worker_resolve(tmp_path, native, monkeypatch, alias):
    if alias == "symlink":
        candidate = tmp_path / "alias.sqlite"
        candidate.symlink_to(native.database)
    elif alias == "parent_symlink":
        parent = tmp_path / "aliased-parent"
        parent.symlink_to(native.database.parent, target_is_directory=True)
        candidate = parent / native.database.name
    else:
        candidate = native.database.parent / "hard-alias.sqlite"
        os.link(native.database, candidate)
    before = source_inventory(native.database)
    monkeypatch.setattr(source_reads, "readonly_copy", lambda *a, **kw: pytest.fail("Alias captured"))
    try:
        with pytest.raises(SnapshotError, match="UNALIASED"):
            with source_reads.source_connection(candidate, deadline=time.monotonic()+.5):
                pass
        with pytest.raises(SnapshotError, match="UNALIASED"):
            ShadowRuntime(candidate, evidence_root=tmp_path / "shadow", source_roots=[])
        assert source_inventory(native.database) == before
    finally:
        candidate.unlink() if alias != "parent_symlink" else parent.unlink()


def test_original_identity_replacement_between_stat_and_copy_is_rejected(tmp_path, monkeypatch):
    from scripts.rc6_issue465_stress import fixture_database
    path = tmp_path / "paper.db"
    fixture_database(path, catalog_count=5)
    other = tmp_path / "replacement.db"
    fixture_database(other, catalog_count=5)
    actual = source_reads.readonly_copy
    @contextmanager
    def replaced(source, **kwargs):
        os.replace(other, source)
        with actual(source, **kwargs) as connection:
            yield connection
    monkeypatch.setattr(source_reads, "readonly_copy", replaced)
    with pytest.raises(SnapshotError, match="SOURCE_SNAPSHOT_BUSY"):
        with source_reads.source_connection(path, deadline=time.monotonic()+.5):
            pytest.fail("New source bytes received the prior inode key")


def test_expired_capture_deadline_never_opens_sqlite_or_changes_source(native, monkeypatch):
    before = source_inventory(native.database)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("Expired deadline opened SQLite"))
    with pytest.raises(SnapshotError, match="TIME_BUDGET_EXHAUSTED"):
        with source_reads.source_connection(native.database, deadline=time.monotonic()-1):
            pass
    assert source_inventory(native.database) == before


def test_canonical_scratch_quota_is_shared_and_failure_preserves_original_source(native, monkeypatch):
    from rc6_audit_evidence import sqlite_scratch
    root = artifact_root(native.database) / "sqlite-read-scratch"
    root.mkdir(mode=0o700)
    monkeypatch.setenv("DATA_DIR", str(native.database.parent.parent))
    monkeypatch.setenv("PAPER_V17_DB_PATH", str(native.database))
    monkeypatch.setenv("POROTA_SQLITE_SCRATCH_ROOT", str(root))
    monkeypatch.setenv("POROTA_SQLITE_SCRATCH_MAX_BYTES", str(512*1024**2))
    monkeypatch.setenv("POROTA_SQLITE_SCRATCH_RESERVE_BYTES", str(2*1024**3))
    monkeypatch.setenv("POROTA_SQLITE_SCRATCH_MIN_FREE_INODE_PERCENT", "10")
    before = source_inventory(native.database)
    with source_reads.source_connection(native.database, deadline=time.monotonic()+.5) as (connection, info):
        copied = Path(connection.execute("PRAGMA database_list").fetchone()[2])
        assert copied.parent.parent == root and info.st_ino == native.database.stat().st_ino
        assert connection.execute("SELECT count(*) FROM paper_positions").fetchone()[0] == 1
    assert set(path.name for path in root.iterdir()) == {sqlite_scratch.LOCK}
    monkeypatch.setenv("POROTA_SQLITE_SCRATCH_MAX_BYTES", "1")
    with pytest.raises(SnapshotError, match="SCRATCH_BYTE_BUDGET_EXHAUSTED"):
        native_call("runtime", native)
    assert source_inventory(native.database) == before


def test_native_worker_restart_keeps_original_keys_clocks_and_source_sidecars(native):
    before = source_inventory(native.database)
    original = native.cut["checkpoint"]
    restarted = ShadowRuntime.from_environment(native.database, source_roots=[])
    report = restarted.tick(native.as_of + timedelta(seconds=30))
    from rc6_shadow_runtime.persistence import read_committed_generation
    cut = read_committed_generation(native.root)
    key = digest([str(native.database), native.database.stat().st_dev, native.database.stat().st_ino])
    assert report["checkpoint_reused"]
    assert cut["checkpoint"]["lab"]["source_key"] == original["lab"]["source_key"] == key
    assert cut["checkpoint"]["entry_signals"]["source_key"] == original["entry_signals"]["source_key"] == key
    assert cut["checkpoint"]["lab"]["active"]
    for paper_id, record in original["lab"]["active"].items():
        successor = cut["checkpoint"]["lab"]["active"][paper_id]
        assert canonical({key: successor[key] for key in ("entry", "native_clocks", "entry_evidence_sha256")}) == canonical(
            {key: record[key] for key in ("entry", "native_clocks", "entry_evidence_sha256")})
    assert report["provider_requests"] == report["real_orders_sent"] == 0
    assert report["real_routes"] == "NOT_CALLED"
    assert source_inventory(native.database) == before


def test_incumbent_512_checkpoint_keeps_business_fingerprint_seed_cursors_and_completed_scopes_after_transport_change(monkeypatch):
    # Reproduce the preceding SOURCE SQLite transport only during native
    # fixture preparation. SQL, business evaluator, writers and checkpoint
    # publisher are the real producers, not fabricated state/clock payloads.
    @contextmanager
    def incumbent_connection(path, **_):
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=.005)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()
    factory = ShadowRuntime.from_environment
    def incumbent_factory(cls, database, **kwargs):
        return factory(database, maximum_files=512, **kwargs)
    with tempfile.TemporaryDirectory(prefix=".rc6-incumbent-512-", dir=Path.cwd()) as directory:
        with monkeypatch.context() as incumbent:
            incumbent.setattr(source_reads, "readonly_copy", incumbent_connection)
            incumbent.setattr(ShadowRuntime, "from_environment", classmethod(incumbent_factory))
            fixture = native_fixture(Path(directory), count=5)
        anchor = fixture.store.connect()
        anchor.execute("SELECT count(*) FROM paper_positions").fetchone()
        try:
            before = source_inventory(fixture.database)
            prior = fixture.cut["checkpoint"]
            current = ShadowRuntime.from_environment(fixture.database, source_roots=[], maximum_files=512)
            assert current.configuration == fixture.worker.configuration
            assert current.configuration_fingerprint(fixture.as_of) == fixture.cut["manifest"]["configuration_fingerprint"]
            # Same-clock restart isolates transport equivalence from a new
            # observation, expiry or session transition.
            report = current.tick(fixture.as_of)
            from rc6_shadow_runtime.persistence import read_committed_generation
            after = read_committed_generation(fixture.root)["checkpoint"]
            assert report["checkpoint_reused"]
            assert after["started_at"] == prior["started_at"]
            for name in ("lab", "entry_signals", "funnel"):
                assert canonical(after[name]) == canonical(prior[name])
            assert source_inventory(fixture.database) == before
        finally:
            anchor.close()


def test_native_writer_change_during_capture_remains_fail_closed_without_a_source_reader(native, monkeypatch):
    from rc6_audit_evidence import sqlite_snapshot
    actual_read = sqlite_snapshot._read
    before = source_inventory(native.database)
    changed = []
    def capture(member, expected, **kwargs):
        result = actual_read(member, expected, **kwargs)
        if member == native.database and kwargs.get("destination") is not None and not changed:
            # This is the authoritative native PAPER producer, deliberately
            # interleaved with capture. No consumer repairs or retries source.
            native.store.state(heartbeat_at=(native.as_of + timedelta(seconds=1)).isoformat(), real_orders_sent=0)
            changed.append(source_inventory(native.database))
        return result
    monkeypatch.setattr(sqlite_snapshot, "_read", capture)
    with pytest.raises(SnapshotError, match="SOURCE_SNAPSHOT_BUSY"):
        read_runtime(native.database, as_of=native.as_of)
    assert changed and changed[0] != before
    assert source_inventory(native.database) == changed[0]
