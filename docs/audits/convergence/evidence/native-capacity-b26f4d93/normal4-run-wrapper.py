from pathlib import Path
import hashlib, importlib.metadata, json, os, platform, sqlite3, stat, subprocess, time
source=Path('/tmp/rc6-v3-normal-b26f4d93-source'); raw=Path('/tmp/rc6-v3-normal-b26f4d93-raw'); pin_path=raw/'source.index.json'
pin=json.loads(pin_path.read_bytes())
def inventory():
 result={}
 for name in pin['files']:
  path=source/name;info=path.lstat();fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME)
  try:
   data=[]
   while block:=os.read(fd,1024*1024):data.append(block)
   wire=b''.join(data)
   assert os.fstat(fd)==info and path.lstat()==info
  finally:os.close(fd)
  mode='100'+format(stat.S_IMODE(info.st_mode),'03o')
  result[name]={'sha256':hashlib.sha256(wire).hexdigest(),'git_mode':mode,'git_blob':hashlib.sha1(b'blob '+str(len(wire)).encode()+b'\0'+wire).hexdigest()}
 assert {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file()}==set(result)
 assert all(row['sha256']==pin['files'][name] and row['git_mode']==pin['modes'][name] and row['git_blob']==pin['blob_ids'][name] for name,row in result.items())
 return result
before=inventory()
command=['/workspace/venv_rc6/bin/python','-u',str(source/'docs/audits/rc6-convergence-persistence-evidence/native_archive_v3_profile_probe_v2.py'),
 '--source-root',str(source),'--source-sha',pin['source_sha'],'--source-tree',pin['source_tree'],'--source-index',str(pin_path),
 '--root','/workspace/rc6-native-normal4-b26f4d93','--catalog-count','1200','--ticks','4']
env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
metadata={'schema':'rc6.native-normal4-whole-source-trial-wrapper.v1','source_sha':pin['source_sha'],'source_tree':pin['source_tree'],
 'source_index_sha256':hashlib.sha256(pin_path.read_bytes()).hexdigest(),'tar_sha256':pin['tar_sha256'],'overlay_count':0,
 'source_files':len(before),'command':command,'cwd':str(source),'umask':'022','uid':os.geteuid(),'gid':os.getegid(),
 'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'platform':platform.platform(),
 'distributions':sorted([{'name':d.metadata['Name'],'version':d.version} for d in importlib.metadata.distributions()], key=lambda d:d['name'].lower()),
 'scope':'INTERMEDIATE_OFFLINE_SYNTHETIC_DIAGNOSTIC_NO_ARTIFACT_OR_RUNTIME_ACCEPTANCE','acceptance_complete':False,'artifact_validated':False,'runtime_validated':False}
(raw/'wrapper-before.json').write_text(json.dumps(metadata,indent=2,sort_keys=True)+'\n')
start=time.monotonic()
with (raw/'normal4.log').open('wb') as log:
 process=subprocess.run(command,cwd=source,env=env,stdout=log,stderr=subprocess.STDOUT)
try:
 after=inventory();metadata['whole_source_files_and_modes_unchanged']=before==after
except BaseException as error:
 metadata['whole_source_files_and_modes_unchanged']=False;metadata['source_error']={'class':type(error).__name__,'reason':str(error)}
metadata.update(returncode=process.returncode,wrapper_elapsed_wall_seconds=time.monotonic()-start,rawlog_sha256=hashlib.sha256((raw/'normal4.log').read_bytes()).hexdigest())
result_path=Path('/workspace/rc6-native-normal4-b26f4d93/result.json')
if result_path.exists():
 wire=result_path.read_bytes();(raw/'native-normal4-result.json').write_bytes(wire);metadata['native_result_sha256']=hashlib.sha256(wire).hexdigest()
(raw/'wrapper-final.json').write_text(json.dumps(metadata,indent=2,sort_keys=True)+'\n')
print(json.dumps({k:metadata[k] for k in ('source_sha','source_files','returncode','whole_source_files_and_modes_unchanged','wrapper_elapsed_wall_seconds')}),flush=True)
raise SystemExit(process.returncode or int(not metadata['whole_source_files_and_modes_unchanged']))
