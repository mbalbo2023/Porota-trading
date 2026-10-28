"""Copia temporal autorizada: estabilidad, aislamiento y limpieza sin migración."""
from contextlib import closing
import gc
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3

import pytest

from scripts import v17_ledger_preflight as probe
from test_ledger_preflight_v17 import position, host
from test_production_paper_v1634 import partial_spot


@pytest.fixture
def source(position, tmp_path):
    broker, _ = position('closed')
    gc.collect()
    origin = tmp_path/'original'
    scratch = tmp_path/'scratch'
    origin.mkdir()
    scratch.mkdir()
    path = origin/'observer.db'
    # Preparación de una fixture propia, no parte del procedimiento desplegado.
    with closing(sqlite3.connect(broker.store.path)) as original:
        with closing(sqlite3.connect(path)) as destination:
            original.backup(destination)
    with closing(sqlite3.connect(path)) as c:
        c.execute('PRAGMA journal_mode=WAL')
    assert not Path(str(path)+'-wal').exists()
    assert not Path(str(path)+'-shm').exists()
    path.chmod(0o444)
    origin.chmod(0o555)
    before = path.read_bytes()
    try:
        yield path, scratch, before
    finally:
        origin.chmod(0o755)  # sólo limpieza de la fixture propia


def assert_preserved(source):
    path, scratch, before = source
    assert path.read_bytes() == before
    assert path.stat().st_mode & 0o777 == 0o444
    assert path.parent.stat().st_mode & 0o777 == 0o555
    assert list(scratch.iterdir()) == []
    assert all(not Path(str(path)+s).exists() for s in ('-wal','-shm','-journal'))


def test_copy_wal_audits_and_removes_only_copy(source, monkeypatch):
    path, scratch, before = source
    connect = sqlite3.connect
    calls = []
    def copy_only(database, **kwargs):
        calls.append(database)
        assert database.startswith('file:'+str(scratch)) and database.endswith('?mode=ro')
        assert str(path) not in database and 'immutable' not in database
        return connect(database, **kwargs)
    monkeypatch.setattr(probe.sqlite3, 'connect', copy_only)
    report = probe.collect_copy(path, scratch)
    assert report['status'] == 'OBSERVED_NO_DETECTED_INCONSISTENCIES'
    assert report['read_mode'] == 'TEMPORARY_COPY' and len(calls) == 1
    assert report['counts']['positions_checked'] == 1
    assert report['temporary_copy_created'] and report['temporary_copy_removed']
    assert report['copy_evidence'] == {'bytes':len(before), 'copy_hash_matches':True, 'source_hash_rechecks':2}
    assert report['source_filesystem_evidence']['database']['header_journal_mode'] == 'WAL'
    assert report['source_filesystem_evidence']['wal']['state'] == 'MISSING'
    assert not report['database_modified'] and not report['migration_performed'] and not report['promotion_allowed']
    assert hashlib.sha256(before).hexdigest() not in json.dumps(report)
    assert str(path) not in json.dumps(report)  # no exportar rutas/filas/hash de la base
    assert_preserved(source)


@pytest.mark.skipif(os.geteuid() == 0, reason='Reproducción requiere usuario no-root habitual; sin cambio de identidad')
def test_direct_wal_blocked_but_authorized_copy_succeeds_nonroot(source):
    path, scratch, _ = source
    direct = probe.collect(path)
    assert direct['reason'] == 'SQLITE_ERROR' and direct['stage'] == 'READ_SCHEMA'
    assert direct['sqlite_error']['primary_code'] in {8,14}
    report = probe.collect_copy(path, scratch)
    assert report['status'] == 'OBSERVED_NO_DETECTED_INCONSISTENCIES'
    assert report['temporary_copy_removed']
    assert_preserved(source)


@pytest.mark.parametrize('suffix', ['-wal','-shm','-journal'])
def test_existing_auxiliary_blocks_even_empty(source, suffix):
    path, scratch, _ = source
    path.parent.chmod(0o755)
    auxiliary = Path(str(path)+suffix)
    auxiliary.touch()
    path.parent.chmod(0o555)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'SOURCE_AUXILIARY_PRESENT'
    assert report['counts'] == {} and not report['temporary_copy_created']
    assert auxiliary.exists() and list(scratch.iterdir()) == []


def test_copy_size_limit_stops_before_creation(source, monkeypatch):
    path, scratch, _ = source
    monkeypatch.setattr(probe, 'MAX_COPY_BYTES', path.stat().st_size-1)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'SOURCE_SIZE_OUTSIDE_COPY_LIMIT'
    assert not report['temporary_copy_created']
    assert_preserved(source)


def test_temporary_location_cannot_be_inside_original(source):
    path, _, _ = source
    report = probe.collect_copy(path, path.parent)
    assert report['reason'] == 'TEMPORARY_LOCATION_OVERLAPS_SOURCE'
    assert_preserved(source)


def test_symlink_is_not_copied(source):
    path, scratch, _ = source
    link = scratch/'alias.db'
    link.symlink_to(path)
    report = probe.collect_copy(link, scratch)
    assert report['reason'] == 'SOURCE_NOT_REGULAR_FILE'
    assert not report['temporary_copy_created'] and link.is_symlink()


@pytest.mark.parametrize('phase', ['copy','before_audit','after_audit'])
def test_changed_signature_discards_results_and_cleans(source, monkeypatch, phase):
    path, scratch, _ = source
    stream = probe._stream_source
    calls = []
    fail_at = {'copy':1, 'before_audit':2, 'after_audit':3}[phase]
    def changed(*args, **kwargs):
        calls.append(1)
        if len(calls) == fail_at:
            raise probe.PreflightStop('SOURCE_CHANGED')
        return stream(*args, **kwargs)
    monkeypatch.setattr(probe, '_stream_source', changed)
    report = probe.collect_copy(path, scratch)
    assert report['status'] == 'STOPPED' and report['reason'] == 'SOURCE_CHANGED'
    assert report['counts'] == {} and report['issues'] == {} and report['examples'] == []
    assert report['temporary_copy_removed'] and len(calls) == fail_at
    assert_preserved(source)


@pytest.mark.parametrize('pass_number', [2,3])
def test_hash_change_never_publishes_audit(source, monkeypatch, pass_number):
    path, scratch, _ = source
    stream = probe._stream_source
    calls = []
    def changed(*args, **kwargs):
        calls.append(1)
        digest = stream(*args, **kwargs)
        return b'not the source digest' if len(calls) == pass_number else digest
    monkeypatch.setattr(probe, '_stream_source', changed)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'SOURCE_HASH_CHANGED' and report['counts'] == {}
    assert report['temporary_copy_removed']
    assert_preserved(source)


def test_copy_digest_mismatch_no_audit(source, monkeypatch):
    path, scratch, _ = source
    monkeypatch.setattr(probe, '_copy_digest', lambda p: b'wrong digest')
    def forbidden(*args):
        raise AssertionError('No auditar copia distinta')
    monkeypatch.setattr(probe, 'collect', forbidden)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'COPY_HASH_MISMATCH'
    assert report['temporary_copy_removed']
    assert_preserved(source)


@pytest.mark.parametrize('suffix', ['-wal','-shm','-journal'])
def test_auxiliary_appearing_during_audit_discards_counts(source, monkeypatch, suffix):
    path, scratch, _ = source
    collect = probe.collect
    def introduce_auxiliary(copy):
        report = collect(copy)
        path.parent.chmod(0o755)
        Path(str(path)+suffix).touch()
        path.parent.chmod(0o555)
        return report
    monkeypatch.setattr(probe, 'collect', introduce_auxiliary)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'SOURCE_AUXILIARY_PRESENT' and report['counts'] == {}
    assert report['temporary_copy_removed'] and list(scratch.iterdir()) == []
    assert Path(str(path)+suffix).exists()  # nunca elimina auxiliares del original


def test_native_copy_read_error_still_cleans_and_checks_original(source, monkeypatch):
    path, scratch, _ = source
    def fail(database, **kwargs):
        assert str(scratch) in database and '?mode=ro' in database
        error = sqlite3.OperationalError('PRIVATE_MARKER')
        error.sqlite_errorcode = 14
        error.sqlite_errorname = 'SQLITE_CANTOPEN'
        raise error
    monkeypatch.setattr(probe.sqlite3, 'connect', fail)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'SQLITE_ERROR' and report['stage'] == 'OPEN_SQLITE'
    assert report['sqlite_error']['sqlite_errorcode'] == 14
    assert report['copy_evidence']['source_hash_rechecks'] == 2
    assert report['temporary_copy_removed'] and 'PRIVATE_MARKER' not in json.dumps(report)
    assert_preserved(source)


def test_audit_exception_cleans_copy_without_message(source, monkeypatch):
    path, scratch, _ = source
    def fail(copy):
        raise RuntimeError('PRIVATE_MARKER')
    monkeypatch.setattr(probe, 'collect', fail)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'RuntimeError' and report['stage'] == 'AUDIT_TEMPORARY_COPY'
    assert report['temporary_copy_removed'] and report['counts'] == {}
    assert 'PRIVATE_MARKER' not in json.dumps(report)
    assert_preserved(source)


def test_copy_permission_failure_never_retries_or_changes_mode(source, monkeypatch):
    path, scratch, _ = source
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise PermissionError('PRIVATE_MARKER')
    monkeypatch.setattr(probe, '_stream_source', fail)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'PermissionError' and len(calls) == 1
    assert report['temporary_copy_removed']
    assert_preserved(source)


def test_cleanup_failure_not_success(source, monkeypatch):
    path, scratch, _ = source
    cleanup = probe.tempfile.TemporaryDirectory.cleanup
    def fail(temporary):
        cleanup(temporary)  # no dejar basura real en la fixture
        raise OSError('PRIVATE_MARKER')
    monkeypatch.setattr(probe.tempfile.TemporaryDirectory, 'cleanup', fail)
    report = probe.collect_copy(path, scratch)
    assert report['status'] == 'STOPPED' and report['reason'] == 'TEMPORARY_COPY_CLEANUP_FAILED'
    assert report['temporary_copy_removed'] is False
    assert 'PRIVATE_MARKER' not in json.dumps(report)


def test_copy_deadline_restores_alarm_and_cleans(source, monkeypatch):
    path, scratch, _ = source
    collect_copy = probe.collect_copy
    previous = signal.getsignal(signal.SIGALRM)
    monkeypatch.setattr(probe, 'DATABASE', str(path))
    monkeypatch.setattr(probe, 'collect_copy', lambda p: collect_copy(p, scratch))
    def expire(copy):
        signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
    monkeypatch.setattr(probe, 'collect', expire)
    report = probe.read_report(copy_mode=True)
    assert report['reason'] == 'TIME_LIMIT_120_SECONDS' and report['temporary_copy_removed']
    assert signal.getsignal(signal.SIGALRM) is previous
    assert_preserved(source)


def test_stream_rejects_actual_source_mutation(tmp_path):
    path = tmp_path/'fixture.db'
    path.write_bytes(b'A'*200)
    before = probe._source_signature(path)
    class MutatingDestination:
        def write(self, chunk):
            with path.open('r+b') as f:
                f.write(b'B')
    with pytest.raises(probe.PreflightStop, match='SOURCE_CHANGED'):
        probe._stream_source(path, before, MutatingDestination())


def test_stream_detects_mutation_even_when_metadata_appears_unchanged(tmp_path, monkeypatch):
    path = tmp_path/'fixture.db'
    path.write_bytes(b'A'*200)
    before = probe._source_signature(path)
    monkeypatch.setattr(probe, '_signature', lambda _info: before)
    class MutatingDestination:
        def write(self, chunk):
            with path.open('r+b') as stream:
                stream.write(b'B')
    with pytest.raises(probe.PreflightStop, match='SOURCE_CHANGED'):
        probe._stream_source(path, before, MutatingDestination())


def test_stream_rejects_replaced_inode(tmp_path):
    path = tmp_path/'fixture.db'
    replacement = tmp_path/'replacement.db'
    path.write_bytes(b'A'*200)
    replacement.write_bytes(b'A'*200)
    before = probe._source_signature(path)
    replacement.replace(path)
    with pytest.raises(probe.PreflightStop, match='SOURCE_CHANGED'):
        probe._stream_source(path, before)


def test_host_rejects_engine_started_during_audit_and_cleans(host, monkeypatch):
    archive, states, _, calls, _, _ = host
    docker = probe.docker
    def changed(args, **kwargs):
        output = docker(args, **kwargs)
        if args[0] == 'start':
            states[0]['running'] = True
        return output
    monkeypatch.setattr(probe, 'docker', changed)
    report = probe.host_report(archive)
    assert report['reason'] == 'ENGINES_STATE_CHANGED_DURING_PROBE'
    assert report['counts'] == {} and report['temporary_container_removed']
    assert calls[-1] == ['rm','--force','b'*64]


def test_host_rejects_direct_mode_instead_of_authorized_copy(host):
    archive, _, _, _, _, output = host
    output['start'] = json.dumps(probe.report_base())
    report = probe.host_report(archive)
    assert report['reason'] == 'INVALID_PROBE_OUTPUT' and report['temporary_container_removed']
