"""A scheduled backup must never stage a full data tree on a near-full host."""
from pathlib import Path
from types import SimpleNamespace

import rc6_host_general_backup_job as job


def test_same_filesystem_requires_staging_archive_and_reserve(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "data" / "paper_v17").mkdir(parents=True)
    (source / "data" / "paper_v17" / "observer.db").write_bytes(b"x" * 8192)
    destination = tmp_path / "backups"
    destination.mkdir()
    actual = job.source_upper_bound(source)
    assert actual == 8192
    required, destination_required = job.capacity_required(actual, True)
    assert required == 2 * actual + job.RESERVE_BYTES + job.ARCHIVE_OVERHEAD_BYTES
    assert destination_required == 0
    monkeypatch.setattr(job.shutil, "disk_usage", lambda _path: SimpleNamespace(free=required-1))
    assert job.backup_capacity(source, destination, tmp_path)["status"] == "DEFERRED_DISK_HEADROOM"
    monkeypatch.setattr(job.shutil, "disk_usage", lambda _path: SimpleNamespace(free=required))
    assert job.backup_capacity(source, destination, tmp_path)["status"] == "ADMITTED"


def test_symlink_does_not_expand_backup_source(tmp_path):
    source = tmp_path / "source"
    (source / "data").mkdir(parents=True)
    (source / "data" / "real.db").write_bytes(b"abc")
    (source / "data" / "alias.db").symlink_to(source / "data" / "real.db")
    assert job.source_upper_bound(source) == 3


def test_insufficient_capacity_never_calls_backup(tmp_path, monkeypatch, capsys):
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "backups"
    destination.mkdir()
    monkeypatch.setattr(job, "backup_capacity", lambda *_: {"status": "DEFERRED_DISK_HEADROOM"})
    monkeypatch.setattr(job, "create_backup", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("backup must not run")))
    monkeypatch.setattr(job, "Path", lambda value: source if value == "/opt/porota-trading" else destination)
    assert job.main() == 75
    assert 'DEFERRED_DISK_HEADROOM' in capsys.readouterr().out
