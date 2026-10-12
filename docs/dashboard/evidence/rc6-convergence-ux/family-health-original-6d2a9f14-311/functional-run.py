"""Prepared stdlib launcher; only execute in an explicitly assigned native slot."""
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time

raw=Path('/tmp/rc6-ux-family-health-6d2a9f14-raw')
source=Path('/workspace/rc6-ux-family-health-6d2a9f14-whole')
sha='6d2a9f144da872b9066cc1ffb531386d5dba56da'
version='.'.join(map(str,sys.version_info[:2]))
suffix={'3.11':'311','3.12':'312'}[version]
expected_python={'311':'/workspace/venv_rc6_frozen311/bin/python','312':'/workspace/venv_rc6_py312/bin/python'}[suffix]
assert os.path.abspath(sys.executable)==expected_python
assert Path(sys.prefix).resolve()==Path(expected_python).parent.parent.resolve()
installed={}
for distribution in metadata.distributions():
    name=re.sub(r'[-_.]+','-',distribution.metadata['Name']).lower()
    assert name not in installed
    installed[name]=distribution.version
expected={}
for filename in ('requirements.lock.txt','requirements.build.lock.txt'):
    for line in (source/filename).read_text().splitlines():
        match=re.match(r'^([A-Za-z0-9_.-]+)==([^\\\s]+)',line)
        if match:
            name,value=match.groups();name=re.sub(r'[-_.]+','-',name).lower()
            assert name not in expected or expected[name]==value
            expected[name]=value
assert len(expected)==157 and installed==expected
index=json.loads((raw/'source.index.json').read_text())
assert index['schema']=='rc6.complete-archive-source-pin.v1' and index['source_sha']==sha and index['overlay_count']==0
commit=(raw/'source.commit.raw').read_bytes()
assert hashlib.sha256(commit).hexdigest()==index['raw_git_commit_sha256']
assert hashlib.sha1(b'commit '+str(len(commit)).encode()+b'\0'+commit).hexdigest()==sha
assert commit.split(b'\n',1)[0]==b'tree '+index['source_tree'].encode()
assert hashlib.sha256((raw/'source.tar').read_bytes()).hexdigest()==index['tar_sha256']

def inventory():
    result={}
    for path in source.rglob('*'):
        assert not path.is_symlink()
        if path.is_file():
            fd=os.open(path,os.O_RDONLY|os.O_NOATIME|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno());assert info.st_nlink==1 and stat.S_ISREG(info.st_mode)
                payload=stream.read()
            result[str(path.relative_to(source))]={'sha256':hashlib.sha256(payload).hexdigest(),
                'mode':stat.S_IMODE(info.st_mode),'blob':hashlib.sha1(b'blob '+str(len(payload)).encode()+b'\0'+payload).hexdigest()}
    return result

before=inventory()
assert {name:member['sha256'] for name,member in before.items()}==index['files']
assert {name:'100755' if member['mode']&0o111 else '100644' for name,member in before.items()}==index['modes']
assert {name:member['blob'] for name,member in before.items()}==index['blob_ids']
environment=dict(os.environ,PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1',
    GIT_DIR='/workspace/porota_rc6_convergence/.git/worktrees/ux-browser-coverage',GIT_WORK_TREE=str(source))
environment.pop('PYTHONPATH',None)
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,env=environment,text=True).strip()==sha
modules=['tests/test_rc6_browser_product_ipc.py','tests/test_rc6_projection_large_browser.py',
    'tests/test_rc6_projection_browser_diagnostic.py','tests/test_rc6_browser_family_health_coverage.py']
base=raw/('pytest-'+suffix)
assert not base.exists()
command=[sys.executable,'-B','-m','pytest','-vv','-p','no:cacheprovider',*modules,
    '--basetemp',str(base),'--junitxml',str(raw/('family-health-'+suffix+'.xml'))]
receipt={'schema':'rc6.ux-family-health-functional-execution.v1','source_sha':sha,'source_tree':index['source_tree'],
    'whole_source':str(source),'index':str(raw/'source.index.json'),'overlay_count':0,'sys_executable':sys.executable,
    'python':sys.version,'installed_count':len(installed),'installed':installed,
    'metadata_sha256':hashlib.sha256(json.dumps(installed,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
    'native_scope':'ACTUAL_CANONICAL_SMALL_FAMILY_AND_REAL_PRIVATE_LIVE_HEALTH_PLUS_IPC_NO_CHROME_NO_BIG',
    'expected_prior_scope_cases':55,'expected_new_scope_cases':17,'expected_total_cases':72,
    'original55_b6_receipts_untouched':True,'git_dir_readonly_control':environment['GIT_DIR'],
    'git_work_tree':str(source),'command':command,'started_at_epoch':time.time(),'launcher_pid':os.getpid(),
    'source_before':before,'acceptance_complete':False,'fresh_big_browser_acceptance':'PENDING'}
begin=time.perf_counter()
with (raw/('family-health-'+suffix+'.stdout')).open('xb') as stdout, (raw/('family-health-'+suffix+'.stderr')).open('xb') as stderr:
    process=subprocess.Popen(command,cwd=source,env=environment,stdout=stdout,stderr=stderr)
    receipt['pytest_pid']=process.pid
    (raw/('family-health-'+suffix+'-started.json')).write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'event':'START','pytest_pid':process.pid,'launcher_pid':os.getpid(),
        'source_sha':sha,'source_tree':index['source_tree'],'executable':sys.executable,
        'installed_count':len(installed),'command':command}),flush=True)
    code=process.wait()
after=inventory()
receipt.update(returncode=code,elapsed_seconds=time.perf_counter()-begin,finished_at_epoch=time.time(),
    source_after=after,source_hashes_modes_blobs_unchanged=before==after,tracked_source_files=len(before))
for extension in ('stdout','stderr','xml'):
    path=raw/('family-health-'+suffix+'.'+extension)
    if path.exists():receipt[extension+'_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
(raw/('family-health-'+suffix+'-receipt.json')).write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
print(json.dumps({'event':'END','pytest_pid':process.pid,'returncode':code,'source_hashes_modes_blobs_unchanged':before==after,
    'elapsed_seconds':receipt['elapsed_seconds'],'receipt':str(raw/('family-health-'+suffix+'-receipt.json'))}),flush=True)
raise SystemExit(code if before==after else 97)
