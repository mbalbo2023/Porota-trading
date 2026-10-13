import gc,gzip,hashlib,io,json,os,resource,subprocess,sys,time,zlib
from datetime import timedelta
from pathlib import Path
sys.path.insert(0,'/workspace/porota_rc6_worktrees/persistence')
from scripts.rc6_issue465_stress import fixture_database,PRE,AT
from rc6_shadow_runtime.worker import ShadowRuntime
from rc6_shadow_runtime.retention import EvidenceRetention
from rc6_shadow_runtime.archive_namespace import inspect_archive

base=Path('/tmp/rc6-native-normal1200-732b1e51-four-cuts');base.mkdir()
store=fixture_database(base/'source.db',catalog_count=1200)
source=Path(store.path);del store;gc.collect()
def read(path):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME)
 with os.fdopen(fd,'rb') as stream:return stream.read()
source_sha=hashlib.sha256(read(source)).hexdigest()
worker=ShadowRuntime.from_environment(source,evidence_root=base/'shadow',source_roots=[])
archive=worker.files.archive_root
result={'source_sha':subprocess.check_output(['git','rev-parse','HEAD'],cwd='/workspace/porota_rc6_worktrees/persistence',text=True).strip(),
 'source_tree':subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd='/workspace/porota_rc6_worktrees/persistence',text=True).strip(),
 'profile':'OFFLINE_SYNTHETIC_NATIVE_1200_IDENTITIES_6000_INPUT_OBSERVATIONS', 'gate_claim':False,
 'archive_maximum_bytes':worker.files.archive_maximum_bytes,'archive_root':str(archive),'phases':[],'delta_models':[]}
objects=[]
for at in [PRE,AT,AT+timedelta(seconds=30),AT+timedelta(seconds=60)]:
 wall,cpu=time.monotonic(),time.process_time();report=worker.tick(at)
 node={'as_of':at.isoformat(),'phase':report['phase'],'sequence':report['sequence'],'generation_id':report['generation_id'],
  'checkpoint_reused':report['checkpoint_reused'],'cpu_seconds':time.process_time()-cpu,'wall_seconds':time.monotonic()-wall,
  'catalog_ready_count':len(report['catalog_ready']), 'configuration_fingerprint':report['configuration_fingerprint'],
  'provider_requests':report['provider_requests'],'real_orders_sent':report['real_orders_sent'],'real_routes':report['real_routes']}
 generation=worker.root/('gen-'+report['generation_id']);del report;gc.collect()
 archiver=EvidenceRetention(worker.root,archive_root=archive)
 wall,cpu=time.monotonic(),time.process_time();receipt=archiver.archive_generation(generation)
 data=read(archive/(receipt['generation_id']+'.tar.gz'))
 node.update(archive_cpu_seconds=time.process_time()-cpu,archive_wall_seconds=time.monotonic()-wall,
  archive_object_bytes=len(data), archive_receipt_bytes=(archive/(receipt['generation_id']+'.receipt.json')).stat().st_size,
  member_bytes={p.name:p.stat().st_size for p in generation.iterdir()},receipt_digest=hashlib.sha256(read(archive/(receipt['generation_id']+'.receipt.json'))).hexdigest())
 result['phases'].append(node);objects.append(data)
 (base/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(node),flush=True)

# Exact-byte offline delta models, not a production format or gate. Every
# compressed chunk is decoded against the same declared prior-byte dictionary.
for previous,current in zip(objects[1:],objects[2:]):
 for model in ['PREVIOUS_WINDOW_32K','PREVIOUS_WINDOW_4K_EIGHT_BIT_ALIGNMENTS']:
  compressed_size=chunks=0; started=time.monotonic();cpu=time.process_time()
  for offset in range(0,len(current),3072):
   raw=current[offset:offset+3072]
   start=max(0,min(len(previous)-4097,offset-512))
   if model=='PREVIOUS_WINDOW_32K':
    start=max(0,min(len(previous)-32768,offset-16384));dictionary=previous[start:start+32768]
   else:
    block=previous[start:start+4097]
    integer=int.from_bytes(block,'big');mask=(1<<(4096*8))-1
    dictionary=b''.join(((integer>>shift)&mask).to_bytes(4096,'big') for shift in range(8))
   codec=zlib.compressobj(level=1,zdict=dictionary);encoded=codec.compress(raw)+codec.flush()
   decoder=zlib.decompressobj(zdict=dictionary);decoded=decoder.decompress(encoded)+decoder.flush()
   assert decoded==raw and decoder.eof and not decoder.unused_data
   compressed_size+=len(encoded);chunks+=1
  # Explicit conservative 96-byte per-chunk metadata, receipt/control2KiB.
  stored_bound=compressed_size+96*chunks+2048
  result['delta_models'].append({'model':model,'previous_object_bytes':len(previous),'target_object_bytes':len(current),
   'compressed_bytes':compressed_size,'chunks':chunks,'metadata_bytes_per_chunk':96,'physical_estimate_with_metadata_bytes':stored_bound,
   'ratio_with_metadata':stored_bound/len(current),'wall_seconds':time.monotonic()-started,'cpu_seconds':time.process_time()-cpu,
   'all_original_archive_bytes_roundtrip_equal':True,'production_implementation':False})
 result['source_database_bytes_unchanged']=source_sha==hashlib.sha256(read(source)).hexdigest()
 result['peak_rss_bytes']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024
 result['archive_admission']=inspect_archive(archive)
 (base/'result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'result_path':str(base/'result.json'),'delta_models':result['delta_models'],'archive_admission':result['archive_admission'],'peak_rss_bytes':result['peak_rss_bytes']}),flush=True)
