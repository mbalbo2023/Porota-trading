"""Private detail materialization preserves every causal output and failure."""
import copy
from decimal import Decimal

import pytest

from rc6_shadow_runtime import funnel as f
from tests.test_rc6_shadow_runtime_wiring import PRE


def checkpoint():
    return {'started_at':PRE.isoformat(),'event_keys':{},'reaches':{},'cohorts':{},
            'details':[],'events_recorded':0,'gaps':{}}


def planner(count, detail='plain-ascii'):
    return {'session':PRE.date().isoformat(),'engines':{'SCALPING':{
        'preopen_digest':detail,'configuration_fingerprint':'fixed-config',
        'telemetry':[{'identity':[f'SYMBOL{i:05d}','ACCIONES','BYMA','ARS','A-24HS'],
            'pipeline':{'CATALOG_READY':True},'strategy':'SCALPING_BASELINE',
            'rejection_reason':['SOURCE_UNAVAILABLE']} for i in range(count)]}},
        'catalog_ready':[],'family_routing':{'instruments':[],'families':[]}}


def counts(monkeypatch):
    calls={'row_hash':0,'detail_roundtrip':0};digest,normalize=f.digest,f._json
    def hashed(value):
        if isinstance(value,dict) and 'stage' in value and 'entry_authority' in value:
            calls['row_hash']+=1
        return digest(value)
    def normalized(value):
        calls['detail_roundtrip']+=1
        return normalize(value)
    monkeypatch.setattr(f,'digest',hashed);monkeypatch.setattr(f,'_json',normalized)
    return calls


def test_owned_batch_materializes_only_retained_detail_hashes_and_same_full_state(monkeypatch):
    n=f.MAX_DETAILS+37;ordinary,owned=checkpoint(),checkpoint();report=planner(n)
    expected=f._planner_stages(ordinary,copy.deepcopy(report),PRE,consume=False)
    calls=counts(monkeypatch)
    actual=f._planner_stages(owned,copy.deepcopy(report),PRE,consume=True)
    assert actual==expected and owned==ordinary
    assert owned['events_recorded']==n and len(owned['details'])==f.MAX_DETAILS
    assert calls=={'row_hash':f.MAX_DETAILS,'detail_roundtrip':0}
    assert all(type(row) is dict for row in owned['details'])
    for row in owned['details']:
        raw={k:v for k,v in row.items() if k!='evidence_sha256'}
        assert row['evidence_sha256']==f.digest(raw)


@pytest.mark.parametrize('detail',['á non-ascii',Decimal('1.25'),{'nested':['original']},2**200,1.25])
def test_nonfast_detail_keeps_original_normalization_hashing_and_independence(monkeypatch,detail):
    n=f.MAX_DETAILS+1;ordinary,owned=checkpoint(),checkpoint();source=planner(n,detail)
    expected=f._planner_stages(ordinary,copy.deepcopy(source),PRE,consume=False)
    calls=counts(monkeypatch)
    actual=f._planner_stages(owned,source,PRE,consume=True)
    assert actual==expected and owned==ordinary
    assert calls=={'row_hash':n,'detail_roundtrip':n}
    if isinstance(detail,dict):
        before=copy.deepcopy(owned['details']);detail['nested'].append('later mutation')
        assert owned['details']==before


@pytest.mark.parametrize('detail',[float('nan'),float('inf'),float('-inf'),'\ud800',pytest.param(1<<20000,id='huge-int')])
def test_evicted_or_retained_bad_detail_still_raises_same_error_and_partial_state(detail):
    ordinary,owned=checkpoint(),checkpoint();report=planner(f.MAX_DETAILS+1,detail)
    with pytest.raises((ValueError,UnicodeError)) as old:
        f._planner_stages(ordinary,copy.deepcopy(report),PRE,consume=False)
    with pytest.raises(type(old.value),match=__import__('re').escape(str(old.value))):
        f._planner_stages(owned,copy.deepcopy(report),PRE,consume=True)
    assert ordinary==owned and ordinary['events_recorded']==0


def test_owned_finish_does_not_repair_prior_unsigned_details():
    ordinary,owned=checkpoint(),checkpoint()
    ordinary['details']=[{'prior':'unsigned-historical-value'}];owned['details']=copy.deepcopy(ordinary['details'])
    report=planner(2)
    assert f._planner_stages(owned,copy.deepcopy(report),PRE,consume=True)==f._planner_stages(ordinary,report,PRE,consume=False)
    assert owned==ordinary and owned['details'][0]=={'prior':'unsigned-historical-value'}


def test_factual_and_public_event_detail_hashing_remains_immediate(monkeypatch):
    calls=counts(monkeypatch);state=checkpoint()
    assert f._event(state,ident=('A','ACCIONES','A-24HS','ARS','BYMA'),strategy='fixed',stage='CATALOG_READY',
        event_at=PRE,as_of=PRE,event_id='one',channel='NATIVE_FACTUAL',clock_basis='actual',detail={'original':'value'})
    assert calls=={'row_hash':1,'detail_roundtrip':1}
    row=state['details'][0];assert type(row) is dict and row['evidence_sha256']==f.digest({k:v for k,v in row.items() if k!='evidence_sha256'})


def test_duplicate_owned_events_preserve_all_original_noop_semantics(monkeypatch):
    report=planner(3);reference,owned=checkpoint(),checkpoint()
    f._planner_stages(reference,copy.deepcopy(report),PRE,consume=False)
    f._planner_stages(owned,copy.deepcopy(report),PRE,consume=True)
    calls=counts(monkeypatch)
    expected=f._planner_stages(reference,copy.deepcopy(report),PRE,consume=False)
    actual=f._planner_stages(owned,copy.deepcopy(report),PRE,consume=True)
    assert actual==expected and owned==reference and calls=={'row_hash':0,'detail_roundtrip':0}


@pytest.mark.parametrize('bad',[float('nan'),'\ud800'])
def test_later_error_finalizes_only_current_call_rows_and_same_partial_state(bad):
    ordinary,owned=checkpoint(),checkpoint();report=planner(3)
    report['engines']['OTHER']={'telemetry':[{'identity':['LAST','ACCIONES','BYMA','ARS','A-24HS'],
        'pipeline':{'CATALOG_READY':True},'rejection_reason':[],'strategy':bad}]}
    with pytest.raises((ValueError,UnicodeError)) as old:
        f._planner_stages(ordinary,copy.deepcopy(report),PRE,consume=False)
    with pytest.raises(type(old.value),match=__import__('re').escape(str(old.value))):
        f._planner_stages(owned,copy.deepcopy(report),PRE,consume=True)
    assert owned==ordinary and owned['events_recorded']==3
    assert all(type(row)is dict and 'evidence_sha256' in row for row in owned['details'])


def test_private_hash_owner_does_not_finalize_another_call_or_existing_row():
    owner,other=object(),object();state=checkpoint()
    old=f._OwnedEventDetail({'prior':'unsigned'},other)
    state['details']=[old]
    f._finish_owned_details(state,owner)
    assert state['details'][0] is old and old=={'prior':'unsigned'}


class ChangingText:
    def __init__(self):
        self.calls=0
    def __str__(self):
        self.calls+=1
        return 'actual-'+str(self.calls)


def test_custom_scalar_runs_original_normalization_for_every_event():
    old_value,new_value=ChangingText(),ChangingText();ordinary,owned=checkpoint(),checkpoint()
    count=f.MAX_DETAILS+2
    expected=f._planner_stages(ordinary,planner(count,old_value),PRE,consume=False)
    actual=f._planner_stages(owned,planner(count,new_value),PRE,consume=True)
    assert actual==expected and ordinary==owned and old_value.calls==new_value.calls==count
