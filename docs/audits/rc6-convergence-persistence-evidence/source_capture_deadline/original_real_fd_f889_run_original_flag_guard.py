from pathlib import Path
import hashlib
import importlib.metadata
import json
import os
import platform
import socket
import sys
import time

RAW = Path('/tmp/rc6-source-capture-original-flag-f8895434-157-raw')
SOURCE = Path('/tmp/rc6-v3-big-f8895434-157-source')
ORIGINAL_RAW = Path('/tmp/rc6-v3-big-f8895434-157-raw')
INTERPRETER = '/workspace/venv_rc6_frozen311/bin/python'
namespace = {'__name__':'rc6_original_guard_definitions'}
original_wrapper = (ORIGINAL_RAW/'run-wrapper.py').read_text()
exec(compile(original_wrapper.split('checks = preflight()')[0],str(ORIGINAL_RAW/'run-wrapper.py'),'exec'),namespace)
namespace['RAW'] = RAW
namespace['DATA'] = Path('/workspace/rc6-capture-original-guard-fixture-marker-never-precreated')
checks = namespace['preflight']()
before = namespace['inventory']()
PIN = namespace['PIN']
if hashlib.sha256((ORIGINAL_RAW/'source.tar').read_bytes()).hexdigest()!=PIN['tar_sha256']:
    raise ValueError('SOURCE_ARCHIVE_HASH_MISMATCH')
network = []
def forbidden(*args, **kwargs):
    network.append('DENIED')
    raise AssertionError('OFFLINE_CAPTURE_GUARD_NETWORK_FORBIDDEN')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
socket.socket.sendto = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
sys.path.insert(0,str(SOURCE))
os.umask(0o077)
import pytest
command = [str(RAW/'test_rc6_source_capture_deadlines.py')+'::test_missing_nonblocking_open_support_fails_closed_without_opening_source','-q','-o','addopts=',
    '-p','no:cacheprovider','--import-mode=importlib','--junitxml='+str(RAW/'original-native-guards.xml')]
(RAW/'wrapper-before.json').write_text(json.dumps({'schema':'rc6.source-capture-original-guard-replay.v1',
    'source_sha':PIN['source_sha'],'source_tree':PIN['source_tree'],'interpreter':sys.executable,
    'distribution_count':checks['distribution_count'],'preflight_exact_names_versions':checks['passed'],
    'source_index_sha256':hashlib.sha256((ORIGINAL_RAW/'source.index.json').read_bytes()).hexdigest(),
    'source_files':len(before),'tar_sha256':PIN['tar_sha256'],'product_overlay_count':0,
    'external_guard_file':str(RAW/'test_rc6_source_capture_deadlines.py'),
    'external_guard_sha256':hashlib.sha256((RAW/'test_rc6_source_capture_deadlines.py').read_bytes()).hexdigest(),
    'command':[INTERPRETER,'-B','-u',str(RAW/'run_original_flag_guard.py')],
    'pytest_arguments':command,'cwd':str(Path.cwd()),'umask':'077','diagnostic_only':True,
    'acceptance_complete':False,'artifact_validated':False,'runtime_validated':False},indent=2,sort_keys=True)+'\n')
started = time.monotonic()
code = pytest.main(command)
imports = []
alien = []
for name,module in sorted(sys.modules.items()):
    if name.startswith(('rc6_','cg_paper_workspace')) and getattr(module,'__file__',None):
        path=Path(module.__file__).resolve()
        row={'module':name,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        imports.append(row)
        if not path.is_relative_to(SOURCE):alien.append(row)
after=namespace['inventory']()
receipt={'schema':'rc6.source-capture-original-guard-replay-result.v1',
    'source_sha':PIN['source_sha'],'source_tree':PIN['source_tree'],'interpreter':sys.executable,
    'distribution_count':checks['distribution_count'],'source_files':len(before),
    'whole_source_sha_modes_git_blobs_unchanged':before==after,'product_overlay_count':0,
    'external_guard_file':str(RAW/'test_rc6_source_capture_deadlines.py'),
    'external_guard_sha256':hashlib.sha256((RAW/'test_rc6_source_capture_deadlines.py').read_bytes()).hexdigest(),
    'product_imports':imports,'alien_product_imports':alien,'network_attempts':len(network),
    'pytest_returncode':int(code),'elapsed_seconds':time.monotonic()-started,
    'junit_sha256':hashlib.sha256((RAW/'original-native-guards.xml').read_bytes()).hexdigest(),
    'diagnostic_only':True,'acceptance_complete':False,'artifact_validated':False,'runtime_validated':False}
(RAW/'original-guard-receipt.json').write_text(json.dumps(receipt,indent=2,sort_keys=True)+'\n')
print(json.dumps({k:v for k,v in receipt.items() if k not in ('product_imports','alien_product_imports')},sort_keys=True),flush=True)
raise SystemExit(int(code) or int(before!=after or bool(alien) or bool(network)))
