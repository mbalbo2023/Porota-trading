"""F-03 adversarial publication and restart tests; no provider or runtime input."""
from datetime import timedelta
import errno
import gzip
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import time
import uuid

import pytest

from rc6_shadow_runtime.worker import ShadowRuntime
from rc6_shadow_runtime.persistence import EvidenceFiles, ROLES, read_committed_generation
from rc6_dynamic_universe.common import digest
from tests.test_rc6_shadow_runtime_wiring import make_store, quote, PRE, OPEN


def test_checkpoint_compression_disk_failure_cannot_publish_new_report_with_old_checkpoint(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    worker.tick(PRE)
    original = gzip.compress
    calls = 0

    def fail_checkpoint(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.ENOSPC, "injected checkpoint encoding failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(gzip, "compress", fail_checkpoint)
    with pytest.raises(OSError, match="checkpoint encoding"):
        worker.tick(PRE + timedelta(seconds=30))
    with worker.files as files:
        report = files.read("latest.json.gz")
        checkpoint = files.read("checkpoint.json.gz")
        status = files.read("status.json")
    assert report["as_of"] == checkpoint["as_of"] == status["as_of"] == PRE.isoformat()


def publish(files, number, *, as_of=None):
    at = (as_of or PRE + timedelta(seconds=number)).isoformat()
    base = {"as_of": at, "mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED"}
    return files.commit_generation({**base, "number": number, "source_reports": [], "source_audit": {}},
        {**base, "number": number}, {**base, "number": number, "status": "SHADOW_OBSERVING"},
        source_watermark={"as_of": at, "source_identity": "synthetic-input"},
        configuration_fingerprint="synthetic-config")


def coherent(bundle, number):
    assert [bundle[role]["number"] for role in ROLES] == [number] * 3
    assert len({bundle[role]["generation_id"] for role in ROLES}) == 1
    assert len({bundle[role]["as_of"] for role in ROLES}) == 1
    assert bundle["status"]["report_digest"] == digest(bundle["report"])
    assert bundle["status"]["checkpoint_digest"] == digest(bundle["checkpoint"])
    assert bundle["pointer"]["generation_id"] == bundle["manifest"]["generation_id"]


FAULT_POINTS = ("after_report", "after_checkpoint", "after_status", "before_fsync", "after_fsync",
                "before_commit_pointer", "after_commit_pointer")


@pytest.mark.parametrize("point", FAULT_POINTS)
def test_every_commit_failure_boundary_exposes_only_one_valid_cut(tmp_path, point):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        old = publish(files, 1)

    def crash(stage):
        if stage == point:
            raise OSError(errno.EIO, "injected " + point)

    with EvidenceFiles(root, fault_inject=crash) as files:
        with pytest.raises(OSError, match=point):
            publish(files, 2)
    current = read_committed_generation(root)
    coherent(current, 2 if point == "after_commit_pointer" else 1)
    if point != "after_commit_pointer":
        assert current["pointer"] == old["pointer"]
    with EvidenceFiles(root) as files:
        publish(files, 3)
    coherent(read_committed_generation(root), 3)


def _kill_at_boundary(root, point, pipe, database=None):
    def stop(stage):
        if stage == point:
            pipe.send(stage)
            signal.pause()
    if database:
        ShadowRuntime(database, evidence_root=root, source_roots=[], fault_inject=stop).tick(
            PRE + timedelta(seconds=30))
    else:
        with EvidenceFiles(root, fault_inject=stop) as files:
            publish(files, 2)


@pytest.mark.parametrize("point", FAULT_POINTS)
def test_real_sigkill_at_every_boundary_never_exposes_staging_as_current(tmp_path, point):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        publish(files, 1)
    ctx = multiprocessing.get_context("fork")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_kill_at_boundary, args=(root, point, child))
    process.start()
    try:
        assert parent.poll(15), "child did not reach fault boundary"
        assert parent.recv() == point
        os.kill(process.pid, signal.SIGKILL)
        process.join(10)
        assert process.exitcode == -signal.SIGKILL
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        parent.close()
        child.close()
    coherent(read_committed_generation(root), 2 if point == "after_commit_pointer" else 1)
    with EvidenceFiles(root) as files:
        publish(files, 3)
    assert not list(root.glob(".generation-*.tmp"))
    coherent(read_committed_generation(root), 3)


def test_canonical_worker_restart_after_kill_reuses_only_committed_checkpoint(tmp_path):
    store, _ = make_store(tmp_path, count=2)
    root = tmp_path / "shadow"
    worker = ShadowRuntime(store.path, evidence_root=root, source_roots=[])
    first = worker.tick(PRE)
    ctx = multiprocessing.get_context("fork")
    parent, child = ctx.Pipe(duplex=False)
    process = ctx.Process(target=_kill_at_boundary, args=(root, "after_checkpoint", child, store.path))
    process.start()
    try:
        assert parent.poll(15)
        assert parent.recv() == "after_checkpoint"
        process.kill()
        process.join(10)
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        parent.close()
        child.close()
    old = read_committed_generation(root)
    assert old["report"]["generation_id"] == first["generation_id"]
    assert all(old[role]["as_of"] == PRE.isoformat() for role in ROLES)
    restarted = ShadowRuntime(store.path, evidence_root=root, source_roots=[])
    again = restarted.tick(PRE + timedelta(seconds=60))
    assert again["checkpoint_reused"] and again["generation_id"] != first["generation_id"]
    assert again["real_orders_sent"] == again["provider_requests"] == 0
    assert not list(root.glob(".generation-*.tmp"))


def test_enospc_from_real_fsync_does_not_change_current(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        old = publish(files, 1)
    original = os.fsync

    def no_space(_):
        raise OSError(errno.ENOSPC, "injected disk full")

    with EvidenceFiles(root) as files:
        monkeypatch.setattr(os, "fsync", no_space)
        with pytest.raises(OSError) as error:
            publish(files, 2)
        assert error.value.errno == errno.ENOSPC
        monkeypatch.setattr(os, "fsync", original)
    assert read_committed_generation(root)["pointer"] == old["pointer"]


def test_actual_read_only_directory_permission_failure_preserves_current(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        old = publish(files, 1)
        root.chmod(0o500)
        try:
            with pytest.raises(PermissionError):
                publish(files, 2)
        finally:
            root.chmod(0o700)
    assert read_committed_generation(root)["pointer"] == old["pointer"]


def _manifest_rebind(root, bundle, *, role=None, payload=None, wire=None, change=None):
    """Adversary may rehash individual JSON; logical consistency must still hold."""
    directory = root / ("gen-" + bundle["pointer"]["generation_id"])
    manifest = json.loads((directory / "manifest.json").read_bytes())
    if role:
        name = ROLES[role]
        if payload is not None:
            raw = json.dumps({"digest": digest(payload), "payload": payload}, sort_keys=True).encode()
            wire = gzip.compress(raw, mtime=0) if name.endswith(".gz") else raw
            manifest["files"][role]["payload_digest"] = digest(payload)
        (directory / name).write_bytes(wire)
        manifest["files"][role]["sha256"] = hashlib.sha256(wire).hexdigest()
    if change:
        change(manifest)
    raw = json.dumps(manifest, sort_keys=True).encode()
    (directory / "manifest.json").write_bytes(raw)
    current = dict(bundle["pointer"], manifest_sha256=hashlib.sha256(raw).hexdigest())
    current["digest"] = digest({k: v for k, v in current.items() if k != "digest"})
    (root / "CURRENT.json").write_text(json.dumps(current))


def test_truncated_gzip_rejected_even_with_individual_wire_hash_rebound(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        bundle = publish(files, 1)
    directory = root / ("gen-" + bundle["pointer"]["generation_id"])
    wire = (directory / ROLES["checkpoint"]).read_bytes()
    _manifest_rebind(root, bundle, role="checkpoint", wire=wire[:-12])
    with pytest.raises((EOFError, OSError, ValueError)):
        read_committed_generation(root)


@pytest.mark.parametrize("mutation,reason", [
    (lambda p: p.update(generation_id=uuid.uuid4().hex), "MEMBER_MISMATCH"),
    (lambda p: p.update(as_of=(PRE-timedelta(days=1)).isoformat()), "CROSS_HASH_MISMATCH"),
    (lambda p: p.update(configuration_fingerprint="other-config"), "MEMBER_MISMATCH"),
    (lambda p: p.update(source_watermark={"as_of": PRE.isoformat()}), "MEMBER_MISMATCH"),
])
def test_individually_valid_checkpoint_cannot_be_mixed_into_generation(tmp_path, mutation, reason):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        bundle = publish(files, 1)
    payload = dict(bundle["checkpoint"])
    mutation(payload)
    _manifest_rebind(root, bundle, role="checkpoint", payload=payload)
    with pytest.raises(ValueError, match=reason):
        read_committed_generation(root)


def test_corrupt_file_and_manifest_hash_each_fail_closed(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        bundle = publish(files, 1)
    directory = root / ("gen-" + bundle["pointer"]["generation_id"])
    path = directory / ROLES["report"]
    original = path.read_bytes()
    path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(ValueError, match="FILE_HASH"):
        read_committed_generation(root)
    path.write_bytes(original)
    manifest = directory / "manifest.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(ValueError, match="MANIFEST_HASH"):
        read_committed_generation(root)


def test_stale_current_is_rejected_by_durable_high_water_without_borrowing_newer_members(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        old = publish(files, 1)
        publish(files, 2)
    (root / "CURRENT.json").write_text(json.dumps(old["pointer"]))
    with pytest.raises(ValueError, match="CURRENT_ROLLBACK"):
        read_committed_generation(root)


def test_current_pointing_to_missing_generation_fails_closed(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        old = publish(files, 1)
    pointer = dict(old["pointer"], generation_id=uuid.uuid4().hex)
    pointer["digest"] = digest({k: v for k, v in pointer.items() if k != "digest"})
    (root / "CURRENT.json").write_text(json.dumps(pointer))
    with pytest.raises(ValueError, match="DIRECTORY_INVALID"):
        read_committed_generation(root)


def test_clock_rollback_never_reuses_generation_id_or_future_worker_checkpoint(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        first = publish(files, 1, as_of=PRE + timedelta(days=1))
        earlier = publish(files, 2, as_of=PRE)
    assert earlier["pointer"]["generation_id"] != first["pointer"]["generation_id"]
    assert earlier["pointer"]["sequence"] == first["pointer"]["sequence"] + 1
    coherent(read_committed_generation(root), 2)
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "worker", source_roots=[])
    original = worker.tick(PRE)
    with pytest.raises(ValueError, match="CHECKPOINT_FROM_FUTURE"):
        worker.tick(PRE - timedelta(seconds=1))
    assert read_committed_generation(worker.root)["report"]["generation_id"] == original["generation_id"]


def test_failure_health_does_not_overwrite_committed_status_and_recovery_is_forward(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        old = publish(files, 1)
        diagnostic = files.record_failure(as_of=PRE + timedelta(seconds=2), error=OSError(errno.EIO, "fault"))
        assert diagnostic["observed_generation_id"] == old["pointer"]["generation_id"]
        assert files.read_generation(allow_degraded=True)["status"] == old["status"]
    with pytest.raises(ValueError, match="GENERATION_DEGRADED"):
        read_committed_generation(root)
    with EvidenceFiles(root) as files:
        publish(files, 3)
    coherent(read_committed_generation(root), 3)


def test_legacy_independent_latest_cannot_bypass_committed_generation_reader(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root):
        pass
    payload = {"as_of": PRE.isoformat(), "mode": "SHADOW", "real_orders_sent": 0}
    (root / "latest.json.gz").write_bytes(gzip.compress(json.dumps({"digest": digest(payload), "payload": payload}).encode()))
    from rc6_dynamic_universe.promotion import RuntimeCapacityController
    reader = RuntimeCapacityController(environ={"POROTA_CAPACITY_SHADOW_PATH": str(root / "latest.json.gz")})
    with pytest.raises(ValueError, match="NOT_COMMITTED"):
        reader.shadow_report()
    with EvidenceFiles(root) as files:
        with pytest.raises(ValueError, match="GENERATION_COMMIT_REQUIRED"):
            files.write("status.json", payload)
        bundle = publish(files, 1)
    assert reader.shadow_report() == bundle["report"]


def test_shared_reader_lock_is_nonblocking_and_does_not_create_files(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        read_committed_generation(missing)
    assert not missing.exists()
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        publish(files, 1)
        started = time.monotonic()
        with pytest.raises(BlockingIOError):
            read_committed_generation(root)
        assert time.monotonic() - started < 1
    coherent(read_committed_generation(root), 1)


@pytest.mark.parametrize("attack", ["member_hardlink", "member_symlink", "directory_symlink", "current_symlink"])
def test_hostile_aliases_fail_closed_without_modifying_external_inputs(tmp_path, attack):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        bundle = publish(files, 1)
    directory = root / ("gen-" + bundle["pointer"]["generation_id"])
    outside = tmp_path / "source.json"
    original = (directory / ROLES["report"]).read_bytes()
    outside.write_bytes(original)
    if attack in {"member_hardlink", "member_symlink"}:
        target = directory / ROLES["report"]
        target.unlink()
        if attack == "member_hardlink":
            os.link(outside, target)
        else:
            target.symlink_to(outside)
    elif attack == "directory_symlink":
        destination = tmp_path / "outside-generation"
        directory.rename(destination)
        directory.symlink_to(destination, target_is_directory=True)
    else:
        pointer_source = tmp_path / "pointer-input.json"
        pointer_source.write_bytes((root / "CURRENT.json").read_bytes())
        (root / "CURRENT.json").unlink()
        (root / "CURRENT.json").symlink_to(pointer_source)
    with pytest.raises((ValueError, OSError)):
        read_committed_generation(root)
    assert outside.read_bytes() == original


def test_manifest_path_traversal_cannot_select_any_external_input(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        bundle = publish(files, 1)
    outside = tmp_path / "private.json"
    outside.write_text('{"private":"synthetic"}')
    _manifest_rebind(root, bundle, change=lambda m: m["files"]["report"].update(name="../../private.json"))
    with pytest.raises(ValueError, match="PATH_MISMATCH"):
        read_committed_generation(root)
    assert outside.read_text() == '{"private":"synthetic"}'


def test_compressed_payload_expansion_is_bounded_even_when_hashes_match(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        bundle = publish(files, 1)
    payload = dict(bundle["report"], data="x" * 50000)
    _manifest_rebind(root, bundle, role="report", payload=payload)
    with pytest.raises(ValueError, match="PAYLOAD_LIMIT"):
        read_committed_generation(root, payload_limit=4096)


def test_all_three_roles_are_verified_by_the_actual_capacity_consumer(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        bundle = publish(files, 1)
    from rc6_dynamic_universe.promotion import RuntimeCapacityController
    reader = RuntimeCapacityController(environ={"POROTA_CAPACITY_SHADOW_PATH": str(root / "CURRENT.json")})
    assert reader.shadow_report() == bundle["report"]
    directory = root / ("gen-" + bundle["pointer"]["generation_id"])
    (directory / ROLES["checkpoint"]).write_bytes(b"individually unrelated checkpoint")
    with pytest.raises(ValueError, match="FILE_HASH_MISMATCH"):
        reader.shadow_report()


def test_status_cannot_link_a_report_outside_the_committed_cut(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        bundle = publish(files, 1)
    _manifest_rebind(root, bundle, role="status", payload=dict(bundle["status"], report_digest="0" * 64))
    with pytest.raises(ValueError, match="LOGICAL_MISMATCH"):
        read_committed_generation(root)


def test_source_audit_digest_is_bound_to_actual_source_reports_before_commit(tmp_path):
    root = tmp_path / "evidence"
    from rc6_dynamic_universe.sources import audit_sources
    sources = [{"source": "PPI_API", "as_of": PRE.isoformat(), "status": "SHADOW_EVIDENCE",
        "observations": [{"source_at": PRE.isoformat(), "received_at": PRE.isoformat()}],
        "counts": {"seen": 1, "useful": 0, "rejected": 1}}]
    base = {"as_of": PRE.isoformat(), "source_reports": sources, "source_audit": {"source_reports_digest": "0" * 64}}
    with EvidenceFiles(root) as files:
        with pytest.raises(ValueError, match="SOURCE_AUDIT_DIGEST_MISMATCH"):
            files.commit_generation(base, {"as_of": PRE.isoformat()}, {"as_of": PRE.isoformat()},
                source_watermark={"as_of": PRE.isoformat()}, configuration_fingerprint="config")
        assert not (root / "CURRENT.json").exists()
        base["source_audit"] = audit_sources(reports=sources, as_of=PRE)
        bundle = files.commit_generation(base, {"as_of": PRE.isoformat()}, {"as_of": PRE.isoformat()},
            source_watermark={"as_of": PRE.isoformat()}, configuration_fingerprint="config")
    read = read_committed_generation(root)
    assert read["manifest"]["source_reports_digest"] == digest(sources)
    assert read["manifest"]["source_audit_digest"] == digest(base["source_audit"])
    assert read["pointer"] == bundle["pointer"]


def test_uuid_collision_cannot_overwrite_existing_generation(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        bundle = publish(files, 1)
        directory = root / ("gen-" + bundle["pointer"]["generation_id"])
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(hex=bundle["pointer"]["generation_id"]))
        with pytest.raises(ValueError, match="GENERATION_ID_REUSED"):
            publish(files, 2)
    assert before == {path.name: path.read_bytes() for path in directory.iterdir()}
    coherent(read_committed_generation(root), 1)


def test_canonical_worker_soft_pressure_is_committed_and_hard_pressure_never_rewrites_cut(tmp_path):
    from rc6_shadow_runtime.retention import RetentionPressure
    store, _ = make_store(tmp_path, count=2)
    root = tmp_path / "shadow"
    worker = ShadowRuntime(store.path, evidence_root=root, source_roots=[])
    worker.tick(PRE)
    used = sum(path.lstat().st_size for path in root.rglob("*") if not path.is_symlink())
    worker.files.maximum_bytes = used * 10 + 65536
    filler = root / "retained-synthetic-evidence.bin"
    filler.write_bytes(b"s" * int(worker.files.maximum_bytes * .74))
    pressure = worker.tick(PRE + timedelta(seconds=30))
    assert pressure["evidence_retention"]["status"] == "RETENTION_PRESSURE"
    assert pressure["status"] == "RETENTION_PRESSURE" and pressure["real_orders_sent"] == 0
    cut = read_committed_generation(root)
    assert cut["status"]["status"] == "RETENTION_PRESSURE"
    assert cut["report"]["generation_id"] == pressure["generation_id"]
    from rc6_dynamic_universe.promotion import RuntimeCapacityController
    consumer = RuntimeCapacityController(environ={"POROTA_CAPACITY_SHADOW_PATH": str(root / "CURRENT.json")})
    with pytest.raises(ValueError, match="CAPACITY_SHADOW_RETENTION_PRESSURE"):
        consumer.shadow_report()
    with filler.open("r+b") as stream:
        stream.truncate(worker.files.maximum_bytes + 1)
    with pytest.raises(RetentionPressure, match="CAPACITY"):
        worker.tick(PRE + timedelta(seconds=60))
    assert read_committed_generation(root)["pointer"] == cut["pointer"]
    assert store.open_positions() == [] and store.active_future_positions() == []


def test_513_files_and_over_128mib_preserve_committed_evidence(tmp_path):
    from rc6_shadow_runtime.retention import RetentionPressure
    root = tmp_path / "evidence"
    with EvidenceFiles(root, maximum_files=512) as files:
        old = publish(files, 1)
        for number in range(513):
            (root / f"unarchived-{number}.json").write_text("{}")
        with pytest.raises(RetentionPressure, match="FILES_CAPACITY"):
            publish(files, 2)
    coherent(read_committed_generation(root), 1)
    sparse = tmp_path / "large"
    with EvidenceFiles(sparse) as files:
        before = publish(files, 1)
        with (sparse / "unarchived.bin").open("wb") as stream:
            stream.truncate(128 * 1024**2 + 1)
        with pytest.raises(RetentionPressure, match="BYTES_CAPACITY"):
            publish(files, 2)
    assert read_committed_generation(sparse)["pointer"] == before["pointer"]
    assert (root / ("gen-" + old["pointer"]["generation_id"])).exists()


def test_canonical_loop_records_explicit_backpressure_and_does_not_overwrite_status(tmp_path, monkeypatch, caplog):
    import rc6_shadow_runtime.worker as module
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[])
    worker.tick(PRE)
    before = read_committed_generation(worker.root)
    tick = worker.tick
    monkeypatch.setattr(worker, "tick", lambda *_: (_ for _ in ()).throw(ValueError("FUNNEL_CHECKPOINT_CAPACITY_EXCEEDED")))
    from types import SimpleNamespace
    monkeypatch.setattr(module, "ShadowRuntime", SimpleNamespace(from_environment=lambda *args, **kwargs: worker))
    monkeypatch.setattr(module.os, "nice", lambda *_: None)

    class Stop:
        done = False
        waited = []
        def is_set(self):
            return self.done
        def wait(self, seconds):
            self.waited.append(seconds)
            self.done = True

    stop = Stop()
    module.run_worker(store.path, stop, clock_fn=lambda: (PRE + timedelta(seconds=30)).isoformat())
    assert stop.waited == [30]
    assert "FUNNEL_CHECKPOINT_CAPACITY_EXCEEDED" in caplog.text
    with worker.files as files:
        failure = files.read("failure.json")
        assert files.read_generation(allow_degraded=True)["status"] == before["status"]
    assert failure["reason"] == "FUNNEL_CHECKPOINT_CAPACITY_EXCEEDED"
    assert failure["observed_generation_id"] == before["pointer"]["generation_id"]
    monkeypatch.setattr(worker, "tick", tick)
    assert worker.tick(PRE + timedelta(seconds=60))["checkpoint_reused"]
    assert read_committed_generation(worker.root)["report"]["as_of"] == (PRE + timedelta(seconds=60)).isoformat()
    assert store.open_positions() == []


def test_failure_diagnostics_never_persist_exception_message_secrets(tmp_path):
    from rc6_shadow_runtime.persistence import failure_reason
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        publish(files, 1)
        files.record_failure(as_of=PRE, error=ValueError("token=synthetic-sensitive-text"))
        raw = (root / "failure.json").read_text()
    assert "synthetic-sensitive-text" not in raw and "token=" not in raw
    assert failure_reason(OSError(errno.ENOSPC, "private path")) == "SHADOW_EVIDENCE_DISK_FULL"
    assert failure_reason(PermissionError(errno.EACCES, "private path")) == "SHADOW_EVIDENCE_PERMISSION_DENIED"


def test_radar_counter_memory_is_bounded_even_when_old_points_have_expired(tmp_path):
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, evidence_root=tmp_path / "shadow", source_roots=[], row_limit=2)
    old = {"points": [], "counters": {"a": 1, "b": 1}, "last_trade": {"a": PRE.isoformat(), "b": PRE.isoformat()}}
    at = PRE + timedelta(minutes=30)
    row = {"identity": ["NEW", "ACCIONES", "BYMA", "ARS", "A-24HS"],
           "source_at": at.isoformat(), "received_at": at.isoformat(), "source": "PPI_MARKETDATA_CURRENT",
           "useful": True, "endpoint": "current", "is_trade": True, "fields": {}}
    with pytest.raises(ValueError, match="RADAR_CAPACITY"):
        worker._radar([row], old, at, PRE)
    assert len(old["counters"]) == len(old["last_trade"]) == 2


@pytest.mark.parametrize("at", [PRE, OPEN])
def test_received_native_sources_are_audited_even_before_initial_watermark_or_without_preopen(tmp_path, at):
    store, assets = make_store(tmp_path, count=1)
    store.add_quote(quote(assets[0], at))
    root = tmp_path / "shadow"
    report = ShadowRuntime(store.path, evidence_root=root, source_roots=[]).tick(at)
    assert report["source_reports"]
    assert report["source_audit"]["source_reports_digest"] == digest(report["source_reports"])
    cut = read_committed_generation(root)
    assert cut["manifest"]["source_audit_digest"] == digest(report["source_audit"])
    assert cut["manifest"]["source_reports_digest"] == digest(report["source_reports"])
    assert report["provider_requests"] == report["real_orders_sent"] == 0
    if at == OPEN:
        assert report["status"] == "PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION"
    else:
        assert all(r["runtime_ingestion"]["accepted"] == 0 for r in report["source_reports"] if r.get("native_input"))


def test_durability_order_precedes_current_and_root_fsync_failure_keeps_whole_new_cut(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        publish(files, 1)
    synced = []
    fsync = os.fsync

    def record(fd):
        synced.append(Path(os.readlink(f"/proc/self/fd/{fd}")).name)
        return fsync(fd)

    with EvidenceFiles(root) as files:
        monkeypatch.setattr(os, "fsync", record)
        publish(files, 2)
    assert any(name.startswith(".HEAD-") for name in synced)  # durable sequence reservation
    member_syncs = [name for name in synced if name in {*ROLES.values(), "manifest.json"}]
    assert member_syncs[:4] == [*ROLES.values(), "manifest.json"]
    publication = [name for name in synced[synced.index("report.json.gz"):]
        if name in {*ROLES.values(), "manifest.json", root.name}
        or name.startswith((".generation-", ".CURRENT."))]
    assert publication[4].startswith(".generation-")
    assert publication[5] == root.name
    assert publication[6].startswith(".CURRENT.") and publication[7] == root.name
    with EvidenceFiles(root) as files:
        sync_directory = files._sync_directory
        root_calls = 0
        def fail_after_rename(path):
            nonlocal root_calls
            if path == files.root:
                root_calls += 1
                if root_calls == 2:
                    raise OSError(errno.EIO, "injected commit directory fsync failure")
            return sync_directory(path)
        monkeypatch.setattr(files, "_sync_directory", fail_after_rename)
        with pytest.raises(OSError, match="commit directory"):
            publish(files, 3)
    # Every member is durable, but publication is not sealed after root EIO.
    # Consumers fail closed until the exclusive writer seals that exact cut.
    with pytest.raises(ValueError, match="PUBLICATION_RECOVERY_REQUIRED"):
        read_committed_generation(root)
    with EvidenceFiles(root) as files:
        coherent(files.read_generation(), 3)
    coherent(read_committed_generation(root), 3)
