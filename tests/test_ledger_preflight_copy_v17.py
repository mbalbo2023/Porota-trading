"""Copia autorizada del ledger: origen quieto, SQL sólo en copia y limpieza."""
from contextlib import closing
import gc
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import v17_ledger_preflight as probe
from test_ledger_preflight_v17 import position, host
from test_production_paper_v1634 import partial_spot


@pytest.fixture
def source(position, tmp_path):
    b, _ = position('closed')
    gc.collect()
    folder, scratch = tmp_path/'source', tmp_path/'scratch'
    folder.mkdir()
    scratch.mkdir()
    path = folder/'observer.db'
    with closing(sqlite3.connect(b.store.path)) as original, closing(sqlite3.connect(path)) as target:
        original.backup(target)  # construir fixture; no es el procedimiento productivo
        target.execute('PRAGMA journal_mode=WAL')
    assert not Path(str(path)+'-wal').exists()
    assert not Path(str(path)+'-shm').exists()
    path.chmod(0o444)
    folder.chmod(0o555)
    try:
        yield path, scratch
    finally:
        folder.chmod(0o755)  # únicamente limpieza de la fixture propia


def test_copy_audits_current_ledger_and_keeps_original_bytes_permissions(source, monkeypatch):
    path, scratch = source
    before = path.read_bytes()
    signature = probe._source_signature(path)
    connect = sqlite3.connect
    sql_paths = []
    def checked(database, **kwargs):
        assert str(path) not in database
        assert str(scratch) in database and database.endswith('?mode=ro')
        assert 'immutable' not in database
        sql_paths.append(database)
        return connect(database, **kwargs)
    monkeypatch.setattr(probe.sqlite3, 'connect', checked)
    report = probe.collect_copy(path, scratch)
    assert report['status'] == 'OBSERVED_NO_DETECTED_INCONSISTENCIES'
    assert report['read_mode'] == 'TEMPORARY_COPY' and report['stage'] == 'DONE'
    assert report['counts']['positions_checked'] == 1
    assert report['copy_evidence'] == {'bytes':len(before), 'copy_hash_matches':True, 'source_hash_rechecks':2}
    assert report['temporary_copy_created'] and report['temporary_copy_removed']
    assert report['source_filesystem_evidence']['database']['header_journal_mode'] == 'WAL'
    assert report['copy_filesystem_evidence']['database']['header_journal_mode'] == 'WAL'
    assert len(sql_paths) == 1 and not list(scratch.iterdir())
    assert path.read_bytes() == before and probe._source_signature(path) == signature
    assert path.stat().st_mode & 0o777 == 0o444
    assert path.parent.stat().st_mode & 0o777 == 0o555
    assert not report['database_modified'] and not report['migration_performed'] and not report['promotion_allowed']
    assert hashlib.sha256(before).hexdigest() not in json.dumps(report)
    assert not Path(str(path)+'-wal').exists() and not Path(str(path)+'-shm').exists()


@pytest.mark.skipif(os.geteuid() == 0, reason='Reproducción real con usuario no-root del CI, sin cambiar identidad')
def test_wal_copy_succeeds_where_readonly_original_cannot_open(source):
    path, scratch = source
    before = path.read_bytes()
    direct = probe.collect(path)
    assert direct['reason'] == 'SQLITE_ERROR' and direct['stage'] == 'READ_SCHEMA'
    assert direct['sqlite_error']['primary_code'] in {8,14}
    result = probe.collect_copy(path, scratch)
    assert result['status'] == 'OBSERVED_NO_DETECTED_INCONSISTENCIES'
    assert result['temporary_copy_removed'] and path.read_bytes() == before


@pytest.mark.parametrize('suffix', ['-wal','-shm','-journal'])
@pytest.mark.parametrize('when', ['before','after'])
def test_existing_or_appearing_auxiliary_stops_without_publishing_results(source, monkeypatch, suffix, when):
    path, scratch = source
    path.parent.chmod(0o755)  # simular escritor externo sólo en fixture
    auxiliary = Path(str(path)+suffix)
    original = probe.collect
    seen = []
    def audit(copy):
        seen.append(copy)
        result = original(copy)
        auxiliary.write_bytes(b'')
        return result
    if when == 'before':
        auxiliary.write_bytes(b'')
    else:
        monkeypatch.setattr(probe, 'collect', audit)
    result = probe.collect_copy(path, scratch)
    assert result['reason'] == 'SOURCE_AUXILIARY_PRESENT' and result['status'] == 'STOPPED'
    assert result['counts'] == {} and result['issues'] == {} and result['examples'] == []
    assert result['temporary_copy_created'] == (when == 'after')
    assert result['temporary_copy_removed'] == (True if when == 'after' else None)
    assert bool(seen) == (when == 'after') and not list(scratch.iterdir())
    assert auxiliary.exists()  # el diagnóstico NO elimina auxiliares del origen


@pytest.mark.parametrize('change', ['bytes','metadata','replacement'])
def test_changed_source_after_audit_discards_results_without_repair(source, monkeypatch, change):
    path, scratch = source
    path.parent.chmod(0o755)
    path.chmod(0o644)
    original = probe.collect
    changed = []
    def audit(copy):
        result = original(copy)
        if change == 'bytes':
            content = path.read_bytes()
            path.write_bytes(content[:-1]+bytes([content[-1]^1]))
        elif change == 'metadata':
            info = path.stat()
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns+1000000000))
        else:
            replacement = path.parent/'replacement.db'
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        changed.append(path.read_bytes())
        return result
    monkeypatch.setattr(probe, 'collect', audit)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'SOURCE_CHANGED'
    assert report['stage'] == 'VERIFY_SOURCE_AFTER_AUDIT'
    assert report['counts'] == {} and report['issues'] == {}
    assert report['temporary_copy_removed'] and not list(scratch.iterdir())
    assert path.read_bytes() == changed[0]


@pytest.mark.parametrize('call', [2,3])
def test_hash_change_without_detectable_metadata_still_blocks(source, monkeypatch, call):
    path, scratch = source
    original = probe._stream_source
    calls = []
    def changed(*args, **kwargs):
        calls.append(1)
        result = original(*args, **kwargs)
        return b'changed' if len(calls) == call else result
    monkeypatch.setattr(probe, '_stream_source', changed)
    result = probe.collect_copy(path, scratch)
    assert result['reason'] == 'SOURCE_HASH_CHANGED' and result['counts'] == {}
    assert result['temporary_copy_removed'] and len(calls) == call


def test_copy_hash_mismatch_never_reaches_audit(source, monkeypatch):
    path, scratch = source
    monkeypatch.setattr(probe, '_copy_digest', lambda path: b'wrong')
    def forbidden(*args):
        raise AssertionError('No auditar copia diferente')
    monkeypatch.setattr(probe, 'collect', forbidden)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'COPY_HASH_MISMATCH' and report['temporary_copy_removed']
    assert report['copy_evidence']['source_hash_rechecks'] == 0


def test_permission_denial_no_alternate_user_flags_or_retry(source, monkeypatch):
    path, scratch = source
    original = os.open
    calls = []
    def denied(file, flags, *args, **kwargs):
        if Path(file) == path:
            calls.append(flags)
            raise PermissionError(13, 'PRIVATE_MARKER')
        return original(file, flags, *args, **kwargs)
    monkeypatch.setattr(probe.os, 'open', denied)
    report = probe.collect_copy(path, scratch)
    assert report['reason'] == 'PermissionError' and report['os_errno'] == 13
    assert len(calls) == 1 and calls[0] & os.O_ACCMODE == os.O_RDONLY
    assert report['temporary_copy_removed'] and 'PRIVATE_MARKER' not in json.dumps(report)


def test_source_too_large_stops_before_copy(source, monkeypatch):
    path, scratch = source
    monkeypatch.setattr(probe, 'MAX_COPY_BYTES', path.stat().st_size-1)
    result = probe.collect_copy(path, scratch)
    assert result['reason'] == 'SOURCE_SIZE_OUTSIDE_COPY_LIMIT'
    assert not result['temporary_copy_created'] and not list(scratch.iterdir())


def test_temporary_destination_cannot_be_inside_source_directory(source):
    path, scratch = source
    result = probe.collect_copy(path, path.parent)
    assert result['reason'] == 'TEMPORARY_LOCATION_OVERLAPS_SOURCE'
    assert not result['temporary_copy_created']


def test_missing_source_does_not_create_database(source):
    path, scratch = source
    missing = path.parent/'missing.db'
    result = probe.collect_copy(missing, scratch)
    assert result['reason'] == 'FileNotFoundError'
    assert not result['temporary_copy_created'] and not missing.exists()


def test_symlink_source_is_rejected_before_opening_target(source):
    path, scratch = source
    path.parent.chmod(0o755)
    link = path.parent/'link.db'
    link.symlink_to(path)
    report = probe.collect_copy(link, scratch)
    assert report['reason'] == 'SOURCE_NOT_REGULAR_FILE'
    assert not report['temporary_copy_created']


def test_invalid_header_no_full_copy_or_sql(source):
    path, scratch = source
    path.chmod(0o644)
    path.write_bytes(b'PRIVATE_MARKER'*100)
    result = probe.collect_copy(path, scratch)
    assert result['reason'] == 'SOURCE_HEADER_UNSUPPORTED'
    assert not result['temporary_copy_created'] and 'PRIVATE_MARKER' not in json.dumps(result)


def test_deadline_cleans_copy_and_restores_handler(source, monkeypatch):
    path, scratch = source
    original = probe.collect_copy
    before = signal.getsignal(signal.SIGALRM)
    monkeypatch.setattr(probe, 'DATABASE', str(path))
    monkeypatch.setattr(probe, 'collect_copy', lambda path: original(path, scratch))
    def expire(copy):
        signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
    monkeypatch.setattr(probe, 'collect', expire)
    result = probe.read_report(copy_mode=True)
    assert result['reason'] == 'TIME_LIMIT_120_SECONDS'
    assert result['read_mode'] == 'TEMPORARY_COPY' and result['temporary_copy_removed']
    assert signal.getsignal(signal.SIGALRM) is before and not list(scratch.iterdir())


def test_copy_cleanup_failure_is_not_success(source, monkeypatch):
    path, scratch = source
    real = probe.tempfile.TemporaryDirectory
    managers = []
    def failing(*args, **kwargs):
        manager = real(*args, **kwargs)
        cleanup = manager.cleanup
        managers.append((manager, cleanup))
        def fail():
            raise PermissionError('PRIVATE_MARKER')
        manager.cleanup = fail
        return manager
    monkeypatch.setattr(probe.tempfile, 'TemporaryDirectory', failing)
    try:
        result = probe.collect_copy(path, scratch)
        assert result['reason'] == 'TEMPORARY_COPY_CLEANUP_FAILED' and result['status'] == 'STOPPED'
        assert result['temporary_copy_removed'] is False
        assert 'PRIVATE_MARKER' not in json.dumps(result)
    finally:
        for _, cleanup in managers:
            cleanup()
    assert not list(scratch.iterdir())


def test_copy_validation_error_preserved_without_touching_source(source, monkeypatch):
    path, scratch = source
    before = path.read_bytes()
    def invalid(copy):
        return dict(probe.report_base(), status='STOPPED', stage='READ_SCHEMA',
                    reason='SQLITE_ERROR', sqlite_error={'category':'CORRUPT'})
    monkeypatch.setattr(probe, 'collect', invalid)
    result = probe.collect_copy(path, scratch)
    assert result['reason'] == 'SQLITE_ERROR' and result['sqlite_error']['category'] == 'CORRUPT'
    assert result['temporary_copy_removed'] and result['copy_evidence']['source_hash_rechecks'] == 2
    assert path.read_bytes() == before


def test_engine_started_during_probe_discards_report_and_removes_only_own_container(host, monkeypatch):
    archive, states, _, calls, _, _ = host
    original = probe.docker
    def changed(args, **kwargs):
        if args[0] == 'start':
            states[0]['running'] = True
        return original(args, **kwargs)
    monkeypatch.setattr(probe, 'docker', changed)
    report = probe.host_report(archive)
    assert report['reason'] == 'ENGINES_STATE_CHANGED_DURING_PROBE'
    assert report['counts'] == {} and report['temporary_container_removed']
    assert calls[-1] == ['rm','--force','b'*64]
    assert not any(c[0] in {'stop','update','restart'} for c in calls)


def test_host_rejects_direct_mode_even_with_matching_version(host):
    archive, _, _, _, _, outputs = host
    outputs['start'] = json.dumps(probe.report_base())
    report = probe.host_report(archive)
    assert report['reason'] == 'INVALID_PROBE_OUTPUT' and report['temporary_container_removed']
