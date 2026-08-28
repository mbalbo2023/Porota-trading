"""La continuidad nueva nunca adopta el ledger viejo ni mezcla sus derivados."""
from contextlib import closing
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cg_paper_workspace as workspace
from be_paper_engine import PaperStore
from test_legacy_observed_shape_v17 import legacy_shape


def test_default_ignores_old_variable_and_uses_own_directory(tmp_path):
    env = {'DATA_DIR': str(tmp_path), 'PAPER_DB_PATH': str(tmp_path/'observer/observer_production.db')}
    assert workspace.database_path(env) == tmp_path/'paper_v17/observer_v17.db'
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('suffix', ['observer/observer_production.db', 'observer_production.db', 'observer/renamed.db'])
def test_old_path_rejected_before_sqlite_or_directory_creation(tmp_path, monkeypatch, suffix):
    def forbidden(*args, **kwargs):
        pytest.fail('No abrir el libro anterior con SQLite')
    monkeypatch.setattr(workspace.sqlite3, 'connect', forbidden)
    with pytest.raises(ValueError, match='LEGACY_PATH_FORBIDDEN'):
        workspace.runtime_store(tmp_path/suffix)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_alias_is_not_an_independent_database(tmp_path, kind):
    old = tmp_path/'observer_production.db'
    old.write_bytes(b'FIXTURE DO NOT MODIFY')
    new = tmp_path/'new.db'
    if kind == 'symlink':
        new.symlink_to(old)
    else:
        os.link(old, new)
    with pytest.raises(ValueError):
        workspace.runtime_store(new)
    assert old.read_bytes() == b'FIXTURE DO NOT MODIFY'


def test_existing_unmarked_legacy_is_not_adopted_or_migrated(legacy_shape):
    before = legacy_shape.read_bytes()
    with pytest.raises(ValueError, match='UNVERIFIED_DATABASE'):
        workspace.runtime_store(legacy_shape)
    assert legacy_shape.read_bytes() == before
    with closing(sqlite3.connect(legacy_shape)) as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert 'paper_workspace' not in tables
        assert 'paper_sale_receivables' not in tables
        assert c.execute('SELECT COUNT(*) FROM paper_positions').fetchone()[0] == 10


def test_fresh_dataset_keeps_old_bytes_and_starts_without_imports(legacy_shape, tmp_path):
    before = legacy_shape.read_bytes()
    env = {'DATA_DIR': str(tmp_path/'new'), 'PAPER_DB_PATH': str(legacy_shape),
           'PAPER_INITIAL_CAPITAL_ARS': '50000', 'PAPER_INITIAL_CAPITAL_USD_MEP': '250'}
    store = workspace.runtime_store(environ=env)
    identity = workspace.read_identity(store.path)
    assert identity['origin'] == 'FRESH_EMPTY' and identity['imported_legacy'] == 0
    assert json.loads(identity['initial_capital_json']) == {'ARS':'5E+4', 'USD':'0', 'USD_MEP':'2.5E+2', 'USD_CCL':'0'}
    with store.connect() as c:
        for table in ('paper_positions','paper_fills','paper_learning_samples',
                      'market_snapshots','paper_cauciones','paper_decisions',
                      'candle_versions','paper_sale_receivables'):
            assert c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] == 0
    assert legacy_shape.read_bytes() == before
    restarted = workspace.runtime_store(environ=env)
    assert workspace.read_identity(restarted.path) == identity


@pytest.mark.parametrize('currency', ['ARS','USD','USD_MEP','USD_CCL'])
def test_changed_initial_capital_cannot_rebase_an_existing_history(tmp_path, currency):
    env = {workspace.DB_ENV: str(tmp_path/'paper.db')}
    store = workspace.runtime_store(environ=env)
    before = workspace.read_identity(store.path)
    env[workspace.CAPITAL_DEFAULTS[currency][0]] = '2'
    with pytest.raises(ValueError, match='INITIAL_CAPITAL_OR_ORIGIN_MISMATCH'):
        workspace.runtime_store(environ=env)
    assert workspace.read_identity(store.path) == before


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-1', '', 'not-money'])
def test_invalid_capital_does_not_create_files(tmp_path, value):
    with pytest.raises(ValueError, match='INVALID_INITIAL_CAPITAL'):
        workspace.runtime_store(tmp_path/'new'/'paper.db', environ={'PAPER_INITIAL_CAPITAL_ARS':value})
    assert not list(tmp_path.iterdir())


def test_equivalent_capital_not_a_false_configuration_change(tmp_path):
    path = tmp_path/'paper.db'
    first = workspace.runtime_store(path, environ={'PAPER_INITIAL_CAPITAL_ARS':'1000000.00'})
    second = workspace.runtime_store(path, environ={'PAPER_INITIAL_CAPITAL_ARS':'1e6'})
    assert workspace.read_identity(first.path)['dataset_id'] == workspace.read_identity(second.path)['dataset_id']


def test_capital_identity_does_not_round_with_decimal_context(tmp_path):
    from decimal import localcontext
    path = tmp_path/'paper.db'
    with localcontext() as context:
        context.prec = 3
        workspace.runtime_store(path, environ={'PAPER_INITIAL_CAPITAL_ARS':'12345.67'})
        with pytest.raises(ValueError, match='INITIAL_CAPITAL_OR_ORIGIN_MISMATCH'):
            workspace.runtime_store(path, environ={'PAPER_INITIAL_CAPITAL_ARS':'12345.68'})
        workspace.runtime_store(path, environ={'PAPER_INITIAL_CAPITAL_ARS':'12345.6700'})


def test_standalone_test_store_is_not_automatically_promoted_to_runtime(tmp_path):
    store = PaperStore(str(tmp_path/'standalone.db'))
    with pytest.raises(ValueError, match='INITIAL_CAPITAL_OR_ORIGIN_MISMATCH'):
        workspace.runtime_store(store.path)


@pytest.mark.parametrize('field,value', [('namespace','OLD'), ('origin','IMPORTED'), ('imported_legacy',1), ('dataset_id','invalid')])
def test_broken_identity_is_not_repaired(tmp_path, field, value):
    store = workspace.runtime_store(tmp_path/'paper.db')
    with store.connect() as c:
        c.execute('UPDATE paper_workspace SET '+field+'=?', (value,))
    with pytest.raises(ValueError, match='UNVERIFIED_DATABASE'):
        workspace.runtime_store(store.path)
    with store.connect() as c:
        assert c.execute('SELECT '+field+' FROM paper_workspace').fetchone()[0] == value


def test_dashboard_does_not_create_or_fall_back_to_legacy(legacy_shape, tmp_path, monkeypatch):
    import bg_paper_dashboard as panel
    before = legacy_shape.read_bytes()
    monkeypatch.setattr(panel, 'DB_PATH', str(tmp_path/'missing'/'paper.db'))
    monkeypatch.setenv('PAPER_DB_PATH', str(legacy_shape))
    assert panel.snapshot()['spot_state'] == 'UNAVAILABLE'
    assert not (tmp_path/'missing').exists()
    assert legacy_shape.read_bytes() == before
    monkeypatch.setattr(panel, 'DB_PATH', str(legacy_shape))
    assert panel.snapshot()['spot_state'] == 'UNAVAILABLE'


def test_all_entrypoints_use_new_path_even_with_old_env(tmp_path):
    path = tmp_path/'v17'/'paper.db'
    env = dict(os.environ, PAPER_DB_PATH=str(tmp_path/'observer/observer_production.db'),
               PAPER_V17_DB_PATH=str(path))
    code = '''import json
import bf_production_paper_observer as scanner
import bg_paper_dashboard as panel
import bi_operational_services as services
import cg_paper_workspace as workspace
print(json.dumps([scanner.DB_PATH,panel.DB_PATH,str(workspace.database_path()),
                  str(services.REPORT_DIR),str(services.BACKUP_DIR)]))'''
    result = subprocess.run([sys.executable, '-c', code], env=env,
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [str(path), str(path), str(path),
                                        str(workspace.artifact_root(path)/'reports'),
                                        str(workspace.artifact_root(path)/'backups/paper')]
    assert not path.exists()


def test_selector_routes_dashboard_and_runtime_to_same_new_image_and_store(tmp_path, monkeypatch):
    import porota_mode_manager as manager
    monkeypatch.setattr(manager, 'ROOT', tmp_path)
    monkeypatch.setattr(manager, 'DATA', tmp_path/'data')
    settings = tmp_path/'data/diagnosticos'
    settings.mkdir(parents=True)
    (settings/'dashboard_preview_v1633.env').write_text(
        'PAPER_DB_PATH=/app/data/observer/observer_production.db\n'
        'PAPER_V17_DB_PATH=/wrong/path.db\nDASHBOARD_ACCESS_TOKEN=fixture-not-real\n')
    monkeypatch.setattr(manager, 'env_file', lambda: {'GEMINI_API_KEY':'fixture-not-real'})
    dashboard = manager.dashboard_env('PRODUCTION_PAPER').read_text()
    runtime = manager.observer_ai_env().read_text()
    assert f'{workspace.DB_ENV}={workspace.CONTAINER_DB}' in dashboard
    assert f'{workspace.DB_ENV}={workspace.CONTAINER_DB}' in runtime
    assert 'PAPER_DB_PATH=' not in dashboard+runtime
    assert '/wrong/' not in dashboard+runtime
    assert manager.IMAGE == workspace.IMAGE != 'porota-trading-bot:16.3.5'


def test_runtime_bad_arguments_do_not_initialize_storage(tmp_path, monkeypatch):
    import bv_paper_runtime as runtime
    monkeypatch.setenv(workspace.DB_ENV, str(tmp_path/'new'/'paper.db'))
    with pytest.raises(ValueError, match='Argumentos desconocidos'):
        runtime.main(['--unknown'])
    assert not list(tmp_path.iterdir())


def test_two_processes_initialize_exactly_one_dataset(tmp_path):
    path = tmp_path/'paper.db'
    code = '''import sys
from cg_paper_workspace import runtime_store,read_identity
store=runtime_store(sys.argv[1])
print(read_identity(store.path)['dataset_id'])'''
    children = [subprocess.Popen([sys.executable, '-c', code, str(path)],
                    cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        output = [p.communicate(timeout=30) for p in children]
        assert all(p.returncode == 0 for p in children), output
        assert output[0][0] == output[1][0]
        assert workspace.read_identity(path)['dataset_id'] == output[0][0].strip()
    finally:
        for p in children:
            if p.poll() is None:
                p.kill()
                p.communicate(timeout=5)


def test_offline_candle_worker_uses_new_store_and_stops_cleanly(tmp_path):
    old = tmp_path/'observer_production.db'
    old.write_bytes(b'LEGACY FIXTURE UNTOUCHED')
    path = tmp_path/'v17'/'paper.db'
    env = dict(os.environ, PAPER_DB_PATH=str(old), PAPER_V17_DB_PATH=str(path))
    child = subprocess.Popen([sys.executable, 'bv_paper_runtime.py', '--candle-worker'],
                    cwd=Path(__file__).resolve().parents[1], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    ready = False
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and child.poll() is None:
            if path.exists():
                try:
                    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as c:
                        row = c.execute('SELECT state FROM candle_worker_state WHERE id=1').fetchone()
                        ready = bool(row and row[0] == 'RUNNING')
                except sqlite3.Error:
                    pass
            if ready:
                break
            time.sleep(.05)
        assert ready, 'El worker offline no llegó a RUNNING'
        child.terminate()
        output = child.communicate(timeout=10)
        assert child.returncode == 0, output
        with closing(sqlite3.connect(path)) as c:
            assert c.execute('SELECT state FROM candle_worker_state WHERE id=1').fetchone()[0] == 'STOPPED'
            assert c.execute('SELECT COUNT(*) FROM paper_positions').fetchone()[0] == 0
        assert old.read_bytes() == b'LEGACY FIXTURE UNTOUCHED'
        assert workspace.read_identity(path)['origin'] == 'FRESH_EMPTY'
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_dashboard_allocation_endpoint_rejects_unmarked_history(legacy_shape):
    from cb_caucion_audit import allocation_history
    result = allocation_history(legacy_shape, require_workspace=True)
    assert result['state'] == 'READ_ERROR'
    assert result['total'] is None and result['records'] == []


def test_report_and_backup_namespaces_do_not_collide_for_two_databases(tmp_path):
    first = workspace.artifact_root(tmp_path/'first.db')
    second = workspace.artifact_root(tmp_path/'second.db')
    assert first != second
    assert first.parent == second.parent == tmp_path/'artifacts'
    assert not list(tmp_path.iterdir())
