import hashlib,importlib.metadata,json,os,re,runpy,stat,subprocess,sys,time,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=Path('/workspace/scratch/rc6-readonly-3091e93-ofT80Pum/source')
EPOCH=sys.argv[1]
assert EPOCH in ('311','312')
PY=SOURCE.parent/('venv'+EPOCH)/'bin/python'
assert sys.executable==str(PY)
assert tuple(sys.version_info[:2])==((3,11) if EPOCH=='311' else (3,12))
os.umask(0o022)
OUT=ROOT/('v2-epoch'+EPOCH)
OUT.mkdir(mode=0o700)
for name in ('hypothesis','logs','tmp'):(OUT/name).mkdir(mode=0o700)
EXPECTED={'scripts/rc6_controlled_native_child_manager.py': '55325b3108e175a42b87ebe544fd307fa45ffd29b6f7ab471443803ad9ba53b8', 'scripts/porota_predeploy_test_workspace.py': 'e768b5ca51df5646cac1c2739fecb35aa394cbcedecc6d428ddf8f47b7b21e85', 'scripts/rc6_controlled_governed_runner.py': '316e22fc9711c2e8bea0f2af7615cdb1cd84fb55f2b0c611c38372d50d59ba67', 'tests/test_rc6_controlled_native_child_manager.py': 'c91be232136465265fc3971d0516d8c503620f402c20a9aacf4bf06aa2cdff33', 'tests/test_rc6_predeploy_scoped_cleanup.py': '6443a48ad2ca5a68ee281062c392ff97c27eec836638707eb8556228e31e727f', 'scripts/porota_predeploy_cleanup.py': '31f54855e581c48ffa2ba150819157ae0672390961b452e13eceafe33a85e85f', 'docs/audits/rc6-convergence-persistence-evidence/run_full_horizon_1201_v2.py.source': 'ac9ba0f457ee4592646d91c5654f037efbfa2bc4f758469e16b7cf51d87e7aec', '.github/workflows/porota-predeploy-v2.yml': '45b5ba23d59843cdc2ea84f458e40c214ae3b78fb23d44cd16ec0fd22a3c2125', 'tests/test_ci_productive_predeploy_contract.py': '18e0840717256861c2f9a187aac88a96e36252339a6b854da2a29bb328c3c1f2'}
MEMBERS=tuple(EXPECTED)
STATS=('st_dev','st_ino','st_uid','st_gid','st_mode','st_nlink','st_size','st_blocks','st_atime_ns','st_mtime_ns','st_ctime_ns')
def attrs(st):return {name:getattr(st,name) for name in STATS}
def read(path,maximum=2*1024**2):
 fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
 try:
  st=os.fstat(fd);before=attrs(st)
  assert stat.S_ISREG(st.st_mode) and st.st_nlink==1 and st.st_uid==os.geteuid() and st.st_size<=maximum
  pieces=[];size=0
  while b:=os.read(fd,min(1024**2,maximum+1-size)):
   pieces.append(b);size+=len(b)
   assert size<=maximum
  assert size==st.st_size and attrs(os.fstat(fd))==before and attrs(path.lstat())==before
  return b''.join(pieces),before
 finally:os.close(fd)
def capture(path):
 raw,info=read(path)
 return {'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'stat':info}
def source_capture():return {name:capture(SOURCE/name) for name in MEMBERS}
def stable10(before,after):
 return all(before[name]['sha256']==after[name]['sha256'] and before[name]['bytes']==after[name]['bytes']
  and {k:v for k,v in before[name]['stat'].items() if k!='st_atime_ns'}==
      {k:v for k,v in after[name]['stat'].items() if k!='st_atime_ns'} for name in MEMBERS)
def git_identity():
 env={**os.environ,'GIT_NO_REPLACE_OBJECTS':'1','GIT_NO_LAZY_FETCH':'1','GIT_OPTIONAL_LOCKS':'0'}
 return {label:subprocess.check_output(['git','--no-replace-objects','-C',str(SOURCE),'rev-parse',value],env=env,text=True,timeout=10).strip()
         for label,value in (('sha','HEAD'),('tree','HEAD^{tree}'))}
def closure():
 rows=sorted([{'name':re.sub(r'[-_.]+','-',d.metadata['Name']).lower(),'version':d.version}
              for d in importlib.metadata.distributions()],key=lambda r:(r['name'],r['version']))
 assert len(rows)==157 and len({r['name'] for r in rows})==157
 return rows
def save(path,value):
 raw=(json.dumps(value,indent=2,sort_keys=True)+'\n').encode()
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
 with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
 return hashlib.sha256(raw).hexdigest()
original=SOURCE/'docs/audits/rc6-convergence-persistence-evidence/run_full_horizon_1201_v2.py.source'
assert capture(original)['sha256']==EXPECTED[str(original.relative_to(SOURCE))]
driver=runpy.run_path(str(original))
before_kernel=driver['pre_capture_kernel_state']()
source_before=source_capture()
assert {n:r['sha256'] for n,r in source_before.items()}==EXPECTED
head_before=git_identity();closure_before=closure()
env={k:v for k,v in os.environ.items() if not k.startswith(('POROTA_','PAPER_','PPI_','IOL_','BYMA_','PYTHON')) and k not in ('DATA_DIR','HIST_DB_PATH')}
env.update(PYTHONDONTWRITEBYTECODE='1',PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',HYPOTHESIS_STORAGE_DIRECTORY=str(OUT/'hypothesis'),LOG_DIR=str(OUT/'logs'),RUNNER_TEMP=str(OUT),TMPDIR=str(OUT/'tmp'))
files=['tests/test_ci_productive_predeploy_contract.py','tests/test_rc6_predeploy_scoped_cleanup.py','tests/test_rc6_controlled_native_child_manager.py']
common=[str(PY),'-B','-m','pytest','-q',*files,'-p','no:cacheprovider']
collection_argv=common+['--collect-only','-v']
execution_argv=common+['--basetemp='+str(OUT/'pytest'),'--junitxml='+str(OUT/'junit.xml')]
save(OUT/'launch.json',{'epoch':EPOCH,'collection_argv':collection_argv,'execution_argv':execution_argv,'cwd':str(SOURCE),'source_before':source_before,'python':sys.version,'actual_kernel_before':before_kernel,'git_head_before':head_before,'installed_metadata_before':closure_before,'source_qualification':'UNCOMMITTED_DIAGNOSTIC_ONLY_SOURCE_SHA_QUALIFICATION_PENDING','expected_cases':67,'producer_scope':'PROGRAMMATIC_FOCALS_NOT_FULL_GOV_OR_PREDEPLOY','real_orders_sent':0,'PAPER_SHADOW_ONLY':True})
progress_seq=0
phase='collection'
def progress(stage,pid,entered,deadline,fd):
 global progress_seq
 row={'phase':phase,'stage':stage,'pid':pid,'wall_seconds':time.monotonic()-entered,'owned_log_bytes_fstat_only':os.fstat(fd).st_size,'management_deadline':deadline,'scope':'LOCAL_FOCAL_PARENT_ONLY_NOT_GLOBAL_FIN'}
 save(OUT/('progress-'+str(progress_seq).zfill(4)+'.json'),row);progress_seq+=1
 print(json.dumps({'epoch':EPOCH,'progress':row}),flush=True)
def run(argv,phase_name):
 global phase
 phase=phase_name
 print(json.dumps({'epoch':EPOCH,'phase':phase,'start':str(OUT),'argv':argv}),flush=True)
 k=driver['managed_native_child'](argv,SOURCE,OUT/(phase+'.log'),env,300,terminate_grace=2,progress_poll=5,progress=progress)
 save(OUT/(phase+'.kernel.json'),k)
 if not driver['managed_custody_closed'](k):
  save(OUT/'unknown-fin-control-only.json',{'phase':phase,'kernel':k,'source_junit_log_payload_reads_after_unknown':0,'source_qualification':'UNCOMMITTED_DIAGNOSTIC_ONLY','real_orders_sent':0})
  print(json.dumps({'epoch':EPOCH,'phase':phase,'physical_fin_closed':False,'payload_reads':0}),flush=True)
  sys.exit(1)
 return k,driver['pre_capture_kernel_state']()
collected,collected_final=run(collection_argv,'collection')
assert driver['managed_phase_green'](collected)
collection_raw,_=read(OUT/'collection.log')
nodeids=[line.strip() for line in collection_raw.decode().splitlines() if line.startswith('tests/') and '::' in line]
assert len(nodeids)==67 and len(set(nodeids))==67
save(OUT/'collection-identities.json',{'nodeids':nodeids,'count':len(nodeids),'duplicate_ids':len(nodeids)-len(set(nodeids)),'raw_log_sha256':hashlib.sha256(collection_raw).hexdigest(),'nodeids_sha256':hashlib.sha256(('\n'.join(sorted(nodeids))+'\n').encode()).hexdigest(),'actual_kernel_final':collected_final})
source_after_collection=source_capture();assert stable10(source_before,source_after_collection)
executed,executed_final=run(execution_argv,'execution')
source_after=source_capture();head_after=git_identity();closure_after=closure()
junit_raw,_=read(OUT/'junit.xml');xml=ET.fromstring(junit_raw);cases=list(xml.iter('testcase'))
ids=[c.get('classname','')+'::'+c.get('name','') for c in cases]
pytest_ids=[c.get('classname','').replace('.','/')+'.py::'+c.get('name','') for c in cases]
junit={'path':str(OUT/'junit.xml'),'sha256':hashlib.sha256(junit_raw).hexdigest(),'cases':len(cases),'failures':sum(c.find('failure') is not None for c in cases),'errors':sum(c.find('error') is not None for c in cases),'skipped':sum(c.find('skipped') is not None for c in cases),'duplicate_case_ids':len(ids)-len(set(ids)),'case_identity_sha256':hashlib.sha256(('\n'.join(sorted(ids))+'\n').encode()).hexdigest(),'collection_and_junit_set_equal':set(pytest_ids)==set(nodeids)}
# Own new diagnostic namespace: lstat/NOFOLLOW/NOATIME metadata only; no targets or deletions.
queue=[OUT];seen=set();rows=allocated=logical=aliases=hardlinked=special=0
while queue:
 parent=queue.pop();fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
 try:names=os.listdir(fd)
 finally:os.close(fd)
 for name in names:
  path=parent/name;st=path.lstat();rows+=1;identity=(st.st_dev,st.st_ino)
  if identity not in seen:seen.add(identity);allocated+=st.st_blocks*512;logical+=st.st_size
  if stat.S_ISDIR(st.st_mode):queue.append(path)
  elif stat.S_ISLNK(st.st_mode):aliases+=1
  elif stat.S_ISREG(st.st_mode):hardlinked+=st.st_nlink>1
  else:special+=1
fd=os.open(OUT,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_NOATIME|os.O_CLOEXEC)
try:v=os.fstatvfs(fd)
finally:os.close(fd)
retained={'root':str(OUT),'entries':rows,'unique_inodes':len(seen),'allocated_bytes_nofollow_deduplicated':allocated,'logical_bytes_nofollow_deduplicated':logical,'aliases_retained':aliases,'hardlinked_regular_entries_retained':hardlinked,'special_entries_retained':special,'removed':False,'free_bytes':v.f_bavail*v.f_frsize,'free_inodes':v.f_favail,'free_inodes_fraction':v.f_favail/v.f_files if v.f_files else None,'floor_2GiB_ok':v.f_bavail*v.f_frsize>=2*1024**3,'floor_10pct_inodes_ok':v.f_files>0 and v.f_favail/v.f_files>=.1,'scope':'THIS_NEW_DIAGNOSTIC_NAMESPACE_ONLY_NOT_GLOBAL_CLEANUP_GREEN'}
phase_green=driver['managed_phase_green'](executed)
receipt={'epoch':EPOCH,'collection_kernel':collected,'execution_kernel':executed,'actual_kernel_before':before_kernel,'actual_kernel_after_collection':collected_final,'actual_kernel_after_execution':executed_final,'physical_fin_closed':True,'original_managed_phase_green':phase_green,'source_before':source_before,'source_after_collection':source_after_collection,'source_after':source_after,'source_bytes_and_original10_unchanged':stable10(source_before,source_after),'code_atime_evolution_declared':{n:[source_before[n]['stat']['st_atime_ns'],source_after[n]['stat']['st_atime_ns']] for n in MEMBERS},'git_head_before':head_before,'git_head_after':head_after,'git_head_unchanged':head_before==head_after,'installed_metadata_157_unchanged':closure_before==closure_after,'installed_metadata_after':closure_after,'junit':junit,'collection_nodeids':nodeids,'execution_log':capture(OUT/'execution.log'),'diagnostic_namespace_retained':retained,'source_qualification':'UNCOMMITTED_DIAGNOSTIC_ONLY_SOURCE_SHA_QUALIFICATION_PENDING','scope':'PROGRAMMATIC_FOCALS_NOT_FULL_GOV_OR_PREDEPLOY','namespace_retention':'EXPLICIT_RETAINED_DIAGNOSTIC_OUTPUTS_NOT_CLEANED','real_orders_sent':0,'docker_real_calls':0,'host_calls':0}
sha=save(OUT/'receipt.json',receipt)
print(json.dumps({'epoch':EPOCH,'receipt':str(OUT/'receipt.json'),'sha256':sha,'physical_fin_closed':True,'original_managed_phase_green':phase_green,'junit':junit,'retained':retained}),flush=True)
green=(phase_green and receipt['source_bytes_and_original10_unchanged'] and receipt['git_head_unchanged'] and receipt['installed_metadata_157_unchanged'] and len(cases)==67 and junit['failures']==junit['errors']==junit['skipped']==junit['duplicate_case_ids']==0 and junit['collection_and_junit_set_equal'] and retained['floor_2GiB_ok'] and retained['floor_10pct_inodes_ok'])
sys.exit(0 if green else 1)
