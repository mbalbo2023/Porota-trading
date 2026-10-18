import cProfile, gc, hashlib, json, resource, sys, tempfile, time
from datetime import timedelta
from pathlib import Path
root=Path(sys.argv[1]).resolve();sys.path.insert(0,str(root))
from scripts.rc6_postdeploy_stress import FULL_CATALOG_SHAPE
from be_paper_engine import PaperStore
from bf_production_paper_observer import _support_schema
from cf_intraday_scalping import init_schema
from tests.test_rc6_shadow_runtime_wiring import PRE
from rc6_shadow_runtime.worker import ShadowRuntime
from rc6_shadow_runtime.persistence import EvidenceFiles, read_committed_generation, _native_json_bound
out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
folder=Path(tempfile.mkdtemp(prefix='profile-',dir=out))
database=Path(sys.argv[3]).resolve() if len(sys.argv)>3 else folder/'paper.db'
exists=database.exists()
store=PaperStore(str(database));_support_schema(store);init_schema(store)
rows=[]
for family,(total,ready) in FULL_CATALOG_SHAPE.items():
    for n in range(total):
        rows.append((f'LOAD_{family}_{n:05d}',family,'BYMA',('ARS','USD_MEP','USD_CCL')[n%3],'A-24HS','SYNTHETIC','synthetic public-cardinality fixture',PRE.isoformat(),'stress-full-catalog','AVAILABLE' if n<ready else 'PAUSED_EXPLICIT','READY_PAPER_SPOT' if n<ready else 'NO_VERIFICADO','{}'))
if not exists:
    with store.connect() as c:c.executemany('INSERT INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',rows)
fixture_sha=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest();del rows;gc.collect()
worker=ShadowRuntime(store.path,evidence_root=folder/'shadow',source_roots=[])
measurements=[]
def wrap(name):
    orig=getattr(EvidenceFiles,name)
    def observed(self,*args,**kwargs):
        t=time.monotonic();p=time.process_time()
        result=orig(self,*args,**kwargs)
        measurements.append({'operation':name,'name':args[0] if args and isinstance(args[0],str) else None,'wall':time.monotonic()-t,'cpu':time.process_time()-p})
        return result
    setattr(EvidenceFiles,name,observed)
for name in ('read','write','write_streamed_report','publish_generation','prune_computed_reports'):wrap(name)
assert Path(sys.modules['rc6_shadow_runtime.worker'].__file__).resolve().is_relative_to(root)
receipts=[]
for n in range(2):
    t=time.monotonic();p=time.process_time();prof=cProfile.Profile();prof.enable()
    report=worker.tick(PRE+timedelta(seconds=30*n));prof.disable();prof.dump_stats(str(out/f'tick-{n}.prof'))
    receipts.append({'tick':n,'wall':time.monotonic()-t,'cpu':time.process_time()-p,'rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'computed':read_committed_generation(worker.root)['report']['computed_report']})
    del report;gc.collect()
    print(json.dumps(receipts[-1]),flush=True)
receipt={'root':str(root),'folder':str(folder),'native_backend':_native_json_bound is not None,'fixture_sha256':fixture_sha,'measurements':measurements,'ticks':receipts}
(out/'receipt.json').write_text(json.dumps(receipt,indent=2))
