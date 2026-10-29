"""A186: provider outages alert Deploy V2 but never admit unsafe PAPER.

Historical daily preopen and local contract integrity remain fail-closed.
"""
import json
import sqlite3
from types import SimpleNamespace

import pytest

import rc6_preopen as preopen


@pytest.mark.parametrize('auth', ('ERROR', 'COOLDOWN', 'NOT_ATTEMPTED'))
def test_ppi_auth_failure_is_deploy_only_amber_with_safe_db(tmp_path, monkeypatch, auth):
    db = tmp_path / 'observer.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE observer_state (id INTEGER, mode TEXT, process_state TEXT, '
                  'session_state TEXT, ppi_auth TEXT, real_orders_sent INTEGER, heartbeat_at TEXT)')
        c.execute('INSERT INTO observer_state VALUES(1,?,?,?,?,?,?)',
                  ('PRODUCTION_PAPER', 'DEGRADED', 'MARKET_OPEN', auth, 0, '2026-10-29'))
    monkeypatch.setattr(preopen, 'DB', db)
    monkeypatch.setattr(preopen, 'runtime_contracts', lambda *_: {'state': 'GREEN'})
    daily = preopen.observer_db()
    assert daily['state'] == 'RED'
    assert daily['reason'] == 'PPI_AUTH_UNAVAILABLE'
    deploy = preopen.deploy_optional_source_availability('observer_db', daily)
    assert deploy['state'] == 'AMBER'
    assert deploy['source_state'] == 'RED'
    assert deploy['provider'] == 'PPI'
    assert deploy['deploy_effect'] == 'ALERT_ONLY'


@pytest.mark.parametrize('mode,orders,runtime_state,auth', (
    ('LIVE', 0, 'GREEN', 'ERROR'),
    ('PRODUCTION_PAPER', 1, 'GREEN', 'COOLDOWN'),
    ('PRODUCTION_PAPER', 0, 'RED', 'ERROR'),
    ('PRODUCTION_PAPER', 0, 'GREEN', 'FALSIFIED_AUTH_STATE'),
))
def test_non_source_safety_or_invalid_auth_stays_hard_red(
        tmp_path, monkeypatch, mode, orders, runtime_state, auth):
    db = tmp_path / 'observer.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE observer_state (id INTEGER, mode TEXT, process_state TEXT, '
                  'session_state TEXT, ppi_auth TEXT, real_orders_sent INTEGER, heartbeat_at TEXT)')
        c.execute('INSERT INTO observer_state VALUES(1,?,?,?,?,?,?)',
                  (mode, 'DEGRADED', 'MARKET_OPEN', auth, orders, '2026-10-29'))
    monkeypatch.setattr(preopen, 'DB', db)
    monkeypatch.setattr(preopen, 'runtime_contracts',
                        lambda *_: {'state': runtime_state})
    row = preopen.observer_db()
    assert row['state'] == 'RED'
    assert preopen.deploy_optional_source_availability('observer_db', row)['state'] == 'RED'


@pytest.mark.parametrize('worker_healthy,expected', ((True, 'AMBER'), (False, 'RED')))
def test_no_ready_candidates_is_only_deploy_alert_when_all_worker_guards_hold(
        tmp_path, monkeypatch, worker_healthy, expected):
    db = tmp_path / 'observer.db'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE observer_state (id INTEGER, mode TEXT, process_state TEXT, '
                  'session_state TEXT, ppi_auth TEXT, real_orders_sent INTEGER, heartbeat_at TEXT)')
        c.execute('INSERT INTO observer_state VALUES(1,?,?,?,?,?,?)',
                  ('PRODUCTION_PAPER', 'RUNNING', 'MARKET_CLOSED', 'OK', 0, '2026-10-29'))
    monkeypatch.setattr(preopen, 'DB', db)
    monkeypatch.setattr(preopen, 'runtime_contracts', lambda *_: {
        'state':'RED', 'unclassified_residuals':0, 'scalping_eligible_identities':0,
        'real_routes_expected':[],
        'workers':{
            name:{'state':'GREEN' if worker_healthy or name != 'supervisor' else 'RED'}
            for name in ('scalping','caucion_cash_sweep','supervisor')},
    })
    row = preopen.observer_db()
    assert row['state'] == 'RED'  # No PAPER openings based on missing identities.
    deploy = preopen.deploy_optional_source_availability('observer_db', row)
    assert deploy['state'] == expected
    if worker_healthy:
        assert deploy['provider'] == 'PAPER_READINESS'
        assert deploy['reason'] == 'PAPER_CANDIDATES_UNAVAILABLE'
    else:
        assert 'reason' not in deploy


@pytest.mark.parametrize('check,reason,detail,expected', (
    ('byma_morning_watch', 'BYMA_WATCH_NOT_TODAY', None, 'AMBER'),
    ('byma_morning_watch', 'BYMA_DRY_VALIDATION_LATEST_NOT_SAFE', None, 'AMBER'),
    ('byma_morning_watch', 'BYMA_WATCH_DEGRADED', None, 'AMBER'),
    ('byma_morning_watch', 'BYMA_WATCH_UNAVAILABLE', 'FileNotFoundError:absent', 'AMBER'),
    ('byma_morning_watch', 'BYMA_WATCH_UNAVAILABLE', 'JSONDecodeError:corrupt', 'RED'),
    ('byma_morning_watch', 'BYMA_AUTHORITY_CHANGE_REVIEW_REQUIRED', None, 'RED'),
    ('byma_morning_watch', 'BYMA_WATCH_STATE_UNKNOWN', None, 'RED'),
    ('iol_reference_fallback', 'IOL_REFERENCE_UNAVAILABLE', 'FileNotFoundError:absent', 'AMBER'),
    ('iol_reference_fallback', 'IOL_REFERENCE_UNAVAILABLE', 'PermissionError:denied', 'RED'),
    ('iol_reference_fallback', 'IOL_REFERENCE_UNAVAILABLE', 'JSONDecodeError:corrupt', 'RED'),
    ('iol_reference_fallback', 'IOL_FAILSAFE_CONTRACT_INVALID', None, 'RED'),
    ('observer_db', 'PAPER_CANDIDATES_UNAVAILABLE', None, 'AMBER'),
))
def test_deploy_optional_source_outage_is_explicitly_classified(check, reason, detail, expected):
    source = {'state': 'RED', 'reason': reason}
    if detail:
        source['detail'] = detail
    result = preopen.deploy_optional_source_availability(check, source)
    assert result['state'] == expected
    assert source['state'] == 'RED'  # Never modify the source evidence in place.
    assert ('deploy_effect' in result) == (expected == 'AMBER')


def test_missing_source_files_alert_only_in_deploy_not_in_daily_preopen(tmp_path, monkeypatch):
    monkeypatch.setattr(preopen, 'BYMA_MORNING_WATCH', tmp_path / 'missing_byma.json')
    monkeypatch.setattr(preopen, 'IOL_FAMILY_REFERENCE', tmp_path / 'missing_iol.json')
    byma = preopen.byma_morning_watch(preopen.datetime.now(preopen.TZ).date())
    iol = preopen.iol_reference_state(preopen.datetime.now(preopen.TZ).date())
    assert byma['state'] == iol['state'] == 'RED'
    assert preopen.deploy_optional_source_availability('byma_morning_watch', byma)['state'] == 'AMBER'
    assert preopen.deploy_optional_source_availability('iol_reference_fallback', iol)['state'] == 'AMBER'


def test_preopen_report_records_three_provider_alarms_only_for_exact_deploy(monkeypatch, capsys):
    monkeypatch.setattr(preopen.byma, 'es_dia_habil_operativo', lambda _: True)
    monkeypatch.setattr(preopen, 'release_identity', lambda: {'state':'GREEN'})
    monkeypatch.setattr(preopen, 'container', lambda *_, **__: {'state':'GREEN'})
    monkeypatch.setattr(preopen, 'required_timers', lambda *_: {'state':'GREEN'})
    monkeypatch.setattr(preopen, 'observer_db', lambda: {
        'state':'RED', 'reason':'PPI_AUTH_UNAVAILABLE'})
    monkeypatch.setattr(preopen, 'byma_morning_watch', lambda *_, **__: {
        'state':'RED', 'reason':'BYMA_WATCH_DEGRADED'})
    monkeypatch.setattr(preopen, 'iol_reference_state', lambda *_: {
        'state':'RED', 'reason':'IOL_REFERENCE_UNAVAILABLE',
        'detail':'FileNotFoundError:missing'})
    monkeypatch.setattr(preopen, 'blocked_history_timers', lambda: {'state':'GREEN'})
    monkeypatch.setattr(preopen, 'foreign_market_policy', lambda *_: {'state':'GREEN'})
    monkeypatch.setattr(preopen, 'operator_pause_reason', lambda *_, **__: None)
    monkeypatch.setattr(preopen, 'evaluate_rearm', lambda *_, **__: (False, 'NO_GO', None))
    monkeypatch.setattr(preopen.shutil, 'disk_usage',
                        lambda _: SimpleNamespace(free=9*1024**3))

    assert preopen.main('T_MINUS_45', 'a'*40, publish_paper_gate=False) == 0
    deployed = json.loads(capsys.readouterr().out)
    assert deployed['status'] == 'GREEN' and deployed['red_checks'] == []
    assert deployed['read_only'] is True
    assert deployed['network_order_test_performed'] is False
    assert {row['provider'] for row in deployed['external_source_alerts']} == {
        'PPI', 'BYMA', 'IOL'}
    assert all(row['paper_readiness'] == 'FAIL_CLOSED_PER_INSTRUMENT'
               for row in deployed['external_source_alerts'])

    assert preopen.main('T_MINUS_45', None, publish_paper_gate=False) == 2
    daily = json.loads(capsys.readouterr().out)
    assert daily['status'] == 'RED'
    assert set(daily['red_checks']) == {
        'observer_db', 'byma_morning_watch', 'iol_reference_fallback'}
    assert 'external_source_alerts' not in daily
