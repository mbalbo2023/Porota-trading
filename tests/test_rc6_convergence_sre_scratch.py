"""Native disk-copy guards; synthetic DATA layout, no Docker/provider execution."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

import porota_mode_manager as manager
from cg_paper_workspace import DB_ENV, artifact_root
from rc6_audit_evidence.sqlite_snapshot import readonly_copy
from rc6_audit_evidence.sqlite_scratch import inspect_scratch, snapshot_peak_bytes
from scripts.rc6_disk_space_guard import load_policy, required_pretransfer_free
from scripts.rc6_sqlite_scratch_guard import (
    ENV_KEYS, MAX_BYTES, RESERVE_BYTES, SCHEMA, ScratchAdmissionError,
    emit_probe, history_container_path, probe, validate_policy,
)
from tests.test_rc6_convergence_sre_launcher import (
    disk_path, prepare_launcher, read_env, source_fixture,
)

REPO = Path(__file__).resolve().parents[1]
POLICY = REPO / "ops/policy/rc6-disk-housekeeping-v1.json"


def make_database(path, *, large=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE native_copy_rows(id INTEGER PRIMARY KEY,payload BLOB)")
        connection.executemany("INSERT INTO native_copy_rows(payload) VALUES(zeroblob(4096))",
                               [()] * (10000 if large else 3))
    path.chmod(0o600)
    return path


def identity(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NOATIME)
    with os.fdopen(descriptor, "rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size,
            info.st_atime_ns, info.st_mtime_ns, info.st_ctime_ns, digest)


def layout(disk_path):
    data = disk_path / "data"
    primary = make_database(data / "paper_v17/observer_v17.db")
    return data, primary


def test_generated_both_launcher_envs_filter_preview_scratch_and_pin_reviewed_history(disk_path, monkeypatch):
    _, primary = layout(disk_path)
    reviewed = "/app/data/reviewed/history-preserved-copy.db"
    make_database(disk_path / reviewed.removeprefix("/app/"))
    prepare_launcher(disk_path, monkeypatch, {"HIST_DB_PATH": reviewed})
    preview = manager.DATA / "diagnosticos/dashboard_preview_v1633.env"
    with preview.open("a") as handle:
        for key in ENV_KEYS:
            handle.write(key + "=/tmp/wrong-preview-value\n")
        handle.write("HIST_DB_PATH=/tmp/old-preview.db\n")
    observer, dashboard = manager.observer_runtime_env(), manager.dashboard_env("PRODUCTION_PAPER")
    left, right = read_env(observer), read_env(dashboard)
    for key in (*ENV_KEYS, "HIST_DB_PATH"):
        assert left[key] == right[key]
        assert observer.read_text().count(key + "=") == dashboard.read_text().count(key + "=") == 1
    assert left["HIST_DB_PATH"] == reviewed
    assert left["POROTA_SQLITE_SCRATCH_ROOT"] == "/app/data/paper_v17/artifacts/observer_v17.db/sqlite-read-scratch"
    assert left["POROTA_SQLITE_SCRATCH_MAX_BYTES"] == str(MAX_BYTES)
    assert left["POROTA_SQLITE_SCRATCH_RESERVE_BYTES"] == str(RESERVE_BYTES)
    assert left["POROTA_SQLITE_SCRATCH_MIN_FREE_INODE_PERCENT"] == "10"
    assert stat.S_IMODE(observer.stat().st_mode) == stat.S_IMODE(dashboard.stat().st_mode) == 0o600
    assert primary.is_file()


@pytest.mark.parametrize("key,value", [
    (ENV_KEYS[0], "/tmp/scratch"), (ENV_KEYS[1], str(MAX_BYTES + 1)),
    (ENV_KEYS[2], "0"), (ENV_KEYS[3], "0"),
    (ENV_KEYS[0], "/app/data/a\nUNREVIEWED=1"),
])
def test_launcher_scratch_overrides_fail_before_publishing_any_runtime_env(disk_path, monkeypatch, key, value):
    layout(disk_path)
    prepare_launcher(disk_path, monkeypatch, {key: value})
    with pytest.raises(ValueError, match="CONFIGURATION_IMMUTABLE"):
        manager.observer_runtime_env()
    with pytest.raises(ValueError, match="CONFIGURATION_IMMUTABLE"):
        manager.dashboard_env("PRODUCTION_PAPER")
    assert not (manager.DATA / "diagnosticos/observer_runtime_v17.env").exists()
    assert not (manager.DATA / "diagnosticos/dashboard_mode_v17.env").exists()


@pytest.mark.parametrize("value", ["data/market_history.db", "/tmp/history.db",
    "/app/data/../secret.db", "/app/data/review/./history.db", "/app/data//history.db",
    "/app/data/history.db\nTOKEN=secret", "/app/data/history.db;touch-x"])
def test_history_target_rejects_escape_or_unsupported_alias_without_echoing_path(value):
    with pytest.raises(ScratchAdmissionError, match="RC6_HISTORY_PATH_OUTSIDE_DATA") as error:
        history_container_path({"HIST_DB_PATH": value})
    assert value not in str(error.value)


def test_history_selected_target_is_native_measured_and_missing_copy_never_migrates(disk_path, monkeypatch):
    data, primary = layout(disk_path)
    history = "/app/data/reviewed/history-copy.sqlite"
    (disk_path / ".env").write_text(f"HIST_DB_PATH='{history}'\n")
    with pytest.raises(ScratchAdmissionError, match="NEED_COPY_TARGET"):
        probe(primary, data)
    target = make_database(disk_path / history.removeprefix("/app/"))
    before = identity(target)
    report = probe(primary, data)
    assert report["history_container"] == history
    assert report["source_sizes"]["history_selected"][""] == target.stat().st_size
    assert report["capture_peak_bytes"]["history_selected"] == snapshot_peak_bytes(target.stat().st_size)
    assert identity(target) == before
    prepare_launcher(disk_path, monkeypatch, {"HIST_DB_PATH": history})
    monkeypatch.setenv("POROTA_PRETRANSFER_HIST_DB_PATH", "/app/data/market_history.db")
    with pytest.raises(ValueError, match="PRETRANSFER_CONFIG_DRIFT"):
        manager.observer_runtime_env()


@pytest.mark.parametrize("mutation", ["primary_hardlink", "wal_symlink", "source_world_write", "hot_journal", "parent_alias"])
def test_native_pretransfer_metadata_rejects_source_custody_or_busy_state(disk_path, mutation):
    data, primary = layout(disk_path)
    if mutation == "primary_hardlink": os.link(primary, disk_path / "foreign-hardlink.db")
    elif mutation == "wal_symlink": Path(str(primary) + "-wal").symlink_to(primary)
    elif mutation == "source_world_write": primary.chmod(0o666)
    elif mutation == "hot_journal": Path(str(primary) + "-journal").write_bytes(b"busy")
    elif mutation == "parent_alias":
        actual = disk_path / "real-paper"; primary.parent.rename(actual)
        (data / "paper_v17").symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError):
        probe(primary, data)
    assert not (data / "paper_v17/artifacts/observer_v17.db/sqlite-read-scratch").exists()


@pytest.mark.parametrize("mutation", ["root_mode", "root_alias", "unknown_residue", "busy_lease"])
def test_native_scratch_admission_rejects_unknown_custody_and_does_not_fix_or_delete(disk_path, mutation):
    data, primary = layout(disk_path)
    root = artifact_root(primary) / "sqlite-read-scratch"; root.mkdir(parents=True, mode=0o700)
    lock = None
    if mutation == "root_mode": root.chmod(0o777)
    elif mutation == "root_alias":
        actual = root.with_name("aliased-root"); root.rename(actual); root.symlink_to(actual)
    elif mutation == "unknown_residue": (root / "unrecognized-source.db").write_bytes(b"keep-me")
    elif mutation == "busy_lease":
        descriptor = os.open(root / ".rc6-sqlite-scratch.lock", os.O_RDWR | os.O_CREAT, 0o600)
        lock = os.fdopen(descriptor, "wb")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    before = {path.name: path.lstat().st_mode for path in root.iterdir()}
    try:
        with pytest.raises(ValueError): probe(primary, data)
    finally:
        if lock is not None: lock.close()
    after = {path.name: path.lstat().st_mode for path in root.iterdir()}
    assert all(after[name] == mode for name, mode in before.items())
    if mutation == "unknown_residue": assert (root / "unrecognized-source.db").read_bytes() == b"keep-me"
    if mutation == "root_mode": assert stat.S_IMODE(root.stat().st_mode) == 0o777
    if mutation == "root_alias": assert root.is_symlink()


def test_candidate_stream_probe_runs_with_native_python_without_checkout_and_preserves_source(disk_path):
    data, primary = layout(disk_path)
    before = identity(primary)
    emitted = emit_probe(REPO)
    isolated = disk_path / "no-repository"; isolated.mkdir()
    result = subprocess.run([sys.executable, "-", "--database", str(primary), "--data-root", str(data)],
        input=emitted, cwd=isolated, env={**os.environ, "PYTHONPATH": ""},
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(result.stdout.encode()) < 4096
    report = json.loads(result.stdout)
    assert report["schema"] == SCHEMA and report["status"] == "GREEN"
    assert report["source_sizes"]["primary"][""] == primary.stat().st_size
    assert report["capture_peak_bytes"]["primary"] == snapshot_peak_bytes(primary.stat().st_size)
    assert report["lease_owner_uid"] == report["lease_owner_gid"] == 1000
    assert report["source_sqlite_opened"] is report["residue_deleted"] is False
    assert not Path(report["scratch_root"]).exists()
    assert identity(primary) == before


def test_native_tmpfs_is_rejected_before_launcher_creates_any_scratch_namespace(monkeypatch):
    with tempfile.TemporaryDirectory(prefix="rc6-native-volatile-", dir="/dev/shm") as name:
        root = Path(name); data, _ = layout(root)
        prepare_launcher(root, monkeypatch, {})
        with pytest.raises(ValueError, match="SCRATCH_DISK_BACKED_REQUIRED"):
            manager.prepare_sqlite_scratch_root({})
        assert not (data / "paper_v17/artifacts").exists()


@pytest.mark.parametrize("mutation", ["target_alias", "parent_alias", "source_hardlink"])
def test_reviewed_history_cannot_alias_another_dataset(disk_path, mutation):
    data, primary = layout(disk_path)
    target = make_database(data / "reviewed/history-copy.db")
    (disk_path / ".env").write_text("HIST_DB_PATH=/app/data/reviewed/history-copy.db\n")
    if mutation == "target_alias": target.unlink(); target.symlink_to(primary)
    elif mutation == "source_hardlink": os.link(target, disk_path / "foreign.db")
    else:
        actual = disk_path / "actual-history"; target.parent.rename(actual)
        (data / "reviewed").symlink_to(actual)
    with pytest.raises(ValueError): probe(primary, data)
    assert not (data / "paper_v17/artifacts").exists()


def test_fifo_history_config_fails_bounded_without_opening_sqlite_or_allocating_scratch(disk_path):
    data, primary = layout(disk_path)
    os.mkfifo(disk_path / ".env", mode=0o600)
    started = time.monotonic()
    with pytest.raises(ScratchAdmissionError, match="CONFIG_CUSTODY_REQUIRED"):
        probe(primary, data)
    assert time.monotonic() - started < 1
    assert not (data / "paper_v17/artifacts").exists()


def test_scratch_reserve_grows_only_by_unoccupied_quota_and_shares_two_gib_headroom():
    policy = load_policy(POLICY)
    validate_policy(policy)
    resident = 86_016
    required = required_pretransfer_free(100, 1000, 10, policy, resident)
    assert required == 100 + 1000 + 2 * 10 + MAX_BYTES - resident + RESERVE_BYTES
    for value in (True, -1, MAX_BYTES, MAX_BYTES + 1):
        with pytest.raises(ValueError, match="RESIDUE_BYTE_LIMIT"):
            required_pretransfer_free(100, 1000, 10, policy, value)
    for key, value in (("max_bytes", MAX_BYTES + 1), ("reserve_bytes", 0),
                       ("min_free_inode_percent", 0), ("owner_uid", True), ("mode", "0777")):
        changed = copy.deepcopy(policy); changed["sqlite"]["private_read_scratch"][key] = value
        with pytest.raises(ValueError, match="POLICY_DRIFT"):
            required_pretransfer_free(100, 1000, 10, changed)


def test_real_launcher_disk_layout_copies_more_than_observer_tmpfs_and_cleans_private_copy(disk_path, monkeypatch):
    source_fixture(disk_path, monkeypatch)
    prepare_launcher(disk_path, monkeypatch, {})
    primary = make_database(manager.DATA / "paper_v17/observer_v17.db", large=True)
    assert primary.stat().st_size > 32 * 1024**2
    secret = disk_path / ".secrets/ppi_production.json"; secret.parent.mkdir(); secret.write_text("{}")
    for name in ("cf_intraday_scalping.py", "co_market_sessions_hf6.py"):
        (disk_path / name).write_bytes(b"# offline fixture\n")
    calls, env_reads = [], []
    def frozen_env():
        env_reads.append(1)
        return {} if len(env_reads) == 1 else {"HIST_DB_PATH": "/outside/unreviewed.db"}
    monkeypatch.setattr(manager, "env_file", frozen_env)
    def docker(*args, **kwargs):
        calls.append(args)
        if args[:3] == ("docker", "image", "inspect"):
            source = json.loads((disk_path / "POROTA_SOURCE_PROVENANCE.json").read_text())
            return SimpleNamespace(stdout=json.dumps({"porota.commit": source["candidate_sha"],
                "porota.tree": source["candidate_tree_sha"], "porota.predeploy": "v2",
                "porota.source-manifest-sha256": hashlib.sha256(
                    (disk_path / "POROTA_SOURCE_PROVENANCE.json").read_bytes()).hexdigest()}))
        if args[:3] == ("docker", "inspect", "-f"): return SimpleNamespace(stdout="running")
        if args[:3] == ("docker", "exec", "porota_production_observer"):
            source = disk_path / args[-1].removeprefix("/app/")
            return SimpleNamespace(stdout=hashlib.sha256(source.read_bytes()).hexdigest() + "  fixture")
        return SimpleNamespace(stdout="synthetic-container-id")
    monkeypatch.setattr(manager, "run", docker)
    monkeypatch.setattr(manager, "stop_engines", lambda *a, **kw: None)
    monkeypatch.setattr(manager, "write_mode", lambda *a, **kw: None)
    monkeypatch.setattr(manager, "notify", lambda *a, **kw: "OFFLINE")
    manager.simulation()
    assert len(env_reads) == 1
    launchers = [row for row in calls if row[:3] == ("docker", "run", "-d")]
    assert len(launchers) == 2
    observer = next(row for row in launchers if "porota_production_observer" in row)
    assert "/tmp:rw,noexec,nosuid,size=32m" in observer
    for row in launchers:
        assert f"{manager.DATA}:/app/data" in row
    left = read_env(manager.DATA / "diagnosticos/observer_runtime_v17.env")
    right = read_env(manager.DATA / "diagnosticos/dashboard_mode_v17.env")
    for key in (*ENV_KEYS, "HIST_DB_PATH", DB_ENV): assert left[key] == right[key]
    for key in (DB_ENV, ENV_KEYS[0], "HIST_DB_PATH"):
        monkeypatch.setenv(key, str(disk_path / left[key].removeprefix("/app/")))
    for key in ENV_KEYS[1:]: monkeypatch.setenv(key, left[key])
    root = artifact_root(primary) / "sqlite-read-scratch"
    root_info = root.stat()
    assert root_info.st_uid == root_info.st_gid == 1000
    assert stat.S_IMODE(root_info.st_mode) == 0o700
    os.utime(primary, ns=(1_000_000_000, primary.stat().st_mtime_ns))
    before = identity(primary)
    started = time.monotonic()
    with readonly_copy(primary, deadline=started + 5) as connection:
        assert connection.execute("SELECT count(*) FROM native_copy_rows").fetchone()[0] == 10000
        assert connection.execute("PRAGMA temp_store").fetchone()[0] == 2
        location = Path(connection.execute("PRAGMA database_list").fetchone()[2])
        assert root in location.parents and location.stat().st_size > 32 * 1024**2
        assert not str(location).startswith("/tmp/")
    assert time.monotonic() - started < 5
    assert identity(primary) == before
    assert inspect_scratch(root)["residual_sessions"] == 0
    assert set(path.name for path in root.iterdir()) == {".rc6-sqlite-scratch.lock"}


def test_deployment_wiring_runs_candidate_disk_probe_before_transfer_and_pins_history():
    text = (REPO / ".github/workflows/porota-deploy-v2-promote.yml").read_text()
    start = text.index("Dynamic pre-transfer disk guard and conditional safe cleanup")
    transfer = text.index("- name: Transfer frozen artifact")
    block = text[start:transfer]
    assert "--emit-probe --repo-root ." in block
    assert "sudo -n timeout 20 python3 -" in block
    assert "--scratch-occupied-bytes" in block
    assert "RC6_SQLITE_SCRATCH_PRETRANSFER_GATE=GREEN" in block
    assert 'test "$SCRATCH_RC" -eq 0' in block
    assert 'test "${RC6_SQLITE_SCRATCH_PREFLIGHT_GREEN:-0}" = "1"' in text[transfer:]
    assert "POROTA_PRETRANSFER_HIST_DB_PATH='$RC6_SQLITE_SCRATCH_HISTORY_PATH'" in text[transfer:]
    assert '"POROTA_PRETRANSFER_HIST_DB_PATH=$POROTA_PRETRANSFER_HIST_DB_PATH" python3 "$REPO/porota_mode_manager.py" simulation' in text
    assert "TMPDIR=" not in text
