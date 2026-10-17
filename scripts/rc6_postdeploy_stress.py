#!/usr/bin/env python3
"""Reproducible offline session replay and adversaries in the deployed image.

The host launcher supplies no credentials and no production volume. This is
synthetic evidence, never a claim of actual provider capacity or live fills.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

# Stress scenarios supplement, never replace, the automatic full Predeploy
# suite. Each group names a runtime subsystem and its causal adversaries.
GROUPS={
 'sql_shadow_wal':['tests/test_rc6_shadow_sqlite_load.py','tests/test_rc6_shadow_sqlite_stage_diagnostics.py',
                   'tests/test_rc6_shadow_runtime_wiring.py','tests/test_rc6_observer_metric_contention.py',
                   'tests/test_rc6_shadow_contract_projection.py'],
 'publication_io_identity':['tests/test_rc6_issue600_regressions.py','tests/test_rc6_issue603_regressions.py',
                            'tests/test_rc6_shadow_failure_context.py','tests/test_rc6_shadow_stage_reads.py',
                            'tests/test_rc6_shadow_streaming_cost.py','tests/test_rc6_shadow_report_lifetime.py',
                            'tests/test_rc6_shadow_owned_state.py'],
 'signals_risk_exit_ledger':['tests/test_rc6_convergence_core_admission.py','tests/test_exit_supervision_v17.py',
                           'tests/test_exit_ledger_isolation_v17.py','tests/test_rc6_future_programming_complete.py',
                           'tests/test_rc6_performance_runtime.py','tests/test_rc6_final_programming_runtime.py',
                           'tests/test_rc6_timestamp_atomicity.py',
                           'tests/test_rc6_catalog_publication_lifecycle.py',
                           'tests/test_rc6_go_startup_readiness.py'],
 'sources_readiness_network':['tests/test_rc6_dynamic_sources.py','tests/test_rc6_source_consolidation.py',
                             'tests/test_rc6_family_readiness.py','tests/test_rc6_official_source_adapters.py',
                             'tests/test_rc6_final_family_source_policy.py','tests/test_rc6_family_quote_lookup_cost.py'],
 'scalping_capacity':['tests/test_intraday_scalping_rc6_integration.py','tests/test_issue465_budget_adversarial.py'],
 'treasury_caucion':['tests/test_caucion_treasury_v17.py','tests/test_caucion_ledger_v17.py',
                     'tests/test_ws_caucion_12_live_ppi_wiring.py'],
 'dashboard_auth_52':['tests/test_ws_dash_trader_terminal_08.py','tests/test_rc6_issue600_dashboard_adenda.py',
                      'tests/test_rc6_dashboard_clock_bounds.py','tests/test_rc6_runtime_all_routes_http.py'],
 'scheduler_monitor_backup':['tests/test_rc6_market_watch_silence.py','tests/test_host_general_backup_v17.py',
                             'tests/test_rc6_host_backup_symlink.py','tests/test_rc6_reader_status_connection_lifecycle.py'],
}


# Public native metadata cardinalities (READ_ONLY run 38998730556), synthetic
# identities, empty prices/history and zero providers. Counts are load, not
# readiness/contract evidence for any real instrument.
FULL_CATALOG_SHAPE = {
 'BONOS':(3012,2414), 'OBLIGACIONES':(5018,3639), 'FCI':(1082,1060),
 'ACCIONES':(198,163), 'CEDEARS':(2441,1266), 'LETRAS':(53,38),
 'FUTUROS':(203,0), 'OPCIONES':(2502,1317), 'CAUCIONES':(10,5),
 'ON':(91,0), 'INDICES':(1,0)}


def full_catalog_replay(folder):
    """Two real worker cuts at catalogue scale and its actual poll cadence."""
    from be_paper_engine import PaperStore
    from bf_production_paper_observer import _support_schema
    from cf_intraday_scalping import init_schema
    from tests.test_rc6_shadow_runtime_wiring import PRE
    from rc6_shadow_runtime import worker as runtime
    from rc6_shadow_runtime.persistence import read_committed_generation
    from rc6_shadow_runtime.timing import generation_budget
    folder.mkdir(parents=True,exist_ok=True)
    store=PaperStore(str(folder/'paper.db'));_support_schema(store);init_schema(store)
    rows=[]
    for family,(total,ready) in FULL_CATALOG_SHAPE.items():
        for n in range(total):
            rows.append((f'LOAD_{family}_{n:05d}',family,'BYMA',
                ('ARS','USD_MEP','USD_CCL')[n%3],'A-24HS','SYNTHETIC',
                'synthetic public-cardinality fixture',PRE.isoformat(),'stress-full-catalog',
                'AVAILABLE' if n<ready else 'PAUSED_EXPLICIT',
                'READY_PAPER_SPOT' if n<ready else 'NO_VERIFICADO','{}'))
    with store.connect() as c:
        c.executemany('INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',rows)
    total=len(rows);del rows
    ready=sum(ready for _,ready in FULL_CATALOG_SHAPE.values())
    worker=runtime.ShadowRuntime(store.path,evidence_root=folder/'shadow',source_roots=[])
    started=time.monotonic();cuts=[]
    class Stop:
        def is_set(self):return len(cuts)>=2
        def wait(self,_seconds):
            cut=read_committed_generation(worker.root)
            report,status=cut['report'],cut['status']
            assert status['status']=='SHADOW_OBSERVING'
            assert status['real_orders_sent']==0 and status['real_routes']=='NOT_CALLED'
            assert report['family_routing']['instruments_total']==total
            assert all(p['telemetry_total']==ready for p in report['engines'].values())
            assert report['computed_report']['raw_bytes']>100*1024**2
            receipt={'sequence':cut['pointer']['sequence'],'as_of':status['as_of'],
                     'tick_seconds':status['generation_timing']['tick_seconds'],
                     'computed_report_sha256':report['computed_report']['sha256']}
            if cuts:
                assert receipt['sequence']>cuts[-1]['sequence']
                previous_age=(PRE+timedelta(seconds=time.monotonic()-started)-
                    datetime.fromisoformat(cuts[-1]['as_of'])).total_seconds()
                assert previous_age<=cuts[-1]['freshness_budget_seconds'],('FULL_CATALOG_FRESHNESS_GAP',previous_age)
            receipt['freshness_budget_seconds']=generation_budget(status)[0]
            cuts.append(receipt)
            if len(cuts)<2:
                time.sleep(_seconds)
    with patch.object(runtime,'ShadowRuntime',lambda *_args,**_kwargs:worker):
        runtime.run_worker(store.path,Stop(),clock_fn=lambda:(PRE+timedelta(seconds=time.monotonic()-started)).isoformat())
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert len(cuts)==2 and peak<=512*1024,('FULL_CATALOG_PEAK_RSS_OVER_512MIB',peak)
    return {'schema':'rc6.synthetic-full-catalog.v1','catalog_count':total,'ready_fixture_count':ready,
            'cuts':cuts,'elapsed_seconds':round(time.monotonic()-started,3),'process_peak_rss_kib':peak,
            'poll_sleep':'ACTUAL_WORKER_POLL_SECONDS','real_orders_sent':0,'real_routes':'NOT_CALLED',
            'provider_requests':0,'production_data_mounted':False,'actual_market_acceptance':False,
            'memory_measurement':'PROCESS_RSS; host launcher independently enforces container memory cap'}


def session_replay(folder, *, minutes=405):
    from be_paper_engine import PaperBroker,D
    from bq_exit_policy import PaperSessionPolicy
    from bm_exit_supervisor import PositionExitSupervisor
    from bl_candle_engine import SampleMaterializer,init_schema
    from rc6_local_eod_history import consolidate,RESERVE
    from rc6_shadow_runtime.worker import ShadowRuntime
    from rc6_trader_dashboard.navigation import CANONICAL_PATHS
    from rc6_trader_dashboard.routes import build_page
    from tests.test_rc6_shadow_runtime_wiring import make_store,quote,OPEN,PRE
    from bn_telegram_bus import OutboxWorker
    import bv_paper_runtime as runtime
    store,assets=make_store(folder,count=32)
    init_schema(store)
    now=[OPEN];calls=[0]
    def clock():
        calls[0]+=1
        return (now[0]+timedelta(microseconds=calls[0])).isoformat()
    def publish_reader():
        with patch.object(runtime,'now_iso',clock):
            return runtime._reader_status_write(store,'READY','ISOLATED_SYNTHETIC_BOOK_FEED')
    broker=PaperBroker(store,clock_fn=clock,session_policy=PaperSessionPolicy(),
        signal_min_samples=8,ai_mode='OFF',daily_loss_pct='100',
        economics_mode='SHADOW',require_supervisor=True)
    supervisor=PositionExitSupervisor(broker,clock_fn=clock,
        session_policy=broker.session_policy,max_hold_minutes=30)
    candles=SampleMaterializer(store)
    shadow=ShadowRuntime(store.path,evidence_root=folder/'shadow',source_roots=[])
    shadow.tick(PRE)
    deliveries=[]
    def captured_transport(body):
        deliveries.append(hashlib.sha256(body.encode()).hexdigest())
        return str(len(deliveries))
    outbox=OutboxWorker(store,clock_fn=clock,send=captured_transport)
    durations=[];cuts=0;treasury=[]
    for minute in range(minutes+1):
        started=time.monotonic();now[0]=OPEN+timedelta(minutes=minute);calls[0]=0
        active=minute<390
        supervisor.heartbeat(clock())
        if active:assert publish_reader()
        store.state(process_state='RUNNING' if active else 'WAITING_MARKET',
                    session_state='MARKET_OPEN' if active else 'MARKET_CLOSED',
                    heartbeat_at=clock(),real_orders_sent=0)
        for index,asset in enumerate(assets[:4] if active else []):
            # Distinct deterministic bull, stop, flat and re-entry regimes.
            price=D(100)+D(minute%60)/2
            if index==1 and minute%60>=20:price=D(90)
            if index==2:price=D(100)+D(min(minute,12))/5
            q=quote(asset,now[0],bid=str(price),ask=str(price+D('.01')))
            store.add_quote(q)
            broker.on_quote(q)
        supervisor.tick()
        candles.tick(clock(),batch_size=100)
        outbox.tick()
        if minute in (365,375):
            from ce_caucion_treasury import CaucionWindow
            window=CaucionWindow(PRE.isoformat(),(OPEN+timedelta(hours=6)).isoformat(),
                (OPEN+timedelta(hours=6,minutes=25)).isoformat(),
                (OPEN+timedelta(days=1,hours=6,minutes=30)).isoformat(),'SYNTHETIC_SESSION_NOT_PROVIDER_TERMS')
            key='STRESS_TREASURY_'+str(minute)
            attempt=broker.allocate_conservative_caucion([],window,key,as_of=clock())
            assert attempt['status']=='HOLD'
            assert broker.allocate_conservative_caucion([],window,key,as_of=clock())==attempt
            treasury.append({'minute':minute,'status':attempt['status'],'code':attempt.get('code')})
        if minute%30==0:
            cut=shadow.tick(now[0]+timedelta(seconds=1));cuts+=1
            assert cut['real_orders_sent']==0 and cut['real_routes']=='NOT_CALLED'
        if minute and minute%120==0:
            # Reconstruct the real owners from the same durable isolated book.
            broker.store=type(store)(store.path)
            supervisor=PositionExitSupervisor(broker,clock_fn=clock,
                session_policy=broker.session_policy,max_hold_minutes=30)
            shadow=ShadowRuntime(store.path,evidence_root=folder/'shadow',source_roots=[])
        durations.append(time.monotonic()-started)
    if minutes<380:
        # Harness smoke still reaches the actual EOD clock with fresh books;
        # jumping the clock cannot make a stale bid executable.
        now[0]=OPEN+timedelta(minutes=380);calls[0]=0
        assert publish_reader()
        for asset in assets[:4]:
            store.add_quote(quote(asset,now[0],bid='110',ask='110.01'))
        supervisor.tick()
    closed=OPEN+timedelta(hours=6,minutes=50)
    now[0]=closed;calls[0]=0
    store.state(process_state='WAITING_MARKET',session_state='MARKET_CLOSED',
                heartbeat_at=closed.isoformat(),real_orders_sent=0)
    # Source-generated candles and local daily history remain PARTIAL/SAMPLED.
    for _ in range(100):
        candle=candles.tick(closed,batch_size=500)
        if candle['state']!='BACKLOG':break
    else:raise AssertionError('CANDLE_BACKLOG_DID_NOT_DRAIN')
    batches=[]
    for i in range(100):
        value=consolidate(store.path,folder/'history.db',
            as_of=closed+timedelta(microseconds=i),free_bytes=RESERVE)
        batches.append(value['state'])
        if value['state']!='BACKLOG':break
    assert batches[-1]=='LOCAL_EOD_READY',batches[-1]
    repeated=consolidate(store.path,folder/'history.db',as_of=closed+timedelta(seconds=1),free_bytes=RESERVE)
    assert repeated['imported']==0
    shadow.tick(closed+timedelta(seconds=1));shadow.tick(closed+timedelta(seconds=2));cuts+=2
    routes=[]
    for path in sorted(CANONICAL_PATHS):
        started=time.monotonic();html,headers=build_page(path,{},store.path)
        assert 'terminal-content' in html and 'PRODUCTION_PAPER' in html
        routes.append({'path':path,'elapsed_ms':round((time.monotonic()-started)*1000,3),
                       'bytes':len(html.encode()),'read_queries':headers.get('X-Porota-Read-Queries')})
    with store.connect() as c:
        assert c.execute('PRAGMA quick_check').fetchone()[0]=='ok'
        mode,orders=c.execute('SELECT mode,real_orders_sent FROM observer_state WHERE id=1').fetchone()
        assert (mode,orders)==('PRODUCTION_PAPER',0)
        counts={table:c.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                for table in ['market_snapshots','paper_decisions','paper_positions','paper_fills','candle_versions']}
        exits={str(row[0]):row[1] for row in c.execute('SELECT close_reason,COUNT(*) FROM paper_positions WHERE status=\'CLOSED\' GROUP BY close_reason')}
        reasons={str(row[0]):row[1] for row in c.execute('SELECT reason,COUNT(*) FROM paper_decisions GROUP BY reason')}
        assert counts['paper_fills']>0,('SESSION_DID_NOT_EXERCISE_FILL',reasons)
        assert c.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0]==0
    return {'schema':'rc6.synthetic-full-session.v1','simulated_minutes':minutes,
            'counts':counts,'exit_reasons':exits,'decision_reasons':reasons,'shadow_cuts':cuts,
            'treasury_attempts':treasury,
            'dashboard_routes':routes,'captured_notification_count':len(deliveries),
            'tick_p95_seconds':sorted(durations)[int(.95*(len(durations)-1))],
            'tick_max_seconds':max(durations),'local_eod_batches':len(batches),
            'local_eod_repeated_imported':repeated['imported'],
            'real_orders_sent':0,'real_routes':'NOT_CALLED','provider_requests':0,
            'provider_contracts':'SYNTHETIC_NOT_VALIDATED','actual_market_acceptance':False}


def run(output, *, smoke=False):
    output.mkdir(parents=True,exist_ok=True)
    os.environ.update(PAPER_HISTORICAL_CANDLE_SHADOW='OFF',PAPER_SECTOR_CONCENTRATION_POLICY='OBSERVATION_ONLY',
                      PAPER_AI_GATE_MODE='OFF',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    context=os.environ.get('RC6_STRESS_EXECUTION_CONTEXT','LOCAL_HARNESS_CHECK')
    result={'schema':'rc6.postdeploy-stress.v1','started_at':datetime.now(timezone.utc).isoformat(),
            'status':'RUNNING','evidence_kind':context,
            'production_data_mounted':False,'ppi_watch_accessed':False,
            'network':'DISABLED_BY_HOST_LAUNCHER' if context=='DROPLET_ISOLATED_CONTAINER' else 'NO_LIVE_TRANSPORT_CONFIGURED',
            'smoke':smoke,'scenarios':[],'limitations':['Real provider/network capacity and Android voice interaction are not simulated facts.']}
    start=time.monotonic()
    try:
        if not smoke:
            # Run inline before the session and pytest children, so the 512MiB
            # container does not hold a second Python/pytest parent beside it.
            with tempfile.TemporaryDirectory(prefix='rc6-catalog-') as temporary:
                result['full_catalog']=full_catalog_replay(Path(temporary))
        with tempfile.TemporaryDirectory(prefix='rc6-day-') as temporary:
            result['session']=session_replay(Path(temporary),minutes=8 if smoke else 405)
        if not smoke:
            for name,paths in GROUPS.items():
                missing=[p for p in paths if not (ROOT/p).is_file()]
                if missing:raise AssertionError('STRESS_SCENARIO_PATH_MISSING:'+','.join(missing))
                report=output/(name+'.xml')
                with tempfile.TemporaryFile() as log:
                    run=subprocess.run([sys.executable,'-m','pytest','-q','-p','no:cacheprovider',*paths,
                                        '--junitxml='+str(report)],cwd=ROOT,stdout=log,stderr=log,timeout=600)
                xml=ET.parse(report).getroot()
                cases=list(xml.iter('testcase'))
                counts={key:sum(len(c.findall(key)) for c in cases) for key in ('failure','error','skipped')}
                value={'name':name,'cases':len(cases),'exit_code':run.returncode,**counts,
                       'junit_sha256':hashlib.sha256(report.read_bytes()).hexdigest()}
                result['scenarios'].append(value)
                print(json.dumps({'scenario':value}),flush=True)
                if run.returncode or any(counts.values()):raise AssertionError('STRESS_SCENARIO_RED:'+name)
        result['status']='GREEN'
    except BaseException as error:
        result.update(status='RED',error_class=type(error).__name__,error_code=str(error)[:300])
        raise
    finally:
        result.update(elapsed_seconds=round(time.monotonic()-start,3),
                      self_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      child_peak_rss_kib=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                      finished_at=datetime.now(timezone.utc).isoformat())
        (output/'stress.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
        print(json.dumps({'status':result['status'],'elapsed_seconds':result['elapsed_seconds']}),flush=True)
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--smoke',action='store_true')
    parser.add_argument('--full-catalog-check',action='store_true');args=parser.parse_args()
    if args.full_catalog_check:
        with tempfile.TemporaryDirectory(prefix='rc6-catalog-') as temporary:
            receipt=full_catalog_replay(Path(temporary))
        args.output.mkdir(parents=True,exist_ok=True)
        (args.output/'full-catalog.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    else:
        run(args.output,smoke=args.smoke)
