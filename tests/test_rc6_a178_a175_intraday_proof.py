"""Run the real A175 inherited-origin validator against open/closed PAPER evidence.

Mock only host boundaries. The mode checks, receipt comparisons, epoch and
NO_GO binding are production source, not monkeypatched assertions.
"""
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import rc6_deploy_quiescence as q


CANDIDATE='d'*40


class IncidentClock(datetime):
    @classmethod
    def now(cls,tz=None):
        value=datetime(2026,10,29,15,0,tzinfo=timezone.utc)
        return value.replace(tzinfo=None) if tz is None else value.astimezone(tz)


@pytest.fixture
def pinned_running_origin(monkeypatch,tmp_path):
    # Replay the date-scoped incident independently of the CI execution date.
    # Production expiry logic is exercised unchanged, including below at ART
    # midnight, rather than extending the historical operator authorization.
    monkeypatch.setattr(q,'datetime',IncidentClock)
    now=IncidentClock.now(timezone.utc).isoformat()
    lifecycle={'Status':'running','ExitCode':0,'OOMKilled':False,
               'StartedAt':'2026-10-29T09:00:00Z','FinishedAt':'2026-10-29T08:50:00Z'}
    originals={name:{'Id':q.A175_RUNNING_IDS[name],
                     'Image':q.A175_LOADED_IMAGE_ID,
                     'State':{'Status':'running',**lifecycle}}
               for name in q.OWNED}
    frozen={'candidate_sha':q.A175_FAILED_CANDIDATE,
            'candidate_tree_sha':q.A175_CANDIDATE_TREE_SHA,
            'image_tar_sha256':'7'*64}
    frozen_raw=json.dumps(frozen).encode()
    frozen_sha=hashlib.sha256(frozen_raw).hexdigest()
    marker={'candidate_sha':q.A175_FAILED_CANDIDATE,
            'deploy_sha':q.A175_DEPLOY_SHA,'image_id':q.A175_LOADED_IMAGE_ID,
            'recorded_at':now,'validation_status':'DEPLOYED_VALIDATION_PENDING',
            'ppi_watch_untouched':True}
    marker_sha=hashlib.sha256(json.dumps(marker).encode()).hexdigest()
    prior={'candidate_sha':q.A175_FAILED_CANDIDATE,
           'status':'FIX_FORWARD_VALIDATION_REQUIRED',
           'installation_started':True,'a174_pending_adopted':True,
           'a174_origin_candidate':q.A174_FAILED_CANDIDATE,
           'a174_origin_sha256':q.A174_FAILED_RECEIPT_SHA256,
           'inflight':'ABSENT_OWNED_RUNTIME_STOPPED',
           'runtime_health':'NEW_A175_CANDIDATE_NOT_GO',
           'market_open_suspended':False,
           'open_paper_carryover':False,'paused_postclose':False,
           'intraday_caucion':False,'pending_caucion':False,
           'spot_carryover':None,'caucion_carryover':None,
           'real_orders_sent':0,'ppi_watch_accessed':False,
           'a174_previous_proof':{'origin_candidate':q.A174_FAILED_CANDIDATE,
                                  'origin_sha256':q.A174_FAILED_RECEIPT_SHA256,
                                  'operator_receipt_sha256':'a'*64},
           'candidate_image_intent':{
               'frozen_manifest_b64':base64.b64encode(frozen_raw).decode(),
               'recorded_at':now,'manifest_sha256':frozen_sha,
               'candidate_tree_sha':q.A175_CANDIDATE_TREE_SHA,
               'image_tar_sha256':frozen['image_tar_sha256'],
               'image_id':q.A175_LOADED_IMAGE_ID},
           'candidate_runtime_after_failure':{
               'status':'running','flat_book':{
                   'source_sha':q.A175_FAILED_CANDIDATE,'real_orders_sent':0,
                   'process_state':'WAITING_MARKET','session_state':'MARKET_CLOSED'},
               'critical_admission':{'capability':'issues_only'},
               'containers':{name:{'id':v['Id'],'image':v['Image'],
                                    'lifecycle':q.lifecycle(v)}
                             for name,v in originals.items()}}}
    monkeypatch.setattr(q,'A175_FROZEN_SHA256',frozen_sha)
    monkeypatch.setattr(q,'A175_MARKER_SHA256',marker_sha)
    monkeypatch.setattr(q,'current_runtime_marker',lambda:marker)
    monkeypatch.setattr(q,'inspect',lambda name:originals[name])
    monkeypatch.setattr(q,'critical_admission',lambda:{'capability':'issues_only'})
    monkeypatch.setattr(q,'A175_FAILED_RECEIPT_SHA256','b'*64)
    def exact_file(path,bound):
        match=Path(path).name
        if match=='porota-frozen-candidate.json':return frozen,frozen_sha
        if match=='CURRENT_STATE_V2.json':return marker,marker_sha
        if match=='quiescence-'+q.A175_FAILED_CANDIDATE+'.json':
            return prior,q.A175_FAILED_RECEIPT_SHA256
        raise AssertionError(match)
    monkeypatch.setattr(q,'_a143_exact_json',exact_file)
    book={'source_sha':q.A175_FAILED_CANDIDATE,'real_orders_sent':0,
          'process_state':'RUNNING','session_state':'MARKET_OPEN'}
    monkeypatch.setattr(q,'rescue_flat_book',lambda:dict(book))
    admits=[]
    def admit(**kwargs):
        admits.append(kwargs)
        if kwargs.get('allow_market_open_suspend'):
            return 'RUNNING'
        raise ValueError('QUIESCENCE_CLOSED_REQUIRED')
    monkeypatch.setattr(q,'admit',admit)
    current={'status':'NO_GO_PENDING_EXPLICIT_REARM',
             'business_day_art':'2026-10-29','paper_execution_authorized':False,
             'a151_origin_sha256':'a'*64,
             'a175_quiescence_sha256':q.A175_FAILED_RECEIPT_SHA256,
             'a175_scheduler_sha256':q.A175_SCHEDULER_RECEIPT_SHA256,
             'receipt_sha256':q.A176_OPERATOR_NO_GO_RECEIPT_SHA256,
             'operation_id':'A176-40875630811-1'}
    monkeypatch.setattr(q,'ROOT',tmp_path)
    return prior,book,current,admits,originals


def _prove(pinned,*,open_mode=True):
    _,_,current,_,_=pinned
    return q.a175_failed_running_proof(
        CANDIDATE,allow_intraday_a175=open_mode,
        operator_a151_proof=lambda:('a'*64,'A151-40686116017-1'),
        operator_timer_disabled=lambda:True,
        operator_current_no_go_proof=lambda:current)


def test_real_a175_open_origin_accepts_explicit_paper_flat_intraday(pinned_running_origin):
    _,book,current,admits,_=pinned_running_origin
    proof=_prove(pinned_running_origin)
    assert proof['intraday_a175'] is True
    assert proof['flat_book']==book
    assert proof['origin_sha256']==q.A175_FAILED_RECEIPT_SHA256
    assert proof['operator_receipt_sha256']==current['receipt_sha256']
    assert any(row.get('allow_market_open_suspend') is True for row in admits)


@pytest.mark.parametrize('tamper,value',[
    ('real_orders_sent',1),
    ('process_state','DEGRADED'),
    ('session_state','MARKET_CLOSED'),
    ('source_sha','0'*40),
])
def test_real_a175_open_origin_rejects_changed_book(
        pinned_running_origin,tamper,value):
    _,book,_,_,_=pinned_running_origin
    book[tamper]=value
    with pytest.raises(ValueError,match='QUIESCENCE_A175_RUNNING_BOOK_CHANGED'):
        _prove(pinned_running_origin)


def test_real_a175_open_origin_rejects_closed_selected_mode(pinned_running_origin):
    with pytest.raises(ValueError,match='QUIESCENCE_A175_RUNNING_BOOK_CHANGED'):
        _prove(pinned_running_origin,open_mode=False)


def test_real_a175_open_origin_rejects_missing_daily_no_go(pinned_running_origin):
    _,_,current,_,_=pinned_running_origin
    current['paper_execution_authorized']=True
    with pytest.raises(ValueError,match='QUIESCENCE_A175_NO_GO_HOLD_CHANGED'):
        _prove(pinned_running_origin)


def test_real_a175_next_art_day_rechecks_historical_anchor_and_live_timer(
        pinned_running_origin,monkeypatch):
    class NextDayClock(datetime):
        @classmethod
        def now(cls,tz=None):
            value=datetime(2026,10,30,3,0,tzinfo=timezone.utc)
            return value.replace(tzinfo=None) if tz is None else value.astimezone(tz)
    monkeypatch.setattr(q,'datetime',NextDayClock)
    proof=_prove(pinned_running_origin)
    assert proof['technical_24x7'] is True
    assert proof['technical_hold']==q.A175_TECHNICAL_HOLD
    assert proof['operator_hold_business_day']=='2026-10-29'
    assert proof['paper_execution_authorized'] is False
    _,_,current,_,_=pinned_running_origin
    with pytest.raises(ValueError,match='QUIESCENCE_A175_NO_GO_HOLD_CHANGED'):
        q.a175_failed_running_proof(CANDIDATE,allow_intraday_a175=True,
            operator_a151_proof=lambda:('a'*64,'A151-40686116017-1'),
            operator_timer_disabled=lambda:False,
            operator_current_no_go_proof=lambda:current)


@pytest.mark.parametrize('when',[
    datetime(2026,11,1,15,0,tzinfo=timezone.utc),  # Sunday
    datetime(2026,11,6,15,0,tzinfo=timezone.utc),  # audited BYMA holiday
])
def test_real_a175_closed_technical_recovery_sunday_holiday_and_receipt_origin(
        pinned_running_origin,monkeypatch,when):
    prior,book,current,_,originals=pinned_running_origin
    class TechnicalClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return when.replace(tzinfo=None) if tz is None else when.astimezone(tz)
    monkeypatch.setattr(q,'datetime',TechnicalClock)
    book.update(process_state='WAITING_MARKET',session_state='MARKET_CLOSED')
    monkeypatch.setattr(q,'admit',lambda **kwargs:'WAITING_MARKET'
                        if not kwargs.get('allow_market_open_suspend') else
                        (_ for _ in ()).throw(ValueError('NO_WEEKEND_OPEN')))
    hold={'timer_disabled':True}
    def prove():
        return q.a175_failed_running_proof(
            CANDIDATE,allow_intraday_a175=False,
            operator_timer_disabled=lambda:hold['timer_disabled'],
            operator_current_no_go_proof=lambda:current)
    proof=prove()
    assert proof['technical_24x7'] is True
    assert proof['intraday_a175'] is False
    assert proof['operator_hold_business_day']=='2026-10-29'
    assert proof['flat_book']['process_state']=='WAITING_MARKET'
    stopped={name:{'id':v['Id'],'image':v['Image']} for name,v in originals.items()}
    receipt={'candidate_sha':CANDIDATE,'status':'DRAINED',
             'a175_pending_adopted':True,
             'a175_origin_candidate':q.A175_FAILED_CANDIDATE,
             'a175_origin_sha256':q.A175_FAILED_RECEIPT_SHA256,
             'a175_previous_proof':proof,'market_open_suspended':False,
             'inflight':'ABSENT_OWNED_RUNTIME_STOPPED',
             'containers':stopped,'stop_attempted':list(q.OWNED),
             'stop_evidence':{name:{'before':q.lifecycle(v),
                                    'classification':'CLEAN_EXIT',
                                    'after':{'Status':'exited','ExitCode':0}}
                              for name,v in originals.items()},
             'recorded_at':when.isoformat()}
    operator=type('Operator',(),{
        'exact_a176_operator_no_go_continuation':staticmethod(
            lambda *,historical_anchor=False: current if historical_anchor else
                (_ for _ in ()).throw(ValueError('HISTORICAL_REQUIRED'))),
        'operator_preopen_timer_disabled':staticmethod(lambda:hold['timer_disabled'])})
    monkeypatch.setattr(q,'candidate_operator_module',lambda **_:operator)
    assert q.a175_receipt_origin(receipt)==prior
    current['receipt_sha256']='f'*64
    with pytest.raises(ValueError,match='A175_CURRENT_NO_GO_CHANGED'):
        q.a175_receipt_origin(receipt)
    current['receipt_sha256']=q.A176_OPERATOR_NO_GO_RECEIPT_SHA256
    hold['timer_disabled']=False
    with pytest.raises(ValueError,match='A175_CURRENT_NO_GO_CHANGED'):
        q.a175_receipt_origin(receipt)
    with pytest.raises(ValueError,match='A175_NO_GO_HOLD_CHANGED'):
        prove()
