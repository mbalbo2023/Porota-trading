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


@pytest.fixture
def pinned_running_origin(monkeypatch,tmp_path):
    now=datetime.now(timezone.utc).isoformat()
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
