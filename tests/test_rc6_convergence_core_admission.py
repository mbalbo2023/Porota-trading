"""Native caller regressions for 468/469; synthetic PAPER only, no provider IO."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import hashlib
import json

import pytest

import be_paper_engine as engine
import bv_paper_runtime as runtime
import cf_intraday_scalping as scalping
from bm_exit_supervisor import PositionExitSupervisor, init_schema as init_exit
from bq_exit_policy import PaperSessionPolicy
from tests.rc6_convergence_fixtures import with_synthetic_volume_contract

AT = datetime.fromisoformat('2026-10-05T13:46:00+00:00')


def record():
    return with_synthetic_volume_contract(dict(ticker='GGAL',instrument_type='ACCIONES',
        market='BYMA',currency='ARS',settlement='A-24HS',capability='READY_PAPER_SPOT',status='AVAILABLE'))


def quote(at, *, currency='ARS', market='BYMA', bid='107.5', ask='107.55', size='1000'):
    return engine.Quote('GGAL','ACCIONES','A-24HS',D(bid),D(bid),D(ask),D(size),D(size),
        at.isoformat(),currency=currency,market=market,metadata_source='PPI_CATALOG:OFFLINE_TEST',
        book_at=at.isoformat(),trade_at=at.isoformat(),last_kind='TRADE')


def points(at, count=16, step=60):
    return [(scalping._stamp(at-timedelta(seconds=(count-i)*step)),D(100)+D(i)/2,D(20 if i%2==0 else 10))
            for i in range(count)]


def seeded(tmp_path):
    store=engine.PaperStore(str(tmp_path/'paper.sqlite'))
    scalping.init_schema(store)
    r=record()
    scalping.persist_payload(store,r,points(AT-timedelta(minutes=1),15),received_at=AT-timedelta(minutes=1))
    assert scalping.persist_payload(store,r,points(AT),received_at=AT)['state']=='CONFIRMED_INTERVAL_VOLUME'
    store.add_quote(quote(AT))
    return store,r


def test_u03_recovery_completion_cannot_precede_durable_start_or_survive_restart(tmp_path):
    r=record();cache=scalping.IntradayCapabilityCache();fp='a'*64
    negative=cache.outcome(r,None,at=AT,fingerprint=fp,result='PPI_INSTRUMENT_NOT_FOUND')
    start=AT+timedelta(minutes=15)
    pending=cache.begin_probe(r,negative,at=start,fingerprint=fp,reason='TTL_REPROBE')
    with pytest.raises(ValueError,match='CLOCK_ROLLBACK'):
        cache.outcome(r,pending,at=AT+timedelta(seconds=100),fingerprint=fp,result='READ_ONLY_RECOVERED',
            recovered=True,attempt_started=True)
    store=engine.PaperStore(str(tmp_path/'paper.sqlite'));scalping.init_schema(store)
    scalping._invalidate_intraday_capability(store,r,pending,at=start)
    restarted=engine.PaperStore(store.path)
    durable=scalping._capability_detail(scalping._state(restarted,scalping._identity(r)))
    assert durable['probe_started_at']==scalping._stamp(start)
    with pytest.raises(ValueError,match='CLOCK_ROLLBACK'):
        scalping.persist_payload(restarted,r,points(AT+timedelta(seconds=100)),received_at=AT+timedelta(seconds=100))
    with restarted.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM ppi_intraday_points').fetchone()[0]==0


def test_f02_recovered_native_worker_keeps_stale_book_closed_after_real_warmup(tmp_path, monkeypatch):
    from tests.test_issue465_capability_cache import DAY, Harness, missing_once
    harness = Harness(tmp_path, monkeypatch,
        times=[DAY+timedelta(minutes=value) for value in (0, 15, 21, 24, 31)], behavior=missing_once)
    monkeypatch.setenv('PAPER_SCALPING_MODE', 'ACTIVE_OBSERVE')
    harness.run()
    assert harness.cuts[-1]['states'][0]['state'] == 'CONFIRMED_INTERVAL_VOLUME'
    assert harness.cuts[-1]['candidates'][-1]['action'] == 'BUY_CANDIDATE'
    assert harness.cuts[-1]['fills'] == 0
    cut = harness.times[-1]
    stale = replace(quote(cut, bid='121.5', ask='121.55'),
                    book_at=(cut-timedelta(minutes=3)).isoformat())
    harness.store.add_quote(stale)
    monkeypatch.setenv('PAPER_SCALPING_MODE', 'ACTIVE_PAPER')
    assert scalping.promote_paper_candidate(harness.store, harness.records[0], at=cut) == 'STALE_BOOK'
    assert scalping.evaluate_candidate(harness.store, harness.records[0], at=cut+timedelta(seconds=1)) == 'HOLD'
    with harness.store.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0] == 0
        assert connection.execute('SELECT real_orders_sent FROM observer_state').fetchone()[0] == 0


def test_u06_full_scalping_caller_recomputes_binding_economics_and_never_falsifies_passed(tmp_path,monkeypatch):
    store,r=seeded(tmp_path)
    assert scalping.evaluate_candidate(store,r,at=AT)=='BUY_CANDIDATE'
    monkeypatch.setenv('PAPER_SCALPING_MODE','ACTIVE_PAPER')
    def constructor(store, **kwargs):
        return engine.PaperBroker(store,**kwargs,clock_fn=lambda:AT.isoformat(),session_policy=PaperSessionPolicy(),
            economics_mode='BINDING',ai_mode='OFF',daily_loss_pct='5',daily_soft_stop_pct='3')
    monkeypatch.setattr(runtime,'broker_from_environment',constructor)
    outcome=scalping.promote_paper_candidate(store,r,at=AT)
    assert outcome=='BLOCKED:ECONOMICS_BINDING_NET_REWARD_RISK_INSUFFICIENT'
    with store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0]==0
        gate=json.loads(c.execute('SELECT detail_json FROM trade_gate_evaluations').fetchone()[0])
        assert gate['economics']['passed'] is False
        assert D(gate['economics']['net_reward_risk'])<D('1.20')
        assert gate['economics']['cost_contract']['authority']=='EXPLICIT_PAPER_ASSUMPTIONS'
        assert gate['signal_snapshot_key'].startswith('scalping-native:')
        assert len(gate['entry_signal_inputs']['price_samples'])==16
        assert c.execute('SELECT real_orders_sent FROM observer_state').fetchone()[0]==0


def test_u06_private_primitive_cannot_bypass_same_binding_gate(tmp_path):
    store=engine.PaperStore(str(tmp_path/'paper.sqlite'))
    b=engine.PaperBroker(store,economics_mode='BINDING',stop_loss_pct='.008',target_gain_pct='.02',
        clock_fn=lambda:AT.isoformat(),session_policy=PaperSessionPolicy(),ai_mode='OFF')
    forged={'economics':{'passed':True}}
    opened,reason,_=b._open(quote(AT),D('.8'),forged)
    assert not opened and reason=='ECONOMICS_BINDING_NET_REWARD_RISK_INSUFFICIENT'
    assert forged['economics']['passed'] is False


@pytest.mark.parametrize('currency,market',[('USD_MEP','BYMA'),('ARS','OTHER')])
def test_u08_stop_book_survives_newer_wrong_identity_and_restart(tmp_path,currency,market):
    store=engine.PaperStore(str(tmp_path/'paper.sqlite'));init_exit(store)
    clock=[AT.isoformat()]
    broker=engine.PaperBroker(store,clock_fn=lambda:clock[0],session_policy=PaperSessionPolicy(),
        economics_mode='SHADOW',ai_mode='OFF',daily_loss_pct='5',daily_soft_stop_pct='3')
    opened,reason,pid=broker.admit_paper_candidate(quote(AT),D('.8'),{})
    assert opened,reason
    position=store.open_positions()[0]
    cut=AT+timedelta(seconds=1);clock[0]=cut.isoformat()
    stop=str(D(position['stop_price'])-D('1'))
    store.add_quote(quote(cut,bid=stop,ask=str(D(stop)+D('.05'))))
    store.add_quote(quote(cut+timedelta(microseconds=1),currency=currency,market=market))
    restarted=engine.PaperStore(store.path)
    broker.store=restarted
    supervisor=PositionExitSupervisor(broker,clock_fn=lambda:clock[0],session_policy=broker.session_policy)
    result=supervisor.tick()
    assert any(v.state=='CLOSED_SIMULATED' or v.state=='EXIT_EXECUTED' for v in result) or not restarted.open_positions()
    assert not restarted.open_positions()
    with restarted.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM paper_fills WHERE side='SELL_SIMULATED'").fetchone()[0]==1


def test_u08_invalid_latest_same_identity_is_tombstone_and_partial_key_is_closed(tmp_path):
    store=engine.PaperStore(str(tmp_path/'paper.sqlite'))
    identity=dict(symbol='GGAL',asset_class='ACCIONES',settlement='A-24HS',currency='ARS',market='BYMA')
    store.add_quote(quote(AT))
    store.add_quote(quote(AT+timedelta(microseconds=1),bid='0',ask='0',size='0'))
    assert store.latest_quote(identity).bid==0
    assert store.latest_quote({k:v for k,v in identity.items() if k!='currency'}) is None


def test_aud01_native_stale_tail_remains_hold_with_a_fresh_book_and_refresh(tmp_path, monkeypatch):
    store, r = seeded(tmp_path)
    cut = AT + timedelta(minutes=20)
    # A repeated response and new book must preserve the old provider clock.
    scalping.persist_payload(store, r, points(AT), received_at=cut)
    store.add_quote(quote(cut))
    monkeypatch.setenv('PAPER_SCALPING_MODE', 'ACTIVE_PAPER')
    assert scalping.evaluate_candidate(store, r, at=cut) == 'HOLD'
    assert scalping.promote_paper_candidate(store, r, at=cut) == 'STALE_INTRADAY_SOURCE'
    with store.connect() as connection:
        reason = connection.execute('SELECT reason FROM scalping_candidates ORDER BY id DESC LIMIT 1').fetchone()[0]
        assert reason == 'STALE_INTRADAY_SOURCE'
        assert connection.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0] == 0
        assert connection.execute('SELECT real_orders_sent FROM observer_state').fetchone()[0] == 0


def test_aud02_fifteen_burst_events_do_not_become_a_fifteen_minute_signal(tmp_path):
    store,r=seeded(tmp_path)
    at=AT+timedelta(seconds=32)
    burst=[(scalping._stamp(AT+timedelta(seconds=2*i)),D(108)+i,D(10)) for i in range(15)]
    scalping.persist_payload(store,r,points(AT)+burst,received_at=at)
    store.add_quote(quote(at,bid='122',ask='122.05'))
    assert scalping.evaluate_candidate(store,r,at=at)=='HOLD'
    with store.connect() as c:
        candidate=c.execute('SELECT reason,economics_json FROM scalping_candidates ORDER BY id DESC').fetchone()
        assert candidate['reason']=='INTRADAY_EVENT_SPAN_INSUFFICIENT'
        model=json.loads(candidate['economics_json'])['temporal_contract']
        assert model['observed_span_seconds']==28
        assert model['sample_kind']=='DISTINCT_SOURCE_EVENTS' and model['bar_duration_seconds'] is None


def test_aud02_native_forty_two_minute_sparse_window_is_not_continuous(tmp_path):
    store = engine.PaperStore(str(tmp_path / 'paper.sqlite'))
    scalping.init_schema(store)
    r = record()
    sparse = [(scalping._stamp(AT-timedelta(minutes=46-3*i)), D(100)+D(i)/2,
               D(20 if i % 2 == 0 else 10)) for i in range(16)]
    scalping.persist_payload(store, r, sparse[:15], received_at=AT-timedelta(minutes=4))
    assert scalping.persist_payload(store, r, sparse, received_at=AT)['state'] == 'CONFIRMED_INTERVAL_VOLUME'
    store.add_quote(quote(AT))
    assert scalping.evaluate_candidate(store, r, at=AT) == 'HOLD'
    with store.connect() as connection:
        row = connection.execute('SELECT reason,economics_json FROM scalping_candidates ORDER BY id DESC LIMIT 1').fetchone()
        assert row['reason'] == 'INTRADAY_EVENT_CONTINUITY_UNVERIFIED'
        assert json.loads(row['economics_json'])['temporal_contract']['observed_span_seconds'] == 42*60
        assert connection.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0] == 0


def test_aud03_reset_or_monotone_volume_does_not_supply_missing_unit_contract(tmp_path):
    store=engine.PaperStore(str(tmp_path/'paper.sqlite'));scalping.init_schema(store)
    r=record();r['raw']={}
    scalping.persist_payload(store,r,points(AT-timedelta(minutes=1),15),received_at=AT-timedelta(minutes=1))
    assert scalping.persist_payload(store,r,points(AT),received_at=AT)['state']=='PPI_VOLUME_CONTRACT_UNVERIFIED'
    from rc6_signal_contracts import quantity_activity
    contract=record()['raw']['intraday_volume_contract']|{'unit':'TURNOVER_MONEY','currency':'ARS'}
    with pytest.raises(ValueError,match='QUANTITY_VOLUME_UNAVAILABLE'):
        quantity_activity([D(100),D(50)],contract)


@pytest.mark.parametrize('accumulation', ['INTERVAL', 'CUMULATIVE'])
def test_aud03_native_volume_semantics_come_from_the_dated_contract(tmp_path, accumulation):
    store = engine.PaperStore(str(tmp_path / 'paper.sqlite'))
    scalping.init_schema(store)
    r = record()
    r['raw']['intraday_volume_contract']['accumulation'] = accumulation
    native = points(AT)
    if accumulation == 'INTERVAL':
        # Increasing interval quantities are legitimate; no descent heuristic.
        native = [(time, price, D(10+i)) for i, (time, price, _) in enumerate(native)]
    scalping.persist_payload(store, r, native[:15], received_at=AT-timedelta(minutes=1))
    result = scalping.persist_payload(store, r, native, received_at=AT)
    store.add_quote(quote(AT))
    if accumulation == 'INTERVAL':
        assert result['state'] == 'CONFIRMED_INTERVAL_VOLUME'
        assert scalping.evaluate_candidate(store, r, at=AT) == 'BUY_CANDIDATE'
    else:
        assert result['state'] == 'PPI_CUMULATIVE_VOLUME_RESET_UNVERIFIED'
        assert scalping.evaluate_candidate(store, r, at=AT) == 'HOLD'
    with store.connect() as connection:
        assert connection.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0] == 0


@pytest.mark.parametrize('span_minutes', [1, 15, 30, 90])
def test_aud05_native_main_reports_the_observed_horizon_for_each_event_window(tmp_path, span_minutes):
    store = engine.PaperStore(str(tmp_path / 'paper.sqlite'))
    for index in range(20):
        at = AT-timedelta(seconds=span_minutes*60*(19-index)/19)
        store.add_quote(quote(at))
    broker = engine.PaperBroker(store, clock_fn=lambda: AT.isoformat(), ai_mode='OFF')
    vector = store.signal_prices(quote(AT), AT, window_minutes=90)
    inputs = broker._entry_signal_inputs(vector, quote(AT), AT)
    assert len(vector) == 20
    assert inputs['temporal_contract']['observed_span_seconds'] == span_minutes*60
    assert inputs['temporal_contract']['sample_kind'] == 'DISTINCT_SOURCE_EVENTS'
    assert inputs['temporal_contract']['bar_duration_seconds'] is None
    assert len(inputs['price_samples']) == 20


def test_aud05_main_snapshot_declares_event_horizon_and_records_actual_span(tmp_path):
    store=engine.PaperStore(str(tmp_path/'paper.sqlite'))
    for i in range(20):store.add_quote(quote(AT-timedelta(seconds=20-i)))
    q=quote(AT);b=engine.PaperBroker(store,clock_fn=lambda:AT.isoformat(),ai_mode='OFF')
    values=store.signal_prices(q,AT,window_minutes=90)
    inputs=b._entry_signal_inputs(values,q,AT)
    contract=inputs['temporal_contract']
    assert len(values)==20 and contract['observed_span_seconds']==19
    assert contract['sample_kind']=='DISTINCT_SOURCE_EVENTS' and contract['bar_duration_seconds'] is None
    assert contract['lookback_limit_minutes']==90
    assert 'OOS_EDGE_NO_VERIFICADO' in contract['strategy_validation']


def test_aud19_native_snapshots_survive_mutable_revisions_and_hold_without_book(tmp_path):
    store,r=seeded(tmp_path);scalping.evaluate_candidate(store,r,at=AT)
    with store.connect() as c:before=[tuple(x) for x in c.execute('SELECT * FROM decision_evidence_snapshots')]
    # The last minute is mutable, but its already made decision is immutable.
    revised=points(AT);revised[-1]=(revised[-1][0],D('999'),revised[-1][2])
    scalping.persist_payload(store,r,revised,received_at=AT+timedelta(seconds=1))
    with store.connect() as c:
        after=[tuple(x) for x in c.execute('SELECT * FROM decision_evidence_snapshots')]
        assert after==before
        for x in after:assert hashlib.sha256(x[4].encode()).hexdigest()==x[3]
        c.execute('DELETE FROM market_snapshots')
    restarted=engine.PaperStore(store.path)
    assert scalping.evaluate_candidate(restarted,r,at=AT+timedelta(seconds=1))=='HOLD'
    with restarted.connect() as c:
        tail=json.loads(c.execute('SELECT payload_json FROM decision_evidence_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()[0])
        assert tail['decision']['action']=='HOLD'
        assert len(tail['inputs_used']['entry_signal_inputs']['price_samples'])==16


def test_u13_child_crash_health_is_observable_without_blocking_exit_clock(tmp_path,monkeypatch):
    class Process:
        pid=1234
        result=None
        def poll(self):return self.result
        def terminate(self):self.result=0
        def wait(self,timeout):return self.result
    process=Process();mono=[0]
    monkeypatch.setattr(runtime,'now_iso',lambda:AT.isoformat())
    children=runtime.ChildProcesses({'dynamic_shadow':['offline']},startup_grace_seconds=0,
        spawn=lambda *a,**kw:process,clock=lambda:mono[0])
    children.poll();assert children.snapshot()['dynamic_shadow']['state']=='RUNNING'
    process.result=1;mono[0]=1;children.poll()
    health=runtime.publish_child_health(engine.PaperStore(str(tmp_path/'paper.sqlite')),children,recorded_at=AT.isoformat())
    assert health['children']['dynamic_shadow']['state']=='CRASH_BACKOFF'
    assert health['children']['dynamic_shadow']['restarts']==1
    assert health['children']['dynamic_shadow']['pid'] is None
    assert health['real_orders_sent']==0


def test_u03_real_worker_rollback_retains_pending_attempt_and_requires_real_post_recovery_samples(tmp_path,monkeypatch):
    from tests.test_issue465_capability_cache import Harness,payload,missing_once,DAY
    starts=[DAY+timedelta(minutes=m) for m in (0,15,16,30,46,47)]
    def behavior(h,request):
        if h.cycle==0:return missing_once(h,request)
        if h.cycle==1:
            h.at=DAY+timedelta(seconds=100)
            return payload(h.at)
        return None
    h=Harness(tmp_path,monkeypatch,times=starts,behavior=behavior).run()
    pending=json.loads(h.cuts[1]['states'][0]['detail'])['capability']
    assert pending['probe_started_at']==scalping._stamp(starts[1])
    assert pending['warmup_reset_required'] is True and pending['warmup_after'] is None
    assert all(cut['fills']==0 for cut in h.cuts[:5])
    assert any(e['event_type']=='INTRADAY_SCALPING_CLOCK_ROLLBACK' for e in h.cuts[-1]['events'])


def test_aud19_direct_future_fill_captures_evidence_atomically_and_failure_rolls_back(tmp_path,monkeypatch):
    from tests.test_rc6_convergence_finance_review import broker,quote,OPEN
    clock=[OPEN]
    b=broker(tmp_path/'future.sqlite',clock)
    original=engine._insert_evidence_snapshot
    def fail_capture(connection,evidence):
        original(connection,evidence)
        raise RuntimeError('OFFLINE_CAPTURE_FAILURE')
    monkeypatch.setattr(engine,'_insert_evidence_snapshot',fail_capture)
    with pytest.raises(RuntimeError,match='OFFLINE_CAPTURE_FAILURE'):
        b._open_future(quote(),D('.8'),{})
    with b.store.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM paper_future_positions').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM paper_family_lifecycle_events').fetchone()[0]==0
        assert c.execute('SELECT COUNT(*) FROM decision_evidence_snapshots').fetchone()[0]==0
    monkeypatch.setattr(engine,'_insert_evidence_snapshot',original)
    opened,reason,key=b._open_future(quote(),D('.8'),{})
    assert opened,reason
    with b.store.connect() as c:
        row=c.execute('SELECT payload_json,payload_sha256 FROM decision_evidence_snapshots').fetchone()
        assert hashlib.sha256(row[0].encode()).hexdigest()==row[1]
        evidence=json.loads(row[0])
        assert evidence['decision']['paper_id']==key
        assert evidence['admission_at']==OPEN
        assert evidence['capture_phase']=='ATOMIC_PAPER_ADMISSION'
        assert evidence['entry_fill_committed_at'] is None
        assert evidence['entry_fill_recorded_at']==evidence['captured_at']==OPEN
        assert evidence['inputs_used']['signal_contract_status']=='DIRECT_CANDIDATE_SIGNAL_VECTOR_UNAVAILABLE'
        assert evidence['inputs_used']['financial_commit']['lifecycle_id']==key


def test_ux470_i04_future_stop_intent_survives_zero_depth_restart_and_price_recovery(tmp_path):
    from dataclasses import replace
    from tests.test_rc6_convergence_finance_review import broker,quote,open_future,OPEN,CLOSE
    clock=[OPEN];path=tmp_path/'future.sqlite'
    b=broker(path,clock);position=open_future(b);key=position['lifecycle_id']
    clock[0]=CLOSE
    b._on_future_quote(replace(quote(CLOSE,bid='1469',ask='1469.5'),bid_size=D(0)))
    with b.store.connect() as c:
        intent=dict(c.execute('SELECT * FROM paper_future_exit_intents').fetchone())
        assert intent['state']=='EXIT_PENDING_EXECUTION'
        assert intent['cause']=='STOP_PAPER' and intent['due_at']==CLOSE
    clock[0]='2026-10-05T12:02:00-03:00'
    restarted=broker(path,clock)
    restarted._on_future_quote(quote(clock[0],bid='1510',ask='1510.5'))
    assert not restarted.store.active_future_positions()
    with restarted.store.connect() as c:
        final=dict(c.execute('SELECT * FROM paper_future_exit_intents WHERE lifecycle_id=?',(key,)).fetchone())
        assert final['state']=='CLOSED' and final['cause']=='STOP_PAPER'
        assert final['due_at']==intent['due_at'] and final['attempts']==2


def test_ux470_i04_future_missing_book_preserves_timed_exit_without_fabricating_mark(tmp_path):
    from tests.test_rc6_convergence_finance_review import broker,open_future,OPEN
    clock=[OPEN];b=broker(tmp_path/'future.sqlite',clock);position=open_future(b)
    clock[0]='2026-10-05T14:50:00-03:00'
    b.supervise_futures(clock[0])
    with b.store.connect() as c:
        intent=dict(c.execute('SELECT * FROM paper_future_exit_intents').fetchone())
        assert intent['state']=='EXIT_PENDING_NO_QUOTE'
        assert intent['cause']=='EOD_PAPER' and intent['supervised_at']==clock[0]
        assert c.execute("SELECT COUNT(*) FROM paper_family_lifecycle_events WHERE to_state='CLOSE'").fetchone()[0]==0
        assert c.execute("SELECT COUNT(*) FROM paper_future_marks").fetchone()[0]==0
    assert b.store.active_future_positions()[0]['lifecycle_id']==position['lifecycle_id']
