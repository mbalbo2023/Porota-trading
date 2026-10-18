"""Finite fresh and legacy-bootstrap A/B in one existing frozen ABI, offline."""
import argparse
import gc
import gzip
import hashlib
import inspect
import json
from pathlib import Path
import resource
import shutil
import sys
import time
from datetime import timedelta


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(65536):value.update(block)
    return value.hexdigest()


def raw_sha(path):
    value=hashlib.sha256();size=0
    with gzip.open(path,'rb') as stream:
        while block:=stream.read(65536):value.update(block);size+=len(block)
    return {'sha256':value.hexdigest(),'bytes':size}


def measure(args):
    root=Path('/experiment');sys.path.insert(0,str(root))
    from be_paper_engine import PaperStore
    from bf_production_paper_observer import _support_schema
    from cf_intraday_scalping import init_schema
    from scripts.rc6_postdeploy_stress import FULL_CATALOG_SHAPE
    from tests.test_rc6_shadow_runtime_wiring import PRE
    from rc6_shadow_runtime.worker import ShadowRuntime,release_idle_heap
    from rc6_shadow_runtime.persistence import EvidenceFiles,read_committed_generation,_native_json_bound
    assert _native_json_bound is not None
    assert Path(sys.modules['rc6_shadow_runtime.worker'].__file__).is_relative_to(root)
    manifest=json.loads(Path('/probe/source-manifest.json').read_text());assert manifest
    for row in manifest:
        path=(root/row['path']).resolve();assert path.is_relative_to(root)
        assert sha(path)==row['sha256'],('IMPORT_SOURCE_DRIFT',row['path'])
    database=Path('/evidence/fixture.db')
    if not database.exists():
        assert args.arm=='baseline' and not args.legacy
        store=PaperStore(str(database));_support_schema(store);init_schema(store)
        rows=[]
        for family,(total,ready) in FULL_CATALOG_SHAPE.items():
            for n in range(total):
                rows.append((f'LOAD_{family}_{n:05d}',family,'BYMA',('ARS','USD_MEP','USD_CCL')[n%3],
                    'A-24HS','SYNTHETIC','synthetic public-cardinality fixture',PRE.isoformat(),
                    'stress-full-catalog','AVAILABLE' if n<ready else 'PAUSED_EXPLICIT',
                    'READY_PAPER_SPOT' if n<ready else 'NO_VERIFICADO','{}'))
        with store.connect() as c:c.executemany('INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',rows)
        del rows,store;gc.collect()
    mode='legacy' if args.legacy else 'fresh'
    out=Path('/evidence')/(('legacy-' if args.legacy else '')+args.arm);out.mkdir()
    result={'schema':'rc6.progressive624.exact-resource-ab.v1','purpose':'EXPERIMENT_SOURCE_OVERLAY_NOT_RELEASE_IMAGE',
        'native_host_acceptance':False,'arm':args.arm,'mode':mode,'source_sha':args.source,'python':sys.version,
        'database':str(database),'database_sha256':sha(database),'inode':database.stat().st_ino,
        'backend_sha256':sha(_native_json_bound.__file__),'catalog_count':sum(n for n,_ in FULL_CATALOG_SHAPE.values()),
        'ready_fixture_count':sum(n for _,n in FULL_CATALOG_SHAPE.values()),'ticks':[]}
    assert result['catalog_count']==14611 and result['ready_fixture_count']==9902
    if args.legacy:
        seed=Path('/evidence/baseline/shadow')
        assert (seed/'checkpoint.json.gz').read_bytes()[:4]==b'\x1f\x8b\x08\x00'
        result['legacy_seed_sha256']={p.name:sha(p) for p in seed.iterdir() if p.is_file()}
        shutil.copytree(seed,out/'shadow')
    private='spool_audit' in inspect.signature(ShadowRuntime).parameters
    assert private==(args.arm=='candidate')
    result['private_progressive_audit']=private
    worker=ShadowRuntime(database,evidence_root=out/'shadow',source_roots=[],**({'spool_audit':True} if private else {}))
    clocks=(86460,86490,172860,172890) if args.legacy else (0,30,86400,86430)
    result['source_clock_offsets_seconds']=list(clocks)
    for offset in clocks:
        at=PRE+timedelta(seconds=offset);wall=time.monotonic();cpu=time.process_time()
        report=worker.tick(at);cpu=time.process_time()-cpu;wall=time.monotonic()-wall
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        cut=read_committed_generation(worker.root);computed=cut['report']['computed_report']
        funnel=report['operational_funnel']
        row={'source_as_of':at.isoformat(),'wall':wall,'cpu':cpu,'peak_rss_kib':peak,'computed':computed,
            'timing':cut['status']['generation_timing'],
            'funnel':{'sha256':funnel['checkpoint_sha256'],'reaches':len(funnel['state_reaches']),
                      'cohorts':len(funnel['cohorts']),'sessions':funnel['sessions_retained'],'gaps':funnel['source_gaps']}}
        assert report['provider_requests']==report['real_orders_sent']==0 and report['real_routes']=='NOT_CALLED'
        del report,cut,funnel;row['idle']=release_idle_heap()
        raw=raw_sha(worker.root/computed['file'])
        assert raw['sha256']==computed['sha256'] and raw['bytes']==computed['raw_bytes']>100*1024**2
        row['raw_independent_verification']={**raw,'before_next_prune':True}
        row['checkpoint_raw']=raw_sha(worker.root/'checkpoint.json.gz')
        with EvidenceFiles(worker.root) as files:
            row['latest_payload_sha256']=hashlib.sha256(json.dumps(files.read('latest.json.gz'),
                sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()).hexdigest()
        result['ticks'].append(row);(out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'arm':args.arm,'mode':mode,**row}),flush=True)
    assert result['database_sha256']==sha(database)
    for row in manifest:assert sha(root/row['path'])==row['sha256']
    fallback=[name for name,module in sys.modules.items() if str(getattr(module,'__file__','')).startswith('/app/')]
    assert not fallback,('CONTROL_SOURCE_FALLBACK',fallback)


def compare(args):
    root=Path(args.directory);comparisons=[]
    for prefix in ('','legacy-'):
        b=json.loads((root/(prefix+'baseline/receipt.json')).read_text());a=json.loads((root/(prefix+'candidate/receipt.json')).read_text())
        for key in ('database','database_sha256','inode','python','backend_sha256','catalog_count','ready_fixture_count','source_clock_offsets_seconds','mode','legacy_seed_sha256'):
            assert b.get(key)==a.get(key),('CONTROL_MISMATCH',key)
        assert len(a['ticks'])==len(b['ticks'])==4
        rows=[]
        for x,y in zip(b['ticks'],a['ticks']):
            for key in ('source_as_of','funnel','raw_independent_verification','checkpoint_raw','latest_payload_sha256'):
                assert x[key]==y[key],('VALUE_MISMATCH',key)
            assert x['computed']['sha256']==y['computed']['sha256'] and x['computed']['raw_bytes']==y['computed']['raw_bytes']
            rows.append({'as_of':x['source_as_of'],'raw_bytes':x['computed']['raw_bytes'],'sha256':x['computed']['sha256'],
                'baseline_cpu':x['cpu'],'candidate_cpu':y['cpu'],'baseline_peak_rss_kib':x['peak_rss_kib'],'candidate_peak_rss_kib':y['peak_rss_kib']})
        before=max(x['baseline_peak_rss_kib'] for x in rows);after=max(x['candidate_peak_rss_kib'] for x in rows)
        rss_ratio=after/before;cpu_ratio=sum(x['candidate_cpu'] for x in rows)/sum(x['baseline_cpu'] for x in rows)
        result={'mode':a['mode'],'same_controls':True,'all_full_report_hashes_equal':True,
            'baseline_peak_rss_kib':before,'candidate_peak_rss_kib':after,'rss_ratio':rss_ratio,'cpu_ratio':cpu_ratio,'rows':rows}
        result['status']='GREEN_EXPERIMENT' if rss_ratio<=.60 and after<=192*1024 and cpu_ratio<=1.05 else 'RED_EXPERIMENT'
        comparisons.append(result)
    result={'schema':'rc6.progressive624.ab-comparison.v1','purpose':'EXPERIMENT_SOURCE_OVERLAY_NOT_RELEASE_IMAGE',
        'native_host_acceptance':False,'baseline_source':b['source_sha'],'candidate_source':a['source_sha'],
        'acceptance':{'max_rss_ratio':.60,'absolute_rss_kib_max':192*1024,'max_total_cpu_ratio':1.05,
            'cpu_boundary':'WHOLE_FOUR_CUT_SERIES; legacy normalization paid once; native120s remains required for each cut'},
        'comparisons':comparisons,'all_sixteen_full_audits_verified_before_pruning':True}
    result['status']='GREEN_EXPERIMENT' if all(x['status']=='GREEN_EXPERIMENT' for x in comparisons) else 'RED_EXPERIMENT'
    (root/'comparison.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
    if result['status']!='GREEN_EXPERIMENT':raise SystemExit(42)


if __name__=='__main__':
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    m=sub.add_parser('measure');m.add_argument('--arm',choices=['baseline','candidate'],required=True)
    m.add_argument('--source',required=True);m.add_argument('--legacy',action='store_true')
    c=sub.add_parser('compare');c.add_argument('--directory',required=True)
    args=parser.parse_args();measure(args) if args.action=='measure' else compare(args)
