"""Cheap architectural guards: one coherent image, explicit contract/custody."""
from contextlib import closing, contextmanager
from contextvars import copy_context
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import copy
import os
import sqlite3
import threading
import time

import pytest

from rc6_audit_evidence import sqlite_snapshot as snapshots
from rc6_audit_evidence.sqlite_snapshot import SnapshotError, readonly_copy
from rc6_dynamic_universe.runtime import read_runtime
from rc6_performance.common import canonical
from rc6_shadow_runtime import entry_signals, families, lab, preopen, stages
from rc6_shadow_runtime.persistence import EvidenceFiles
from rc6_shadow_runtime.read_contract import (DEFAULT_READ_CONTRACT, SourceReadContract,
    current_read_contract, query_budget_seconds)
from rc6_shadow_runtime.source_reads import source_connection, source_tick
from rc6_shadow_runtime.worker import ShadowRuntime, session_context
from cg_paper_workspace import artifact_root
from tests.rc6_fixture_sqlite import fixture_sqlite_writers, fixture_write
from tests.rc6_external_disk_fixture import external_disk_fixture
from tests.test_rc6_shadow_runtime_wiring import OPEN, PRE, make_store
from tests.test_rc6_native_source_reads import source_inventory


def small_database(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE observations(value TEXT)")
        connection.execute("INSERT INTO observations VALUES('captured-value')")
    return path


def test_tick_uses_one_physical_image_and_publishes_only_after_validation_cleanup(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path, count=3)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    original_image = snapshots._captured_image
    original_connect = sqlite3.connect
    original_write = EvidenceFiles.write
    original_commit = EvidenceFiles.commit_generation
    images, connections, publications = [], [], []

    @contextmanager
    def capture(path, **kwargs):
        with original_image(path, **kwargs) as image:
            images.append(image[0])
            yield image

    def connect(database, *args, **kwargs):
        assert str(store.path) not in str(database), "Source SQLite must never open"
        connection = original_connect(database, *args, **kwargs)
        if kwargs.get("uri") and "mode=ro" in str(database):
            connections.append(Path(connection.execute("PRAGMA database_list").fetchone()[2]))
        return connection

    def assert_closed():
        assert images and all(not image.exists() for image in images)
        assert worker.last_source_read_receipt["source_unchanged"] is True
        assert worker.last_source_read_receipt["private_image_unchanged"] is True
        assert worker.last_source_read_receipt["cleanup_complete"] is True

    def write(files, *args, **kwargs):
        assert_closed()
        publications.append("preopen")
        return original_write(files, *args, **kwargs)

    def commit(files, *args, **kwargs):
        assert_closed()
        publications.append("generation")
        return original_commit(files, *args, **kwargs)

    monkeypatch.setattr(snapshots, "_captured_image", capture)
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(EvidenceFiles, "write", write)
    monkeypatch.setattr(EvidenceFiles, "commit_generation", commit)
    report = worker.tick(PRE)
    assert len(images) == 1
    assert len(connections) >= 7 and set(connections) == set(images)
    receipt = worker.last_source_read_receipt
    assert receipt["primary_captures"] == 1 and receipt["additional_captures"] == []
    assert {entry["consumer"] for entry in receipt["query_consumers"]} >= {
        "runtime", "metadata", "stages", "families", "lab", "entry_signals"}
    assert receipt["read_contract_sha256"] == worker.read_contract.fingerprint()
    assert receipt["private_image_unchanged"] is True
    assert publications[0] == "preopen" and "generation" in publications
    assert report["real_orders_sent"] == report["provider_requests"] == 0
    assert report["real_routes"] == "NOT_CALLED" and report["ppi_watch"] == "UNTOUCHED"


def test_shared_consumer_rows_identities_clocks_match_standalone_bytes(tmp_path):
    store, _ = make_store(tmp_path, count=4)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])

    def consumers():
        return {"runtime": read_runtime(store.path, as_of=PRE),
            "families": families._read(store.path, PRE),
            "lab": lab._read(store.path, PRE, None, 20),
            "entry": entry_signals._read_rows(store.path, as_of=PRE,
                tables=entry_signals.READ_COLUMNS),
            "metadata": worker._metadata(PRE, PRE - timedelta(days=1)),
            "stages": stages.enrich_pipeline(store.path, {"engines": {}}, as_of=PRE)}

    expected = canonical(consumers())
    with source_tick(store.path) as capture:
        actual = canonical(consumers())
    assert actual == expected
    assert capture.receipt["primary_captures"] == 1
    assert capture.receipt["source_unchanged"] and capture.receipt["cleanup_complete"]


def test_canonical_factory_128_catalogue_uses_one_disk_capture_with_original_source_custody(monkeypatch):
    from rc6_audit_evidence import sqlite_scratch
    with external_disk_fixture(prefix=".rc6-source-factory-128-") as private:
        store, assets = make_store(private, count=128)
        database = Path(store.path)
        artifacts = artifact_root(database)
        scratch_root = artifacts / "sqlite-read-scratch"
        scratch_root.mkdir(parents=True, mode=0o700)
        environment = {"DATA_DIR": str(private), "PAPER_V17_DB_PATH": str(database),
            "HIST_DB_PATH": str(private / "absent-history.db"),
            "POROTA_DYNAMIC_SHADOW_ROOT": str(artifacts / "dynamic-shadow"),
            "POROTA_SHADOW_RUNTIME_ROOT": str(artifacts / "dynamic-shadow"),
            "POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT": str(artifacts / "dynamic-shadow-archive")}
        for name, value in {"ROOT": scratch_root, "MAX_BYTES": 512 * 1024**2,
                "RESERVE_BYTES": 2 * 1024**3, "MIN_FREE_INODE_PERCENT": 10}.items():
            environment[sqlite_scratch.ENV_PREFIX + name] = str(value)
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        before = source_inventory(database)
        original_image = snapshots._captured_image
        images = []
        @contextmanager
        def counted(path, **kwargs):
            with original_image(path, **kwargs) as image:
                images.append(image[0])
                yield image
        monkeypatch.setattr(snapshots, "_captured_image", counted)
        worker = ShadowRuntime.from_environment(database, source_roots=[])
        report = worker.tick(PRE)
        receipt = worker.last_source_read_receipt
        assert len(images) == receipt["primary_captures"] == 1
        assert images[0].parent.parent == scratch_root and not images[0].exists()
        assert receipt["scratch_admission"]["lease"] == "EXCLUSIVE_OWNER"
        assert receipt["source_unchanged"] is True and receipt["cleanup_complete"] is True
        assert receipt["read_contract_sha256"] == DEFAULT_READ_CONTRACT.fingerprint()
        assert len(report["catalog_ready"]) == len(assets) == 128
        assert all(plan["catalog_ready_count"] == 128 for plan in report["engines"].values())
        assert report["real_orders_sent"] == report["provider_requests"] == 0
        assert report["real_routes"] == "NOT_CALLED" and report["ppi_watch"] == "UNTOUCHED"
        assert {entry["consumer"] for entry in receipt["query_consumers"]} >= {
            "runtime", "metadata", "stages", "families", "lab", "entry_signals", "funnel"}
        assert source_inventory(database) == before
        assert set(path.name for path in scratch_root.iterdir()) == {sqlite_scratch.LOCK}


def test_genuine_concurrent_source_writer_blocks_all_publication(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path, count=2)
    root = tmp_path / "shadow"
    worker = ShadowRuntime(store.path, evidence_root=root, source_roots=[])
    metadata = worker._metadata
    writes = []

    def changed(*args):
        result = metadata(*args)
        fixture_write(store.state, heartbeat_at=(PRE + timedelta(seconds=1)).isoformat(),
                      real_orders_sent=0)
        return result

    monkeypatch.setattr(worker, "_metadata", changed)
    monkeypatch.setattr(EvidenceFiles, "write", lambda *a, **kw: writes.append("write"))
    monkeypatch.setattr(EvidenceFiles, "commit_generation", lambda *a, **kw: writes.append("commit"))
    with pytest.raises(SnapshotError, match="SOURCE_SNAPSHOT_BUSY"):
        worker.tick(PRE)
    assert writes == [] and worker.last_source_read_receipt is None
    assert not (root / "CURRENT.json").exists()
    assert not (root / "preopen-2026-10-05.json.gz").exists()


@pytest.mark.parametrize("suffix", ("", "-wal"), ids=("main", "wal"))
def test_private_image_tamper_blocks_publication_with_original_source_unchanged(tmp_path, monkeypatch, suffix):
    store, _ = make_store(tmp_path, count=2)
    root = tmp_path / "shadow"
    worker = ShadowRuntime(store.path, evidence_root=root, source_roots=[])
    before = source_inventory(Path(store.path))
    prepare = worker._prepare_tick
    publications = []
    tampered = []
    def changed(*args):
        prepared = prepare(*args)
        capture = snapshots._ACTIVE_CAPTURE.get()
        member = Path(str(capture._copy)+suffix)
        assert member.exists() and member.stat().st_mode & 0o777 == 0o400
        os.chmod(member, 0o600)
        with member.open("ab") as stream:
            stream.write(b"unauthorized-private-change")
        os.chmod(member, 0o400)
        tampered.append(member)
        return prepared
    monkeypatch.setattr(worker, "_prepare_tick", changed)
    monkeypatch.setattr(EvidenceFiles, "write", lambda *a, **kw: publications.append("write"))
    monkeypatch.setattr(EvidenceFiles, "commit_generation", lambda *a, **kw: publications.append("commit"))
    with pytest.raises(SnapshotError, match="PRIVATE_IMAGE_CHANGED"):
        worker.tick(PRE)
    assert tampered and all(not member.exists() for member in tampered)
    assert publications == [] and worker.last_source_read_receipt is None
    assert source_inventory(Path(store.path)) == before
    assert not (root / "CURRENT.json").exists()


def test_private_image_sha256_check_catches_same_size_tamper_independent_of_metadata(tmp_path, monkeypatch):
    database = small_database(tmp_path / "source.db")
    before = source_inventory(database)
    with pytest.raises(SnapshotError, match="PRIVATE_IMAGE_CHANGED"):
        with source_tick(database) as capture:
            member = capture._copy
            expected = capture._image_inventory[""]
            original_metadata = snapshots._metadata
            def metadata(info):
                if (info.st_dev, info.st_ino) == expected[:2]:
                    # Isolate the byte guard from the independent metadata
                    # guard, while leaving its live expected digest untouched.
                    return expected
                return original_metadata(info)
            monkeypatch.setattr(snapshots, "_metadata", metadata)
            os.chmod(member, 0o600)
            with member.open("r+b") as stream:
                stream.seek(-1, 2)
                old = stream.read(1)
                stream.seek(-1, 2)
                stream.write(bytes([old[0] ^ 1]))
            os.chmod(member, 0o400)
    assert source_inventory(database) == before


def test_sealed_readonly_main_wal_keeps_pending_rows_and_allows_private_shm_indexing(tmp_path):
    from tests.test_rc6_history_snapshot_copy import inventory, wal_transport
    folder, database = wal_transport(tmp_path)
    before = inventory(folder)
    with source_tick(database) as capture:
        for suffix in ("", "-wal"):
            assert Path(str(capture._copy)+suffix).stat().st_mode & 0o777 == 0o400
        with source_connection(database, deadline=time.monotonic()+.25, consumer="lab") as (c, _):
            assert c.execute("SELECT x FROM paper_fills").fetchone()[0] == "pending-wal-row"
        # SHM is a reconstructed private lock/index, not financial Source bytes.
        shm = Path(str(capture._copy)+"-shm")
        assert shm.exists() and shm.stat().st_mode & 0o777 == 0o600
        os.utime(shm, None)
    assert capture.receipt["private_image_unchanged"] is True
    assert capture.receipt["source_unchanged"] is True and capture.receipt["cleanup_complete"] is True
    assert inventory(folder) == before


def test_tick_context_reentry_exception_and_next_tick_have_exact_context_rollback(tmp_path):
    database = small_database(tmp_path / "source.db")
    with pytest.raises(RuntimeError, match="caller failed"):
        with source_tick(database):
            with pytest.raises(ValueError, match="REENTRY_FORBIDDEN"):
                with source_tick(database):
                    pass
            raise RuntimeError("caller failed")
    assert current_read_contract() is DEFAULT_READ_CONTRACT
    assert snapshots._ACTIVE_CAPTURE.get() is None
    with source_tick(database) as capture:
        with source_connection(database, deadline=time.monotonic()+.25, consumer="lab") as (c, _):
            assert c.execute("SELECT value FROM observations").fetchone()[0] == "captured-value"
    assert capture.receipt["source_unchanged"] and capture.receipt["cleanup_complete"]


def test_copied_context_cannot_lend_a_snapshot_to_a_different_thread(tmp_path):
    database = small_database(tmp_path / "source.db")
    errors = []
    with source_tick(database) as capture:
        context = copy_context()
        def borrow():
            try:
                with source_connection(database, deadline=time.monotonic()+.25):
                    raise AssertionError("Different thread borrowed the private image")
            except SnapshotError as error:
                errors.append(str(error))
        child = threading.Thread(target=lambda: context.run(borrow))
        child.start()
        child.join(timeout=2)
        assert not child.is_alive()
    assert errors == ["SOURCE_SNAPSHOT_SCOPE_CUSTODY_REQUIRED"]
    assert capture.receipt["query_consumers"] == []


def test_capture_deadline_is_not_a_query_or_publication_deadline(tmp_path, monkeypatch):
    database = small_database(tmp_path / "source.db")
    now = [100.0]
    monkeypatch.setattr(snapshots.time, "monotonic", lambda: now[0])
    contract = replace(DEFAULT_READ_CONTRACT, capture_budget_seconds=.01,
                       verification_budget_seconds=.01)
    with source_tick(database, contract=contract) as capture:
        # Advancing the clock models consumer/planner CPU, without a sleep.
        now[0] += 10
        with source_connection(database, deadline=now[0]+.25, consumer="lab") as (c, _):
            assert c.execute("SELECT value FROM observations").fetchone()[0] == "captured-value"
    assert capture.receipt["source_unchanged"] and capture.receipt["cleanup_complete"]
    assert capture.receipt["capture_seconds"] == capture.receipt["verification_seconds"] == 0


def test_shared_query_deadline_is_still_fail_closed(tmp_path, monkeypatch):
    database = small_database(tmp_path / "source.db")
    now = [100.0]
    monkeypatch.setattr(snapshots.time, "monotonic", lambda: now[0])
    with pytest.raises(SnapshotError, match="TIME_BUDGET_EXHAUSTED"):
        with source_tick(database):
            with source_connection(database, deadline=now[0]+.25, consumer="lab"):
                now[0] += 1
    assert snapshots._ACTIVE_CAPTURE.get() is None
    assert current_read_contract() is DEFAULT_READ_CONTRACT


@pytest.mark.parametrize("field", ("capture_budget_seconds", "verification_budget_seconds",
    "runtime_query_seconds", "families_query_seconds", "maximum_source_bytes"))
@pytest.mark.parametrize("invalid", (True, 0, -1, float("nan"), float("inf"), "0.5"))
def test_read_contract_invalid_types_or_nonfinite_limits_cannot_weaken_guards(field, invalid):
    with pytest.raises(ValueError, match="INVALID_SOURCE_READ_CONTRACT"):
        replace(DEFAULT_READ_CONTRACT, **{field: invalid})


def test_contract_fingerprint_is_stable_and_actual_runtime_override_is_bound(tmp_path):
    store, _ = make_store(tmp_path, count=1)
    changed = replace(DEFAULT_READ_CONTRACT, runtime_query_seconds=1.0)
    assert changed.fingerprint() != DEFAULT_READ_CONTRACT.fingerprint()
    assert SourceReadContract().fingerprint() == DEFAULT_READ_CONTRACT.fingerprint()
    with source_tick(store.path, contract=changed):
        assert query_budget_seconds("runtime") == 1.0
        assert read_runtime(store.path, as_of=PRE)["safety"]["real_orders_sent"] == 0
        with pytest.raises(ValueError, match="SOURCE_QUERY_CONTRACT_MISMATCH"):
            read_runtime(store.path, as_of=PRE, query_budget_seconds=.5)
    first = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    other = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[],
                          query_budget_seconds=1.0)
    assert first.configuration != other.configuration
    assert other.read_contract.fingerprint() == changed.fingerprint()


def test_entry_and_funnel_queries_use_their_distinct_contract_binding(tmp_path):
    store, _ = make_store(tmp_path, count=1)
    contract = replace(DEFAULT_READ_CONTRACT, entry_signals_query_seconds=.2,
                       funnel_query_seconds=.15)
    with source_tick(store.path, contract=contract) as capture:
        entry_signals._read_rows(store.path, as_of=PRE, tables=entry_signals.READ_COLUMNS)
        entry_signals._read_rows(store.path, as_of=PRE, tables=entry_signals.READ_COLUMNS,
                                consumer="funnel")
        with pytest.raises(ValueError, match="UNKNOWN_SOURCE_QUERY_CONSUMER"):
            entry_signals._read_rows(store.path, as_of=PRE, tables=entry_signals.READ_COLUMNS,
                                    consumer="runtime")
    assert [item["consumer"] for item in capture.receipt["query_consumers"]] == ["entry_signals", "funnel"]


def test_preopen_default_uses_productive_contract_and_preserves_standalone_bytes(tmp_path):
    store, _ = make_store(tmp_path, count=2)
    context = session_context(PRE)
    kwargs = {"as_of": PRE, "session_open": context["opening"], "cutoff": context["cutoff"]}
    before = source_inventory(Path(store.path))
    implicit = preopen.build_preopen_inputs(store.path, **kwargs)
    explicit = preopen.build_preopen_inputs(store.path, query_budget_seconds=2.0, **kwargs)
    assert canonical(implicit) == canonical(explicit)
    limits = implicit["quality"]["source_snapshot_limits"]
    assert limits["total_seconds"] == DEFAULT_READ_CONTRACT.preopen_query_seconds == 2.0
    assert limits["processing_seconds"] == 1.9 and limits["cleanup_reserve_seconds"] == .1
    with source_tick(store.path) as capture:
        shared = preopen.build_preopen_inputs(store.path, **kwargs)
    assert canonical(shared) == canonical(implicit)
    assert [query["consumer"] for query in capture.receipt["query_consumers"]] == ["preopen"]
    assert capture.receipt["private_image_unchanged"] and capture.receipt["source_unchanged"]
    assert source_inventory(Path(store.path)) == before


def test_preopen_custom_contract_is_bound_and_fixture_only_mismatch_fails_closed(tmp_path):
    store, _ = make_store(tmp_path, count=1)
    context = session_context(PRE)
    kwargs = {"as_of": PRE, "session_open": context["opening"], "cutoff": context["cutoff"]}
    contract = replace(DEFAULT_READ_CONTRACT, preopen_query_seconds=1.0)
    with source_tick(store.path, contract=contract) as capture:
        inherited = preopen.build_preopen_inputs(store.path, **kwargs)
        explicit = preopen.build_preopen_inputs(store.path, query_budget_seconds=1.0, **kwargs)
        assert canonical(inherited) == canonical(explicit)
        for mismatch in (2.0, .5):
            with pytest.raises(ValueError, match="SOURCE_QUERY_CONTRACT_MISMATCH"):
                preopen.build_preopen_inputs(store.path, query_budget_seconds=mismatch, **kwargs)
        for invalid in (True, float("nan"), float("inf"), "1.0", 0, -1):
            with pytest.raises(ValueError, match="INVALID_PREOPEN_READ_BUDGET"):
                preopen.build_preopen_inputs(store.path, query_budget_seconds=invalid, **kwargs)
    limits = inherited["quality"]["source_snapshot_limits"]
    assert limits["total_seconds"] == 1.0
    assert limits["processing_seconds"] == .9 and limits["cleanup_reserve_seconds"] == .1
    assert capture.receipt["read_contract_sha256"] == contract.fingerprint()
    assert [query["consumer"] for query in capture.receipt["query_consumers"]] == ["preopen", "preopen"]
    # The public standalone API retains its explicitly smaller incumbent budget.
    standalone = preopen.build_preopen_inputs(store.path, query_budget_seconds=1.0, **kwargs)
    assert canonical(standalone) == canonical(inherited)


def test_material_source_capture_checker_accepts_real_tiny_bound_preopen_open_receipts(tmp_path):
    from scripts.rc6_material_big import source_capture_checks
    store, _ = make_store(tmp_path, count=2)
    before = source_inventory(Path(store.path))
    at = OPEN + timedelta(minutes=1)
    rows = []
    with source_tick(store.path) as preliminary:
        read_runtime(store.path, as_of=at)
    rows.append({"phase": "BOUNDED_READ", "receipt": preliminary.receipt})
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    worker.tick(PRE)
    rows.append({"phase": "PREOPEN", "receipt": worker.last_source_read_receipt})
    report = worker.tick(at)
    rows.append({"phase": "OPEN", "receipt": worker.last_source_read_receipt})
    assert report["phase"] == "OPEN" and report["real_orders_sent"] == 0
    assert report["real_routes"] == "NOT_CALLED" and report["ppi_watch"] == "UNTOUCHED"
    declared = DEFAULT_READ_CONTRACT.document()
    for row in rows:
        assert row["receipt"]["primary_captures"] == 1
        assert all(query["consumer"] + "_query_seconds" in declared
                   for query in row["receipt"]["query_consumers"])
    assert "preopen" in {query["consumer"] for query in rows[1]["receipt"]["query_consumers"]}
    actual = {"source_read_receipts": rows}
    assert all(source_capture_checks(actual, DEFAULT_READ_CONTRACT).values())
    # The exact incumbent ledger defect must stay RED in the actual checker.
    unregistered = copy.deepcopy(actual)
    preopen_query = next(query for query in unregistered["source_read_receipts"][1]["receipt"]["query_consumers"]
                        if query["consumer"] == "preopen")
    preopen_query["consumer"] = "direct_readonly_copy"
    assert source_capture_checks(unregistered, DEFAULT_READ_CONTRACT)["source_queries_obey_productive_budgets"] is False
    assert source_inventory(Path(store.path)) == before


def test_preopen_worker_fixture_writers_are_explicitly_closed_before_source_capture(tmp_path, monkeypatch):
    from tests.test_rc6_shadow_preopen_runtime import _worker_store
    original_connect = sqlite3.connect
    writers = []
    def observed(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        writers.append(connection)
        return connection
    monkeypatch.setattr(sqlite3, "connect", observed)
    store = _worker_store(tmp_path)
    assert writers
    for connection in writers:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
    assert not Path(str(store.path) + "-wal").exists()
    assert not Path(str(store.path) + "-shm").exists()


def test_only_distinct_history_source_gets_an_explained_additional_capture(tmp_path):
    database = small_database(tmp_path / "source.db")
    history = small_database(tmp_path / "history.db")
    with source_tick(database) as capture:
        with readonly_copy(database, deadline=time.monotonic()+.25, validate=False) as c:
            assert c.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
        with readonly_copy(history, deadline=time.monotonic()+.25, validate=False) as c:
            assert c.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
    assert capture.receipt["primary_captures"] == 1
    additional = capture.receipt["additional_captures"]
    assert len(additional) == 1 and additional[0]["reason"] == "DISTINCT_SOURCE_IDENTITY"
    assert additional[0]["source_unchanged"] is True and additional[0]["cleanup_complete"] is True
    assert additional[0]["source_path_sha256"] != capture.receipt["source_path_sha256"]
    assert additional[0]["source_sha256"] == capture.receipt["source_sha256"]


def test_primary_and_distinct_history_share_canonical_scratch_lease_safely(monkeypatch):
    from rc6_audit_evidence import sqlite_scratch
    with external_disk_fixture(prefix=".rc6-nested-history-") as private:
        database = small_database(private / "source.db")
        history = small_database(private / "history.db")
        root = artifact_root(database) / "sqlite-read-scratch"
        root.mkdir(parents=True, mode=0o700)
        monkeypatch.setenv("PAPER_V17_DB_PATH", str(database))
        for name, value in {"ROOT": root, "MAX_BYTES": 512 * 1024**2,
                "RESERVE_BYTES": 2 * 1024**3, "MIN_FREE_INODE_PERCENT": 10}.items():
            monkeypatch.setenv(sqlite_scratch.ENV_PREFIX + name, str(value))
        with source_tick(database) as capture:
            with readonly_copy(history, deadline=time.monotonic()+.25, validate=False) as c:
                assert c.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
                with pytest.raises(SnapshotError, match="SCRATCH_LEASE_BUSY"):
                    sqlite_scratch.inspect_scratch(root)
            with source_connection(database, deadline=time.monotonic()+.25, consumer="lab") as (c, _):
                assert c.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
        assert capture.receipt["primary_captures"] == 1
        assert capture.receipt["additional_captures"][0]["source_unchanged"] is True
        assert capture.receipt["additional_captures"][0]["cleanup_complete"] is True
        admission = capture.receipt["additional_captures"][0]["scratch_admission"]
        assert admission["lease"] == "BORROWED_AUTHENTICATED_OWNER"
        assert admission["live_unmaterialized_peak_bytes"] > 0
        assert admission["estimated_total_peak_bytes"] <= 512 * 1024**2
        assert set(path.name for path in root.iterdir()) == {sqlite_scratch.LOCK}


def test_borrowed_history_admission_counts_simultaneous_images_before_any_copy(monkeypatch):
    from rc6_audit_evidence import sqlite_scratch
    with external_disk_fixture(prefix=".rc6-nested-capacity-") as private:
        database = small_database(private / "source.db")
        history = small_database(private / "history.db")
        with closing(sqlite3.connect(history)) as connection, connection:
            connection.execute("INSERT INTO observations VALUES(zeroblob(?))", (2 * 1024**2,))
        root = artifact_root(database) / "sqlite-read-scratch"
        root.mkdir(parents=True, mode=0o700)
        maximum = 4 * 1024**2
        monkeypatch.setenv("PAPER_V17_DB_PATH", str(database))
        for name, value in {"ROOT": root, "MAX_BYTES": maximum,
                "RESERVE_BYTES": 2 * 1024**3, "MIN_FREE_INODE_PERCENT": 10}.items():
            monkeypatch.setenv(sqlite_scratch.ENV_PREFIX + name, str(value))
        primary_peak = sqlite_scratch.snapshot_peak_bytes(database.stat().st_size)
        history_peak = sqlite_scratch.snapshot_peak_bytes(history.stat().st_size)
        assert max(primary_peak, history_peak) < maximum < primary_peak+history_peak
        original_read = snapshots._read
        def no_history_read(member, *args, **kwargs):
            assert member != history, "Capacity must reject before copying/hash-reading history"
            return original_read(member, *args, **kwargs)
        monkeypatch.setattr(snapshots, "_read", no_history_read)
        with source_tick(database):
            with pytest.raises(SnapshotError, match="SCRATCH_BYTE_BUDGET_EXHAUSTED"):
                with readonly_copy(history, deadline=time.monotonic()+.25, validate=False):
                    raise AssertionError("Individually affordable images bypassed total quota")
        assert set(path.name for path in root.iterdir()) == {sqlite_scratch.LOCK}


def test_borrowed_lease_rejects_foreign_thread_and_expired_owner_without_cleanup(monkeypatch):
    from rc6_audit_evidence import sqlite_scratch
    with external_disk_fixture(prefix=".rc6-nested-owner-") as private:
        database = small_database(private / "source.db")
        history = small_database(private / "history.db")
        root = artifact_root(database) / "sqlite-read-scratch"
        root.mkdir(parents=True, mode=0o700)
        monkeypatch.setenv("PAPER_V17_DB_PATH", str(database))
        for name, value in {"ROOT": root, "MAX_BYTES": 512 * 1024**2,
                "RESERVE_BYTES": 2 * 1024**3, "MIN_FREE_INODE_PERCENT": 10}.items():
            monkeypatch.setenv(sqlite_scratch.ENV_PREFIX + name, str(value))
        def borrow(lease):
            return sqlite_scratch.private_scratch(history, main_bytes=history.stat().st_size,
                wal_bytes=0, source_shm_bytes=0, owner_lease=lease)
        errors = []
        with source_tick(database) as capture:
            def foreign_thread():
                try:
                    with borrow(capture._guard):
                        raise AssertionError("Foreign thread borrowed the live owner")
                except SnapshotError as error:
                    errors.append(str(error))
            child = threading.Thread(target=foreign_thread)
            child.start()
            child.join(timeout=2)
            assert not child.is_alive()
        assert errors == ["SCRATCH_OWNER_LEASE_REQUIRED"]
        with pytest.raises(SnapshotError, match="SCRATCH_OWNER_LEASE_REQUIRED"):
            with borrow(capture._guard):
                raise AssertionError("A receipt/cache became live scratch authority")
        assert set(path.name for path in root.iterdir()) == {sqlite_scratch.LOCK}


def test_fixture_writers_close_explicitly_at_boundaries_without_gc(tmp_path):
    with fixture_sqlite_writers() as writers:
        connection = sqlite3.connect(tmp_path / "fixture.db")
        connection.execute("PRAGMA journal_mode=WAL")
        with connection:
            connection.execute("CREATE TABLE observed(value)")
            connection.execute("INSERT INTO observed VALUES(1)")
        assert connection.execute("SELECT count(*) FROM observed").fetchone()[0] == 1
        writers.quiesce()
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")
        assert writers.closed_writers == 1 and writers.quiescent_boundaries == 1
    assert not Path(str(tmp_path / "fixture.db")+"-wal").exists()
    assert not Path(str(tmp_path / "fixture.db")+"-shm").exists()


def test_fixture_uncommitted_writer_cannot_be_declared_quiescent(tmp_path):
    with pytest.raises(ValueError, match="FIXTURE_UNCOMMITTED_WRITER"):
        with fixture_sqlite_writers() as writers:
            connection = sqlite3.connect(tmp_path / "fixture.db")
            connection.execute("CREATE TABLE observed(value)")
            connection.execute("INSERT INTO observed VALUES(1)")
            writers.quiesce()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
