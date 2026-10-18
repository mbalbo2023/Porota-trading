#!/usr/bin/env python3
"""Reproducible offline session replay and adversaries in the deployed image.

The host launcher supplies no credentials and no production volume. This is
synthetic evidence, never a claim of actual provider capacity or live fills.
"""
import argparse
from collections import Counter
from contextlib import closing, nullcontext
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
                            'tests/test_rc6_shadow_streaming_cost.py','tests/test_rc6_shadow_report_lifetime.py','tests/test_rc6_shadow_cold_audit.py',
                            'tests/test_rc6_shadow_owned_state.py','tests/test_rc6_shadow_shared_audit.py',
                            'tests/test_rc6_shadow_native_bound.py',
                            'tests/test_rc6_issue620_temporal.py','tests/test_rc6_deploy_generation_gate.py',
                            'tests/test_rc6_preopen_scalar_storage.py','tests/test_rc6_native_soak.py',
                            'tests/test_rc6_checkpoint_streaming.py','tests/test_rc6_owned_preopen.py',
                            'tests/test_rc6_missing_view_sharing.py','tests/test_rc6_progressive_audit.py'],
 'signals_risk_exit_ledger':['tests/test_rc6_owned_funnel_details.py','tests/test_rc6_convergence_core_admission.py','tests/test_exit_supervision_v17.py',
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
 'scheduler_monitor_backup':['tests/test_rc6_physical_cadence.py','tests/test_rc6_market_watch_silence.py','tests/test_host_general_backup_v17.py',
                             'tests/test_rc6_candle_inmarket_evidence.py',
                             'tests/test_rc6_host_backup_symlink.py','tests/test_rc6_reader_status_connection_lifecycle.py',
                             'tests/test_rc6_backup_preservation.py','tests/test_rc6_deploy_quiescence.py'],
}


# Public native metadata cardinalities (READ_ONLY run 38998730556), synthetic
# identities, empty prices/history and zero providers. Counts are load, not
# readiness/contract evidence for any real instrument.
FULL_CATALOG_SHAPE = {
 'BONOS':(3012,2414), 'OBLIGACIONES':(5018,3639), 'FCI':(1082,1060),
 'ACCIONES':(198,163), 'CEDEARS':(2441,1266), 'LETRAS':(53,38),
 'FUTUROS':(203,0), 'OPCIONES':(2502,1317), 'CAUCIONES':(10,5),
 'ON':(91,0), 'INDICES':(1,0)}


CADENCE_GENERATIONS = 12


def _file_sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(65536):value.update(block)
    return value.hexdigest()


def _gzip_sha(path):
    import gzip
    value=hashlib.sha256();size=0
    with gzip.open(path,'rb') as stream:
        while block:=stream.read(65536):value.update(block);size+=len(block)
    return {'sha256':value.hexdigest(),'bytes':size}


def cadence_observation(*, origin, started, published, observed, as_of, budget, previous=None):
    """Actual monotonic publication endpoints bound every intervening age.

    The old cut is current until the next publication, not until the next
    COLD audit finishes. Current source clocks are never refreshed here.
    """
    import math
    from rc6_performance.common import stamp
    numbers=(started,published,observed,budget)
    if any(type(n) not in (int,float) or not math.isfinite(n) for n in numbers):
        raise ValueError('CADENCE_CLOCK_UNVERIFIED')
    if not 0<=started<=published<=observed or not 0<budget<=120:
        raise ValueError('CADENCE_CLOCK_OR_BUDGET_INVALID')
    source=stamp(as_of);base=stamp(origin)
    publication_age=(base+timedelta(seconds=published-started)-source).total_seconds()
    current_age=(base+timedelta(seconds=observed-started)-source).total_seconds()
    if not 0<=publication_age<=current_age<=budget:
        raise ValueError('CADENCE_CURRENT_SOURCE_STALE_OR_FUTURE')
    result={'publication_source_age_seconds':publication_age,'observed_source_age_seconds':current_age,
            'publication_monotonic':published,'observation_monotonic':observed,'source_as_of':source.isoformat(),
            'freshness_budget_seconds':budget}
    if previous is not None:
        if published<=previous['publication_monotonic'] or source<=stamp(previous['source_as_of']):
            raise ValueError('CADENCE_SOURCE_OR_PUBLICATION_NOT_ADVANCING')
        age=(base+timedelta(seconds=published-started)-stamp(previous['source_as_of'])).total_seconds()
        if not 0<=age<=previous['freshness_budget_seconds']<=120:
            raise ValueError('CADENCE_PREVIOUS_SOURCE_EXPIRED_BEFORE_PUBLICATION')
        result['previous_age_at_publication_seconds']=age
    return result


def verify_cadence_series(receipt):
    """Promotion cannot substitute the historical two-cut smoke for twelve."""
    import math,re
    from rc6_performance.common import stamp
    from rc6_shadow_runtime.timing import generation_budget
    cuts=receipt.get('cuts',[])
    if (receipt.get('cadence_mode') not in {'fresh','legacy'} or len(cuts)!=CADENCE_GENERATIONS
            or type(receipt.get('required_generations'))is not int
            or receipt.get('required_generations')!=CADENCE_GENERATIONS):
        raise ValueError('CADENCE_TWELVE_PHYSICAL_CUTS_REQUIRED')
    peak=receipt.get('process_peak_rss_kib')
    if type(peak)is not int or not 0<peak<=192*1024:
        raise ValueError('CADENCE_PROCESS_MEMORY_LIMIT')
    if (receipt.get('real_orders_sent')!=0 or type(receipt.get('real_orders_sent'))is not int
            or receipt.get('provider_requests')!=0 or type(receipt.get('provider_requests'))is not int
            or receipt.get('real_routes')!='NOT_CALLED'):
        raise ValueError('CADENCE_PAPER_REQUIRED')
    if type(receipt.get('nice'))is not int or receipt['nice']!=10 or receipt.get('database_unchanged') is not True:
        raise ValueError('CADENCE_PRIORITY_OR_INPUT_UNVERIFIED')
    elapsed=receipt.get('elapsed_seconds')
    if type(elapsed) not in (int,float) or not math.isfinite(elapsed):
        raise ValueError('CADENCE_ELAPSED_CLOCK_UNVERIFIED')
    if (any(type(receipt.get(k))is not int for k in ('catalog_count','ready_fixture_count','observed_publications'))
            or receipt.get('catalog_count')!=14611 or receipt.get('ready_fixture_count')!=9902
            or receipt.get('observed_publications')!=CADENCE_GENERATIONS
            or receipt.get('elapsed_seconds',0)<(CADENCE_GENERATIONS-1)*30
            or receipt.get('production_data_mounted')is not False
            or receipt.get('actual_market_acceptance')is not False
            or any(not isinstance(receipt.get(k),str) or not re.fullmatch('[0-9a-f]{40}',receipt[k]) for k in ('source_sha','candidate_tree'))):
        raise ValueError('CADENCE_REAL_PERIOD_OR_SOURCE_UNVERIFIED')
    if len({c['generation_id'] for c in cuts})!=CADENCE_GENERATIONS:
        raise ValueError('CADENCE_DUPLICATE_GENERATION')
    for index,cut in enumerate(cuts):
        audit=cut.get('cold_audit',{});computed=audit.get('computed_report',{})
        wall=audit.get('wall_seconds')
        if (audit.get('state')!='COMPLETE' or type(wall) not in (int,float)
                or not math.isfinite(wall) or not 0<=wall<=30
                or audit.get('source_as_of')!=cut.get('as_of')
                or type(computed.get('raw_bytes'))is not int
                or computed.get('raw_bytes',0)!=cut.get('computed_report_raw_bytes')
                or computed.get('raw_bytes',0)<=100*1024**2
                or computed.get('sha256')!=cut.get('computed_report_sha256')
                or not isinstance(computed.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',computed['sha256'])):
            raise ValueError('CADENCE_FULL_ORIGINAL_COLD_AUDIT_REQUIRED')
        measured=cut['physical_observation']
        if generation_budget({'generation_timing':cut['worker_timing']})[0]!=cut['freshness_budget_seconds']:
            raise ValueError('CADENCE_ORIGINAL_BUDGET_CHANGED')
        actual=cadence_observation(origin=receipt['source_origin'],started=receipt['measurement_started_monotonic'],
            published=measured['publication_monotonic'],observed=measured['observation_monotonic'],
            as_of=cut['as_of'],budget=cut['freshness_budget_seconds'],
            previous=cuts[index-1]['physical_observation'] if index else None)
        if actual!=measured or cut['worker_timing'].get('poll_seconds')!=30:
            raise ValueError('CADENCE_ACTUAL_CLOCK_BINDING_INVALID')
        if index and (stamp(cut['as_of'])-stamp(cuts[index-1]['as_of'])).total_seconds()<30:
            raise ValueError('CADENCE_VIRTUAL_FAST_FORWARD_FORBIDDEN')
        if not 0<=measured['publication_source_age_seconds']<=measured['observed_source_age_seconds']<=cut['freshness_budget_seconds']<=120:
            raise ValueError('CADENCE_CURRENT_AGE_UNVERIFIED')
        if index and (cut['sequence']<=cuts[index-1]['sequence'] or
                not 0<=measured.get('previous_age_at_publication_seconds',121)<=cuts[index-1]['freshness_budget_seconds']):
            raise ValueError('CADENCE_PROGRESS_OR_CONTINUITY_UNVERIFIED')
    if elapsed+0.001<cuts[-1]['physical_observation']['observation_monotonic']-receipt['measurement_started_monotonic']:
        raise ValueError('CADENCE_PHYSICAL_WINDOW_TRUNCATED')
    if receipt['cadence_mode']=='legacy':
        seed=receipt.get('legacy_seed',{})
        if (seed.get('standard_gzip')is not True or seed.get('raw_before')!=seed.get('raw_after')
                or receipt.get('first_checkpoint_reused')is not True):
            raise ValueError('CADENCE_LEGACY_BOOTSTRAP_UNVERIFIED')
    return True


def standard_legacy_seed(source,target):
    """Only original causal checkpoint/preopen bytes enter a new empty root."""
    import gzip,shutil
    from rc6_shadow_runtime.persistence import EvidenceFiles,checkpoint_ascii_chunks
    from rc6_shadow_runtime.checkpoint_parts import CheckpointParts
    source=Path(source);target=Path(target);source_files=EvidenceFiles(source)
    checkpoint=source_files.path('checkpoint.json.gz')
    with checkpoint.open('rb') as stream:
        if stream.read(4)!=b'\x1f\x8b\x08\x04':raise ValueError('CADENCE_INDEXED_SEED_REQUIRED')
    with CheckpointParts(source_files,checkpoint_ascii_chunks) as parts:
        as_of=parts.head['as_of']
    before=_gzip_sha(checkpoint);target.mkdir(mode=0o700,parents=True,exist_ok=False)
    for path in sorted(source.glob('preopen-*.json.gz')):
        checked=source_files.path(path.name);shutil.copy2(checked,target/path.name)
    destination=target/'checkpoint.json.gz'
    with gzip.open(checkpoint,'rb') as raw,destination.open('wb') as physical:
        with gzip.GzipFile(filename='',fileobj=physical,mode='wb',compresslevel=1,mtime=0) as zipped:
            shutil.copyfileobj(raw,zipped,65536)
        physical.flush();os.fsync(physical.fileno())
    os.chmod(destination,0o600)
    after=_gzip_sha(destination)
    with destination.open('rb') as stream:standard=stream.read(4)==b'\x1f\x8b\x08\x00'
    if before!=after or not standard:raise ValueError('CADENCE_LEGACY_RAW_SOURCE_CHANGED')
    return {'source_as_of':as_of,'raw_before':before,'raw_after':after,'standard_gzip':standard,
            'source_checkpoint_sha256':_file_sha(checkpoint),'legacy_checkpoint_sha256':_file_sha(destination),
            'files':{p.name:_file_sha(p) for p in target.iterdir()}}


def full_catalog_replay(folder, *, required_generations=2, database=None, legacy_source=None):
    """Default two-cut contract; optional twelve-cut physical cadence proof."""
    from be_paper_engine import PaperStore
    from bf_production_paper_observer import _support_schema
    from cf_intraday_scalping import init_schema
    from tests.test_rc6_shadow_runtime_wiring import PRE
    from rc6_shadow_runtime import worker as runtime
    from rc6_shadow_runtime.persistence import read_committed_generation
    from rc6_shadow_runtime.timing import generation_budget
    from rc6_performance.common import stamp
    if type(required_generations)is not int or required_generations not in (2,CADENCE_GENERATIONS):
        raise ValueError('CADENCE_GENERATION_COUNT_INVALID')
    physical=required_generations==CADENCE_GENERATIONS
    if legacy_source is not None and (not physical or database is None):
        raise ValueError('CADENCE_LEGACY_SHARED_FIXTURE_REQUIRED')
    folder.mkdir(parents=True,exist_ok=True)
    if database is None:
        database=folder/'paper.db'
        store=PaperStore(str(database));_support_schema(store);init_schema(store)
        rows=[]
        for family,(total,ready) in FULL_CATALOG_SHAPE.items():
            for n in range(total):
                rows.append((f'LOAD_{family}_{n:05d}',family,'BYMA',
                    ('ARS','USD_MEP','USD_CCL')[n%3],'A-24HS','SYNTHETIC',
                    'synthetic public-cardinality fixture',PRE.isoformat(),'stress-full-catalog',
                    'AVAILABLE' if n<ready else 'PAUSED_EXPLICIT',
                    'READY_PAPER_SPOT' if n<ready else 'NO_VERIFICADO','{}'))
        with store.connect() as connection:
            connection.executemany('INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',rows)
        del rows,store
    database=Path(database)
    if database.is_symlink() or database.stat().st_nlink!=1:raise ValueError('CADENCE_FIXTURE_IDENTITY_INVALID')
    database=database.resolve()
    before={'path':str(database),'inode':database.stat().st_ino,'sha256':_file_sha(database)}
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as connection:
        total,ready=connection.execute("SELECT COUNT(*),SUM(status='AVAILABLE' AND capability LIKE 'READY_PAPER%') FROM financial_instrument_catalog").fetchone()
    assert (total,ready)==(14611,9902)
    seed=standard_legacy_seed(legacy_source,folder/'shadow') if legacy_source is not None else None
    origin=stamp(seed['source_as_of'])+timedelta(seconds=30) if seed else PRE
    worker=runtime.ShadowRuntime(database,evidence_root=folder/'shadow',source_roots=[],spool_audit=True,defer_audit=True)
    started=time.monotonic();cuts=[];publications=[];reused=[]
    original_publish=worker.files.publish_generation
    def observed_publish(report,status):
        before_publish=time.monotonic();result=original_publish(report,status);after_publish=time.monotonic()
        publications.append({**result,'source_as_of':status['as_of'],'before':before_publish,'after':after_publish})
        reused.append(report.get('checkpoint_reused'))
        return result
    class Stop:
        def is_set(self):return len(cuts)>=required_generations
        def wait(self,seconds):
            wait_started=time.monotonic();cut=read_committed_generation(worker.root)
            report,status=cut['report'],cut['status']
            assert status['status']=='SHADOW_OBSERVING'
            assert type(status['real_orders_sent'])is int and status['real_orders_sent']==0 and status['real_routes']=='NOT_CALLED'
            assert report['family_routing']['instruments_total']==total
            assert set(report['engines'])=={'SCALPING','EQUITY_SPOT'}
            assert all(plan['telemetry_total']==ready for plan in report['engines'].values())
            assert type(report['provider_requests'])is int and report['provider_requests']==0
            assert type(report['real_orders_sent'])is int and report['real_orders_sent']==0 and report['real_routes']=='NOT_CALLED'
            audit=cut['computed_audit'];assert audit['state']=='COMPLETE'
            computed=audit['computed_report'];raw=_gzip_sha(worker.root/computed['file'])
            assert raw['bytes']==computed['raw_bytes']>100*1024**2 and raw['sha256']==computed['sha256']
            assert 0<=audit['wall_seconds']<=30 and audit['source_as_of']==status['as_of']
            receipt={'sequence':cut['pointer']['sequence'],'as_of':status['as_of'],
                'generation_id':cut['pointer']['generation_id'],'manifest_sha256':cut['pointer']['manifest_sha256'],
                'tick_seconds':status['generation_timing']['tick_seconds'],'worker_timing':status['generation_timing'],
                'computed_report_sha256':computed['sha256'],'computed_report_raw_bytes':raw['bytes'],
                'cold_audit':audit,'freshness_budget_seconds':generation_budget(status)[0]}
            try:
                if physical:
                    assert len(publications)==len(cuts)+1
                    publication=publications[-1]
                    receipt['actual_publication']=dict(publication)
                    assert all(publication[k]==receipt[k] for k in ('generation_id','sequence','manifest_sha256'))
                    assert publication['source_as_of']==status['as_of']
                    assert os.getpriority(os.PRIO_PROCESS,0)==10
                    receipt['physical_observation']=cadence_observation(origin=origin,started=started,
                        published=publication['after'],observed=time.monotonic(),as_of=status['as_of'],
                        budget=receipt['freshness_budget_seconds'],
                        previous=cuts[-1]['physical_observation'] if cuts else None)
                if cuts:
                    assert receipt['sequence']>cuts[-1]['sequence']
                    previous_age=(origin+timedelta(seconds=time.monotonic()-started)-stamp(cuts[-1]['as_of'])).total_seconds()
                    receipt['previous_cut_age_seconds']=previous_age
                    receipt['previous_cut_freshness_budget_seconds']=cuts[-1]['freshness_budget_seconds']
                    if not physical:
                        assert previous_age<=cuts[-1]['freshness_budget_seconds'],('FULL_CATALOG_FRESHNESS_GAP',previous_age)
            except Exception as error:
                receipt['cadence_rejection']={'kind':type(error).__name__,'reason':str(error)}
                raise
            finally:
                print('RC6_FULL_CATALOG_CUT='+json.dumps(receipt,sort_keys=True,separators=(',',':')),flush=True)
                if physical:
                    (folder/'full-catalog-progress.json').write_text(json.dumps({'status':'PARTIAL_NOT_ACCEPTED',
                        'required_generations':required_generations,'cuts':cuts+[receipt]},indent=2)+'\n')
            cuts.append(receipt)
            if len(cuts)<required_generations:
                # Verification occurs inside the already remaining nominal wait.
                # The historical default keeps its original stricter wait.
                time.sleep(max(0,seconds-(time.monotonic()-wait_started)) if physical else seconds)
    def actual_factory(*_args,**kwargs):
        assert kwargs['spool_audit']is True and kwargs['defer_audit']is True
        worker.shutdown_requested=kwargs['shutdown_requested'];return worker
    with patch.object(worker.files,'publish_generation',observed_publish) if physical else nullcontext():
        with patch.object(runtime,'ShadowRuntime',actual_factory):
            runtime.run_worker(database,Stop(),clock_fn=lambda:(origin+timedelta(seconds=time.monotonic()-started)).isoformat())
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert len(cuts)==required_generations and peak<=192*1024,('FULL_CATALOG_PROGRESSIVE_RSS_OVER_192MIB',peak)
    unchanged=before=={'path':str(database),'inode':database.stat().st_ino,'sha256':_file_sha(database)}
    assert unchanged,'CADENCE_INPUT_DATABASE_CHANGED'
    result={'schema':'rc6.synthetic-full-catalog.v1','catalog_count':total,'ready_fixture_count':ready,
        'cuts':cuts,'elapsed_seconds':round(time.monotonic()-started,3),'process_peak_rss_kib':peak,
        'poll_sleep':'REMAINING_NOMINAL_30S_AFTER_HOT_PUBLICATION','real_orders_sent':0,'real_routes':'NOT_CALLED',
        'provider_requests':0,'production_data_mounted':False,'actual_market_acceptance':False,
        'memory_measurement':'PROCESS_RSS; host launcher independently enforces container memory cap',
        'required_generations':required_generations,'fixture_database':before,'database_unchanged':unchanged}
    if physical:
        result.update(cadence_mode='legacy' if seed else 'fresh',source_origin=origin.isoformat(),
            source_sha=os.environ.get('POROTA_BUILD_SHA'),candidate_tree=os.environ.get('POROTA_CANDIDATE_TREE_SHA'),
            nice=os.getpriority(os.PRIO_PROCESS,0),legacy_seed=seed,first_checkpoint_reused=reused[0],
            measurement_started_monotonic=started,
            observed_publications=len(publications),native_host_acceptance=False)
        verify_cadence_series(result)
        result['status']='GREEN'
    return result


def cadence_stage_replay(output,stage):
    if stage not in ('fresh','legacy'):raise ValueError('CADENCE_STAGE_INVALID')
    output.mkdir(parents=True,exist_ok=True)
    if stage=='fresh':
        result=full_catalog_replay(output/'fresh',required_generations=CADENCE_GENERATIONS)
    else:
        prior=json.loads((output/'cadence-fresh.json').read_text())
        verify_cadence_series(prior)
        if (prior.get('cadence_mode')!='fresh' or prior.get('status')!='GREEN' or prior.get('source_sha')!=os.environ.get('POROTA_BUILD_SHA')
                or prior.get('candidate_tree')!=os.environ.get('POROTA_CANDIDATE_TREE_SHA')):
            raise ValueError('CADENCE_FROZEN_FRESH_SOURCE_REQUIRED')
        database=Path(prior['fixture_database']['path'])
        if _file_sha(database)!=prior['fixture_database']['sha256'] or database.stat().st_ino!=prior['fixture_database']['inode']:
            raise ValueError('CADENCE_LEGACY_FIXTURE_CHANGED')
        result=full_catalog_replay(output/'legacy',required_generations=CADENCE_GENERATIONS,
            database=database,legacy_source=output/'fresh/shadow')
    (output/('cadence-'+stage+'.json')).write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    return result


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
    shadow=ShadowRuntime(store.path,evidence_root=folder/'shadow',source_roots=[],spool_audit=True)
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
            shadow=ShadowRuntime(store.path,evidence_root=folder/'shadow',source_roots=[],spool_audit=True)
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
    parser.add_argument('--full-catalog-check',action='store_true')
    parser.add_argument('--cadence-stage',choices=('fresh','legacy'));args=parser.parse_args()
    if args.cadence_stage:
        if args.smoke or args.full_catalog_check:parser.error('cadence stage cannot use smoke or two-cut mode')
        cadence_stage_replay(args.output,args.cadence_stage)
    elif args.full_catalog_check:
        with tempfile.TemporaryDirectory(prefix='rc6-catalog-') as temporary:
            receipt=full_catalog_replay(Path(temporary))
        args.output.mkdir(parents=True,exist_ok=True)
        (args.output/'full-catalog.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
    else:
        run(args.output,smoke=args.smoke)
