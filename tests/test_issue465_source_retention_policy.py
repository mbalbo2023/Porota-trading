"""F-05: pressure is explicit; deleting committed evidence requires exact ACK."""
import gzip
import fcntl
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from rc6_dynamic_universe.common import digest
from rc6_shadow_runtime.retention import EvidenceRetention, RetentionPolicy, RetentionPressure


def generation(root, number=1, size=400):
    generation_id = f"{number:032x}"
    path = root / ("gen-" + generation_id)
    path.mkdir()
    files = {}
    for role, name in (("report", "report.json.gz"), ("checkpoint", "checkpoint.json.gz"), ("status", "status.json")):
        payload = {"generation_id": generation_id, "data": "x" * size, "real_orders_sent": 0}
        raw = json.dumps({"payload": payload, "digest": digest(payload)}, sort_keys=True).encode()
        if name.endswith("gz"):
            raw = gzip.compress(raw, mtime=0)
        (path / name).write_bytes(raw)
        files[role] = {"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "payload_digest": digest(payload)}
    manifest = {"schema": "rc6.shadow-evidence-generation.v1", "generation_id": generation_id,
        "sequence": number, "files": files}
    (path / "manifest.json").write_text(json.dumps(manifest, sort_keys=True))
    return path


def acknowledgement(path, **changes):
    generation_id = path.name.removeprefix("gen-")
    ack = {"schema": "RC6_SHADOW_ARCHIVE_ACK_V1", "generation_id": generation_id,
        "manifest_sha256": hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest(),
        "archive_sha256": "a" * 64, "archive_uri": "private-archive://fixture/verified-export",
        "archive_verified": True, "durable": True, "acknowledged_at": "2020-01-01T00:00:00+00:00", **changes}
    target = path.parent / f"archive-ack-{generation_id}.json"
    target.write_text(json.dumps(ack))
    return target


def pointer(path):
    value = {"generation_id": path.name.removeprefix("gen-"), "sequence": 1,
        "manifest_sha256": hashlib.sha256((path / "manifest.json").read_bytes()).hexdigest()}
    value["digest"] = digest(value)
    (path.parent / "CURRENT.json").write_text(json.dumps(value))


def test_explicit_default_policy_has_soft_warning_and_hard_limits():
    policy = RetentionPolicy()
    assert policy.maximum_files == 512 and policy.maximum_bytes == 128 * 1024**2
    assert policy.soft_ratio == .8 and policy.control_reserve_bytes > 0


@pytest.mark.parametrize("changes", [{"maximum_files": 0}, {"maximum_bytes": -1},
    {"soft_ratio": 1}, {"soft_ratio": float("nan")}, {"maximum_files": True}])
def test_invalid_policy_fails_closed(changes):
    with pytest.raises(ValueError, match="POLICY"):
        RetentionPolicy(**changes)


def test_soft_threshold_alerts_before_hard_and_admits_shadow(tmp_path):
    for n in range(8):
        (tmp_path / str(n)).write_bytes(b"x")
    status = EvidenceRetention(tmp_path, maximum_files=10).prepare()
    assert status["status"] == "RETENTION_PRESSURE"
    assert status["reason"] == "RETENTION_SOFT_THRESHOLD"
    assert not status["shadow_degraded"] and status["files"] == 9


def test_soft_bytes_threshold_and_recursive_directory_bytes_are_visible(tmp_path):
    path = tmp_path / "archive"
    path.mkdir()
    (path / "payload").write_bytes(b"x" * 9000)
    actual = path.lstat().st_size + (path / "payload").lstat().st_size
    metrics = EvidenceRetention(tmp_path, maximum_bytes=actual + 100).prepare()
    assert metrics["bytes"] == actual and metrics["files"] == 3
    assert metrics["status"] == "RETENTION_PRESSURE" and not metrics["shadow_degraded"]


def test_513_files_never_deleted_without_ack_and_shadow_degrades(tmp_path):
    for n in range(513):
        (tmp_path / str(n)).write_bytes(b"evidence")
    with pytest.raises(RetentionPressure, match="HARD_FILES.*CAPACITY") as error:
        EvidenceRetention(tmp_path).prepare()
    assert len([path for path in tmp_path.iterdir() if path.name != "writer.lock"]) == 513
    assert error.value.metrics["shadow_degraded"]
    assert error.value.metrics["factual_paths_effect"] == "NONE"
    assert error.value.metrics["real_orders_sent"] == 0


def test_logical_bytes_above_128mib_are_counted_even_for_sparse_files(tmp_path):
    path = tmp_path / "evidence.gz"
    with path.open("wb") as stream:
        stream.truncate(128 * 1024**2 + 1)
    with pytest.raises(RetentionPressure, match="HARD_BYTES.*CAPACITY") as error:
        EvidenceRetention(tmp_path).prepare()
    assert error.value.metrics["bytes"] == 128 * 1024**2 + 1
    assert path.exists()


def test_peak_admission_includes_new_generation_not_just_final_pointer(tmp_path):
    committed = generation(tmp_path)
    pointer(committed)
    policy = EvidenceRetention(tmp_path, maximum_files=10)
    with pytest.raises(RetentionPressure, match="HARD_FILES") as error:
        policy.prepare(additional_files=6, additional_bytes=5000)
    assert error.value.metrics["projected_files"] == 13
    assert committed.exists()


def test_no_filesystem_space_is_explicit_even_when_logical_quota_allows(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "statvfs", lambda _: SimpleNamespace(f_bavail=0, f_frsize=4096))
    with pytest.raises(RetentionPressure, match="RETENTION_NO_SPACE") as error:
        EvidenceRetention(tmp_path).prepare(additional_bytes=100, additional_files=1)
    assert error.value.metrics["filesystem_available_bytes"] == 0
    assert error.value.metrics["shadow_degraded"]


def test_acknowledged_unpinned_generation_rotates_but_ack_ledger_remains(tmp_path):
    old, current = generation(tmp_path), generation(tmp_path, number=2)
    ack = acknowledgement(old)
    pointer(current)
    result = EvidenceRetention(tmp_path, maximum_files=14).prepare(additional_files=6)
    assert not old.exists() and current.exists() and ack.exists()
    assert result["rotated_archived_generations"] == 1
    assert result["projected_files"] <= 14


def test_no_ack_never_rotates_committed_generation_even_under_pressure(tmp_path):
    old = generation(tmp_path)
    with pytest.raises(RetentionPressure, match="HARD_FILES"):
        EvidenceRetention(tmp_path, maximum_files=6).prepare(additional_files=6)
    assert old.exists() and len(list(old.iterdir())) == 4


@pytest.mark.parametrize("changes", [
    {"manifest_sha256": "b" * 64}, {"generation_id": "f" * 32},
    {"archive_sha256": "0" * 64}, {"archive_uri": ""}, {"archive_verified": False},
    {"durable": False}, {"acknowledged_at": "2020-01-01"}, {"acknowledged_at": "2099-01-01T00:00:00+00:00"},
    {"schema": "UNTRUSTED"},
])
def test_wrong_unverified_expiring_or_future_ack_does_not_authorize_deletion(tmp_path, changes):
    path = generation(tmp_path)
    acknowledgement(path, **changes)
    with pytest.raises(RetentionPressure, match="HARD_FILES"):
        EvidenceRetention(tmp_path, maximum_files=6).prepare(additional_files=6)
    assert path.exists()


def test_changed_generation_bytes_cannot_be_deleted_with_old_manifest_and_ack(tmp_path):
    path = generation(tmp_path)
    acknowledgement(path)
    (path / "report.json.gz").write_bytes(b"tampered after export")
    with pytest.raises(RetentionPressure, match="HARD_FILES"):
        EvidenceRetention(tmp_path, maximum_files=6).prepare(additional_files=6)
    assert path.exists()


@pytest.mark.parametrize("pin", ["argument", "registry", "current"])
def test_pinned_generation_survives_valid_ack_and_hard_pressure(tmp_path, pin):
    path = generation(tmp_path)
    acknowledgement(path)
    generation_id = path.name.removeprefix("gen-")
    supplied = [generation_id] if pin == "argument" else []
    if pin == "current":
        pointer(path)
    if pin == "registry":
        (tmp_path / "retention-pins.json").write_text(json.dumps({
            "schema": "RC6_SHADOW_EVIDENCE_PINS_V1", "generation_ids": [generation_id]}))
    with pytest.raises(RetentionPressure, match="HARD_FILES"):
        EvidenceRetention(tmp_path, maximum_files=10).prepare(additional_files=6, pinned=supplied)
    assert path.exists()


def test_preopen_freezes_and_unknown_files_never_rotate(tmp_path):
    freeze = tmp_path / "preopen-2026-10-05.json.gz"
    freeze.write_bytes(b"immutable freeze provenance")
    unknown = tmp_path / "uncommitted-looking-evidence.tmp"
    unknown.write_bytes(b"not an owned temporary")
    with pytest.raises(RetentionPressure, match="HARD_FILES"):
        EvidenceRetention(tmp_path, maximum_files=3).prepare(additional_files=6)
    assert freeze.read_bytes() == b"immutable freeze provenance" and unknown.exists()


def test_owned_staging_and_exclusive_old_temporaries_are_cleaned_safely(tmp_path):
    staging = tmp_path / (".generation-" + "a" * 32 + ".tmp")
    staging.mkdir()
    (staging / "report.json.gz").write_bytes(b"uncommitted")
    temp = tmp_path / ".CURRENT.json.abcdefgh.tmp"
    temp.write_bytes(b"uncommitted pointer")
    result = EvidenceRetention(tmp_path).prepare(additional_files=6)
    assert not staging.exists() and not temp.exists()
    assert result["cleaned_temporaries"] == 2


def test_exact_protocol_pointer_and_independent_uuid_temporaries_are_cleaned(tmp_path):
    paths = [tmp_path / (".CURRENT." + "a" * 32 + ".tmp"),
        tmp_path / (".independent-" + "b" * 32 + ".tmp")]
    for path in paths:
        path.write_bytes(b"uncommitted")
    result = EvidenceRetention(tmp_path).prepare()
    assert all(not path.exists() for path in paths)
    assert result["cleaned_temporaries"] == 2


def test_active_writer_staging_cannot_be_cleaned_by_another_caller(tmp_path):
    lock = (tmp_path / "writer.lock").open("w+")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        staging = tmp_path / (".generation-" + "a" * 32 + ".tmp")
        staging.mkdir()
        payload = staging / "report.json.gz"
        payload.write_bytes(b"active write")
        with pytest.raises(RetentionPressure, match="WRITER_BUSY"):
            EvidenceRetention(tmp_path).prepare()
        assert payload.read_bytes() == b"active write"
        # The actual writer's exact fd can clean its abandoned staging while
        # its own exclusive lock remains held after admission.
        EvidenceRetention(tmp_path).prepare(writer_fd=lock.fileno())
        assert not staging.exists()
        with pytest.raises(RetentionPressure, match="WRITER_BUSY"):
            EvidenceRetention(tmp_path).prepare()
    finally:
        lock.close()


def test_other_file_descriptor_is_not_authority_to_delete_staging(tmp_path):
    (tmp_path / "writer.lock").write_bytes(b"")
    with (tmp_path / "different.lock").open("w+") as lock:
        with pytest.raises(RetentionPressure, match="LOCK_ALIAS_INVALID"):
            EvidenceRetention(tmp_path).prepare(writer_fd=lock.fileno())


def test_unowned_content_in_staging_is_preserved_and_degrades(tmp_path):
    staging = tmp_path / (".generation-" + "a" * 32 + ".tmp")
    staging.mkdir()
    secret = staging / "unowned.db"
    secret.write_bytes(b"protected")
    with pytest.raises(RetentionPressure, match="TEMP_CLEANUP_UNSAFE"):
        EvidenceRetention(tmp_path).prepare()
    assert secret.read_bytes() == b"protected"


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_aliases_are_rejected_before_any_cleanup_or_rotation(tmp_path, alias):
    external = tmp_path.parent / (tmp_path.name + "-source.db")
    external.write_bytes(b"trading input")
    target = tmp_path / ".CURRENT.json.abcdefgh.tmp"
    if alias == "symlink":
        target.symlink_to(external)
    else:
        os.link(external, target)
    with pytest.raises(RetentionPressure, match="ALIAS"):
        EvidenceRetention(tmp_path).prepare()
    assert external.read_bytes() == b"trading input" and target.exists()


def test_root_and_ancestor_aliases_are_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    (real / "child").mkdir()
    for root in (alias, alias / "child"):
        with pytest.raises(ValueError, match="ALIAS"):
            EvidenceRetention(root)


def test_corrupt_or_stale_current_and_pin_registry_prevent_rotation(tmp_path):
    path = generation(tmp_path)
    acknowledgement(path)
    pointer(path)
    current = tmp_path / "CURRENT.json"
    data = json.loads(current.read_text())
    data["generation_id"] = "f" * 32
    current.write_text(json.dumps(data))
    with pytest.raises(RetentionPressure, match="PIN_OR_CURRENT_INVALID"):
        EvidenceRetention(tmp_path).prepare()
    assert path.exists()


def test_recursive_inventory_is_bounded_and_does_not_follow_deep_trees(tmp_path):
    path = tmp_path
    for _ in range(6):
        path /= "nested"
        path.mkdir()
    with pytest.raises(RetentionPressure, match="INVENTORY_DEPTH"):
        EvidenceRetention(tmp_path).prepare()


@pytest.mark.parametrize("projection", [{"additional_bytes": -1}, {"additional_files": True}])
def test_invalid_peak_projection_fails_closed(tmp_path, projection):
    with pytest.raises(ValueError, match="PROJECTION"):
        EvidenceRetention(tmp_path).prepare(**projection)
