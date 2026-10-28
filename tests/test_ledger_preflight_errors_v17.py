"""Errores nativos y lectura WAL sin ampliar permisos del diagnóstico."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import v17_ledger_preflight as probe
from scripts import build_v17_ledger_preflight as builder
from test_ledger_preflight_v17 import position
from test_production_paper_v1634 import partial_spot


@pytest.mark.parametrize('query,stage', [
    ('PRAGMA query_only', 'SET_QUERY_ONLY'),
    ('BEGIN', 'BEGIN_READ_TRANSACTION'),
    ('SELECT name,type,sql', 'READ_SCHEMA'),
    ('PRAGMA table_info', 'READ_COLUMNS'),
    ('SELECT COUNT(*) FROM (', 'COUNT_ROWS'),
    ('SELECT * FROM paper_positions ORDER BY', 'VALIDATE_POSITIONS'),
    ('SELECT COUNT(*) FROM paper_fills f', 'CHECK_ORPHANS'),
])
def test_exact_failed_stage_and_native_code_without_raw_message(position, monkeypatch, query, stage):
    broker, _ = position()
    connect = sqlite3.connect
    error = sqlite3.OperationalError('PRIVATE_MARKER: account number and SQL row')
    error.sqlite_errorcode = sqlite3.SQLITE_READONLY
    error.sqlite_errorname = 'SQLITE_READONLY'
    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith(query):
                raise error
            return super().execute(sql, *args, **kwargs)
    def injected(database, **kwargs):
        assert database.endswith('?mode=ro') and 'immutable' not in database
        return connect(database, factory=Connection, **kwargs)
    monkeypatch.setattr(probe.sqlite3, 'connect', injected)
    report = probe.collect(broker.store.path)
    assert report['status'] == 'STOPPED' and report['reason'] == 'SQLITE_ERROR'
    assert report['stage'] == stage
    assert report['sqlite_error'] == {'error_class':'OperationalError',
        'sqlite_errorcode':8, 'sqlite_errorname':'SQLITE_READONLY', 'primary_code':8, 'category':'READONLY'}
    assert 'PRIVATE_MARKER' not in json.dumps(report)
    assert report['database_modified'] is False and report['migration_performed'] is False


def test_failed_sqlite_open_attempt_is_not_retried(position, monkeypatch):
    broker, _ = position()
    calls = []
    def fail(database, **kwargs):
        calls.append(database)
        error = sqlite3.OperationalError('PRIVATE_MARKER')
        error.sqlite_errorcode = sqlite3.SQLITE_CANTOPEN
        error.sqlite_errorname = 'SQLITE_CANTOPEN'
        raise error
    monkeypatch.setattr(probe.sqlite3, 'connect', fail)
    report = probe.collect(broker.store.path)
    assert len(calls) == 1 and report['stage'] == 'OPEN_SQLITE'
    assert report['sqlite_error']['category'] == 'CANNOT_OPEN'
    assert report['counts'] == {} and 'PRIVATE_MARKER' not in json.dumps(report)


@pytest.mark.parametrize('code,name,category', [
    (sqlite3.SQLITE_BUSY,'SQLITE_BUSY','BUSY'),
    (sqlite3.SQLITE_LOCKED,'SQLITE_LOCKED','LOCKED'),
    (sqlite3.SQLITE_CORRUPT,'SQLITE_CORRUPT','CORRUPT'),
    (sqlite3.SQLITE_NOTADB,'SQLITE_NOTADB','NOT_A_DATABASE'),
    (sqlite3.SQLITE_READONLY_CANTINIT,'SQLITE_READONLY_CANTINIT','READONLY'),
    (None,None,'OTHER'),
    (-1,'PRIVATE message','OTHER'),
])
def test_sqlite_codes_and_extended_codes(code, name, category):
    error = sqlite3.OperationalError('PRIVATE_MARKER')
    error.sqlite_errorcode = code
    error.sqlite_errorname = name
    detail = probe.sqlite_failure(error)
    assert detail['category'] == category
    assert 'PRIVATE' not in json.dumps(detail)
    if code is not None and code >= 0:
        assert detail['sqlite_errorcode'] == code
        assert detail['primary_code'] == code & 255


def test_metadata_only_fixed_paths_no_directory_listing(position, monkeypatch):
    broker, _ = position()
    original = Path.open
    opened = []
    def record(path, mode='r', *args, **kwargs):
        assert mode == 'rb'
        opened.append(str(path))
        return original(path, mode, *args, **kwargs)
    def no_listing(*args, **kwargs):
        raise AssertionError('No listar directorios')
    monkeypatch.setattr(Path, 'open', record)
    monkeypatch.setattr(Path, 'iterdir', no_listing)
    evidence = probe.filesystem_evidence(Path(broker.store.path))
    assert set(evidence) == {'database','wal','shm','rollback_journal'}
    assert set(opened) <= {broker.store.path+suffix for suffix in ('','-wal','-shm','-journal')}
    assert evidence['database']['read_open_succeeded']
    assert evidence['database']['header_journal_mode'] in {'WAL','ROLLBACK'}


def test_metadata_permission_error_is_evidence_not_permission_change(position, monkeypatch):
    broker, _ = position()
    def denied(*args, **kwargs):
        raise PermissionError(13, 'PRIVATE_MARKER')
    monkeypatch.setattr(Path, 'open', denied)
    evidence = probe.filesystem_evidence(Path(broker.store.path))
    assert evidence['database']['state'] == 'ACCESS_ERROR'
    assert evidence['database']['os_errno'] == 13
    assert 'PRIVATE_MARKER' not in json.dumps(evidence)


def test_corrupt_database_reports_native_error_without_private_header(tmp_path):
    path = tmp_path/'not_sqlite.db'
    path.write_bytes(b'PRIVATE_MARKER'*40)
    report = probe.collect(path)
    assert report['reason'] == 'SQLITE_ERROR'
    assert report['sqlite_error']['category'] == 'NOT_A_DATABASE'
    assert report['filesystem_evidence']['database']['header_journal_mode'] == 'NOT_SQLITE_HEADER'
    assert 'PRIVATE_MARKER' not in json.dumps(report)


def test_missing_database_reports_completed_stage(tmp_path):
    path = tmp_path/'absent.db'
    report = probe.collect(path)
    assert report['stage'] == 'FILE_METADATA' and report['completed_at']
    assert report['filesystem_evidence']['database']['state'] == 'MISSING'
    assert not path.exists()


def test_success_reports_done_and_runtime(position):
    broker, _ = position()
    report = probe.collect(broker.store.path)
    assert report['stage'] == 'DONE'
    assert report['reader_runtime']['sqlite'] == sqlite3.sqlite_version
    assert report['reader_runtime']['python'] == '.'.join(map(str, sys.version_info[:3]))


@pytest.mark.parametrize('journal', ['DELETE','WAL'])
@pytest.mark.skipif(os.geteuid() == 0, reason='Requiere usuario no-root; no cambia identidades ni permisos del entorno')
def test_actual_nonprivileged_sqlite_on_readonly_fixture(journal):
    """Permisos sólo de una fixture propia; nunca toca servidor/datos del operador.

    Utiliza el usuario no-root habitual del CI y sólo permisos de su fixture.
    No cambia identidad del proceso ni solicita capacidades adicionales.
    """
    with tempfile.TemporaryDirectory(prefix='porota-wal-fixture-') as directory:
        folder = Path(directory)
        archive = folder/'reader.zip'
        database = folder/'observer.db'
        builder.build(archive)
        with closing(sqlite3.connect(database)) as c:
            c.execute('PRAGMA journal_mode='+journal)
            c.executescript('CREATE TABLE paper_positions(x); CREATE TABLE paper_fills(x);')
            c.commit()
        assert not Path(str(database)+'-wal').exists()
        assert not Path(str(database)+'-shm').exists()
        database.chmod(0o444)
        archive.chmod(0o444)
        folder.chmod(0o555)
        before = database.read_bytes()
        code = "import sys,runpy,json; sys.path.insert(0,sys.argv[1]); m=runpy.run_path(sys.argv[1]); print(json.dumps(m['collect'](sys.argv[2])))"
        try:
            run = subprocess.run([sys._base_executable, '-I', '-S', '-c', code,
                                  str(archive), str(database)], cwd=folder,
                                 capture_output=True, text=True, timeout=20)
            assert run.returncode == 0, run.stderr
            report = json.loads(run.stdout)
            assert report['filesystem_evidence']['database']['read_open_succeeded']
            assert report['filesystem_evidence']['wal']['state'] == 'MISSING'
            if journal == 'WAL':
                assert report['filesystem_evidence']['database']['header_journal_mode'] == 'WAL'
                assert report['stage'] == 'READ_SCHEMA'
                assert report['reason'] == 'SQLITE_ERROR'
                assert report['sqlite_error']['primary_code'] in {8,14}
                assert report['counts'] == {}
            else:
                assert report['reason'] == 'UNSUPPORTED_LEDGER_SCHEMA'
                assert report['stage'] == 'READ_COLUMNS'
            assert database.read_bytes() == before
            assert not Path(str(database)+'-wal').exists()
            assert not Path(str(database)+'-shm').exists()
            assert database.stat().st_mode & 0o777 == 0o444
            assert folder.stat().st_mode & 0o777 == 0o555
        finally:
            folder.chmod(0o755)  # permitir únicamente la limpieza de esta fixture
