"""Causal engine scheduling and actual runtime integration regression guards."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from rc6_dynamic_universe.common import digest, identity
from rc6_dynamic_universe.orchestrator import (EnginePolicy, UniverseOrchestrator,
    reconcile_endpoint_slots, apply_shared_allocation)
from rc6_dynamic_universe.routing import (strategy_route, dispatch_observation,
    option_observation_universe)
from rc6_dynamic_universe.runtime import read_runtime
from cf_intraday_scalping import shadow_sampling_plan
from scripts.rc6_dynamic_universe_shadow import run_shadow
from tests.test_rc6_dynamic_tradeability import instrument, daily, quotes, audited_sessions
from tests.test_rc6_ppi_capacity_benchmark import wire, measure

OPEN = datetime(2026, 10, 5, 13, 30, tzinfo=timezone.utc)
POLICY = EnginePolicy(warmup_samples=3)


def catalog(count=12):
    return [{**instrument(f'S{i}'), 'status': 'AVAILABLE', 'capability': 'READY_PAPER_SPOT'} for i in range(count)]


def capacity(at=OPEN, limit=8, config='capacity-v1'):
    return {'status': 'SHADOW_RECOMMENDATION', 'safe_limit': limit,
            'configuration_fingerprint': config, 'evidence_digest': 'test-evidence',
            'slots_by_endpoint': {'intraday': limit, 'book': limit},
            'generated_at': at.isoformat(), 'expires_at': (OPEN+timedelta(hours=6)).isoformat()}


def frozen(assets, tradeable=True):
    payload = {'cutoff': '2026-10-02T20:00:00+00:00', 'frozen_at': (OPEN-timedelta(minutes=15)).isoformat(),
               'session_open': OPEN.isoformat(), 'capacity_fingerprint': 'capacity-v1',
               'rows': [{'identity': identity(a), 'tradeable': tradeable, 'tradeability_score': 1-i*.01,
                         'rank': i+1, 'components': {}, 'reason_codes': ['LOW_ACTIVITY']} for i,a in enumerate(assets)]}
    return {'payload': payload, 'digest': digest(payload)}


def obs(asset, at, *, source='PPI_MARKETDATA_INTRADAY', endpoint='intraday', confirmed=True):
    return {'identity': identity(asset), 'source': source, 'endpoint': endpoint,
            'source_at': at.isoformat(), 'received_at': at.isoformat(), 'useful': True,
            'intraday_confirmed': confirmed, 'book_at': at.isoformat(), 'book_useful': True}


def plan(assets, at=OPEN, previous=None, observations=(), events=(), opened=(), cap=None, freeze=None, policy=POLICY, phase='OPEN'):
    return UniverseOrchestrator(assets, policy=policy).plan(at=at, session_open=OPEN,
        frozen=freeze or frozen(assets), capacity=cap or capacity(at), previous=previous,
        observations=observations, events=events, opened=opened, phase=phase)


def test_catalog_ready_kept_and_specialized_routes_never_use_equity():
    assets = catalog()+[{**instrument(family=f), 'status':'AVAILABLE','capability':'READY_PAPER_SPECIAL'}
        for f in ('BONOS','LETRAS','OBLIGACIONES','OPCIONES','FUTUROS','CAUCIONES','FCI')]
    original = deepcopy(assets)
    report = plan(assets)
    assert report['catalog_ready_count']==len(assets) and assets==original
    for row in report['telemetry'][12:]:
        assert row['state']=='EXCLUDED' and not row['pipeline']['STRATEGY_ELIGIBLE']
        assert 'SPECIALIZED_LIFECYCLE' in row['rejection_reason']
    calls=[]
    for a in assets[12:]:
        dispatch_observation(a, {'EQUITY_SPOT':lambda *args,**kwargs:calls.append(args)},as_of=OPEN)
    assert calls==[]
    selected=dispatch_observation(assets[-2], {'TREASURY':lambda *args,**kwargs:'OWNED'},as_of=OPEN)
    assert selected['handler_result']=='OWNED' and not selected['entry_authority']


def test_warmup_accumulates_json_restart_and_time_only_capacity_update():
    assets=catalog(); previous=plan(assets)
    for minute in (1,2,3):
        at=OPEN+timedelta(minutes=minute)
        previous=plan(assets,at,previous=json.loads(json.dumps(previous)),observations=[obs(assets[0],at)])
    first=previous['telemetry'][0]
    assert first['state']=='HOT' and first['signal_result']=='SAMPLES_READY'
    assert first['warmup_progress']['distinct_samples']==3 and not first['entry_authority']
    assert previous['checkpoint_reused'] and first['economics_result']=='NO_VERIFICADO'
    assert previous['discovery']['by_family_source']['ACCIONES']['sources']['PPI_MARKETDATA_INTRADAY']['fresh_useful']==1


def test_radar_observations_and_unconfirmed_intraday_cannot_replace_strategy_warmup():
    assets=catalog(); previous=plan(assets)
    for minute in (1,2,3):
        at=OPEN+timedelta(minutes=minute)
        previous=plan(assets,at,previous=previous,observations=[obs(assets[0],at,source='BYMA',endpoint='radar'),obs(assets[1],at,confirmed=False)])
    assert all(r['signal_result']=='NOT_READY' for r in previous['telemetry'])
    assert previous['telemetry'][0]['warmup_progress']['distinct_samples']==0
    assert previous['telemetry'][1]['warmup_progress']['distinct_samples']==0


def test_native_instrument_gap_and_failed_observation_fraction_stay_scoped():
    assets=catalog(); previous=plan(assets)
    for minute in (1,2,3):
        at=OPEN+timedelta(minutes=minute)
        failed=dict(obs(assets[0],at), useful=False, native_reason='PPI_INSTRUMENT_NOT_FOUND',reason='SOURCE_UNAVAILABLE')
        previous=plan(assets,at,previous=previous,observations=[failed,obs(assets[1],at)])
    bad,good=previous['telemetry'][:2]
    assert 'PPI_INSTRUMENT_NOT_FOUND' in bad['rejection_reason']
    assert bad['usable_observation_fraction']==0 and bad['warmup_progress']['distinct_samples']==0
    assert 'PPI_INSTRUMENT_NOT_FOUND' not in good['rejection_reason'] and good['signal_result']=='SAMPLES_READY'
    assert previous['catalog_ready_count']==len(assets) and previous['capacity']['safe_limit']==8


def test_hot_strategy_and_book_freshness_cannot_be_revived_by_radar():
    assets=catalog(); previous=plan(assets)
    for minute in (1,2,3):
        at=OPEN+timedelta(minutes=minute)
        previous=plan(assets,at,previous=previous,observations=[obs(assets[0],at)])
    at=OPEN+timedelta(minutes=6)
    report=plan(assets,at,previous=previous,observations=[obs(assets[0],at,source='BYMA',endpoint='radar')])
    assert report['telemetry'][0]['signal_result']=='NOT_READY'


def test_outside_preopen_event_promotes_prospectively_but_requires_dense_warmup():
    assets=catalog(30); freeze=frozen(assets,tradeable=False)
    before=plan(assets,freeze=freeze)
    at=OPEN+timedelta(minutes=1)
    event={'identity':identity(assets[-1]),'at':at.isoformat(),'promotion_candidate':True,'reason_codes':['RVOL_ANOMALY']}
    promoted=plan(assets,at,previous=before,freeze=freeze,observations=[obs(assets[-1],at)],events=[event])
    row=promoted['telemetry'][-1]
    assert row['state']=='WARM' and row['signal_result']=='NOT_READY'
    assert identity(assets[-1]) in map(tuple,promoted['selected'])
    assert promoted['preopen_digest']==before['preopen_digest']
    for minute in (2,3):
        at=OPEN+timedelta(minutes=minute)
        promoted=plan(assets,at,previous=promoted,freeze=freeze,observations=[obs(assets[-1],at)])
    assert promoted['telemetry'][-1]['state']=='HOT'
    assert not promoted['telemetry'][-1]['entry_authority']
    expired=plan(assets,OPEN+timedelta(minutes=10),previous=promoted,freeze=freeze)
    assert expired['telemetry'][-1]['state']=='DISCOVERY'


def test_open_positions_retained_on_capacity_loss_and_overflow_specialized_delegated():
    assets=catalog(4); opened=list(map(identity,assets))+[('DLR/NOV26','FUTUROS','A3','ARS','INMEDIATA')]
    report=plan(assets,opened=opened,cap={'status':'NO_VERIFICADO','safe_limit':0})
    assert report['selected']==list(map(identity,assets))
    assert report['capacity_overflow_open_positions']==4
    assert report['specialized_opened_priority'][0]['route']['engine']=='FUTURES_LIFECYCLE'


def test_config_change_invalidates_strategy_samples_but_preserves_preopen():
    assets=catalog(); before=plan(assets,observations=[obs(assets[0],OPEN)])
    after=plan(assets,OPEN+timedelta(seconds=30),previous=before,cap=capacity(OPEN+timedelta(seconds=30),limit=9))
    assert after['checkpoint_invalidated'] and after['telemetry'][0]['warmup_progress']['distinct_samples']==0
    assert after['preopen_digest']==before['preopen_digest']


def test_duplicate_source_samples_do_not_complete_warmup_and_order_is_invariant():
    assets=catalog(); start=plan(assets); observations=[obs(assets[0],OPEN+timedelta(seconds=s)) for s in (20,40,60)]
    left=plan(assets,OPEN+timedelta(minutes=1),previous=start,observations=observations)
    right=plan(assets,OPEN+timedelta(minutes=1),previous=start,observations=list(reversed(observations)))
    assert left==right
    repeated=plan(assets,OPEN+timedelta(minutes=1),previous=start,observations=[observations[-1]]*10)
    assert repeated['telemetry'][0]['warmup_progress']['distinct_samples']==1


def test_future_and_wrong_session_inputs_fail_closed():
    assets=catalog()
    with pytest.raises(ValueError,match='FUTURE_OBSERVATION'):
        plan(assets,observations=[obs(assets[0],OPEN+timedelta(seconds=1))])
    freeze=frozen(assets); freeze['payload']['session_open']=(OPEN-timedelta(days=3)).isoformat();freeze['digest']=digest(freeze['payload'])
    with pytest.raises(ValueError,match='PREOPEN_SESSION_MISMATCH'):
        plan(assets,freeze=freeze)
    assert plan(assets,phase='CLOSED')['selected']==[]
    assert plan(assets,cap=capacity(OPEN+timedelta(seconds=1)))['selected']==[]


def test_dense_hot_and_fair_discovery_have_different_cadence_and_measured_coverage():
    assets=catalog(40); previous=plan(assets)
    seen=set()
    for tick in range(50):
        at=OPEN+timedelta(seconds=30*tick)
        previous=plan(assets,at,previous=previous,observations=[obs(assets[0],at)])
        seen.update(tuple(k) for k in previous['selected'])
    assert set(map(identity,assets))<=seen
    first=previous['telemetry'][0]
    assert first['state']=='HOT' and first['revisit_seconds']==30
    assert previous['discovery']['max_discovery_age']>0
    assert not previous['discovery']['all_movements_detected']
    assert first['achieved_revisit_seconds']==30


def test_joint_budget_shares_book_and_rejected_task_does_not_advance_checkpoint():
    assets=catalog(10); scalp=plan(assets)
    equity_policy=EnginePolicy(engine='EQUITY_SPOT',hot_seconds=120,warm_seconds=300,discovery_seconds=600)
    equity=plan(assets,policy=equity_policy)
    allocation=reconcile_endpoint_slots([(scalp,POLICY),(equity,equity_policy)],{'current':1,'book':1,'intraday':1})
    assert len(allocation['tasks'])==2 and any(r['reused_endpoints']==['book'] for r in allocation['tasks'])
    apply_shared_allocation([(scalp,POLICY),(equity,equity_policy)],allocation)
    for report in (scalp,equity):
        for key in report['aggregate_rejected']:
            assert report['instruments'][digest(key)]['selected_at'] is None


def test_existing_scalping_adapter_is_shadow_and_real_routes_never_called():
    assets=catalog(); report=shadow_sampling_plan(assets,at=OPEN,session_open=OPEN,frozen=frozen(assets),capacity=capacity(),policy=POLICY)
    assert report['real_orders_sent']==0 and report['real_routes']=='NOT_CALLED'
    assert all(not r['entry_authority'] for r in report['telemetry'])
    root=Path(__file__).resolve().parents[1]
    assert 'DEFAULT_INTRADAY_BATCH_LIMIT = 40' in (root/'cf_intraday_scalping.py').read_text()
    assert 'ACTIVE_SYMBOL_LIMIT' not in (root/'rc6_dynamic_universe/orchestrator.py').read_text()


@pytest.mark.parametrize('family',['BONOS','LETRAS','OBLIGACIONES','OPCIONES','FUTUROS','CAUCIONES','FCI'])
def test_generic_paper_decide_cannot_inherit_equity_momentum(tmp_path,family,monkeypatch):
    from be_paper_engine import PaperStore, PaperBroker
    from tests.test_production_paper_v1634 import quote
    from dataclasses import replace
    # This guard may not invent #453's dedicated FUTUROS binding. That owner's
    # real handler is verified separately in the reconciled workstream suite.
    if family == 'FUTUROS':
        monkeypatch.delattr(PaperBroker,'_on_future_quote',raising=False)
    broker=PaperBroker(PaperStore(str(tmp_path/'paper.db')))
    action,score,reason,features=broker.decide(replace(quote(),asset_class=family))
    assert action=='HOLD' and score==0
    if family == 'OPCIONES':
        assert 'contrato financiero explícito' in reason
        return
    if family in {'FUTUROS','CAUCIONES','FCI'}:
        assert 'ciclo financiero específico' in reason or 'FUTURES_EXACT_PAPER_CONTRACT_REQUIRED' in reason
        return
    assert 'STRATEGY_NOT_VALIDATED' in reason
    assert features['reason_codes']==['SPECIALIZED_LIFECYCLE','STRATEGY_NOT_VALIDATED']


def test_runtime_read_is_causal_nonwriting_and_mutable_revision_is_not_backdated(tmp_path):
    from be_paper_engine import PaperStore
    from bf_production_paper_observer import _support_schema
    from cf_intraday_scalping import init_schema
    store=PaperStore(str(tmp_path/'paper.db')); _support_schema(store); init_schema(store)
    store.state(mode='PRODUCTION_PAPER',real_orders_sent=0)
    with store.connect() as c:
        c.execute('INSERT INTO ppi_intraday_points VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            ('A','ACCIONES','BYMA','ARS','A-24HS',OPEN.isoformat(),'100','10',OPEN.isoformat(),(OPEN+timedelta(minutes=1)).isoformat(),'PPI_MARKETDATA_INTRADAY'))
    before=Path(store.path).read_bytes()
    report=read_runtime(store.path,as_of=OPEN)
    assert report['observations']==[] and Path(store.path).read_bytes()==before
    report=read_runtime(store.path,as_of=OPEN+timedelta(minutes=1))
    assert report['observations'][0]['intraday_confirmed'] is False
    assert 'interval_volume' not in report['observations'][0]['fields']


def test_runtime_ready_view_keeps_every_ready_family_and_does_not_mutate_catalog(tmp_path):
    from be_paper_engine import PaperStore
    from bf_production_paper_observer import _support_schema
    store=PaperStore(str(tmp_path/'paper.db')); _support_schema(store)
    store.state(mode='PRODUCTION_PAPER',real_orders_sent=0)
    with store.connect() as c:
        gap=dict(ticker='GAP',instrument_type='UNKNOWN',market='UNKNOWN',currency='UNKNOWN',
                 settlement='UNKNOWN',status='METADATA_GAP',capability='NO_VERIFICADO')
        for a in [gap]+catalog(4)+[{**instrument(family='BONOS'),'status':'AVAILABLE','capability':'READY_PAPER_SPECIAL'}]:
            c.execute("INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(a[k] for k in ('ticker','instrument_type','market','currency','settlement'))+
                ('FIXTURE','fixture',OPEN.isoformat(),'test',a['status'],a['capability'],'{}'))
    before=Path(store.path).read_bytes()
    report=read_runtime(store.path,as_of=OPEN)
    assert len(report['catalog'])==5 and {r['instrument_type'] for r in report['catalog']}=={'ACCIONES','BONOS'}
    assert Path(store.path).read_bytes()==before


def test_shared_exit_lab_uses_actual_dependency_or_explicitly_reports_missing():
    from rc6_dynamic_universe.economics import (shadow_exit_lab, shadow_economics,
                                               preregister_exit_variants)
    try:
        from rc6_performance.replay import ExitPolicy
        from rc6_performance.costs import paper_fee_model
    except ModuleNotFoundError:
        assert shadow_exit_lab([],[],[],fees=None)['reason']=='DEPENDENCY_PR_456_NOT_INTEGRATED'
        assert shadow_economics({})['status']=='NO_VERIFICADO'
        return
    from decimal import Decimal
    entry={'id':'paper-test','symbol':'A','asset_class':'ACCIONES','settlement':'A-24HS',
           'currency':'ARS','market':'BYMA','opened_at':OPEN.isoformat(),'entry_price':'100','quantity':'1'}
    deadline=OPEN+timedelta(minutes=10)
    base=ExitPolicy('FACTUAL',Decimal('.02'),Decimal('.05'),99999)
    registered=preregister_exit_variants(base,as_of=OPEN,eod_at=deadline,volatility='.01',
        volatility_available_at=OPEN-timedelta(minutes=1),horizon_seconds=3600)
    assert registered['policies'][0] is base and not registered['parameter_promotion']
    path=[]
    for second,bid in ((60,'103'),(120,'97'),(600,'101')):
        at=OPEN+timedelta(seconds=second)
        book={k:entry[k] for k in ('symbol','asset_class','settlement','currency','market')}
        book.update(bid=bid,ask=str(Decimal(bid)+1),bid_size='100',ask_size='100',
                    book_at=at.isoformat(),observed_at=at.isoformat(),source='PPI_PRIMARY')
        path.append({'as_of':at.isoformat(),'book':book})
    result=shadow_exit_lab([entry],registered['policies'],path,fees=paper_fee_model('ACCIONES'),
                           replay_kwargs={'eod_at':deadline.isoformat(),'slippage':'0'})
    assert result['status']=='SHADOW_REPLAY' and result['edge_oos']=='NO_VERIFICADO'
    assert len({r['input_sha256'] for r in result['results']})==1
    assert all(r['result']['factual_entry']==entry for r in result['results'])
    assert result['results'][0]['forward_label']['mfe_observed']==Decimal('.03')
    assert result['results'][0]['forward_label']['mae_observed']==Decimal('-.03')
    assert all(c['complete_eod_labels']==1 and c['continuous_hit_probability']=='NO_VERIFICADO'
               for c in result['entry_hour_cohorts'])
    assert not result['real_order_routes'] and all(not r['result']['real_order_routes'] for r in result['results'])
    partial=shadow_exit_lab([entry],registered['policies'],path[:-1],fees=paper_fee_model('ACCIONES'),
                            replay_kwargs={'eod_at':deadline.isoformat()})
    assert all(c['complete_eod_labels']==0 and c['sampled_target_touch_fraction'] is None
               for c in partial['entry_hour_cohorts'])


def test_shadow_runner_persists_preopen_and_outside_basket_anomaly_path(wire):
    assets=catalog(25); target=assets[-1]
    preopen=OPEN-timedelta(minutes=10)
    bundle={'safety':{'mode':'PRODUCTION_PAPER','real_orders_sent':0,'real_routes':'NOT_CALLED'},
        'as_of':preopen.isoformat(),'session_open':OPEN.isoformat(),'frozen_at':preopen.isoformat(),
        'preopen_cutoff':'2026-10-02T20:00:00+00:00','sessions':audited_sessions(),'catalog':assets,
        'history':sum([daily(a) for a in assets[:-1]],[]),'preopen_observations':sum([quotes(a) for a in assets[:-1]],[])}
    before=run_shadow(bundle)
    from tests.test_rc6_dynamic_tradeability import profiles,intraday
    measured=measure(wire,batches=(20,),cadence_seconds=1)
    at=wire[0].now()+timedelta(seconds=2)
    bundle.update(as_of=at.isoformat(),capacity_report=measured,intraday_history=profiles(target))
    bundle['observations']=[{'identity':identity(target),'source':'SHADOW_TEST_RADAR','source_at':r['observed_at'],
        'received_at':r['published_at'],'useful':True,'fields':{k:r[k] for k in ('cumulative_volume','price','trades','spread_bps')},
        'volume_unit':'SHARES'} for r in [intraday(target,'2026-10-05',25,volume=80),intraday(target,'2026-10-05',30,volume=400)]]
    after=run_shadow(bundle,previous={'engines':before['engines'],'frozen':before['frozen']})
    assert after['events']['events'][0]['promotion_candidate']
    assert 'RVOL_ANOMALY' in after['events']['events'][0]['reason_codes']
    assert after['engines']['SCALPING']['telemetry'][-1]['state']=='WARM'
    assert after['frozen']==before['frozen'] and after['real_orders_sent']==0


def test_economics_dependency_absence_is_explicit_never_an_alternate_cost_model(monkeypatch):
    import rc6_dynamic_universe.economics as economic
    def missing(name):
        raise ModuleNotFoundError(name=name)
    monkeypatch.setattr(economic,'import_module',missing)
    assert economic.shadow_economics({})['reason']=='DEPENDENCY_PR_456_NOT_INTEGRATED'
    assert economic.shadow_exit_lab([],[],[],fees=None)['status']=='NO_VERIFICADO'


def test_global_serial_budget_is_not_multiplied_by_independent_engine_limits():
    assets=catalog(10); report=plan(assets)
    allocation=reconcile_endpoint_slots([(report,POLICY)],{'intraday':10,'book':10},global_slots=1)
    assert len(allocation['tasks'])==1 and allocation['remaining_global_slots']==0
    assert allocation['rejected']


def test_option_discovery_requires_verified_underlying_and_keeps_ancillary_unknown():
    underlying={**catalog(1)[0],'observable':True,'tradeable':True,'price':100,'price_unit':'ARS_PER_SHARE',
        'source_at':OPEN.isoformat(),'received_at':OPEN.isoformat()}
    option={**instrument('OPT','OPCIONES'),'underlying':'S0','strike':100,'strike_unit':'ARS_PER_SHARE',
        'source_at':OPEN.isoformat(),'received_at':OPEN.isoformat(),'expires_at':(OPEN+timedelta(days=10)).isoformat(),
        'bid':2,'ask':2.005,'bid_size':10,'ask_size':10,'iv':.3,'greeks':{'delta':.5}}
    report=option_observation_universe([option],{'S0':underlying},as_of=OPEN)
    assert len(report['selected'])==1 and report['selected'][0]['iv']['status']=='NO_VERIFICADO'
    assert not report['selected'][0]['entry_authority']
    future=deepcopy(option);future['source_at']=(OPEN+timedelta(seconds=1)).isoformat()
    assert option_observation_universe([future],{'S0':underlying},as_of=OPEN)['excluded']
    wrong=deepcopy(underlying);wrong['currency']='USD_MEP'
    assert option_observation_universe([option],{'S0':wrong},as_of=OPEN)['excluded']
    illiquid=dict(underlying,tradeable=False)
    assert option_observation_universe([option],{'S0':illiquid},as_of=OPEN)['excluded'][0]['reason_codes']==['LOW_LIQUIDITY']
    wide=dict(option,ask=3)
    assert option_observation_universe([wide],{'S0':underlying},as_of=OPEN)['excluded'][0]['reason_codes']==['SPREAD_TOO_WIDE']


def test_runtime_current_book_has_distinct_clocks_and_cannot_promote_unconfirmed_volume(tmp_path):
    from be_paper_engine import PaperStore
    from tests.test_production_paper_v1634 import quote
    from dataclasses import replace
    from bf_production_paper_observer import _support_schema
    store=PaperStore(str(tmp_path/'paper.db'));_support_schema(store)
    store.state(mode='PRODUCTION_PAPER',real_orders_sent=0)
    q=replace(quote(),observed_at=OPEN.isoformat(),trade_at=OPEN.isoformat(),book_at=OPEN.isoformat())
    store.add_quote(q)
    report=read_runtime(store.path,as_of=OPEN)
    current=report['observations'][0]
    assert current['endpoint']=='current' and current['source_at']==OPEN.isoformat()
    assert current['book_useful'] is True and current['received_at']==OPEN.isoformat()
