"""Bounded offline A/B in one frozen image; never production acceptance."""
import argparse
import gc
import gzip
import hashlib
import json
from pathlib import Path
import resource
import sys
import time
from datetime import timedelta


def sha(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(65536):value.update(block)
    return value.hexdigest()


def measure(args):
    root=Path('/experiment');sys.path.insert(0,str(root))
    from be_paper_engine import PaperStore
    from bf_production_paper_observer import _support_schema
    from cf_intraday_scalping import init_schema
    from scripts.rc6_postdeploy_stress import FULL_CATALOG_SHAPE
    from tests.test_rc6_shadow_runtime_wiring import PRE
    from rc6_shadow_runtime.worker import ShadowRuntime,release_idle_heap
    from rc6_shadow_runtime.persistence import read_committed_generation,_native_json_bound
    assert _native_json_bound is not None
    assert Path(sys.modules['rc6_shadow_runtime.worker'].__file__).is_relative_to(root)
    for row in json.loads(Path('/probe/source-manifest.json').read_text()):
        assert sha(root/row['path'])==row['sha256'],('IMPORT_SOURCE_DRIFT',row['path'])
    database=Path('/evidence/fixture.db')
    if not database.exists():
        assert args.arm=='baseline'
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
    out=Path('/evidence')/args.arm;out.mkdir()
    result={'schema':'rc6.checkpoint624.exact-resource-ab.v1','purpose':'EXPERIMENT_SOURCE_OVERLAY_NOT_RELEASE_IMAGE',
        'native_host_acceptance':False,'arm':args.arm,'source_sha':args.source,'python':sys.version,
        'database':str(database),'database_sha256':sha(database),'inode':database.stat().st_ino,
        'backend_sha256':sha(_native_json_bound.__file__),'catalog_count':sum(n for n,_ in FULL_CATALOG_SHAPE.values()),
        'ready_fixture_count':sum(n for _,n in FULL_CATALOG_SHAPE.values()),'ticks':[]}
    assert result['catalog_count']==14611 and result['ready_fixture_count']==9902
    worker=ShadowRuntime(database,evidence_root=out/'shadow',source_roots=[])
    for offset in (0,30,86400,86430):
        at=PRE+timedelta(seconds=offset);wall=time.monotonic();cpu=time.process_time()
        report=worker.tick(at);cpu=time.process_time()-cpu;wall=time.monotonic()-wall
        peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        cut=read_committed_generation(worker.root);computed=cut['report']['computed_report']
        raw_sha=hashlib.sha256();size=0
        with gzip.open(worker.root/computed['file'],'rb') as raw:
            while block:=raw.read(65536):size+=len(block);raw_sha.update(block)
        assert raw_sha.hexdigest()==computed['sha256'] and size==computed['raw_bytes']>100*1024**2
        funnel=report['operational_funnel']
        row={'source_as_of':at.isoformat(),'wall':wall,'cpu':cpu,'peak_rss_kib':peak,'computed':computed,
            'full_report_bytes_independently_verified':True,'timing':cut['status']['generation_timing'],
            'funnel':{'sha256':funnel['checkpoint_sha256'],'reaches':len(funnel['state_reaches']),
                      'cohorts':len(funnel['cohorts']),'sessions':funnel['sessions_retained'],'gaps':funnel['source_gaps']}}
        assert report['provider_requests']==report['real_orders_sent']==0 and report['real_routes']=='NOT_CALLED'
        del report,cut,funnel;row['idle']=release_idle_heap();result['ticks'].append(row)
        (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'arm':args.arm,**row}),flush=True)
    assert result['database_sha256']==sha(database)


def compare(args):
    root=Path(args.directory);b=json.loads((root/'baseline/receipt.json').read_text());a=json.loads((root/'candidate/receipt.json').read_text())
    for key in ('database','database_sha256','inode','python','backend_sha256','catalog_count','ready_fixture_count'):
        assert b[key]==a[key],('CONTROL_MISMATCH',key)
    assert len(a['ticks'])==len(b['ticks'])==4
    rows=[]
    for x,y in zip(b['ticks'],a['ticks']):
        assert x['source_as_of']==y['source_as_of'] and x['funnel']==y['funnel']
        assert x['computed']['sha256']==y['computed']['sha256'] and x['computed']['raw_bytes']==y['computed']['raw_bytes']
        assert x['full_report_bytes_independently_verified'] and y['full_report_bytes_independently_verified']
        rows.append({'as_of':x['source_as_of'],'raw_bytes':x['computed']['raw_bytes'],'sha256':x['computed']['sha256'],
            'baseline_cpu':x['cpu'],'candidate_cpu':y['cpu'],'baseline_peak_rss_kib':x['peak_rss_kib'],'candidate_peak_rss_kib':y['peak_rss_kib']})
    rss_ratio=max(x['candidate_peak_rss_kib'] for x in rows)/max(x['baseline_peak_rss_kib'] for x in rows)
    cpu_ratio=sum(x['candidate_cpu'] for x in rows)/sum(x['baseline_cpu'] for x in rows)
    result={'schema':'rc6.checkpoint624.ab-comparison.v1','purpose':'EXPERIMENT_SOURCE_OVERLAY_NOT_RELEASE_IMAGE',
        'native_host_acceptance':False,'baseline_source':b['source_sha'],'candidate_source':a['source_sha'],
        'same_controls':True,'all_full_report_hashes_equal':True,'rss_ratio':rss_ratio,'cpu_ratio':cpu_ratio,'rows':rows,
        'acceptance':{'max_rss_ratio':0.85,'max_total_cpu_ratio':1.10,'justification':'Bounded hashing may cost modest CPU; measured memory reduction is mandatory. Native120s/128MiB remain separate unchanged gates.'}}
    result['status']='GREEN_EXPERIMENT' if rss_ratio<=0.85 and cpu_ratio<=1.10 else 'RED_EXPERIMENT'
    (root/'comparison.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
    if result['status']!='GREEN_EXPERIMENT':raise SystemExit(42)


if __name__=='__main__':
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='action',required=True)
    m=sub.add_parser('measure');m.add_argument('--arm',choices=['baseline','candidate'],required=True);m.add_argument('--source',required=True)
    c=sub.add_parser('compare');c.add_argument('--directory',required=True)
    args=parser.parse_args();measure(args) if args.action=='measure' else compare(args)
