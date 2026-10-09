"""Native launcher archive configuration; isolated DATA, no Docker/provider work."""
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

import pytest

import porota_mode_manager as manager
from cg_paper_workspace import DB_ENV, artifact_root
from rc6_shadow_runtime.persistence import EvidenceFiles
from rc6_shadow_runtime.retention import EvidenceRetention
from rc6_shadow_runtime.worker import ShadowRuntime
from scripts.rc6_disk_space_guard import load_policy, required_pretransfer_free
from scripts.rc6_shadow_live_namespace import inspect_live, LEVEL, MAX_BYTES as LIVE_MAX, MAXIMUM_FILES
from scripts.rc6_sqlite_scratch_guard import component_hashes, emit_probe, report_metrics, validate_shadow_policy
from tests.test_issue465_generations import publish
from tests.test_rc6_convergence_sre_launcher import disk_path, prepare_launcher, read_env, source_fixture
from tests.test_rc6_convergence_sre_scratch import identity, layout, probe


ARCHIVE = "/app/data/paper_v17/artifacts/observer_v17.db/dynamic-shadow-archive"
REPO = Path(__file__).resolve().parents[1]


def adapt_owner(monkeypatch):
    # A GitHub runner's euid may differ from the image's fixed bot1000.
    monkeypatch.setattr(manager, "SQLITE_SCRATCH_UID", os.geteuid())
    monkeypatch.setattr(manager, "SQLITE_SCRATCH_GID", os.getegid())


def test_archive_env_is_canonical_shared_private_and_ignores_preview_drift(disk_path, monkeypatch):
    prepare_launcher(disk_path, monkeypatch, {})
    preview = manager.DATA / "diagnosticos/dashboard_preview_v1633.env"
    with preview.open("a") as stream:
        stream.write("POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT=/tmp/foreign-preview\n")
        stream.write("POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES=2147483648\n")
    observer = manager.observer_runtime_env()
    dashboard = manager.dashboard_env("PRODUCTION_PAPER")
    expected = {"POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT": ARCHIVE,
                "POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES": str(512 * 1024**2)}
    for path in (observer, dashboard):
        values = read_env(path)
        assert {key: values[key] for key in expected} == expected
        assert all(path.read_text().count(key + "=") == 1 for key in expected)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("key,value", [
    ("POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT", "/tmp/foreign-archive"),
    ("POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT", ARCHIVE + "\nPRIVATE_SECRET=EXPLICIT_SYNTHETIC"),
    ("POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT", ""),
    ("POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES", str(512 * 1024**2 + 1)),
    ("POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES", "0"),
    ("POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES", True),
])
def test_archive_operator_drift_rejects_before_any_runtime_env(disk_path, monkeypatch, key, value):
    prepare_launcher(disk_path, monkeypatch, {key: value})
    with pytest.raises(ValueError, match="ARCHIVE_CONFIGURATION_IMMUTABLE") as rejected:
        manager.observer_runtime_env()
    assert "EXPLICIT_SYNTHETIC" not in str(rejected.value)
    assert not (manager.DATA / "diagnosticos/observer_runtime_v17.env").exists()
    assert not (manager.DATA / "diagnosticos/dashboard_mode_v17.env").exists()


def test_prepared_archive_is_owned_private_and_reentry_preserves_residue(disk_path, monkeypatch):
    prepare_launcher(disk_path, monkeypatch, {})
    adapt_owner(monkeypatch)
    root = manager.prepare_shadow_archive_root({})
    info = root.stat()
    assert root == manager.DATA / Path(ARCHIVE).relative_to("/app/data")
    assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (os.geteuid(), os.getegid(), 0o700)
    # This guard proves namespace preservation, not archive semantic admission.
    residue = root / "local-preserved-marker"
    residue.write_bytes(b"EXPLICIT_SYNTHETIC_RESIDUE")
    before = (residue.read_bytes(), residue.stat().st_ino, residue.stat().st_mtime_ns)
    assert manager.prepare_shadow_archive_root({}) == root
    assert (residue.read_bytes(), residue.stat().st_ino, residue.stat().st_mtime_ns) == before


@pytest.mark.parametrize("mutation", ["symlink", "regular_file", "public_mode"])
def test_foreign_archive_namespace_is_not_repaired(disk_path, monkeypatch, mutation):
    prepare_launcher(disk_path, monkeypatch, {})
    adapt_owner(monkeypatch)
    target = manager.DATA / Path(ARCHIVE).relative_to("/app/data")
    target.parent.mkdir(parents=True, exist_ok=True)
    if mutation == "symlink":
        foreign = disk_path / "foreign"
        foreign.mkdir()
        target.symlink_to(foreign, target_is_directory=True)
    elif mutation == "regular_file":
        target.write_bytes(b"FOREIGN")
    else:
        target.mkdir(mode=0o755)
    before = target.lstat()
    with pytest.raises(ValueError, match="ARCHIVE_(PATH_ALIAS|ROOT_CUSTODY_REQUIRED)"):
        manager.prepare_shadow_archive_root({})
    after = target.lstat()
    assert (before.st_mode, before.st_uid, before.st_gid, before.st_ino, before.st_size) == (
        after.st_mode, after.st_uid, after.st_gid, after.st_ino, after.st_size)


def _member(root, name, data=b""):
    descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    return root / name


def _metadata(root):
    result = {}
    queue = [root]
    for path in queue:
        info = path.lstat()
        result[str(path)] = info
        if stat.S_ISDIR(info.st_mode):
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
            try:
                with os.scandir(descriptor) as iterator:
                    queue.extend(path / member.name for member in iterator)
            finally:
                os.close(descriptor)
    return result


def test_native_launcher_env_round_trip_configures_factory_archive_without_source_or_evidence_writes(disk_path, monkeypatch):
    data, primary = layout(disk_path)
    prepare_launcher(disk_path, monkeypatch, {})
    values = read_env(manager.observer_runtime_env())
    for key in ("POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT", "POROTA_DYNAMIC_SHADOW_ROOT",
                "POROTA_SHADOW_RUNTIME_ROOT", "POROTA_CAPACITY_SHADOW_PATH"):
        monkeypatch.setenv(key, str(disk_path / values[key].removeprefix("/app/")))
    monkeypatch.setenv("POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES", values["POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES"])
    monkeypatch.setenv(DB_ENV, str(primary))
    monkeypatch.setenv("DATA_DIR", str(data))
    monkeypatch.setenv("POROTA_DYNAMIC_CAPACITY_MODE", "OFF")
    before = identity(primary)
    runtime = ShadowRuntime.from_environment(primary, source_roots=[])
    explicit_offline = ShadowRuntime(primary, source_roots=[], archive_root=None)
    expected = artifact_root(primary) / "dynamic-shadow-archive"
    assert runtime.files.archive_root == expected and runtime.files.archive_maximum_bytes == 512 * 1024**2
    assert runtime.configuration != explicit_offline.configuration
    assert explicit_offline.files.archive_root is None
    assert not expected.exists() and not runtime.root.exists()
    assert identity(primary) == before


def test_combined_native_inventory_reserves_only_unoccupied_growth_and_binds_all_emitted_sources(disk_path):
    data, primary = layout(disk_path)
    live, archive = (artifact_root(primary) / name for name in ("dynamic-shadow", "dynamic-shadow-archive"))
    with EvidenceFiles(live) as files:
        publish(files, 1)
    generation = next(live.glob("gen-*"))
    archived = EvidenceRetention(live, archive_root=archive).archive_generation(generation)
    before_live, before_archive, before_source = _metadata(live), _metadata(archive), identity(primary)
    observed = probe(primary, data)
    assert observed["status"] == "GREEN" and archived["durable"] is True
    assert observed["live_namespace"]["verification_level"] == LEVEL
    assert observed["live_namespace"]["maximum_files"] == 512
    assert observed["archive_namespace"]["verification_level"] == "BOUNDED_ARCHIVE_NAMESPACE_AND_CUSTODY_METADATA"
    assert observed["live_occupied_bytes"] == sum(info.st_size for path, info in before_live.items() if path != str(live))
    assert observed["archive_namespace"]["occupied_bytes"] == sum(info.st_size for path, info in before_archive.items() if path != str(archive))
    archive_inventory = observed["archive_namespace"]
    assert observed["archive_occupied_bytes"] == min(archive_inventory["occupied_bytes"], archive_inventory.get("allocated_bytes", 0))
    sizes = report_metrics(observed)
    policy = load_policy(REPO / "ops/policy/rc6-disk-housekeeping-v1.json")
    required = required_pretransfer_free(100, 1000, 10, policy, *sizes[:3])
    assert required == 1120 + observed["derived_growth_bytes"] + 2 * 1024**3
    assert before_live == _metadata(live) and before_archive == _metadata(archive)
    assert before_source == identity(primary)
    isolated = disk_path / "without-checkout"; isolated.mkdir()
    run = subprocess.run([sys.executable, "-", "--database", str(primary), "--data-root", str(data),
        "--owner-uid", str(os.geteuid()), "--owner-gid", str(os.getegid())], input=emit_probe(REPO),
        cwd=isolated, env={**os.environ, "PYTHONPATH": ""}, capture_output=True, text=True, timeout=20)
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(run.stdout)
    source_paths = {"scratch": "rc6_audit_evidence/sqlite_scratch.py",
                    "archive": "rc6_shadow_runtime/archive_namespace.py",
                    "live": "scripts/rc6_shadow_live_namespace.py",
                    "admission": "scripts/rc6_sqlite_scratch_guard.py"}
    source_hashes = {name: hashlib.sha256((REPO / path).read_bytes()).hexdigest()
                     for name, path in source_paths.items()}
    assert report["candidate_component_sha256"] == source_hashes == component_hashes(REPO)
    assert report["live_namespace"]["maximum_files"] == 512
    assert report_metrics(report) == sizes
    assert before_live == _metadata(live) and before_archive == _metadata(archive) and before_source == identity(primary)


@pytest.mark.parametrize("namespace,mutation", [
    ("archive", "unknown"), ("archive", "mode"), ("archive", "alias"),
    ("archive", "quota"), ("archive", "writer"), ("archive", "owned_residue"),
    ("archive", "root_self_alias"),
    ("live", "unknown"), ("live", "mode"), ("live", "alias"),
    ("live", "quota"), ("live", "writer"), ("live", "owned_residue"),
    ("live", "nested_alias"), ("live", "hardlink"),
    ("live", "root_self_alias"),
])
def test_native_combined_admission_fails_closed_and_preserves_unknown_or_interrupted_namespace(disk_path, namespace, mutation):
    data, primary = layout(disk_path)
    root = artifact_root(primary) / ("dynamic-shadow-archive" if namespace == "archive" else "dynamic-shadow")
    root.mkdir(parents=True, mode=0o700)
    lock = _member(root, "archive.lock" if namespace == "archive" else "writer.lock")
    leaf = _member(root, "a" * 32 + ".tar.gz" if namespace == "archive" else "status.json", b"EXPLICIT_SYNTHETIC_NAMESPACE_BYTES")
    held = None
    if mutation == "unknown": _member(root, "foreign-source.db", b"PRESERVE_UNKNOWN")
    elif mutation == "mode": leaf.chmod(0o644)
    elif mutation == "alias": leaf.unlink(); leaf.symlink_to(primary)
    elif mutation == "quota":
        with leaf.open("r+b") as handle: handle.truncate(512 * 1024**2 if namespace == "archive" else LIVE_MAX)
    elif mutation == "writer":
        held = os.open(lock, os.O_RDWR | os.O_NOFOLLOW); fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif mutation == "owned_residue":
        _member(root, ".archive-" + "b" * 32 + ".tmp" if namespace == "archive" else ".independent-" + "b" * 32 + ".tmp", b"PRESERVE_RECOVERY")
    elif mutation == "nested_alias":
        (root / ("gen-" + "b" * 32)).symlink_to(primary.parent, target_is_directory=True)
    elif mutation == "root_self_alias":
        root.rename(root.with_name("preserved-" + root.name))
        root.symlink_to(root.name, target_is_directory=True)
    else: os.link(leaf, root / "checkpoint.json.gz")
    before, source = _metadata(root), identity(primary)
    try:
        if mutation == "owned_residue":
            report = probe(primary, data)
            assert report["status"] == "RED"
            assert report[namespace + "_namespace"]["state"] == "RECOVERY_REQUIRED"
            assert report[namespace + "_namespace"]["owned_temporary_count"] == 1
            assert report_metrics(report)
        else:
            with pytest.raises(ValueError): probe(primary, data)
    finally:
        if held is not None: os.close(held)
    assert before == _metadata(root) and source == identity(primary)


def test_live_inventory_bounded_entries_and_ancestor_replacement_reject_before_alias_is_followed(disk_path, monkeypatch):
    root = disk_path / "live"; root.mkdir(mode=0o700)
    _member(root, "writer.lock")
    with pytest.raises(ValueError, match="FILES_CAPACITY"):
        inspect_live(root, maximum_files=1, owner_uid=os.geteuid())
    original = os.open
    swapped = False
    def changed(name, flags, *args, **kwargs):
        nonlocal swapped
        if name == "live" and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            root.rename(root.with_name("preserved-live"))
            root.symlink_to(root.with_name("preserved-live"), target_is_directory=True)
        return original(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", changed)
    with pytest.raises(ValueError): inspect_live(root, owner_uid=os.geteuid())
    assert swapped and root.is_symlink() and (root.with_name("preserved-live") / "writer.lock").is_file()


def test_live_admission_accepts_511_entries_and_closes_at_512_without_mutating_custody(disk_path):
    # Empty owned ACK-shaped controls exercise namespace admission only. Their
    # contents do not assert valid receipt CRCs or producer publication capacity.
    root = disk_path / "live-capacity"
    root.mkdir(mode=0o700)
    _member(root, "writer.lock")
    for number in range(510):
        _member(root, f"archive-ack-{number:032x}.json")
    before = _metadata(root)
    observed = inspect_live(root, owner_uid=os.geteuid())
    assert MAXIMUM_FILES == observed["maximum_files"] == 512
    assert observed["files"] == 511 and observed["state"] == "WITHIN_QUOTA"
    assert observed["verification_level"] == LEVEL
    assert observed["occupied_bytes"] == 0
    assert before == _metadata(root)
    _member(root, f"archive-ack-{510:032x}.json")
    at_capacity = _metadata(root)
    with pytest.raises(ValueError, match="^LIVE_FILES_CAPACITY_REACHED$"):
        inspect_live(root, owner_uid=os.geteuid())
    assert at_capacity == _metadata(root)


@pytest.mark.parametrize("proposed_limit", [513, 8192, True])
def test_admission_rejects_a_relaxed_or_untyped_live_limit_without_changing_other_policy(proposed_limit):
    policy = load_policy(REPO / "ops/policy/rc6-disk-housekeeping-v1.json")
    original = deepcopy(policy)
    assert validate_shadow_policy(policy)["private_live_evidence"]["maximum_files"] == 512
    policy["shadow"]["private_live_evidence"]["maximum_files"] = proposed_limit
    with pytest.raises(ValueError, match="^RC6_SHADOW_POLICY_DRIFT$"):
        validate_shadow_policy(policy)
    policy["shadow"]["private_live_evidence"]["maximum_files"] = 512
    assert policy == original


@pytest.mark.parametrize("mutation", ["missing_archive", "bool_residence", "rebound_source", "unhonest_level", "contradictory_growth", "recovery_green"])
def test_native_report_parser_rejects_unbound_or_contradictory_namespace_metrics(disk_path, mutation):
    data, primary = layout(disk_path)
    report = probe(primary, data)
    if mutation == "missing_archive": del report["archive_namespace"]
    elif mutation == "bool_residence": report["archive_namespace"]["occupied_bytes"] = False
    elif mutation == "rebound_source": report["candidate_component_sha256"]["archive"] = "0" * 64
    elif mutation == "unhonest_level": report["live_namespace"]["verification_level"] = "FULL_ARCHIVE_SEMANTICS"
    elif mutation == "contradictory_growth": report["live_growth_bytes"] -= 1
    else: report["archive_namespace"]["state"] = "RECOVERY_REQUIRED"
    result = subprocess.run([sys.executable, str(REPO / "scripts/rc6_sqlite_scratch_guard.py"), "--validate-report"],
        input=json.dumps(report), capture_output=True, text=True, timeout=10)
    assert result.returncode == 42 and json.loads(result.stdout)["status"] == "RED"


def test_native_pretransfer_config_drift_and_duplicates_reject_before_transfer_without_echoing_values(disk_path):
    data, primary = layout(disk_path)
    path = disk_path / ".env"
    for content in ("POROTA_DYNAMIC_SHADOW_ARCHIVE_ROOT=/tmp/PRIVATE_SYNTHETIC\n",
                    "POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES=536870913\n",
                    "POROTA_DYNAMIC_SHADOW_ARCHIVE_MAX_BYTES=536870912\n" * 2):
        path.write_text(content); path.chmod(0o600)
        before = identity(primary)
        with pytest.raises(ValueError) as rejection: probe(primary, data)
        assert "PRIVATE_SYNTHETIC" not in str(rejection.value)
        assert not artifact_root(primary).exists() and identity(primary) == before


def test_native_launcher_rejects_unknown_archive_before_container_stop_and_preserves_namespace(disk_path, monkeypatch):
    _, _, calls = source_fixture(disk_path, monkeypatch)
    data, primary = layout(disk_path)
    prepare_launcher(disk_path, monkeypatch, {})
    root = artifact_root(primary) / "dynamic-shadow-archive"
    root.mkdir(parents=True, mode=0o700)
    _member(root, "unknown-owned-source.db", b"PRESERVE_NAMESPACE")
    before, source = _metadata(root), identity(primary)
    stopped = []
    monkeypatch.setattr(manager, "stop_engines", lambda: stopped.append(True))
    with pytest.raises(ValueError, match="NATIVE_ADMISSION_FAILED"):
        manager.simulation()
    assert not stopped and not any("rm" in call or "run" in call for call in calls)
    assert before == _metadata(root) and source == identity(primary)


@pytest.mark.parametrize("namespace", ["archive", "live"])
def test_native_sparse_namespace_cannot_discount_unallocated_logical_bytes_from_future_growth(disk_path, namespace):
    data, primary = layout(disk_path)
    root = artifact_root(primary) / ("dynamic-shadow-archive" if namespace == "archive" else "dynamic-shadow")
    root.mkdir(parents=True, mode=0o700)
    _member(root, "archive.lock" if namespace == "archive" else "writer.lock")
    path = _member(root, "a" * 32 + ".tar.gz" if namespace == "archive" else "status.json")
    with path.open("r+b") as handle:
        handle.truncate(4 * 1024**2)
    info = path.stat()
    assert info.st_size == 4 * 1024**2 and info.st_blocks * 512 < info.st_size
    before, source = _metadata(root), identity(primary)
    report = probe(primary, data)
    assert report[namespace + "_namespace"]["occupied_bytes"] == info.st_size
    assert report[namespace + "_occupied_bytes"] <= info.st_blocks * 512
    maximum = 512 * 1024**2 if namespace == "archive" else LIVE_MAX
    assert report[namespace + "_growth_bytes"] >= maximum - info.st_blocks * 512
    assert before == _metadata(root) and source == identity(primary)
