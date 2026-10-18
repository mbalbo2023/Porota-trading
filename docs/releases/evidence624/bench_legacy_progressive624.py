"""One genuine legacy-checkpoint bootstrap at full catalogue scale, fresh process."""
import sys,json,hashlib,gzip,shutil,resource,time,inspect
from pathlib import Path
from datetime import timedelta
root,database,seed,out=map(lambda p:Path(p).resolve(),sys.argv[1:])
sys.path.insert(0,str(root))
from rc6_shadow_runtime.worker import ShadowRuntime
from rc6_shadow_runtime.persistence import EvidenceFiles,read_committed_generation,_native_json_bound
from tests.test_rc6_shadow_runtime_wiring import PRE
assert _native_json_bound is not None
assert Path(sys.modules['rc6_shadow_runtime.worker'].__file__).is_relative_to(root)
out.mkdir(exist_ok=False);shadow=out/'shadow';shutil.copytree(seed,shadow)
source={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in seed.iterdir() if p.is_file()}
private='spool_audit' in inspect.signature(ShadowRuntime).parameters
worker=ShadowRuntime(database,evidence_root=shadow,source_roots=[],**({'spool_audit':True} if private else {}))
start,cpu=time.monotonic(),time.process_time();report=worker.tick(PRE+timedelta(seconds=86460))
row={'private_progressive':private,'wall':time.monotonic()-start,'cpu':time.process_time()-cpu,
     'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
     'database_sha256':hashlib.sha256(database.read_bytes()).hexdigest(),
     'database_inode':database.stat().st_ino,'legacy_seed_sha256':source,'source_root':str(root),
     'real_orders_sent':report['real_orders_sent'],'real_routes':report['real_routes']}
cut=read_committed_generation(shadow);row['computed']=cut['report']['computed_report'];row['timing']=cut['status']['generation_timing']
row['independent_raw_sha256']={}
for role,path in [('computed',shadow/row['computed']['file']),('checkpoint',shadow/'checkpoint.json.gz')]:
 h=hashlib.sha256();count=0
 with gzip.open(path,'rb') as stream:
  while block:=stream.read(65536):h.update(block);count+=len(block)
 row['independent_raw_sha256'][role]={'sha256':h.hexdigest(),'bytes':count}
with EvidenceFiles(shadow) as files:
 row['latest_payload_sha256']=hashlib.sha256(json.dumps(files.read('latest.json.gz'),sort_keys=True,separators=(',',':')).encode()).hexdigest()
assert row['independent_raw_sha256']['computed']['sha256']==row['computed']['sha256']
assert row['computed']['raw_bytes']>100*1024**2 and row['real_orders_sent']==0 and row['real_routes']=='NOT_CALLED'
(out/'receipt.json').write_text(json.dumps(row,indent=2)+'\n');print(json.dumps(row),flush=True)
