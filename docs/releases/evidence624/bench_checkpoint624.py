"""Fixed-source four-cut A/B, including a second retained session; never host acceptance."""
import gc, hashlib, json, resource, sys, time
from datetime import timedelta
from pathlib import Path
root=Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
from tests.test_rc6_shadow_runtime_wiring import PRE
from rc6_shadow_runtime.worker import ShadowRuntime, release_idle_heap
from rc6_shadow_runtime.persistence import read_committed_generation, _native_json_bound
database=Path(sys.argv[2]).resolve();out=Path(sys.argv[3]).resolve();out.mkdir(parents=True,exist_ok=True)
assert _native_json_bound is not None
assert Path(sys.modules['rc6_shadow_runtime.worker'].__file__).resolve().is_relative_to(root)
worker=ShadowRuntime(database,evidence_root=out/'shadow',source_roots=[])
result={'schema':'rc6.checkpoint624.controlled-ab.v1','root':str(root),'native_backend':True,
 'python':sys.version,'database':str(database),'inode':database.stat().st_ino,
 'database_sha256':hashlib.sha256(database.read_bytes()).hexdigest(),
 'source_clock_policy':'PRE, PRE+30s, PRE+1day, PRE+1day+30s identical in both arms',
 'host_acceptance':False,'provider_requests':0,'real_orders_sent':0,'ticks':[]}
for offset in (0,30,86400,86430):
 at=PRE+timedelta(seconds=offset)
 wall=time.monotonic();cpu=time.process_time();report=worker.tick(at)
 cut=read_committed_generation(worker.root)
 funnel=report['operational_funnel']
 row={'source_as_of':at.isoformat(),'wall':time.monotonic()-wall,'cpu':time.process_time()-cpu,
      'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
      'computed':cut['report']['computed_report'],'timing':cut['status']['generation_timing'],
      'funnel':{'reaches':len(funnel['state_reaches']),'cohorts':len(funnel['cohorts']),
                'sha256':funnel['checkpoint_sha256'],'gaps':funnel['source_gaps'],
                'sessions':report['operational_funnel']['sessions_retained']}}
 del report,cut,funnel;gc.collect();row['idle']=release_idle_heap()
 result['ticks'].append(row)
 (out/'receipt.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(row,separators=(',',':')),flush=True)
assert result['database_sha256']==hashlib.sha256(database.read_bytes()).hexdigest()
