"""One isolated local scaling sample; not native host acceptance."""
from datetime import timedelta
import gc,json,resource,sys,tempfile,time
from pathlib import Path
root=Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
from scripts.rc6_postdeploy_stress import FULL_CATALOG_SHAPE
from be_paper_engine import PaperStore
from bf_production_paper_observer import _support_schema
from cf_intraday_scalping import init_schema
from tests.test_rc6_shadow_runtime_wiring import PRE
from rc6_shadow_runtime.worker import ShadowRuntime,release_idle_heap
from rc6_shadow_runtime.persistence import read_committed_generation,_native_json_bound
from rc6_dynamic_universe.runtime import read_runtime
size=int(sys.argv[2]);out=Path(sys.argv[3]);out.parent.mkdir(parents=True,exist_ok=True)
shape=sum(n for n,_ in FULL_CATALOG_SHAPE.values())
counts={k:int(n*size/shape) for k,(n,_) in FULL_CATALOG_SHAPE.items()}
remaining=size-sum(counts.values())
for k in sorted(counts,key=lambda k:FULL_CATALOG_SHAPE[k][0]*size/shape-counts[k],reverse=True)[:remaining]:counts[k]+=1
with tempfile.TemporaryDirectory(prefix='scale624-') as tmp:
 folder=Path(tmp);store=PaperStore(str(folder/'paper.db'));_support_schema(store);init_schema(store)
 rows=[];ready=0
 for family,count in counts.items():
  original,ready_original=FULL_CATALOG_SHAPE[family];r=round(count*ready_original/original);ready+=r
  for n in range(count):
   rows.append((f'LOAD_{family}_{n:05d}',family,'BYMA',('ARS','USD_MEP','USD_CCL')[n%3],'A-24HS','SYNTHETIC','synthetic public-cardinality fixture',PRE.isoformat(),'stress-full-catalog','AVAILABLE' if n<r else 'PAUSED_EXPLICIT','READY_PAPER_SPOT' if n<r else 'NO_VERIFICADO','{}'))
 with store.connect() as c:c.executemany('INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',rows)
 del rows;gc.collect();result={'catalog_count':size,'shape':counts,'ready_fixture_count':ready,'native_backend':_native_json_bound is not None,'import_root':str(root),'host_acceptance':False,'provider_requests':0,'real_orders_sent':0,'clocks':'PRE fixed; second PRE+30s; no physical cadence claim'}
 if size>20000:
  started=time.monotonic()
  try:read_runtime(store.path,as_of=PRE,row_limit=20000,query_budget_seconds=2)
  except ValueError as exc:result.update(boundary_status='REJECTED',reason=str(exc),elapsed=time.monotonic()-started)
  else:raise AssertionError('OVER_LIMIT_CATALOG_ACCEPTED')
 else:
  worker=ShadowRuntime(store.path,evidence_root=folder/'shadow',source_roots=[]);result['ticks']=[]
  for n in range(2):
   started=time.monotonic();cpu=time.process_time();report=worker.tick(PRE+timedelta(seconds=n*30))
   value=read_committed_generation(worker.root)
   item={'wall':time.monotonic()-started,'cpu':time.process_time()-cpu,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'engine_count':len(report['engines']),'report':value['report']['computed_report'],'timing':value['status']['generation_timing']}
   del report,value;item['idle_allocator']=release_idle_heap();result['ticks'].append(item)
 out.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,separators=(',',':')))
