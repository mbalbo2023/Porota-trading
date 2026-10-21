"""A scheduled backup must never stage a full data tree on a near-full host."""
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

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
    monkeypatch.setattr(job, "backups_suspended", lambda: False)
    source = tmp_path / "source"
    source.mkdir()
    destination = tmp_path / "backups"
    destination.mkdir()
    monkeypatch.setattr(job, "backup_capacity", lambda *_: {"status": "DEFERRED_DISK_HEADROOM"})
    monkeypatch.setattr(job, "create_backup", lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("backup must not run")))
    monkeypatch.setattr(job, "Path", lambda value: source if value == "/opt/porota-trading" else destination)
    assert job.main(now=datetime(2026,10,21,4,15,tzinfo=ZoneInfo('America/Argentina/Buenos_Aires'))) == 75
    assert 'DEFERRED_DISK_HEADROOM' in capsys.readouterr().out


def test_persistent_backup_catchup_never_scans_or_copies_in_preopen_or_open(monkeypatch,capsys):
    monkeypatch.setattr(job, "backups_suspended", lambda: False)
    zone=ZoneInfo('America/Argentina/Buenos_Aires')
    monkeypatch.setattr(job,'backup_capacity',lambda *_: (_ for _ in ()).throw(
        AssertionError('heavy backup admission must not run in market window')))
    for hour,minute in ((8,0),(9,45),(11,0),(16,45),(17,30)):
        assert job.main(now=datetime(2026,10,21,hour,minute,tzinfo=zone))==75
        assert 'DEFERRED_MARKET_WINDOW' in capsys.readouterr().out
    assert job.backup_window_admitted(datetime(2026,10,21,4,15,tzinfo=zone))


def test_capacity_bound_excludes_archiver_skipped_backup_subtrees(tmp_path):
    source = tmp_path / "source"
    live = source / "data" / "paper_v17"
    live.mkdir(parents=True)
    (live / "observer.db").write_bytes(b"live")
    previous = live / "artifacts" / "backups"
    previous.mkdir(parents=True)
    (previous / "observer.db.gz").write_bytes(b"historical" * 1000)
    (live / "observer.db-wal").write_bytes(b"wal" * 100)
    assert job.source_upper_bound(source) == 4
