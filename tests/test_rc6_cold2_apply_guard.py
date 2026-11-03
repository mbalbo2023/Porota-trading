"""A future authorization cannot bypass exact quiescence at archive boundaries."""
import json
import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
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
        assert guard.apply(tmp_path, CANDIDATE)['original']['state'] == 'QUARANTINED_FAILED'


@pytest.mark.parametrize('changed', [False, True])
def test_a139_cold2_archive_accepts_only_exact_stopped_a92_rescue_receipt(tmp_path,changed):
    """Regression for actual #40592426296 COLD2 QUIESCENCE_CLOSED_REQUIRED."""
    value=receipt()
    value.update(degraded_closed_rescue=True,market_open_suspended=False,
                 open_paper_carryover=False,paused_postclose=False,
                 intraday_caucion=False,pending_caucion=False,
                 spot_carryover=None,caucion_carryover=None)
    folder(tmp_path,value)
    ids={name:value['containers'][name]['id'] for name in NAMES}
    live={name:{'Id':ids[name], 'Image':'sha256:'+'a'*64,
                'State':{'Status':'exited','ExitCode':0}} for name in NAMES}
    live['porota_critical_approval_rc6']={
        'State':{'Status':'running','Health':{'Status':'healthy'},'OOMKilled':False}}
    def archive(root, *, admission, expected_owned_ids):
        assert expected_owned_ids==ids
        return {'state':'QUARANTINED_FAILED','old_cut_revoked':True,
                'checked':admission()['status']}
    with (patch.object(guard,'ppi_units',return_value={}),
          patch.object(guard.drain,'exact_rescue_receipt'),
          patch.object(guard.drain,'exact_clean_stopped'),
          patch.object(guard.drain,'admit',return_value='WAITING_MARKET' if changed else 'STOPPED') as admit,
          patch.object(guard.drain,'inspect',side_effect=lambda name:live[name]),
          patch.object(guard.cold,'envelope',return_value={
              'status':'FAIL_CLOSED','real_routes':'NOT_CALLED'}),
          patch.object(guard.cold,'quarantine',side_effect=archive)):
        if changed:
            with pytest.raises(ValueError,match='COLD2_RESCUE_A92_STOPPED_REQUIRED'):
                guard.apply(tmp_path,CANDIDATE)
        else:
            assert guard.apply(tmp_path,CANDIDATE)['original']['checked']=='DRAINED'
        assert admit.call_args.kwargs['allow_degraded_closed_rescue'] is True
        assert admit.call_args.kwargs['stopped_receipt']==value


@pytest.mark.parametrize('change', [None,'origin','parent','book','docker','ppi'])
def test_a169_stopped_boot_calendar_archive_requires_exact_held_paper_proof(
        tmp_path,change):
    value=receipt()
    parent={'a143_previous_proof':{'source_sha':guard.drain.A143_PENDING_SOURCE_SHA},
            'containers':value['containers'],'stop_evidence':value['stop_evidence'],
            'stop_attempted':list(NAMES)}
    value.update(a143_pending_adopted=True,
                 a169_origin_candidate=guard.drain.A169_PRIOR_CANDIDATE,
                 a169_origin_sha256=guard.drain.A169_PRIOR_RECEIPT_SHA256,
                 a170_origin_candidate=guard.drain.A170_FAILED_CANDIDATE,
                 a170_origin_sha256=guard.drain.A170_FAILED_RECEIPT_SHA256,
                 a143_previous_proof=parent['a143_previous_proof'],
                 stop_attempted=parent['stop_attempted'],
                 candidate_image_intent={'image_id':'sha256:'+'f'*64},
                 market_open_suspended=False,open_paper_carryover=False,
                 intraday_caucion=False,paused_postclose=False,
                 pending_caucion=False,spot_carryover=None,caucion_carryover=None)
    folder(tmp_path,value)
    ids={name:value['containers'][name]['id'] for name in NAMES}
    live={name:{'Id':ids[name], 'Image':'sha256:'+'a'*64,
                'State':{'Status':'exited','ExitCode':0}} for name in NAMES}
    live['porota_critical_approval_rc6']={
        'State':{'Status':'running','Health':{'Status':'healthy'},'OOMKilled':False}}
    units=[{'ppi-watch.service':('active','enabled')}]
    book=[{'process_state':'STOPPED','session_state':'BOOT_CALENDAR',
           'source_sha':guard.drain.A143_PENDING_SOURCE_SHA,'real_orders_sent':0}]
    observed=[parent]
    callbacks=[]
    receipt_a151=lambda:('e'*64,'A151-40686116017-1')
    timer_disabled=lambda:True
    def check_prior(candidate,**kwargs):
        assert kwargs == {'operator_a151_proof':receipt_a151,
                          'operator_timer_disabled':timer_disabled}
        callbacks.append('prior')
        return observed[0],guard.drain.A169_PRIOR_RECEIPT_SHA256,book[0]
    def check_stopped(row,**kwargs):
        assert kwargs == {'operator_a151_proof':receipt_a151,
                          'operator_timer_disabled':timer_disabled}
        callbacks.append('stopped')
        return book[0]
    if change=='origin': value['a169_origin_sha256']='f'*64
    elif change=='parent': observed[0]=dict(parent,containers={})
    elif change=='book': book[0]=dict(book[0],real_orders_sent=1)
    elif change=='docker': live[NAMES[0]]['Id']='f'*64
    elif change=='ppi': pass
    if change=='origin':
        (tmp_path/'data/deploy'/('quiescence-'+CANDIDATE+'.json')).write_text(json.dumps(value))
    def archive(root,*,admission,expected_owned_ids):
        if change=='ppi': units[0]={'ppi-watch.service':('inactive','enabled')}
        row=admission()
        assert row['market_closed'] is False
        assert row['market_open_suspended'] is True and row['no_positions'] is True
        from scripts import rc6_cold2_quarantine as quarantine
        quarantine._admit(tmp_path,lambda:row,expected_owned_ids)
        return {'state':'QUARANTINED_FAILED','old_cut_revoked':True}
    with (patch.object(guard,'ppi_units',side_effect=lambda:units[0]),
          patch.object(guard.drain,'candidate_operator_module',return_value=SimpleNamespace(
              exact_a151_operator_preopen_pause=receipt_a151,
              operator_preopen_timer_disabled=timer_disabled)),
          patch.object(guard.drain,'a169_prior_a143_stopped_proof',side_effect=check_prior),
          patch.object(guard.drain,'a143_stopped_continuation',side_effect=check_stopped),
          patch.object(guard.drain,'admit',side_effect=AssertionError('generic CLOSED forbidden')),
          patch.object(guard.drain,'inspect',side_effect=lambda name:live[name]),
          patch.object(guard.cold,'envelope',return_value={
              'status':'FAIL_CLOSED','real_routes':'NOT_CALLED'}),
          patch.object(guard.cold,'quarantine',side_effect=archive)):
        if change is None:
            assert guard.apply(tmp_path,CANDIDATE)['old_cut_revoked'] is True
            assert callbacks.count('prior')>=1 and callbacks.count('stopped')>=1
        else:
            with pytest.raises(ValueError,match='COLD2_'):
                guard.apply(tmp_path,CANDIDATE)


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
          -- A207 requires the mandatory family ledger to be inspectable.
          -- This synthetic COLD2 fixture intentionally has no live FCI.
          CREATE TABLE paper_family_lifecycle (family TEXT, state TEXT);
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


def test_intraday_cold2_rechecks_exact_paper_book_at_archive_boundary(tmp_path):
    value = receipt()
    carry = {'open_count':4, 'paper_ids':['a','b','c','d'],
             'sha256':'c'*64}
    value.update(market_open_suspended=True, open_paper_carryover=True,
                 spot_carryover=carry)
    folder(tmp_path, value)
    ids = {name:value['containers'][name]['id'] for name in NAMES}
    live = {name:{'Id':ids[name], 'Image':'sha256:'+'a'*64,
                  'State':{'Status':'exited','ExitCode':0}} for name in NAMES}
    live['porota_critical_approval_rc6'] = {
        'State':{'Status':'running','Health':{'Status':'healthy'},
                 'OOMKilled':False}}
    current = [carry.copy()]
    def archive(root, *, admission, expected_owned_ids):
        state = admission()
        assert state['market_open_suspended'] is True
        assert state['spot_carryover'] == carry
        current[0] = dict(carry, sha256='d'*64)
        with pytest.raises(ValueError, match='COLD2_INTRADAY_CARRYOVER_CHANGED'):
            admission()
        current[0] = carry.copy()
        return {'state':'QUARANTINED_FAILED'}
    with (patch.object(guard, 'ppi_units', return_value={}),
          patch.object(guard.drain, 'exact_clean_stopped'),
          patch.object(guard.drain, 'admit', return_value='RUNNING') as admit,
          patch.object(guard.drain, 'spot_carryover_snapshot', side_effect=lambda:current[0]),
          patch.object(guard.drain, 'inspect', side_effect=lambda name:live[name]),
          patch.object(guard.cold, 'envelope', return_value={
              'status':'FAIL_CLOSED','real_routes':'NOT_CALLED'}),
          patch.object(guard.cold, 'quarantine', side_effect=archive)):
        assert guard.apply(tmp_path, CANDIDATE)['old_cut_revoked'] is True
        assert admit.call_args.kwargs['stopped_receipt'] == value


def test_closed_pending_caucion_rechecks_audited_receipt_at_archive_boundary(tmp_path):
    value=receipt()
    carry={'open_count':1,'maturity_at':'2026-10-23T17:00:00-03:00',
           'allocation_reconciled':True,'sha256':'a'*64,
           'audit_source_sha256':
               'b1d711899a5c85f4efc72e2632a9d81cdddf0a6dc71dee82d5c42923b5639865'}
    value.update(pending_caucion=True,market_open_suspended=False,
                 open_paper_carryover=False,intraday_caucion=False,
                 paused_postclose=False,spot_carryover=None,caucion_carryover=carry)
    folder(tmp_path,value)
    ids={name:value['containers'][name]['id'] for name in NAMES}
    live={name:{'Id':ids[name],'Image':'sha256:'+'a'*64,
                'State':{'Status':'exited','ExitCode':0}} for name in NAMES}
    live['porota_critical_approval_rc6']={
        'State':{'Status':'running','Health':{'Status':'healthy'},'OOMKilled':False}}
    current=[carry.copy()]
    def archive(root,*,admission,expected_owned_ids):
        state=admission()
        assert state['market_closed'] is True and state['no_positions'] is False
        assert state['pending_caucion'] is True and state['caucion_carryover']==carry
        current[0]={**carry,'sha256':'0'*64}
        with pytest.raises(ValueError,match='COLD2_PENDING_CAUCION_CHANGED'):
            admission()
        current[0]=carry.copy()
        return {'state':'QUARANTINED_FAILED'}
    with (patch.object(guard,'ppi_units',return_value={}),
          patch.object(guard.drain,'exact_clean_stopped'),
          patch.object(guard.drain,'admit',return_value='STOPPED') as admit,
          patch.object(guard.drain,'verified_pending_caucion_snapshot',
                       side_effect=lambda:current[0]),
          patch.object(guard.drain,'inspect',side_effect=lambda name:live[name]),
          patch.object(guard.cold,'envelope',return_value={
              'status':'FAIL_CLOSED','real_routes':'NOT_CALLED'}),
          patch.object(guard.cold,'quarantine',side_effect=archive)):
        assert guard.apply(tmp_path,CANDIDATE)['old_cut_revoked'] is True
        assert admit.call_args.kwargs['allow_pending_caucion'] is True


def test_open_flat_cold2_uses_exact_stop_and_empty_paper_book(tmp_path):
    value = receipt()
    value.update(market_open_suspended=True, open_paper_carryover=False,
                 intraday_caucion=False, paused_postclose=False,
                 spot_carryover=None, caucion_carryover=None)
    target = folder(tmp_path, value)
    ids = {name:value['containers'][name]['id'] for name in NAMES}
    live = {name:{'Id':ids[name], 'Image':'sha256:'+'a'*64,
                  'State':{'Status':'exited','ExitCode':0}} for name in NAMES}
    live['porota_critical_approval_rc6'] = {
        'State':{'Status':'running','Health':{'Status':'healthy'},'OOMKilled':False}}
    reached = []
    def archive(root, *, admission, expected_owned_ids):
        state = admission()
        reached.append(state)
        assert state['market_closed'] is False and state['no_positions'] is True
        assert state['market_open_suspended'] is True
        assert state['spot_carryover'] is None
        changed = dict(value, caucion_carryover={'open_count':1})
        (target/('quiescence-'+CANDIDATE+'.json')).write_text(json.dumps(changed))
        with pytest.raises(ValueError, match='COLD2_QUIESCENCE_RECEIPT_CHANGED'):
            admission()
        (target/('quiescence-'+CANDIDATE+'.json')).write_text(json.dumps(value))
        return {'state':'QUARANTINED_FAILED'}
    with (patch.object(guard, 'ppi_units', return_value={}),
          patch.object(guard.drain, 'exact_clean_stopped') as exact,
          patch.object(guard.drain, 'admit', return_value='RUNNING') as admit,
          patch.object(guard.drain, 'inspect', side_effect=lambda name:live[name]),
          patch.object(guard.cold, 'envelope', return_value={
              'status':'FAIL_CLOSED','real_routes':'NOT_CALLED'}),
          patch.object(guard.cold, 'quarantine', side_effect=archive)):
        assert guard.apply(tmp_path, CANDIDATE)['old_cut_revoked'] is True
        assert len(reached) == 1
        exact.assert_called()
        assert admit.call_args.kwargs['allow_market_open_suspend'] is True
        assert admit.call_args.kwargs['stopped_receipt'] == value


def test_intraday_cold2_rechecks_one_caucion_without_spots_at_each_boundary(tmp_path):
    value = receipt()
    caucion = {'open_count': 1, 'sha256': 'd'*64,
               'maturity_at': '2026-10-21T17:00:00-03:00'}
    value.update(market_open_suspended=True, open_paper_carryover=False,
                 intraday_caucion=True, paused_postclose=False,
                 spot_carryover=None, caucion_carryover=caucion)
    folder(tmp_path, value)
    ids = {name:value['containers'][name]['id'] for name in NAMES}
    live = {name:{'Id':ids[name], 'Image':'sha256:'+'a'*64,
                  'State':{'Status':'exited','ExitCode':0}} for name in NAMES}
    live['porota_critical_approval_rc6'] = {
        'State':{'Status':'running','Health':{'Status':'healthy'},'OOMKilled':False}}
    observed = [caucion]
    def archive(root, *, admission, expected_owned_ids):
        state = admission()
        assert state['intraday_caucion'] is True
        assert state['spot_carryover'] is None
        assert state['caucion_carryover'] == caucion
        observed[0] = dict(caucion, sha256='e'*64)
        with pytest.raises(ValueError, match='COLD2_INTRADAY_CAUCION_CHANGED'):
            admission()
        observed[0] = caucion
        return {'state':'QUARANTINED_FAILED'}
    with (patch.object(guard, 'ppi_units', return_value={}),
          patch.object(guard.drain, 'exact_clean_stopped'),
          patch.object(guard.drain, 'admit', return_value='RUNNING') as admit,
          patch.object(guard.drain, 'pending_caucion_snapshot',
                       side_effect=lambda:observed[0]),
          patch.object(guard.drain, 'inspect', side_effect=lambda name:live[name]),
          patch.object(guard.cold, 'envelope', return_value={
              'status':'FAIL_CLOSED','real_routes':'NOT_CALLED'}),
          patch.object(guard.cold, 'quarantine', side_effect=archive)):
        assert guard.apply(tmp_path, CANDIDATE)['old_cut_revoked'] is True
        assert admit.call_args.kwargs['allow_intraday_caucion'] is True
        assert admit.call_args.kwargs['stopped_receipt'] == value


def test_cold2_quarantine_requires_exact_intraday_caucion_witness(tmp_path):
    from scripts import rc6_cold2_quarantine as cold
    ids = {name:hex(i+1)[2:]*64 for i,name in enumerate(NAMES)}
    caucion = {'open_count':1,'sha256':'d'*64,
               'maturity_at':'2026-10-21T17:00:00-03:00'}
    state = {'status':'DRAINED','owned_stopped':True,
             'market_closed':False,'no_positions':False,
             'market_open_suspended':True,'open_paper_carryover':False,
             'intraday_caucion':True,'spot_carryover':None,
             'caucion_carryover':caucion,'lock_held':True,
             'mode':'PRODUCTION_PAPER','real_orders_sent':0,
             'real_routes':'NOT_CALLED','order_routing_blocked':True,
             'inflight_absent':True,'critical_healthy':True,
             'owned_ids':ids,'ppi_watch_accessed':False}
    cold._admit(tmp_path, lambda:state, ids)
    state['caucion_carryover'] = dict(caucion, sha256='invalid')
    with pytest.raises(ValueError, match='COLD2_QUIESCENCE_REQUIRED'):
        cold._admit(tmp_path, lambda:state, ids)
    state['caucion_carryover'] = caucion
    state['open_paper_carryover'] = True
    with pytest.raises(ValueError, match='COLD2_QUIESCENCE_REQUIRED'):
        cold._admit(tmp_path, lambda:state, ids)


def test_cold2_quarantine_accepts_only_open_flat_with_no_carryover(tmp_path):
    from scripts import rc6_cold2_quarantine as cold
    ids = {name:hex(i+1)[2:]*64 for i,name in enumerate(NAMES)}
    state = {'status':'DRAINED','owned_stopped':True,
             'market_closed':False,'no_positions':True,
             'market_open_suspended':True,'open_paper_carryover':False,
             'intraday_caucion':False,'spot_carryover':None,
             'caucion_carryover':None,'lock_held':True,
             'mode':'PRODUCTION_PAPER','real_orders_sent':0,
             'real_routes':'NOT_CALLED','order_routing_blocked':True,
             'inflight_absent':True,'critical_healthy':True,
             'owned_ids':ids,'ppi_watch_accessed':False}
    cold._admit(tmp_path, lambda:state, ids)
    for change in ({'no_positions':False}, {'open_paper_carryover':True},
                   {'caucion_carryover':{'open_count':1}}, {'real_orders_sent':1}):
        changed = dict(state, **change)
        with pytest.raises(ValueError, match='COLD2_QUIESCENCE_REQUIRED'):
            cold._admit(tmp_path, lambda:changed, ids)
def test_postclose_cold2_rejects_caucion_drift_before_archive(tmp_path):
    value=receipt()
    carry={'open_count':2,'paper_ids':['a','b'],'sha256':'c'*64}
    caucion={'open_count':1,'sha256':'d'*64,'maturity_at':'2026-10-21T17:00:00-03:00'}
    value.update(market_open_suspended=True,open_paper_carryover=True,
                 paused_postclose=True,spot_carryover=carry,caucion_carryover=caucion)
    folder(tmp_path,value)
    ids={name:value['containers'][name]['id'] for name in NAMES}
    live={name:{'Id':ids[name],'Image':'sha256:'+'a'*64,
                'State':{'Status':'exited','ExitCode':0}} for name in NAMES}
    live['porota_critical_approval_rc6']={'State':{'Status':'running',
        'Health':{'Status':'healthy'},'OOMKilled':False}}
    observed=[caucion]
    def archive(root,*,admission,expected_owned_ids):
        assert admission()['status']=='DRAINED'
        observed[0]=dict(caucion,sha256='e'*64)
        with pytest.raises(ValueError,match='COLD2_POSTCLOSE_CAUCION_CHANGED'):
            admission()
        observed[0]=caucion
        return {'state':'QUARANTINED_FAILED'}
    with (patch.object(guard,'ppi_units',return_value={}),
          patch.object(guard.drain,'exact_clean_stopped'),
          patch.object(guard.drain,'admit',return_value='RUNNING'),
          patch.object(guard.drain,'spot_carryover_snapshot',return_value=carry),
          patch.object(guard.drain,'pending_caucion_snapshot',side_effect=lambda:observed[0]),
          patch.object(guard.drain,'inspect',side_effect=lambda name:live[name]),
          patch.object(guard.cold,'envelope',return_value={'status':'FAIL_CLOSED',
              'real_routes':'NOT_CALLED'}),
          patch.object(guard.cold,'quarantine',side_effect=archive)):
        assert guard.apply(tmp_path,CANDIDATE)['old_cut_revoked'] is True


def test_archived_cold2_accepts_only_complete_successor_and_exact_prior_receipt(tmp_path):
    value=receipt()
    carry={'open_count':2,'paper_ids':['a','b'],'sha256':'c'*64}
    caucion={'open_count':1,'sha256':'d'*64}
    value.update(market_open_suspended=True,open_paper_carryover=True,
                 paused_postclose=True,spot_carryover=carry,caucion_carryover=caucion)
    deploy=folder(tmp_path,value)
    shadow=tmp_path/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    source=shadow/'cold-audit';source.mkdir(parents=True)
    (source/'queue.json').write_text('bounded fixture')
    archive=deploy/('rc6-failed-cold-'+guard.cold.IDENT);archive.mkdir()
    (archive/'quarantine.json').write_text('bounded fixture')
    prior_path=deploy/('rc6-cold-fix-forward-'+guard.archive_fix.PRIOR_ARCHIVE_SHA+'.json')
    prior_path.write_text(json.dumps({'state':'ARCHIVED_OLD_CUT_STILL_REVOKED'}))
    fix_path=deploy/('rc6-cold-fix-forward-'+CANDIDATE+'.json')
    fix={'state':'ARCHIVED_OLD_CUT_STILL_REVOKED','candidate_sha':CANDIDATE,
         'archived_recovery':True,'adopted_prior_receipt_sha256':hashlib.sha256(prior_path.read_bytes()).hexdigest(),
         'revoked_cut':guard.archive_fix.CUT,'snapshot_id':guard.archive_fix.IDENT,
         'old_cut_gc_complete':True,'current_cut':'f'*64}
    fix_path.write_text(json.dumps(fix))
    ids={name:value['containers'][name]['id'] for name in NAMES}
    live={name:{'Id':ids[name],'Image':'sha256:'+'a'*64,
                'State':{'Status':'exited','ExitCode':0}} for name in NAMES}
    live['porota_critical_approval_rc6']={'State':{'Status':'running',
        'Health':{'Status':'healthy'},'OOMKilled':False}}
    complete={'state':'COMPLETE_UNTOUCHED','snapshot_id':'e'*32,'old_cut_revoked':True}
    def read(path,*args,**kwargs):
        if Path(path).name=='CURRENT.json':return {'generation_id':'f'*64}
        if Path(path).name=='queue.json':return {'state':'COMPLETE','snapshot_id':'e'*32}
        return {'status':'SHADOW_OBSERVING','real_routes':'NOT_CALLED','real_orders_sent':0}
    with (patch.object(guard,'ppi_units',return_value={}),
          patch.object(guard.drain,'exact_clean_stopped'),
          patch.object(guard.drain,'admit',return_value='RUNNING'),
          patch.object(guard.drain,'spot_carryover_snapshot',return_value=carry),
          patch.object(guard.drain,'pending_caucion_snapshot',return_value=caucion),
          patch.object(guard.drain,'inspect',side_effect=lambda name:live[name]),
          patch.object(guard.cold,'paths',return_value=(shadow,source,archive)),
          patch.object(guard.cold,'verify_original_archive',return_value={'state':'ALREADY_QUARANTINED'}),
          patch.object(guard,'verify_complete_queue',return_value=complete),
          patch.object(guard.cold,'envelope',side_effect=read)):
        assert guard.apply(tmp_path,CANDIDATE)['successor']==complete
        fix['adopted_prior_receipt_sha256']='0'*64
        fix_path.write_text(json.dumps(fix))
        with pytest.raises(ValueError,match='COLD2_ARCHIVED_RECOVERY_CHANGED'):
            guard.apply(tmp_path,CANDIDATE)
        fix['adopted_prior_receipt_sha256']=hashlib.sha256(prior_path.read_bytes()).hexdigest()
        fix_path.write_text(json.dumps(fix))
        with patch.object(guard,'verify_complete_queue',return_value={'state':'NO_ACTIVE_FAILED_SUCCESSOR'}):
            with pytest.raises(ValueError,match='COLD2_SHADOW_SAFETY_CHANGED'):
                guard.apply(tmp_path,CANDIDATE)


def test_intraday_cold2_rejects_incomplete_carryover_admission(tmp_path):
    from scripts import rc6_cold2_quarantine as cold
    ids = {name:hex(i+1)[2:]*64 for i,name in enumerate(NAMES)}
    state = {'status':'DRAINED', 'owned_stopped':True,
             'market_closed':False, 'no_positions':False,
             'market_open_suspended':True, 'open_paper_carryover':True,
             'spot_carryover':{'open_count':4, 'paper_ids':['a'], 'sha256':'c'*64},
             'lock_held':True, 'mode':'PRODUCTION_PAPER',
             'real_orders_sent':0, 'real_routes':'NOT_CALLED',
             'order_routing_blocked':True, 'inflight_absent':True,
             'critical_healthy':True, 'owned_ids':ids, 'ppi_watch_accessed':False}
    with pytest.raises(ValueError, match='COLD2_QUIESCENCE_REQUIRED'):
        cold._admit(tmp_path, lambda:state, ids)
    state['spot_carryover']['paper_ids'] = ['a','b','c','d']
    cold._admit(tmp_path, lambda:state, ids)
