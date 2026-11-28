import json
import sqlite3
import tarfile
import time
from contextlib import closing
from pathlib import Path

import pytest

from scripts.v17_host_general_backup import create_backup


def test_backup_general_es_externo_a_docker_y_valida_sqlite(tmp_path):
    source = tmp_path / "porota"
    destination = tmp_path / "backups"
    (source / "data").mkdir(parents=True)
    (source / "sre_vector_db").mkdir()
    with sqlite3.connect(source / "data" / "paper.db") as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES('ok')")
    (source / "data" / "report.txt").write_text("evidencia", encoding="utf-8")
    (source / ".env").write_text("SECRET=hidden", encoding="utf-8")
    result = create_backup(source, destination)
    archive = Path(result["archive"])
    assert archive.is_file()
    assert result["docker_required"] is False
    assert result["predeploy_step"] is False
    with tarfile.open(archive, "r:gz") as bundle:
        names = set(bundle.getnames())
        assert "porota-trading/data/paper.db" in names
        assert "porota-trading/data/report.txt" in names
        assert "porota-trading/.env" not in names
        manifest = json.load(bundle.extractfile("porota-trading/BACKUP_MANIFEST.json"))
    assert manifest["secrets_included"] is False


def test_restore_recovers_committed_wal_from_isolated_fixture(tmp_path, record_property):
    source = tmp_path / 'fixture'
    path = source / 'data' / 'paper.db'
    path.parent.mkdir(parents=True)
    with closing(sqlite3.connect(path)) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE paper_events(id INTEGER PRIMARY KEY, state TEXT)')
        writer.commit()
        writer.execute("INSERT INTO paper_events VALUES (1, 'PAPER_COMMITTED')")
        writer.commit()
        assert path.with_name('paper.db-wal').stat().st_size > 0
        writer.execute("INSERT INTO paper_events VALUES (2, 'PAPER_UNCOMMITTED')")
        result = create_backup(source, tmp_path / 'external')
        writer.rollback()
    restore_started = time.perf_counter()
    restored = tmp_path / 'restored.db'
    with tarfile.open(result['archive'], 'r:gz') as archive:
        member = archive.extractfile('porota-trading/data/paper.db')
        assert member is not None
        restored.write_bytes(member.read())
        assert not any(name.endswith(('-wal', '-shm')) for name in archive.getnames())
    with closing(sqlite3.connect(restored.as_uri() + '?mode=ro', uri=True)) as db:
        assert db.execute('PRAGMA quick_check').fetchone() == ('ok',)
        assert db.execute('SELECT id, state FROM paper_events').fetchall() == [(1, 'PAPER_COMMITTED')]
    record_property('fixture_restore_seconds', round(time.perf_counter() - restore_started, 6))
    record_property('fixture_committed_rows_missing', 0)


def test_backup_rechaza_destino_dentro_del_origen(tmp_path):
    source = tmp_path / "porota"
    source.mkdir()
    with pytest.raises(ValueError, match="destino"):
        create_backup(source, source / "backups")


def test_backup_nunca_archiva_por_recursion_ni_auxiliares_sqlite(tmp_path, monkeypatch):
    source = tmp_path / "porota"
    destination = tmp_path / "backups"
    (source / "data").mkdir(parents=True)
    with sqlite3.connect(source / "data" / "paper.db") as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
    original_add = tarfile.TarFile.add

    def guarded_add(archive, name, arcname=None, recursive=True, *args, **kwargs):
        assert recursive is False
        return original_add(archive, name, arcname=arcname, recursive=recursive,
                            *args, **kwargs)

    monkeypatch.setattr(tarfile.TarFile, "add", guarded_add)
    result = create_backup(source, destination)
    with tarfile.open(result["archive"], "r:gz") as archive:
        names = archive.getnames()
    assert "porota-trading/data/paper.db" in names
    assert not any(name.endswith(("-wal", "-shm")) for name in names)


def test_backup_excluye_copias_previas_restore_y_secretos_historicos(tmp_path):
    source = tmp_path / "porota"
    destination = tmp_path / "external"
    (source / "data" / "backups" / "old").mkdir(parents=True)
    (source / "data" / "restore_test").mkdir()
    (source / "data" / "live").mkdir()
    (source / "data" / "backups" / "old" / ".env").write_text(
        "SECRET=historical", encoding="utf-8"
    )
    (source / "data" / "backups" / "old" / "old.db").write_bytes(b"volatile")
    (source / "data" / "restore_test" / "copy.db").write_bytes(b"volatile")
    (source / "data" / "live" / ".env").write_text("SECRET=nested", encoding="utf-8")
    (source / "data" / "live" / "evidence.json").write_text("{}", encoding="utf-8")
    result = create_backup(source, destination)
    with tarfile.open(result["archive"], "r:gz") as archive:
        names = set(archive.getnames())
        manifest = json.load(archive.extractfile("porota-trading/BACKUP_MANIFEST.json"))
    assert "porota-trading/data/live/evidence.json" in names
    assert not any("/backups/" in name or "/restore_test/" in name for name in names)
    assert not any(name.endswith("/.env") for name in names)
    assert manifest["nested_secret_files_excluded"] is True
