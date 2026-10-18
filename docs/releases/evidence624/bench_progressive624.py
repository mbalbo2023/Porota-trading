"""Four comparable physical computations; all source clocks remain fixed fixture clocks."""
import gc
import gzip
import hashlib
import json
import resource
import sys
import time
from datetime import timedelta
from pathlib import Path

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from tests.test_rc6_shadow_runtime_wiring import PRE
from rc6_shadow_runtime.worker import ShadowRuntime, release_idle_heap
from rc6_shadow_runtime.persistence import read_committed_generation, _native_json_bound

database, output = Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve()
output.mkdir(exist_ok=False)
assert _native_json_bound is not None
assert Path(sys.modules['rc6_shadow_runtime.worker'].__file__).resolve().is_relative_to(root)
manifest = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*.py'))}
result = {'schema': 'rc6.progressive624.controlled-ab.v1', 'root': str(root),
          'database': str(database), 'inode': database.stat().st_ino,
          'database_sha256': hashlib.sha256(database.read_bytes()).hexdigest(),
          'python': sys.version, 'native_backend': True,
          'backend_sha256': hashlib.sha256((root/'rc6_shadow_runtime/_rc6_json_bound.so').read_bytes()).hexdigest(),
          'source_clock_policy': 'PRE, PRE+30s, PRE+1day, PRE+1day+30s',
          'native_host_acceptance': False, 'ticks': []}
(output/'source-manifest.json').write_text(json.dumps(manifest, sort_keys=True)+'\n')
import inspect
private_args = {'spool_audit': True} if 'spool_audit' in inspect.signature(ShadowRuntime).parameters else {}
result['private_progressive_audit'] = bool(private_args)
worker = ShadowRuntime(database, evidence_root=output/'shadow', source_roots=[], **private_args)
for offset in (0, 30, 86400, 86430):
    at = PRE + timedelta(seconds=offset)
    wall, cpu = time.monotonic(), time.process_time()
    report = worker.tick(at)
    cut = read_committed_generation(worker.root)
    funnel = report['operational_funnel']
    row = {'source_as_of': at.isoformat(), 'wall': time.monotonic()-wall,
           'cpu': time.process_time()-cpu,
           'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
           'computed': cut['report']['computed_report'], 'timing': cut['status']['generation_timing'],
           'ready_count': cut['status']['catalog_ready_count'], 'engines': len(report['engines']),
           'funnel': {'reaches': len(funnel['state_reaches']), 'cohorts': len(funnel['cohorts']),
                      'sha256': funnel['checkpoint_sha256'], 'gaps': funnel['source_gaps'],
                      'sessions': funnel['sessions_retained']}}
    del report, cut, funnel
    gc.collect()
    row['idle'] = release_idle_heap()
    # This read is outside the measured computation and after releasing its
    # full graph. Verify EACH real full report before normal pruning can remove it.
    path = worker.root/row['computed']['file']
    assert path.resolve().is_relative_to(worker.root.resolve())
    hasher, count = hashlib.sha256(), 0
    with gzip.open(path, 'rb') as stream:
        while block := stream.read(262144):
            count += len(block)
            hasher.update(block)
    assert count == row['computed']['raw_bytes'] and count > 100*1024**2
    assert hasher.hexdigest() == row['computed']['sha256']
    row['checkpoint_raw_sha256'] = hashlib.sha256(gzip.decompress((worker.root/'checkpoint.json.gz').read_bytes())).hexdigest()
    with worker.files as files:
        row['latest_canonical_sha256'] = hashlib.sha256(json.dumps(files.read('latest.json.gz'),sort_keys=True,separators=(',',':'),default=str,allow_nan=False).encode()).hexdigest()
    row['raw_independent_verification'] = {'bytes': count, 'sha256': hasher.hexdigest(),
                                          'before_next_prune': True}
    assert row['ready_count'] == 9902 and row['engines'] == 2
    result['ticks'].append(row)
    (output/'receipt.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(row, separators=(',', ':')), flush=True)
assert result['database_sha256'] == hashlib.sha256(database.read_bytes()).hexdigest()
assert manifest == {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(root.rglob('*.py'))}
