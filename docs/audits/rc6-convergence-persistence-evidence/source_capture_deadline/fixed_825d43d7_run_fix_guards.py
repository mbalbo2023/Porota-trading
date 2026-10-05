from pathlib import Path
import hashlib,json,os,platform,socket,sys,time
import xml.etree.ElementTree as ET
RAW=Path('/tmp/rc6-source-capture-fix-825d43d7-157-raw')
SOURCE=Path('/workspace/rc6-source-capture-fix-825d43d7-157-source')
ORIGINAL_RAW=Path('/tmp/rc6-v3-big-f8895434-157-raw')
INTERPRETER='/workspace/venv_rc6_frozen311/bin/python'
ns={'__name__':'rc6_fix_guard_definitions'}
original=(ORIGINAL_RAW/'run-wrapper.py').read_text()
exec(compile(original.split('checks = preflight()')[0],str(ORIGINAL_RAW/'run-wrapper.py'),'exec'),ns)
PIN=json.loads((RAW/'source.index.json').read_bytes())
ns.update(SOURCE=SOURCE,RAW=RAW,PIN=PIN,PIN_PATH=RAW/'source.index.json',
 DATA=Path('/workspace/rc6-capture-fix-825d43d7-fixture-marker-never-precreated'))
checks=ns['preflight']()
before=ns['inventory']()
if hashlib.sha256((RAW/'source.tar').read_bytes()).hexdigest()!=PIN['tar_sha256']:raise ValueError('SOURCE_TAR_MISMATCH')
network=[]
def forbidden(*args,**kwargs):
 network.append('DENIED');raise AssertionError('OFFLINE_SOURCE_CAPTURE_NETWORK_FORBIDDEN')
socket.socket.connect=forbidden;socket.socket.connect_ex=forbidden;socket.socket.sendto=forbidden
socket.create_connection=forbidden;socket.getaddrinfo=forbidden
sys.path.insert(0,str(SOURCE));os.umask(0o022)
import pytest
selection=[
 'tests/test_rc6_source_capture_deadlines.py',
 'tests/test_rc6_native_source_reads.py',
 'tests/test_rc6_component_archive.py::test_canonical_factory_and_native_publisher_keep_original_512_file_quota_and_bind_configuration',
 'tests/test_rc6_shadow_lab_runtime.py',
 'tests/test_rc6_shadow_entry_signals.py',
 'tests/test_rc6_shadow_family_runtime.py',
 'tests/test_rc6_shadow_runtime_wiring.py',
 'tests/test_rc6_history_snapshot_copy.py',
 'tests/test_rc6_sqlite_disk_scratch.py',
 'tests/test_issue465_generations.py::test_failure_diagnostics_never_persist_exception_message_secrets']
command=[*selection,'-q','-o','addopts=','-p','no:cacheprovider','--junitxml='+str(RAW/'fix-native-focal.xml')]
metadata={'schema':'rc6.source-capture-fix-whole-source-focal.v1','source_sha':PIN['source_sha'],'source_tree':PIN['source_tree'],
 'interpreter':sys.executable,'python':platform.python_version(),'distribution_count':checks['distribution_count'],
 'preflight_exact_names_versions':checks['passed'],'source_files':len(before),'source_index_sha256':hashlib.sha256((RAW/'source.index.json').read_bytes()).hexdigest(),
 'tar_sha256':PIN['tar_sha256'],'product_overlay_count':0,'cwd':str(Path.cwd()),'umask':'022',
 'command':[INTERPRETER,'-B','-u',str(RAW/'run_fix_guards.py')],'pytest_arguments':command,
 'scope':'OWN_EXACT_COMMIT_NATIVE_SOURCE_FIX_NOT_ROOT_RELEASE_OR_ARTIFACT','acceptance_complete':False,'artifact_validated':False,'runtime_validated':False}
(RAW/'wrapper-before.json').write_text(json.dumps(metadata,indent=2,sort_keys=True)+'\n')
started=time.monotonic();code=pytest.main(command)
imports=[];alien=[]
for name,module in sorted(sys.modules.items()):
 if name.startswith(('rc6_','cg_paper_workspace')) and getattr(module,'__file__',None):
  path=Path(module.__file__).resolve();row={'module':name,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()};imports.append(row)
  if not path.is_relative_to(SOURCE):alien.append(row)
after=ns['inventory']();tests=[]
for case in ET.parse(RAW/'fix-native-focal.xml').iter('testcase'):
 faults=[p for p in case if p.tag in ('failure','error','skipped')]
 tests.append({'class':case.attrib['classname'],'name':case.attrib['name'],'state':faults[0].tag.upper() if faults else 'PASS',
  'seconds':case.attrib['time']})
metadata.update(returncode=int(code),elapsed_seconds=time.monotonic()-started,
 whole_source_sha_modes_git_blobs_unchanged=before==after,product_imports=imports,alien_product_imports=alien,
 network_attempts=len(network),native_cases=tests,junit_sha256=hashlib.sha256((RAW/'fix-native-focal.xml').read_bytes()).hexdigest())
(RAW/'fix-guard-receipt.json').write_text(json.dumps(metadata,indent=2,sort_keys=True)+'\n')
print(json.dumps({k:v for k,v in metadata.items() if k not in ('product_imports','native_cases','pytest_arguments')},sort_keys=True),flush=True)
raise SystemExit(int(code) or int(before!=after or bool(alien) or bool(network)))
