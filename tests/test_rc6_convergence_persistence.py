"""U14-U21/U26 adversarial tests through real offline producers and consumers.

Every DB, archive and mutation belongs to a synthetic TemporaryDirectory. No
provider client, production path, broker route or PPI Watch is accessed.
"""
from copy import deepcopy
from datetime import timedelta
import errno
import gc
import gzip
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time
import types

import pytest

from rc6_dynamic_universe.common import digest
from rc6_dynamic_universe.sources import audit_sources, source_observations
from rc6_shadow_runtime import persistence
from rc6_shadow_runtime.persistence import EvidenceFiles, GENERATION_SCHEMA, ROLES, read_committed_generation, shadow_evidence_root
from rc6_shadow_runtime.retention import EvidenceRetention, RetentionPressure
from rc6_shadow_runtime.worker import ShadowRuntime
from tests.test_issue465_generations import publish
from tests.test_rc6_shadow_runtime_wiring import PRE, make_store, quote


def save_proof(name, value):
    destination = os.getenv("POROTA_RC6_TEST_EVIDENCE_DIR")
    if destination:
        path = Path(destination); path.mkdir(parents=True, exist_ok=True)
        (path / (name + ".json")).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def source_report():
    return source_observations({"records": [{"ticker": "S1", "instrument_type": "ACCIONES",
        "market": "BYMA", "currency": "ARS", "settlement": "A-24HS", "price": 100,
        "price_unit": "PER_SHARE", "timestamp": PRE.isoformat(), "received_at": PRE.isoformat(),
        "source_path": "synthetic/PPI/S1", "state": "READY"}]}, source="PPI", as_of=PRE)


def commit_source(files, reports, audit):
    base = {"as_of": PRE.isoformat(), "mode": "SHADOW", "real_orders_sent": 0, "real_routes": "NOT_CALLED"}
    return files.commit_generation({**base, "source_reports": reports, "source_audit": audit},
        dict(base), dict(base), source_watermark={"as_of": PRE.isoformat()}, configuration_fingerprint="offline-test")


def rehash_all(root, bundle, change):
    """Independent adversary rebuilds every hash, including CURRENT."""
    values = {role: deepcopy(bundle[role]) for role in ROLES}
    for value in values.values():
        value.pop("cross_payload_hashes", None)
    change(values)
    cross = {role: digest(values[role]) for role in ("report", "checkpoint")}
    for value in values.values():
        value["cross_payload_hashes"] = cross
    values["status"].update(report_digest=digest(values["report"]), checkpoint_digest=digest(values["checkpoint"]))
    wires = {}
    for role, name in ROLES.items():
        data = json.dumps({"digest": digest(values[role]), "payload": values[role]}, sort_keys=True, separators=(",", ":")).encode()
        wires[role] = gzip.compress(data, mtime=0) if name.endswith(".gz") else data
    manifest = deepcopy(bundle["manifest"])
    manifest["source_audit_digest"] = digest(values["report"]["source_audit"])
    manifest["source_reports_digest"] = digest(values["report"]["source_reports"])
    manifest["files"] = {role: {"name": name, "sha256": hashlib.sha256(wires[role]).hexdigest(),
        "payload_digest": digest(values[role])} for role, name in ROLES.items()}
    directory = root / ("gen-" + bundle["pointer"]["generation_id"])
    for role, name in ROLES.items():
        (directory / name).write_bytes(wires[role])
    raw = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    (directory / "manifest.json").write_bytes(raw)
    pointer = dict(bundle["pointer"], manifest_sha256=hashlib.sha256(raw).hexdigest())
    pointer["digest"] = digest({key: item for key, item in pointer.items() if key != "digest"})
    (root / "CURRENT.json").write_text(json.dumps(pointer))


def test_default_root_real_worker_reader_and_configuration_identity(tmp_path, monkeypatch):
    from cg_paper_workspace import artifact_root
    monkeypatch.delenv("POROTA_DYNAMIC_SHADOW_ROOT", raising=False)
    monkeypatch.delenv("POROTA_SHADOW_RUNTIME_ROOT", raising=False)
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime.from_environment(store.path, source_roots=[])
    assert worker.root == shadow_evidence_root(store.path) == artifact_root(store.path) / "dynamic-shadow"
    report = worker.tick(PRE)
    cut = read_committed_generation(worker.root)
    assert cut["export_contract"]["generation_schema"] == GENERATION_SCHEMA
    assert all(cut[role]["sequence"] == cut["pointer"]["sequence"] == 1 for role in ROLES)
    assert all(cut[role]["safety"] == cut["manifest"]["safety"] for role in ROLES)
    assert report["configuration_fingerprint"] == worker.configuration_fingerprint(PRE)
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in worker.root.rglob("*") if path.is_file()}
    read_committed_generation(worker.root)
    assert before == {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in worker.root.rglob("*") if path.is_file()}
    exact = str(tmp_path / "explicit")
    assert shadow_evidence_root(store.path, {"POROTA_DYNAMIC_SHADOW_ROOT": exact,
        "POROTA_SHADOW_RUNTIME_ROOT": exact}) == Path(exact)
    with pytest.raises(ValueError, match="CONFIGURATION_CONFLICT"):
        shadow_evidence_root(store.path, {"POROTA_DYNAMIC_SHADOW_ROOT": exact,
            "POROTA_SHADOW_RUNTIME_ROOT": str(tmp_path / "other")})
    alias = tmp_path / "alias"; alias.symlink_to(worker.root, target_is_directory=True)
    with pytest.raises(ValueError, match="ALIAS"):
        shadow_evidence_root(store.path, {"POROTA_DYNAMIC_SHADOW_ROOT": str(alias)})


def test_legacy_v1_real_historical_writer_only_bootstraps_forward_to_v2(tmp_path):
    old_source = subprocess.check_output(["git", "show",
        "c27dfd963c4fe83465c0f2105347e974fbbe6356:rc6_shadow_runtime/persistence.py"], text=True)
    legacy = types.ModuleType("rc6_shadow_runtime._historical_v1"); legacy.__package__ = "rc6_shadow_runtime"
    exec(compile(old_source, "historical:c27dfd9:persistence.py", "exec"), legacy.__dict__)
    root = tmp_path / "evidence"
    with legacy.EvidenceFiles(root) as files:
        old = publish(files, 1)
    with pytest.raises(ValueError, match="LEGACY_REQUIRES_FORWARD_BOOTSTRAP"):
        read_committed_generation(root)
    with EvidenceFiles(root) as files:
        bootstrap = files.read_generation(allow_legacy=True)
        assert bootstrap["export_contract"]["legacy_bootstrap"]
        new = publish(files, 2)
    assert new["pointer"]["sequence"] == 2 and (root / ("gen-" + old["pointer"]["generation_id"])).is_dir()
    assert read_committed_generation(root)["pointer"]["schema"] == GENERATION_SCHEMA
    (root / "CURRENT.json").write_text(json.dumps(old["pointer"]))
    with EvidenceFiles(root) as files, pytest.raises(ValueError, match="CURRENT_ROLLBACK"):
        publish(files, 3)


@pytest.mark.parametrize("field,value", [("real_orders_sent", 7), ("real_orders_sent", False),
    ("real_routes", "CALLED"), ("mode", "REAL"), ("provider_requests", 1), ("ppi_watch", "TOUCHED")])
def test_u17_writer_and_fully_rehashed_cross_role_safety_fail_closed(tmp_path, field, value):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        good = publish(files, 1)
        bad = dict(good["report"]); bad[field] = value
        with pytest.raises(ValueError, match="SAFETY_MISMATCH"):
            files.commit_generation(bad, good["checkpoint"], good["status"],
                source_watermark=good["manifest"]["source_watermark"],
                configuration_fingerprint=good["manifest"]["configuration_fingerprint"])
    rehash_all(root, good, lambda values: values["report"].update({field: value}))
    with pytest.raises(ValueError, match="SAFETY_MISMATCH"):
        read_committed_generation(root)
    from rc6_dynamic_universe.promotion import RuntimeCapacityController
    with pytest.raises(ValueError, match="SAFETY_MISMATCH"):
        RuntimeCapacityController(environ={"POROTA_CAPACITY_SHADOW_PATH": str(root / "CURRENT.json")}).shadow_report()


def test_u17_consistent_full_root_rewrite_requires_the_separate_custody_anchor(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        good = publish(files, 1)
    rehash_all(root, good, lambda values: [value.update(number=999) for value in values.values()])
    with pytest.raises(ValueError, match="LINEAGE_FORK_OR_REHASH"):
        read_committed_generation(root)


def test_u20_old_current_and_missing_current_cannot_create_a_duplicate_sequence(tmp_path):
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        first = publish(files, 1); second = publish(files, 2)
    (root / "CURRENT.json").write_text(json.dumps(first["pointer"]))
    with pytest.raises(ValueError, match="CURRENT_ROLLBACK"):
        read_committed_generation(root)
    with EvidenceFiles(root) as files, pytest.raises(ValueError, match="CURRENT_ROLLBACK"):
        publish(files, 3)
    (root / "CURRENT.json").unlink()
    with pytest.raises(ValueError, match="CURRENT_ROLLBACK"):
        read_committed_generation(root)
    (root / "CURRENT.json").write_text(json.dumps(second["pointer"]))
    with EvidenceFiles(root) as files:
        third = publish(files, 3)
    assert third["pointer"]["sequence"] == 3
    sequences = [json.loads((path / "manifest.json").read_text())["sequence"] for path in root.glob("gen-*")]
    assert len(sequences) == len(set(sequences))


@pytest.mark.parametrize("field,value", [("schema", "UNTRUSTED"), ("source_report_count", 0),
    ("source_report_count", True),
    ("status", "NO_SOURCE_REPORTS"), ("as_of", "2026-10-05T13:19:59+00:00"),
    ("snapshots", {}), ("source_reports_pointer", "/ghost")])
def test_u21_canonical_semantics_reject_correct_digest_but_false_audit_in_writer_and_reader(tmp_path, field, value):
    reports = [source_report()]
    audit = audit_sources(reports=reports, as_of=PRE)
    bad = deepcopy(audit); bad[field] = value
    root = tmp_path / "evidence"
    with EvidenceFiles(root) as files:
        good = commit_source(files, reports, audit)
        with pytest.raises(ValueError, match="SOURCE_AUDIT_SEMANTIC_MISMATCH"):
            commit_source(files, reports, bad)
    rehash_all(root, good, lambda values: values["report"].update(source_audit=bad))
    with pytest.raises(ValueError, match="SOURCE_AUDIT_SEMANTIC_MISMATCH"):
        read_committed_generation(root)


def test_u21_zero_reports_cannot_hide_a_ghost_snapshot_and_per_report_links_are_checked(tmp_path):
    root = tmp_path / "evidence"
    empty = audit_sources(reports=[], as_of=PRE); empty["snapshots"] = {"ghost": {"status": "HEALTHY"}}
    with EvidenceFiles(root) as files:
        with pytest.raises(ValueError, match="SEMANTIC_MISMATCH"):
            commit_source(files, [], empty)
        reports = [source_report()]; linked = audit_sources(reports=reports, as_of=PRE)
        for key, value in (("report_pointer", "/source_reports/999"), ("report_digest", "f" * 64),
                ("counts", {"seen": 0, "useful": 0, "rejected": 0})):
            bad = deepcopy(linked); bad["snapshots"]["0"][key] = value
            with pytest.raises(ValueError, match="SEMANTIC_MISMATCH"):
                commit_source(files, reports, bad)


def test_u26_duplicate_exact_conflicting_report_and_duplicate_row_do_not_inflate_counts():
    report = source_report()
    with pytest.raises(ValueError, match="SOURCE_REPORT_DUPLICATE"):
        audit_sources(reports=[report, deepcopy(report)], as_of=PRE)
    changed = deepcopy(report); changed["observations"][0]["fields"]["price"] = 101
    with pytest.raises(ValueError, match="SOURCE_REPORT_IDENTITY_CONFLICT"):
        audit_sources(reports=[report, changed], as_of=PRE)
    changed = deepcopy(report); changed["observations"] *= 2
    changed["counts"] = {"seen": 2, "useful": 2, "rejected": 0}
    with pytest.raises(ValueError, match="SOURCE_OBSERVATION_DUPLICATE"):
        audit_sources(reports=[changed], as_of=PRE)


@pytest.mark.parametrize("message", ["token=SYNTHETIC_SECRET", "Authorization: Bearer SYNTHETIC_SECRET",
    "https://fixture.invalid/?token=SYNTHETIC_SECRET", "account=SYNTHETIC_SECRET body=private"])
def test_u16_real_collector_cache_checkpoint_and_full_tick_members_never_retain_error_text(tmp_path, message):
    import iol_shadow_collector_rc6 as collector
    class FailedClient:
        def call(self, *_):
            raise RuntimeError(message)
    market = tmp_path / "market"
    policy = collector.CollectionPolicy(retry_attempts=0)
    collector.run_batch(["S1"], FailedClient(), root=market, policy=policy,
        governor=collector.RateGovernor(policy, sleep=lambda _: None), now=lambda: PRE)
    for path in market.glob("*.json"):
        assert "SYNTHETIC_SECRET" not in path.read_text()
    # Inject a legacy cache too: the actual worker sanitizes the ingestion sink.
    legacy = {"symbols": [{"ticker": "S1", "instrument_type": "ACCIONES", "market": "BYMA",
        "currency": "ARS", "settlement": "A-24HS", "price": 100, "price_unit": "PER_SHARE",
        "timestamp": PRE.isoformat(), "received_at": PRE.isoformat(), "state": "READY", "reason": message}],
        "errors": [message], "status": "PARTIAL_SOURCE_ERRORS"}
    (market / "iol_shadow_latest.json").write_text(json.dumps(legacy))
    store, _ = make_store(tmp_path, count=1)
    worker = ShadowRuntime(store.path, source_roots=[market]); worker.tick(PRE)
    cut = read_committed_generation(worker.root)
    directory = worker.root / ("gen-" + cut["pointer"]["generation_id"])
    for name in ROLES.values():
        raw = (directory / name).read_bytes()
        if name.endswith(".gz"):
            raw = gzip.decompress(raw)
        assert b"SYNTHETIC_SECRET" not in raw
    assert cut["report"]["real_orders_sent"] == cut["report"]["provider_requests"] == 0


def archive_fixture(tmp_path):
    root, archive = tmp_path / "evidence", tmp_path / "private-archive"
    with EvidenceFiles(root) as files:
        first = publish(files, 1); publish(files, 2)
    path = root / ("gen-" + first["pointer"]["generation_id"])
    retention = EvidenceRetention(root, maximum_files=14, archive_root=archive)
    receipt = retention.archive_generation(path)
    return root, archive, path, receipt


@pytest.mark.parametrize("attack", ["v1_assertion", "invented_hash", "nonexistent_uri", "unauthorized_writer",
    "missing_object", "unreadable_object", "replay", "corrupt_object"])
def test_u18_invalid_inaccessible_or_replayed_receipt_never_authorizes_deletion(tmp_path, attack):
    root, archive, path, receipt = archive_fixture(tmp_path)
    ack = root / ("archive-ack-" + receipt["generation_id"] + ".json")
    obj = archive / (receipt["generation_id"] + ".tar.gz")
    if attack == "v1_assertion": receipt["schema"] = "RC6_SHADOW_ARCHIVE_ACK_V1"
    elif attack == "invented_hash": receipt["archive_sha256"] = "a" * 64
    elif attack == "nonexistent_uri": receipt["archive_uri"] = "local-private://missing.tar.gz"
    elif attack == "unauthorized_writer": receipt["archiver_id"] = "UNAUTHORIZED"
    elif attack == "missing_object": obj.unlink()
    elif attack == "unreadable_object": obj.chmod(0)
    elif attack == "replay": receipt["generation_id"] = "f" * 32
    elif attack == "corrupt_object": obj.write_bytes(b"corrupt")
    ack.write_text(json.dumps(receipt))
    with pytest.raises(RetentionPressure, match="HARD_FILES"):
        EvidenceRetention(root, maximum_files=14, archive_root=archive).prepare(additional_files=6)
    assert path.is_dir() and set(item.name for item in path.iterdir()) == {*ROLES.values(), "manifest.json"}


ROTATION_BOUNDARIES = ("rotation_before_rename", "rotation_after_rename", "rotation_after_rename_fsync",
    *("rotation_" + side + "_unlink_" + name for name in sorted({*ROLES.values(), "manifest.json"}) for side in ("before", "after")),
    "rotation_before_directory_fsync", "rotation_after_directory_fsync", "rotation_after_remove_directory",
    "rotation_before_compact_ack", "rotation_after_compact_ack")


def pause_rotation(root, archive, point, pipe):
    def fault(stage):
        if stage == point:
            pipe.send(stage); signal.pause()
    EvidenceRetention(root, maximum_files=14, archive_root=archive, fault_inject=fault).prepare(additional_files=6)


@pytest.mark.parametrize("point", ROTATION_BOUNDARIES)
def test_u19_real_sigkill_every_rotation_boundary_recovers_without_losing_archived_evidence(tmp_path, point):
    root, archive, path, receipt = archive_fixture(tmp_path)
    context = multiprocessing.get_context("fork"); parent, child = context.Pipe(duplex=False)
    process = context.Process(target=pause_rotation, args=(root, archive, point, child)); process.start()
    try:
        assert parent.poll(15); assert parent.recv() == point
        os.kill(process.pid, signal.SIGKILL); process.join(10)
        assert process.exitcode == -signal.SIGKILL
    finally:
        if process.is_alive(): process.kill(); process.join(10)
        parent.close(); child.close()
    EvidenceRetention(root, maximum_files=14, archive_root=archive).prepare(additional_files=6)
    assert not path.exists() and not list(root.glob(".deleting-*")) and not list(root.glob("delete-intent-*"))
    assert not list(root.glob("archive-ack-*"))
    assert (archive / (receipt["generation_id"] + ".receipt.json")).is_file()
    EvidenceRetention(root, archive_root=archive)._verify_archive(receipt)
    assert read_committed_generation(root)["report"]["number"] == 2


@pytest.mark.parametrize("point", ROTATION_BOUNDARIES)
def test_u19_eio_every_rotation_boundary_is_reentrant_after_restart(tmp_path, point):
    root, archive, path, receipt = archive_fixture(tmp_path)
    def fault(stage):
        if stage == point: raise OSError(errno.EIO, "synthetic")
    with pytest.raises(RetentionPressure):
        EvidenceRetention(root, maximum_files=14, archive_root=archive, fault_inject=fault).prepare(additional_files=6)
    EvidenceRetention(root, maximum_files=14, archive_root=archive).prepare(additional_files=6)
    assert not path.exists() and not list(root.glob(".deleting-*")) and not list(root.glob("delete-intent-*"))
    EvidenceRetention(root, archive_root=archive)._verify_archive(receipt)


def test_u14_old_512_entry_boundary_is_reproduced_and_default_full_tick_covers_nine_hours_plus_restart(tmp_path):
    small = tmp_path / "old-boundary"; small.mkdir()
    store, _ = make_store(small, count=1)
    worker = ShadowRuntime(store.path, source_roots=[], maximum_files=512)
    soft, commits, hard = None, 0, None
    for index in range(110):
        try:
            report = worker.tick(PRE + timedelta(seconds=30 * index)); commits += 1
            if report["evidence_retention"]["status"] == "RETENTION_PRESSURE" and soft is None:
                soft = index * .5
        except RetentionPressure as error:
            hard = {"minutes": index * .5, "reason": error.reason}; break
    assert soft == 40.5 and commits == 101 and hard["minutes"] == 50.5
    full = tmp_path / "new-horizon"; full.mkdir()
    store, assets = make_store(full, count=5)
    for asset in assets: store.add_quote(quote(asset, PRE))
    # Finish synthetic fixture writers before the byte baseline; late writer
    # GC/WAL checkpoint is not a read performed by the SHADOW caller.
    gc.collect()
    connection = sqlite3.connect(store.path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    database_before = hashlib.sha256(Path(store.path).read_bytes()).hexdigest()
    worker = ShadowRuntime(store.path, source_roots=[])
    started = time.monotonic(); max_files, max_bytes = 0, 0
    # Ten hours gives a complete nine-hour contract plus one-hour margin.
    for index in range(1201):
        if index in (360, 840): worker = ShadowRuntime(store.path, source_roots=[])
        report = worker.tick(PRE + timedelta(seconds=30 * index))
        metrics = report["evidence_retention"]
        assert metrics["status"] == "OK" and report["real_orders_sent"] == report["provider_requests"] == 0
        max_files = max(max_files, metrics["projected_files"]); max_bytes = max(max_bytes, metrics["projected_bytes"])
    final = read_committed_generation(worker.root)
    assert final["pointer"]["sequence"] == 1201 and final["report"]["checkpoint_reused"]
    assert database_before == hashlib.sha256(Path(store.path).read_bytes()).hexdigest()
    save_proof("U14-full-tick-horizon", {"old_soft_minutes": soft, "old_hard": hard,
        "tick_seconds": 30, "elapsed_runtime_hours": 10, "commits": 1201, "restarts": 2,
        "fixture_catalog_identities": 5, "fixture_observations_static": True,
        "maximum_projected_files": max_files, "maximum_projected_bytes": max_bytes,
        "files_budget": worker.files.maximum_files, "bytes_budget": worker.files.maximum_bytes,
        "elapsed_test_seconds": round(time.monotonic() - started, 3), "source_database_bytes_unchanged": True,
        "archive_configured": False, "production_capacity_claim": False,
        "real_orders_sent": 0, "provider_requests": 0, "ppi_watch": "UNTOUCHED"})


def test_u15_two_thousand_commits_archive_rotate_restart_and_bounded_checkpoint_preserve_receipt_chain(tmp_path):
    root, archive = tmp_path / "evidence", tmp_path / "private-archive"
    started = time.monotonic(); maximum_files, maximum_bytes = 0, 0
    for index in range(2000):
        with EvidenceFiles(root, maximum_files=32, archive_root=archive) as files:
            report = publish(files, index + 1)
            maximum_files = max(maximum_files, report["report"]["evidence_retention"]["projected_files"])
            maximum_bytes = max(maximum_bytes, report["report"]["evidence_retention"]["projected_bytes"])
        if index in (505, 1000, 1500): assert read_committed_generation(root)["report"]["number"] == index + 1
    receipts = [json.loads(path.read_text()) for path in archive.glob("*.receipt.json")]
    assert len(receipts) > 1900 and maximum_files <= 32
    previous = None
    for index, receipt in enumerate(sorted(receipts, key=lambda row: row["receipt_sequence"]), 1):
        assert receipt["receipt_sequence"] == index and receipt["previous_receipt_digest"] == previous
        previous = digest(receipt)
    checkpoint = json.loads((archive / "CHECKPOINT.json").read_text())
    assert checkpoint["receipt_count"] == len(receipts) and checkpoint["receipt_digest"] == previous
    assert json.loads((root / "archive-checkpoint.json").read_text()) == checkpoint
    assert not list(root.glob("archive-ack-*")) and not list(root.glob("delete-intent-*"))
    archive_bytes = sum(path.stat().st_size for path in archive.iterdir())
    assert archive_bytes < 512 * 1024**2 and len(list(archive.iterdir())) < 32768
    assert read_committed_generation(root)["report"]["number"] == 2000
    save_proof("U15-bounded-archive-cycles", {"commits": 2000, "writer_restarts": 1999,
        "receipts": len(receipts), "continuous_receipt_digest_chain": True, "live_ack_files": 0,
        "maximum_projected_live_files": maximum_files, "live_files_budget": 32,
        "maximum_projected_live_bytes": maximum_bytes, "archive_bytes": archive_bytes,
        "archive_bytes_budget": 512 * 1024**2, "archive_files_budget": 32768,
        "checkpoint_bytes": (root / "archive-checkpoint.json").stat().st_size,
        "elapsed_test_seconds": round(time.monotonic() - started, 3),
        "custody": "LOCAL_PRIVATE_FSYNC_NOT_WORM", "real_orders_sent": 0, "ppi_watch": "UNTOUCHED"})


def test_archive_quota_failure_preserves_unarchived_generations_and_last_committed_cut(tmp_path):
    root, archive = tmp_path / "evidence", tmp_path / "private-archive"
    cut = None
    for index in range(100):
        try:
            with EvidenceFiles(root, maximum_files=32, archive_root=archive, archive_maximum_bytes=70000) as files:
                cut = publish(files, index + 1)
        except RetentionPressure as error:
            assert error.reason == "RETENTION_ARCHIVE_ROTATION_FAILED"
            break
    else: pytest.fail("bounded archive did not fail closed")
    assert read_committed_generation(root)["pointer"] == cut["pointer"]
    assert (root / ("gen-" + cut["pointer"]["generation_id"])).is_dir()
    assert not list(root.glob(".deleting-*")) and not list(root.glob("delete-intent-*"))


def test_archive_destination_cannot_encompass_protected_input_and_missing_checkpoint_recovers_receipt_high_water(tmp_path):
    protected = tmp_path / "inputs"; protected.mkdir(); source = protected / "source.db"; source.write_bytes(b"private fixture")
    with pytest.raises(ValueError, match="OUTPUT_MUST_BE_SEPARATE"):
        EvidenceFiles(tmp_path / "evidence", protected=[source], archive_root=protected)
    root, archive, path, first = archive_fixture(tmp_path)
    with EvidenceFiles(root) as files:
        third = publish(files, 3)
    policy = EvidenceRetention(root, archive_root=archive)
    second = policy.archive_generation(root / ("gen-" + third["pointer"]["generation_id"]))
    assert second["receipt_sequence"] == 2
    (archive / "CHECKPOINT.json").unlink()
    # Complete immutable receipts recover their head; no sequence is reused.
    policy.prepare()
    assert json.loads((archive / "CHECKPOINT.json").read_text())["receipt_count"] == 2
    assert source.read_bytes() == b"private fixture"
