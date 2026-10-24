"""Finite A3 whole-worker control in the frozen 7ce runtime, with new C ABI."""
import argparse
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import sys
import time
from datetime import timedelta

BASELINE = '7ce63ec6b3b65a0d39a93c11968d97dc510f1c02'
CANDIDATE = '5ed59faff3ace236098a05ecd777f07d444eddd1'

def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block := stream.read(65536):
            value.update(block)
    return value.hexdigest()

def raw_sha(path):
    value = hashlib.sha256(); size = 0
    with gzip.open(path, 'rb') as stream:
        while block := stream.read(65536):
            value.update(block); size += len(block)
    return {'sha256': value.hexdigest(), 'bytes': size}

def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + '\n')

def source_guard(root, manifest):
    for row in manifest:
        path = (root / row['path']).resolve()
        assert path.is_relative_to(root)
        assert sha(path) == row['sha256'], ('IMPORT_SOURCE_DRIFT', row['path'])

def measure(args):
    root = Path('/experiment'); sys.path.insert(0, str(root))
    assert os.getuid() == 1000
    cap = next(x.split()[1] for x in Path('/proc/self/status').read_text().splitlines() if x.startswith('CapEff:'))
    assert int(cap, 16) == 0 and sys.version_info[:3] == (3, 11, 16)
    from be_paper_engine import PaperStore
    from bf_production_paper_observer import _support_schema
    from cf_intraday_scalping import init_schema
    from scripts.rc6_postdeploy_stress import FULL_CATALOG_SHAPE
    from tests.test_rc6_shadow_runtime_wiring import PRE
    from rc6_shadow_runtime.worker import ShadowRuntime, release_idle_heap
    from rc6_shadow_runtime.persistence import read_committed_generation, _native_json_bound, digest
    assert _native_json_bound is not None
    assert Path(sys.modules['rc6_shadow_runtime.worker'].__file__).is_relative_to(root)
    assert Path(_native_json_bound.__file__).is_relative_to(root)
    manifest = json.loads(Path('/probe/source-manifest.json').read_text()); assert manifest
    source_guard(root, manifest)
    database = Path('/evidence/fixture.db')
    if not database.exists():
        assert args.arm == 'baseline' and not args.legacy
        store = PaperStore(str(database)); _support_schema(store); init_schema(store)
        rows = []
        for family, (total, ready) in FULL_CATALOG_SHAPE.items():
            for n in range(total):
                rows.append((f'LOAD_{family}_{n:05d}', family, 'BYMA', ('ARS','USD_MEP','USD_CCL')[n % 3],
                    'A-24HS','SYNTHETIC','synthetic public-cardinality fixture',PRE.isoformat(),
                    'stress-full-catalog','AVAILABLE' if n < ready else 'PAUSED_EXPLICIT',
                    'READY_PAPER_SPOT' if n < ready else 'NO_VERIFICADO','{}'))
        with store.connect() as c:
            c.executemany('INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        del rows, store; gc.collect()
        os.chmod(database, 0o444)
    mode = 'legacy' if args.legacy else 'fresh'
    out = Path('/evidence') / (('legacy-' if args.legacy else '') + args.arm); out.mkdir()
    deferred = args.arm == 'candidate'
    assert args.source == (CANDIDATE if deferred else BASELINE)
    result = {'schema':'rc6.a3-624.exact-resource-ab.v2','purpose':'EXPERIMENT_SOURCE_OVERLAY_NOT_RELEASE_IMAGE',
        'native_host_acceptance':False,'arm':args.arm,'mode':mode,'source_sha':args.source,'python':sys.version,
        'uid':os.getuid(),'cap_eff':cap,'database':str(database),'database_sha256':sha(database),'inode':database.stat().st_ino,
        'backend_sha256':sha(_native_json_bound.__file__),'backend_source_sha256':sha(root/'rc6_shadow_runtime/_rc6_json_bound.c'),
        'catalog_count':sum(n for n,_ in FULL_CATALOG_SHAPE.values()),'ready_fixture_count':sum(n for _,n in FULL_CATALOG_SHAPE.values()),
        'spool_audit':True,'defer_audit':deferred,'ticks':[]}
    assert result['catalog_count'] == 14611 and result['ready_fixture_count'] == 9902
    if args.legacy:
        seed = Path('/evidence/legacy-seed')
        assert (seed/'checkpoint.json.gz').read_bytes()[:4] == b'\x1f\x8b\x08\x00'
        result['legacy_seed_sha256'] = {p.name:sha(p) for p in seed.iterdir() if p.is_file()}
        shutil.copytree(seed, out/'shadow')
    kwargs = {'spool_audit':True}
    if deferred:
        kwargs['defer_audit'] = True
    worker = ShadowRuntime(database, evidence_root=out/'shadow', source_roots=[], **kwargs)
    clocks = (86460,86490,172860,172890) if args.legacy else (0,30,86400,86430)
    result['source_clock_offsets_seconds'] = list(clocks)
    for offset in clocks:
        at = PRE + timedelta(seconds=offset); wall = time.monotonic(); cpu = time.process_time()
        report = worker.tick(at); cut = read_committed_generation(worker.root)
        hot_cpu = time.process_time()-cpu; hot_wall = time.monotonic()-wall
        funnel = report['operational_funnel']
        row = {'source_as_of':at.isoformat(),'hot_cpu':hot_cpu,'hot_wall':hot_wall,
            'timing':cut['status']['generation_timing'],'ready_count':cut['status']['catalog_ready_count'],
            'engines':len(report['engines']),'funnel':{'checkpoint_sha256':funnel['checkpoint_sha256'],
                'sessions':funnel['sessions_retained']}}
        assert report['provider_requests'] == report['real_orders_sent'] == 0 and report['real_routes'] == 'NOT_CALLED'
        computed = cut['report']['computed_report']; del report, cut, funnel
        cold_cpu = cold_wall = 0
        if deferred:
            from rc6_shadow_runtime.cold_audit import drain, prune_completed, Directory
            cw = time.monotonic(); cc = time.process_time()
            with worker.files as files:
                complete = drain(files); prune_completed(files)
                with Directory(files) as directory:
                    queue = directory.read('queue.json')
                    snap = directory.read('manifest-'+queue['snapshot_id']+'.json')
                    row['snapshot_bytes'] = snap['pack_bytes']
            cold_cpu = time.process_time()-cc; cold_wall = time.monotonic()-cw
            assert complete['state'] == 'COMPLETE' and complete['source_as_of'] == at.isoformat()
            computed = complete['computed_report']; row['completion'] = complete
        row.update(cold_cpu=cold_cpu,cold_wall=cold_wall,cpu=hot_cpu+cold_cpu,wall=hot_wall+cold_wall,
            computed=computed,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        raw = raw_sha(worker.root/computed['file'])
        assert raw['sha256'] == computed['sha256'] and raw['bytes'] == computed['raw_bytes'] > 100*1024**2
        row['raw_independent_verification'] = {**raw,'before_next_prune':True}
        row['checkpoint_raw'] = raw_sha(worker.root/'checkpoint.json.gz')
        with worker.files as files:
            latest = files.read('latest.json.gz'); latest['computed_report'] = computed
            row['normalized_operational_sha256'] = digest(latest); del latest
        assert row['ready_count'] == 9902 and row['engines'] == 2
        gc.collect(); row['idle'] = release_idle_heap()
        result['ticks'].append(row); write(out/'receipt.json', result)
        print(json.dumps({'arm':args.arm,'mode':mode,**row}), flush=True)
    assert result['database_sha256'] == sha(database)
    source_guard(root, manifest)
    fallback = [name for name,module in sys.modules.items() if str(getattr(module,'__file__','')).startswith('/app/')]
    assert not fallback, ('CONTROL_SOURCE_FALLBACK', fallback)
    if args.arm == 'baseline' and not args.legacy:
        seed = Path('/evidence/legacy-seed'); shutil.copytree(out/'shadow', seed)
        old = raw_sha(seed/'checkpoint.json.gz')
        with gzip.open(seed/'checkpoint.json.gz','rb') as source, (seed/'checkpoint-standard.tmp').open('wb') as target:
            with gzip.GzipFile(filename='',mode='wb',fileobj=target,mtime=0,compresslevel=1) as compressed:
                shutil.copyfileobj(source, compressed, 65536)
        (seed/'checkpoint-standard.tmp').replace(seed/'checkpoint.json.gz')
        assert raw_sha(seed/'checkpoint.json.gz') == old
        assert (seed/'checkpoint.json.gz').read_bytes()[:4] == b'\x1f\x8b\x08\x00'
        write(Path('/evidence/legacy-seed-proof.json'), {'source':'baseline-fresh-cut4','raw_checkpoint':old,
            'standard_gzip':True,'files':{p.name:sha(p) for p in seed.iterdir() if p.is_file()}})

def compare(args):
    root = Path(args.directory); comparisons = []
    for prefix in ('','legacy-'):
        before = json.loads((root/(prefix+'baseline/receipt.json')).read_text())
        after = json.loads((root/(prefix+'candidate/receipt.json')).read_text())
        for key in ('database','database_sha256','inode','python','catalog_count','ready_fixture_count','source_clock_offsets_seconds','mode','legacy_seed_sha256'):
            assert before.get(key) == after.get(key), ('CONTROL_MISMATCH', key)
        assert before['source_sha'] == BASELINE and after['source_sha'] == CANDIDATE
        assert before['backend_sha256'] != after['backend_sha256']
        assert before['backend_source_sha256'] != after['backend_source_sha256']
        assert len(before['ticks']) == len(after['ticks']) == 4
        rows = []
        for x,y in zip(before['ticks'],after['ticks']):
            for key in ('source_as_of','ready_count','engines','funnel','raw_independent_verification','checkpoint_raw','normalized_operational_sha256'):
                assert x[key] == y[key], ('VALUE_MISMATCH', key)
            assert x['computed']['sha256'] == y['computed']['sha256'] and x['computed']['raw_bytes'] == y['computed']['raw_bytes']
            rows.append({'as_of':x['source_as_of'],'raw_bytes':x['computed']['raw_bytes'],'sha256':x['computed']['sha256'],
                'baseline_cpu':x['cpu'],'candidate_hot_cpu':y['hot_cpu'],'candidate_cold_cpu':y['cold_cpu'],
                'candidate_cpu':y['cpu'],'candidate_cold_wall':y['cold_wall'],
                'baseline_peak_rss_kib':x['peak_rss_kib'],'candidate_peak_rss_kib':y['peak_rss_kib']})
        denominator = sum(x['baseline_cpu'] for x in rows)
        hot = sum(x['candidate_hot_cpu'] for x in rows)/denominator
        total = sum(x['candidate_cpu'] for x in rows)/denominator
        peak = max(x['candidate_peak_rss_kib'] for x in rows)
        cold = max(x['candidate_cold_wall'] for x in rows)
        value = {'mode':after['mode'],'same_controls':True,'all_full_report_hashes_equal':True,
            'hot_cpu_ratio':hot,'total_cpu_ratio':total,'candidate_peak_rss_kib':peak,'max_cold_wall':cold,'rows':rows}
        value['status'] = 'GREEN_EXPERIMENT' if hot <= .80 and total <= 1.30 and peak <= 192*1024 and cold <= 30 else 'RED_EXPERIMENT'
        comparisons.append(value)
    result = {'schema':'rc6.a3-624.ab-comparison.v2','purpose':'EXPERIMENT_SOURCE_OVERLAY_NOT_RELEASE_IMAGE',
        'native_host_acceptance':False,'baseline_source':BASELINE,'candidate_source':CANDIDATE,
        'acceptance':{'max_hot_cpu_ratio':.80,'max_total_cpu_ratio':1.30,'absolute_rss_kib_max':192*1024,'max_cold_wall':30,
            'cpu_boundary':'WHOLE_FOUR_CUT_SERIES; legacy normalization paid once; native120s remains mandatory'},
        'comparisons':comparisons,'all_sixteen_full_audits_verified_before_pruning':True}
    result['status'] = 'GREEN_EXPERIMENT' if all(x['status']=='GREEN_EXPERIMENT' for x in comparisons) else 'RED_EXPERIMENT'
    write(root/'comparison.json', result); print(json.dumps(result), flush=True)
    if result['status'] != 'GREEN_EXPERIMENT':
        raise SystemExit(42)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(); sub = parser.add_subparsers(dest='action',required=True)
    m = sub.add_parser('measure'); m.add_argument('--arm',choices=['baseline','candidate'],required=True)
    m.add_argument('--source',required=True); m.add_argument('--legacy',action='store_true')
    c = sub.add_parser('compare'); c.add_argument('--directory',required=True)
    args = parser.parse_args(); measure(args) if args.action == 'measure' else compare(args)
