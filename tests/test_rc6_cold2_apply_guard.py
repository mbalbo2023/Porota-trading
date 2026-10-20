"""A future authorization cannot bypass exact quiescence at archive boundaries."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import rc6_cold2_apply_guard as guard

CANDIDATE = 'b' * 40
NAMES = guard.OWNED


def receipt():
    ids = {name: hex(index + 1)[2:] * 64 for index, name in enumerate(NAMES)}
    return {
        'candidate_sha': CANDIDATE, 'status': 'HOST_INSTALLATION_STARTED',
        'installation_started': True, 'inflight': 'ABSENT_OWNED_RUNTIME_STOPPED',
        'real_orders_sent': 0, 'ppi_watch_accessed': False,
        'containers': {name: {'id': ids[name], 'image': 'sha256:' + 'a' * 64}
                       for name in NAMES},
        'stop_evidence': {name: {'classification': 'CLEAN_EXIT',
                                 'after': {'Status': 'exited', 'ExitCode': 0}}
                          for name in NAMES},
    }


def folder(tmp_path, value):
    target = tmp_path/'data/deploy'
    target.mkdir(parents=True)
    (target/'.deploy-quiescence.lock').touch()
    (target/('quiescence-'+CANDIDATE+'.json')).write_text(json.dumps(value))
    return target


@pytest.mark.parametrize('change', [
    lambda r: r.update(status='FIX_FORWARD_VALIDATION_REQUIRED'),
    lambda r: r.update(inflight='NO_VERIFICADO'),
    lambda r: r.update(real_orders_sent=True),
    lambda r: r['stop_evidence'][NAMES[1]].update(classification='UNKNOWN'),
])
def test_receipt_rejects_incomplete_or_changed_drain(tmp_path, change):
    value = receipt()
    change(value)
    folder(tmp_path, value)
    with pytest.raises(ValueError):
        guard.receipt_for(tmp_path, CANDIDATE)


def test_each_archive_boundary_rechecks_live_stopped_identity_and_ppi(tmp_path):
    value = receipt()
    folder(tmp_path, value)
    expected = {name: value['containers'][name]['id'] for name in NAMES}
    live = {name: {'Id': expected[name], 'Image': 'sha256:'+'a'*64,
                   'State': {'Status': 'exited', 'ExitCode': 0}} for name in NAMES}
    live['porota_critical_approval_rc6'] = {
        'State': {'Status': 'running', 'Health': {'Status': 'healthy'},
                  'OOMKilled': False}}
    units = [{'ppi-watch.service': ('active', 'enabled')}]

    def archive(root, *, admission, expected_owned_ids):
        assert expected_owned_ids == expected
        assert admission()['status'] == 'DRAINED'
        live[NAMES[0]]['State']['Status'] = 'running'
        with pytest.raises(ValueError, match='COLD2_OWNED_ID_CHANGED'):
            admission()
        live[NAMES[0]]['State']['Status'] = 'exited'
        live[NAMES[0]]['Id'] = 'f'*64
        with pytest.raises(ValueError, match='COLD2_OWNED_ID_CHANGED'):
            admission()
        live[NAMES[0]]['Id'] = expected[NAMES[0]]
        units[0] = {'ppi-watch.service': ('inactive', 'enabled')}
        with pytest.raises(ValueError, match='COLD2_PPI_UNIT_CHANGED'):
            admission()
        units[0] = {'ppi-watch.service': ('active', 'enabled')}
        return {'state': 'QUARANTINED_FAILED'}

    with (patch.object(guard, 'ppi_units', side_effect=lambda: units[0]),
          patch.object(guard.drain, 'admit', return_value='STOPPED'),
          patch.object(guard.drain, 'inspect', side_effect=lambda name: live[name]),
          patch.object(guard.cold, 'envelope', return_value={
              'status': 'FAIL_CLOSED', 'real_routes': 'NOT_CALLED'}),
          patch.object(guard.cold, 'quarantine', side_effect=archive)):
        assert guard.apply(tmp_path, CANDIDATE)['state'] == 'QUARANTINED_FAILED'


def test_missing_exclusive_lock_refuses_before_archive(tmp_path):
    target = folder(tmp_path, receipt())
    (target/'.deploy-quiescence.lock').unlink()
    with patch.object(guard.cold, 'quarantine') as archive:
        with pytest.raises(ValueError, match='COLD2_QUIESCENCE_LOCK_MISSING'):
            guard.apply(tmp_path, CANDIDATE)
        archive.assert_not_called()


@pytest.mark.parametrize('position_status,expected', [
    ('CANCELLED_ADMIN', 'DRAINED'),
    ('OPEN', 'QUIESCENCE_OPEN_POSITION'),
])
def test_observed_stopped_host_waiting_market_after_admin_cancel(
        tmp_path, position_status, expected):
    """Replay the failed run: durable WAITING_MARKET, stopped Docker, CLOSED."""
    value = receipt()
    folder(tmp_path, value)
    db = tmp_path/'data/paper_v17/observer_v17.db'
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as c:
        c.executescript('''
          CREATE TABLE observer_state (id INTEGER PRIMARY KEY, mode TEXT,
            real_orders_sent INTEGER, process_state TEXT, session_state TEXT,
            heartbeat_at TEXT);
          INSERT INTO observer_state VALUES
            (1,'PRODUCTION_PAPER',0,'WAITING_MARKET','CLOSED',
             '2026-10-20T04:34:28+00:00');
          CREATE TABLE paper_positions (status TEXT);
          CREATE TABLE paper_cauciones (status TEXT);
          CREATE TABLE paper_future_positions (status TEXT);
        ''')
        c.execute('INSERT INTO paper_positions VALUES (?)', (position_status,))
    ids = {name: value['containers'][name]['id'] for name in NAMES}
    live = {name: {'Id': ids[name], 'Image': 'sha256:'+'a'*64,
                   'State': {'Status': 'exited', 'ExitCode': 0}} for name in NAMES}
    live['porota_critical_approval_rc6'] = {
        'State': {'Status': 'running', 'Health': {'Status': 'healthy'},
                  'OOMKilled': False}}
    reached = []

    def archive(root, *, admission, expected_owned_ids):
        reached.append(admission())
        return {'state': 'QUARANTINED_FAILED', 'old_cut_revoked': True}

    with (patch.object(guard.drain, 'ROOT', tmp_path),
          patch.object(guard, 'ppi_units', return_value={}),
          patch.object(guard.drain, 'inspect', side_effect=lambda name: live[name]),
          patch.object(guard.cold, 'envelope', return_value={
              'status': 'FAIL_CLOSED', 'real_routes': 'NOT_CALLED'}),
          patch.object(guard.cold, 'quarantine', side_effect=archive)):
        if expected == 'DRAINED':
            assert guard.apply(tmp_path, CANDIDATE)['old_cut_revoked'] is True
            assert len(reached) == 1
            assert reached[0]['status'] == 'DRAINED'
            assert reached[0]['owned_ids'] == ids
        else:
            with pytest.raises(ValueError, match=expected):
                guard.apply(tmp_path, CANDIDATE)
            assert not reached
