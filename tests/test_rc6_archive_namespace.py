"""Private, synthetic archive admission; no source or archive recovery writes."""
import fcntl
import os
from pathlib import Path

import pytest

from rc6_shadow_runtime.archive_namespace import inspect_archive, LEVEL, SCHEMA
from rc6_shadow_runtime.persistence import EvidenceFiles
from rc6_shadow_runtime.retention import EvidenceRetention
from tests.test_issue465_generations import publish


def prepared(tmp_path):
    root = tmp_path / "archive"
    root.mkdir(mode=0o700)
    return root


def member(root, name, data=b""):
    descriptor = os.open(root / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
    return root / name


def inventory(root):
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
    try:
        with os.scandir(descriptor) as iterator:
            paths = [root / entry.name for entry in iterator]
        return {path.relative_to(root): path.lstat() for path in (root, *paths)}
    finally:
        os.close(descriptor)


def test_empty_prepared_root_and_native_archive_have_bounded_honest_inventory_without_metadata_writes(tmp_path):
    root = prepared(tmp_path)
    before = inventory(root)
    empty = inspect_archive(root)
    assert empty["state"] == "WITHIN_QUOTA" and empty["occupied_bytes"] == 0
    assert empty["files"] == 0 and empty["inodes"] == 1
    assert before == inventory(root)
    live = tmp_path / "live"
    with EvidenceFiles(live) as files:
        publish(files, 1)
    generation = next(live.glob("gen-*"))
    receipt = EvidenceRetention(live, archive_root=root).archive_generation(generation)
    before = inventory(root)
    result = inspect_archive(root)
    assert result["schema"] == SCHEMA and result["verification_level"] == LEVEL
    assert result["occupied_bytes"] == sum(path.stat().st_size for path in root.iterdir())
    assert result["files"] == 4 and result["inodes"] == 5
    assert result["growth_remaining_bytes"] + result["occupied_bytes"] == 512 * 1024**2
    assert result["owned_temporary_count"] == 0 and result["free_bytes"] > 0
    assert result["state"] == "WITHIN_QUOTA" and receipt["durable"] is True
    assert before == inventory(root)


def test_recognized_interrupted_archive_is_counted_without_cleanup_or_semantic_claim(tmp_path):
    root = prepared(tmp_path)
    member(root, "archive.lock")
    temporary = member(root, ".archive-" + "a" * 32 + ".tmp", b"synthetic interrupted bytes")
    before = inventory(root)
    result = inspect_archive(root)
    assert result["state"] == "RECOVERY_REQUIRED"
    assert result["owned_temporary_count"] == 1 and result["occupied_bytes"] == temporary.stat().st_size
    assert before == inventory(root)


@pytest.mark.parametrize("fault", ["unknown", "mode", "hardlink", "symlink", "uid", "lock_missing", "bytes", "files"])
def test_unknown_alias_custody_and_exhausted_capacity_fail_closed_without_repair(tmp_path, fault):
    root = prepared(tmp_path)
    lock = member(root, "archive.lock")
    path = member(root, "a" * 32 + ".tar.gz", b"synthetic")
    kwargs = {}
    if fault == "unknown": member(root, "unexpected.json")
    elif fault == "mode": path.chmod(0o644)
    elif fault == "hardlink": os.link(path, root / ("b" * 32 + ".tar.gz"))
    elif fault == "symlink": (root / ("b" * 32 + ".tar.gz")).symlink_to(path)
    elif fault == "uid": kwargs["owner_uid"] = os.getuid() + 1
    elif fault == "lock_missing": lock.unlink()
    elif fault == "bytes": kwargs["max_bytes"] = path.stat().st_size
    elif fault == "files": kwargs["maximum_files"] = 2
    before = inventory(root)
    with pytest.raises(ValueError):
        inspect_archive(root, **kwargs)
    assert before == inventory(root)


def test_missing_root_root_alias_and_ancestor_alias_do_not_create_or_follow_namespace(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(ValueError, match="ROOT_REQUIRED"):
        inspect_archive(missing)
    assert not missing.exists()
    root = prepared(tmp_path)
    alias = tmp_path / "alias"; alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="ALIAS"):
        inspect_archive(alias)
    parent_alias = tmp_path / "parent-alias"; parent_alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="ALIAS"):
        inspect_archive(parent_alias / root.name)


def test_existing_writer_blocks_read_only_admission_and_lock_is_not_recreated(tmp_path):
    root = prepared(tmp_path)
    path = member(root, "archive.lock")
    descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = inventory(root)
        with pytest.raises(ValueError, match="WRITER_ACTIVE"):
            inspect_archive(root)
        assert before == inventory(root)
    finally:
        os.close(descriptor)


def test_namespace_mutation_during_admission_is_rejected(tmp_path, monkeypatch):
    from rc6_shadow_runtime import archive_namespace
    root = prepared(tmp_path)
    member(root, "archive.lock")
    path = member(root, "a" * 32 + ".tar.gz", b"synthetic")
    original = archive_namespace.os.stat
    def mutated(name, *args, **kwargs):
        if name == path.name and kwargs.get("dir_fd") is not None:
            with path.open("ab") as stream:
                stream.write(b"changed")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(archive_namespace.os, "stat", mutated)
    with pytest.raises(ValueError, match="NAMESPACE_CHANGED"):
        inspect_archive(root)


@pytest.mark.parametrize("kwargs", [{"owner_uid": True}, {"owner_uid": -1}, {"max_bytes": True},
    {"max_bytes": 0}, {"max_bytes": 512 * 1024**2 + 1}, {"maximum_files": False},
    {"maximum_files": 0}, {"maximum_files": 32769}])
def test_invalid_policy_is_rejected_before_opening_namespace(tmp_path, kwargs):
    root = tmp_path / "missing"
    with pytest.raises(ValueError, match="POLICY_INVALID"):
        inspect_archive(root, **kwargs)
    assert not root.exists()
