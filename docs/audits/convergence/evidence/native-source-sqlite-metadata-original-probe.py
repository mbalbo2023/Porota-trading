import hashlib,json,os,socket,sys,time
from pathlib import Path
sys.path.insert(0,'/tmp/rc6-v3-normal-107a8319-source')
from rc6_dynamic_universe.runtime import read_runtime
from rc6_shadow_runtime.worker import ShadowRuntime
from scripts.rc6_issue465_stress import AT
origin=Path('/tmp/rc6-v3-native-normal4-107a8319/data/paper_v17/observer_v17.db')
def raw(p):
 fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME)
 try:
  out=[]
  while b:=os.read(fd,65536):out.append(b)
  return b''.join(out)
 finally:os.close(fd)
def snap(p):
 result={}
 for suffix in ('','-wal','-shm','-journal'):
  v=Path(str(p)+suffix)
  if not v.exists():continue
  s=v.lstat();result[suffix]={k:getattr(s,k) for k in ('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_atime_ns','st_mtime_ns','st_ctime_ns')}
  result[suffix]['sha256']=hashlib.sha256(raw(v)).hexdigest()
 return result
calls=[]
def blocked(*a,**k):calls.append(1);raise AssertionError('NETWORK_FORBIDDEN')
socket.socket.connect=blocked;socket.create_connection=blocked;socket.getaddrinfo=blocked
origin_before=snap(origin);result=[]
for caller in ('read_runtime','metadata'):
 folder=Path('/tmp/rc6-source-sidecar-107a-'+caller);folder.mkdir(mode=0o700,exist_ok=False)
 database=folder/'source.db'
 for suffix in origin_before:Path(str(database)+suffix).write_bytes(raw(Path(str(origin)+suffix)))
 before=snap(database);begin=time.monotonic()
 if caller=='read_runtime':read_runtime(database,as_of=AT)
 else:ShadowRuntime(database,evidence_root=folder/'shadow',source_roots=[])._metadata(AT,AT.isoformat())
 after=snap(database)
 changes={suffix:{key:[before.get(suffix,{}).get(key),value] for key,value in member.items() if before.get(suffix,{}).get(key)!=value} for suffix,member in after.items()}
 changes={key:value for key,value in changes.items() if value}
 result.append({'caller':caller,'elapsed_seconds':time.monotonic()-begin,'source_unchanged':before==after,'changes':changes,'before':before,'after':after})
r={'schema':'rc6.native-source-sidecar-ro-probe.v1','source_sha':'107a83197a5f80e299fbe63c9b5ef27f853945be','scope':'PRIVATE_CLONES_OF_COMPLETED_SYNTHETIC_FIXTURE;ORIGIN_READ_NOATIME_NO_WRITES','origin_unchanged':origin_before==snap(origin),'provider_requests':len(calls),'results':result}
p=Path('/tmp/rc6_native_source_sidecar_atime_107a.json');p.write_text(json.dumps(r,sort_keys=True,indent=2)+'\n')
print(json.dumps({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'origin_unchanged':r['origin_unchanged'],'provider_requests':r['provider_requests'],'results':[{k:v for k,v in q.items() if k not in ('before','after')} for q in result]},sort_keys=True))
