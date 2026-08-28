"""Diagnóstico offline: observa el ledger, nunca migra ni arranca motores."""
import base64
from contextlib import closing
import gc
import hashlib
import json
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import v17_ledger_preflight as probe
from scripts import build_v17_ledger_preflight as builder
from be_paper_engine import PaperStore
from test_production_paper_v1634 import partial_spot


def dump(path):
    with closing(sqlite3.connect(path)) as c:
        return list(c.iterdump())


@pytest.fixture
def position(partial_spot):
    def make(kind='open'):
        b, p, q, sell = partial_spot()
        if kind in {'partial', 'closed'}:
            assert b._close(p, sell(), 'FIXTURE_ONLY')
        if kind == 'closed':
            assert b._close(b.store.open_positions()[0], sell(2, '100', '105'), 'FIXTURE_FINAL')
        if kind == 'single':
            assert b._close(p, sell(1, '100'), 'FIXTURE_SINGLE')
        return b, p
    return make


@pytest.mark.parametrize('kind', ['open', 'partial', 'closed', 'single'])
def test_current_ledger_read_without_write(position, kind, monkeypatch):
    b, p = position(kind)
    gc.collect()  # cerrar conexiones de escritura de la fixture antes de comparar bytes
    before = dump(b.store.path)
    raw = Path(b.store.path).read_bytes()
    def forbidden(*a, **k):
        raise AssertionError('No inicializar/migrar PaperStore')
    monkeypatch.setattr(PaperStore, '__init__', forbidden)
    report = probe.collect(b.store.path)
    assert report['status'] == 'OBSERVED_NO_DETECTED_INCONSISTENCIES'
    assert report['counts']['arithmetically_consistent'] == 1
    assert report['issues'] == {} and report['legacy_projection'] == {}
    assert not report['database_modified'] and not report['promotion_allowed']
    assert not report['migration_performed']
    assert p['paper_id'] not in json.dumps(report)
    assert dump(b.store.path) == before and Path(b.store.path).read_bytes() == raw


@pytest.mark.parametrize('kind', ['open', 'single'])
def test_legacy_defaults_are_reported_not_persisted(position, kind):
    b, p = position(kind)
    with b.store.connect() as c:
        for row in c.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            c.execute('DROP TRIGGER "' + row[0] + '"')
        for name in ('currency', 'market', 'currency_source'):
            c.execute('ALTER TABLE paper_positions DROP COLUMN ' + name)
        c.execute('DROP TABLE paper_sale_receivables')
        c.execute('DROP TABLE paper_spot_sales')
    before = dump(b.store.path)
    report = probe.collect(b.store.path)
    assert report['status'] == 'OBSERVED_REVIEW_REQUIRED'
    assert report['legacy_projection'] == {'currency':'ARS', 'market':'BYMA', 'currency_source':'LEGACY_ASSUMED_ARS'}
    assert report['counts']['positions_checked'] == 1
    assert report['counts']['missing_closed_sale_receipts'] == (1 if kind == 'single' else 0)
    assert report['counts']['paper_spot_sales'] is None
    assert not report['issues']
    assert dump(b.store.path) == before


@pytest.mark.parametrize('query,code', [
    ("UPDATE paper_fills SET source='PRIVATE_MARKER' WHERE side='SELL_SIMULATED'", 'PARTIAL_FILL_IDENTITY_OR_SOURCE'),
    ("UPDATE paper_spot_sales SET entry_cost='999999'", 'PARTIAL_ENTRY_COST_ALLOCATION'),
    ("UPDATE paper_positions SET exit_price='999999'", 'AGGREGATE_EXIT_PRICE'),
    ("UPDATE paper_spot_sales SET basis='PRIVATE_MARKER'", 'SALE_SETTLEMENT_TERMS'),
    ("UPDATE paper_positions SET features_json='PRIVATE_MARKER'", 'SPOT_LEDGER_INCONSISTENT'),
    ("UPDATE paper_positions SET net_pnl='NaN'", 'SPOT_LEDGER_INCONSISTENT'),
])
def test_bad_rows_are_counted_without_raw_data(position, query, code):
    b, p = position('closed')
    with b.store.connect() as c:
        c.execute(query)
    before = dump(b.store.path)
    report = probe.collect(b.store.path)
    assert report['status'] == 'OBSERVED_REVIEW_REQUIRED'
    assert report['counts']['positions_inconsistent'] == 1
    assert report['issues'] == {code:1}
    assert report['examples'] == [{'row_number':1, 'issue':code}]
    output = json.dumps(report, allow_nan=False)
    assert 'PRIVATE_MARKER' not in output and p['paper_id'] not in output
    assert dump(b.store.path) == before


@pytest.mark.parametrize('query,code', [
    ("UPDATE paper_sale_receivables SET currency='USD_MEP'", 'SPOT_LEDGER_INCONSISTENT'),
    ("UPDATE paper_sale_receivables SET net_proceeds='999999'", 'SPOT_LEDGER_INCONSISTENT'),
    ("UPDATE paper_sale_receivables SET basis='PRIVATE_MARKER'", 'SALE_SETTLEMENT_TERMS'),
    ("UPDATE paper_sale_receivables SET basis='PENDING_CONFIRMATION'", 'SALE_SETTLEMENT_TERMS'),
])
def test_single_sale_receipt_must_reconcile(position, query, code):
    b, _ = position('single')
    with b.store.connect() as c:
        c.execute(query)
    assert probe.collect(b.store.path)['issues'] == {code:1}


def test_missing_receipt_is_not_invented(position):
    b, _ = position('single')
    with b.store.connect() as c:
        c.execute('DELETE FROM paper_sale_receivables')
    before = dump(b.store.path)
    report = probe.collect(b.store.path)
    assert report['status'] == 'OBSERVED_REVIEW_REQUIRED'
    assert report['counts']['missing_closed_sale_receipts'] == 1
    assert dump(b.store.path) == before


def test_partial_close_cannot_have_a_second_full_receipt(position):
    b, p = position('closed')
    with b.store.connect() as c:
        c.execute('''INSERT INTO paper_sale_receivables
            (paper_id,currency,net_proceeds,available_at,basis) VALUES (?,?,?,?,?)''',
            (p['paper_id'],'ARS','1000',None,'PENDING_CONFIRMATION'))
    report = probe.collect(b.store.path)
    assert report['issues'] == {'SPOT_LEDGER_INCONSISTENT':1}


@pytest.mark.parametrize('kind,code', [('partial', 'ORPHAN_PARTIAL_SALES'), ('single', 'UNMATCHED_SALE_RECEIPTS')])
def test_orphan_fills_sales_and_receipts(position, kind, code):
    b, _ = position(kind)
    with sqlite3.connect(b.store.path) as c:
        c.execute('DELETE FROM paper_positions')
    report = probe.collect(b.store.path)
    assert report['issues']['ORPHAN_FILLS'] == 2
    assert report['issues'][code] == 1


def test_examples_are_bounded(position, monkeypatch):
    b, _ = position('closed')
    monkeypatch.setattr(probe, 'MAX_EXAMPLES', 0)
    with b.store.connect() as c:
        c.execute("UPDATE paper_positions SET status='PRIVATE_MARKER'")
    report = probe.collect(b.store.path)
    assert report['issues'] and report['examples'] == []
    assert report['counts']['position_states'] == {'UNKNOWN':1}


def test_nonexistent_database_is_not_created(tmp_path):
    path = tmp_path/'missing.db'
    assert probe.collect(path)['reason'] == 'DATABASE_NOT_FOUND'
    assert not path.exists()


@pytest.mark.parametrize('sql,reason', [
    ('CREATE TABLE unrelated(x)', 'PAPER_TABLES_MISSING'),
    ('CREATE VIEW paper_positions AS SELECT 1 AS x', 'UNEXPECTED_TABLE_KIND'),
    ('CREATE TABLE paper_positions(x); CREATE TABLE paper_fills(x)', 'UNSUPPORTED_LEDGER_SCHEMA'),
])
def test_schema_failures_do_not_migrate(tmp_path, sql, reason):
    path = tmp_path/'legacy.db'
    with sqlite3.connect(path) as c:
        c.executescript(sql)
    before = dump(path)
    assert probe.collect(path)['reason'] == reason
    assert dump(path) == before


@pytest.mark.parametrize('limit', ['MAX_POSITIONS', 'MAX_FILLS'])
def test_row_limits_stop_before_validation(position, monkeypatch, limit):
    b, _ = position()
    monkeypatch.setattr(probe, limit, 0)
    assert probe.collect(b.store.path)['reason'] == 'ROW_LIMIT_REQUIRES_PLANNED_AUDIT'


def test_wal_commits_visible_without_checkpoint_or_immutable(position, monkeypatch):
    b, _ = position()
    with sqlite3.connect(b.store.path) as writer:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute("UPDATE paper_positions SET entry_cost='PRIVATE_MARKER'")
        writer.commit()
        paths = [Path(b.store.path), Path(b.store.path+'-wal')]
        before = [p.read_bytes() for p in paths]
        connect = sqlite3.connect
        seen = []
        def readonly(database, **kwargs):
            assert '?mode=ro' in database and 'immutable' not in database
            connection = connect(database, **kwargs)
            connection.set_trace_callback(seen.append)
            return connection
        monkeypatch.setattr(probe.sqlite3, 'connect', readonly)
        report = probe.collect(b.store.path)
        assert report['counts']['positions_inconsistent'] == 1
        assert [p.read_bytes() for p in paths] == before
        assert 'PRAGMA query_only=ON' in seen and 'BEGIN' in seen
        assert not any(s.upper().startswith(('UPDATE','INSERT','DELETE','ALTER','CREATE')) for s in seen)


def test_read_deadline_restores_signal(monkeypatch):
    previous = signal.getsignal(signal.SIGALRM)
    def expire(path):
        signal.getsignal(signal.SIGALRM)(signal.SIGALRM, None)
    monkeypatch.setattr(probe, 'collect', expire)
    assert probe.read_report()['reason'] == 'TIME_LIMIT_120_SECONDS'
    assert signal.getsignal(signal.SIGALRM) is previous


def test_read_error_redacts_message(monkeypatch):
    def fail(path):
        raise RuntimeError('PRIVATE_MARKER')
    monkeypatch.setattr(probe, 'collect', fail)
    report = probe.read_report()
    assert report['reason'] == 'RuntimeError' and 'PRIVATE_MARKER' not in json.dumps(report)


@pytest.fixture
def host(tmp_path, monkeypatch):
    archive = tmp_path/'probe.zip'
    builder.build(archive)
    states = [{'name':'/'+name, 'running':False, 'restart':'no', 'image':'sha256:'+'a'*64,
               'tag':'porota-trading-bot:16.3.5', 'user':'botuser'} for name in (probe.BOT, probe.OBSERVER)]
    mounts = [{'Destination':'/app/data', 'Source':probe.HOST_DATA, 'Type':'bind', 'RW':True}]
    calls, failures = [], {}
    output = {'create':'b'*64, 'start':json.dumps(dict(probe.report_base(), status='OBSERVED_REVIEW_REQUIRED', read_mode='TEMPORARY_COPY'))}
    def docker(args, **kwargs):
        calls.append(args)
        if args[0] in failures:
            raise failures[args[0]]
        if args[0] == 'inspect':
            return json.dumps(mounts) if args[4] == '{{json .Mounts}}' else '\n'.join(map(json.dumps, states))
        return output.get(args[0], '')
    monkeypatch.setattr(probe, 'docker', docker)
    return archive, states, mounts, calls, failures, output


def test_host_only_reads_observer_directory_offline(host):
    archive, _, _, calls, _, _ = host
    archive.chmod(0o600)
    before = archive.read_bytes()
    report = probe.host_report(archive)
    assert report['status'] == 'OBSERVED_REVIEW_REQUIRED'
    assert report['temporary_container_removed']
    assert report['host_evidence']['network'] == 'none'
    create = next(a for a in calls if a[0] == 'create')
    assert create[create.index('--network')+1] == 'none'
    assert create[create.index('--pull')+1] == 'never'
    assert create[create.index('--entrypoint')+1] == 'python'
    assert create[create.index('--user')+1] == 'botuser'
    assert '--read-only' in create and '--no-healthcheck' in create
    assert 'sha256:'+'a'*64 in create
    mounts = [create[i+1] for i, a in enumerate(create) if a == '--mount']
    assert len(mounts) == 2 and all(m.endswith(',readonly') for m in mounts)
    assert mounts[1] == 'type=bind,src='+probe.HOST_DATA+'/observer,dst=/observer,readonly'
    assert not any('.env' in a or '/app/data' in a or 'secrets' in a for a in create)
    assert all('.Env' not in ' '.join(a) for a in calls)
    assert calls[-3] == ['start','--attach','b'*64]
    assert calls[-2][0] == 'inspect' and calls[-1] == ['rm','--force','b'*64]
    assert create[-1] == '--copy-read'
    assert create[create.index('--tmpfs')+1] == '/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777'
    assert report['host_evidence']['engines_stopped_after_probe']
    assert not any(a[0] in {'stop','restart','update','exec','pull'} for a in calls)
    copy = Path(mounts[0].split('src=',1)[1].split(',dst=',1)[0])
    assert not copy.exists()
    assert archive.stat().st_mode & 0o777 == 0o600 and archive.read_bytes() == before


@pytest.mark.parametrize('index,field,value', [
    (0,'running',True), (0,'restart','always'), (1,'running',True),
    (1,'restart','unless-stopped'), (1,'tag','other'), (1,'user','root'),
    (1,'image','invalid'), (1,'name','/other'),
])
def test_changed_installation_does_not_start_anything(host, index, field, value):
    archive, states, _, calls, _, _ = host
    states[index][field] = value
    assert probe.host_report(archive)['status'] == 'STOPPED'
    assert all(a[0] == 'inspect' for a in calls)


@pytest.mark.parametrize('field,value', [('Source','/other'), ('Destination','/other'), ('Type','volume')])
def test_changed_mount_never_searches_for_data(host, field, value):
    archive, _, mounts, calls, _, _ = host
    mounts[0][field] = value
    assert probe.host_report(archive)['reason'] == 'OBSERVER_DATA_MOUNT_CHANGED'
    assert all(a[0] == 'inspect' for a in calls)


@pytest.mark.parametrize('action', ['inspect','create'])
def test_failure_before_creation_no_retry_or_removal(host, action):
    archive, _, _, calls, failures, _ = host
    failures[action] = probe.PreflightStop('DOCKER_COMMAND_FAILED_'+action.upper())
    assert probe.host_report(archive)['status'] == 'STOPPED'
    assert not any(a[0] in {'start','rm'} for a in calls)
    assert len([a for a in calls if a[0] == action]) == 1


def test_timeout_removes_only_own_container(host):
    archive, _, _, calls, failures, _ = host
    failures['start'] = subprocess.TimeoutExpired(['docker'],150)
    report = probe.host_report(archive)
    assert report['reason'] == 'TimeoutExpired' and report['temporary_container_removed']
    assert calls[-1] == ['rm','--force','b'*64]


@pytest.mark.parametrize('output', ['not json', '[]', '{}'])
def test_invalid_result_not_success_and_cleanup(host, output):
    archive, _, _, _, _, outputs = host
    outputs['start'] = output
    report = probe.host_report(archive)
    assert report['status'] == 'STOPPED' and report['temporary_container_removed']


def test_unrecognized_container_id_is_not_used(host):
    archive, _, _, calls, _, outputs = host
    outputs['create'] = 'unknown'
    assert probe.host_report(archive)['reason'] == 'UNEXPECTED_CREATE_RESULT_REVIEW_REQUIRED'
    assert not any(a[0] in {'start','rm'} for a in calls)


def test_cleanup_failure_reported(host):
    archive, _, _, _, failures, _ = host
    failures['rm'] = RuntimeError('PRIVATE_MARKER')
    report = probe.host_report(archive)
    assert report['reason'] == 'TEMPORARY_CONTAINER_CLEANUP_FAILED'
    assert report['temporary_container_removed'] is False
    assert 'PRIVATE_MARKER' not in json.dumps(report)


def test_only_docker_uses_sudo_and_no_fallback(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args,1,'','PRIVATE_MARKER')
    monkeypatch.setattr(subprocess, 'run', run)
    with pytest.raises(probe.PreflightStop, match='DOCKER_COMMAND_FAILED_INSPECT'):
        probe.docker(['inspect','fixture'])
    assert calls == [['sudo','-n','docker','inspect','fixture']]


def test_clipboard_contains_same_json(host, monkeypatch, capsys):
    archive = host[0]
    monkeypatch.setattr(sys, 'argv', [str(archive), '--host'])
    assert probe.main() == 0
    displayed, encoded = capsys.readouterr().out.split('\033]52;c;')
    assert json.loads(displayed) == json.loads(base64.b64decode(encoded.rstrip('\a')))


def test_zip_reproducible_exact_pure_sources_and_no_overwrite(tmp_path):
    a, b = tmp_path/'a.zip', tmp_path/'b.zip'
    builder.build(a)
    builder.build(b)
    assert a.read_bytes() == b.read_bytes()
    with zipfile.ZipFile(a) as z:
        assert set(z.namelist()) == set(builder.SOURCES) | {'MANIFEST.json'}
        manifest = json.loads(z.read('MANIFEST.json'))
        for name, entry in manifest['files'].items():
            assert z.read(name) == (ROOT/entry['source']).read_bytes()
            assert hashlib.sha256(z.read(name)).hexdigest() == entry['sha256']
        source = '\n'.join(z.read(name).decode() for name in builder.SOURCES)
        assert 'import requests' not in source and 'import ppi_client' not in source
        assert 'from be_paper_engine' not in source
    result = subprocess.run([sys.executable, '-S', str(a), '--help'], capture_output=True, text=True)
    assert result.returncode == 0 and '--host' in result.stdout
    with pytest.raises(FileExistsError):
        builder.build(a)


def test_zip_reader_runs_without_site_packages_on_fixture(position, tmp_path):
    b, _ = position('closed')
    archive = tmp_path/'standalone.zip'
    builder.build(archive)
    code = "import sys,runpy,json; sys.path.insert(0,sys.argv[1]); m=runpy.run_path(sys.argv[1]); print(json.dumps(m['collect'](sys.argv[2])))"
    before = dump(b.store.path)
    result = subprocess.run([sys.executable, '-S', '-c', code, str(archive), b.store.path], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['status'] == 'OBSERVED_NO_DETECTED_INCONSISTENCIES'
    assert dump(b.store.path) == before
