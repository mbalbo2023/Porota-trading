import hashlib,importlib.metadata as metadata,json,os,pathlib,re,subprocess,sys,time
raw=pathlib.Path('/tmp/rc6-ux-ipc-b6fd4c21-raw'); source=pathlib.Path('/workspace/rc6-ux-ipc-b6fd4c21-whole')
version='.'.join(map(str,sys.version_info[:2])); suffix={'3.11':'311','3.12':'312'}[version]
expected_executable={'311':'/workspace/venv_rc6_frozen311/bin/python','312':'/workspace/venv_rc6_py312/bin/python'}[suffix]
assert os.path.abspath(sys.executable)==expected_executable
installed={re.sub(r'[-_.]+','-',d.metadata['Name']).lower():d.version for d in metadata.distributions()}
expected={}
for filename in ('requirements.lock.txt','requirements.build.lock.txt'):
    for line in (source/filename).read_text().splitlines():
        match=re.match(r'^([A-Za-z0-9_.-]+)==([^\\\s]+)',line)
        if match:
            name,value=match.groups(); name=re.sub(r'[-_.]+','-',name).lower()
            assert name not in expected or expected[name]==value
            expected[name]=value
assert installed==expected and len(installed)==157
index=json.loads((raw/'source.index.json').read_text()); assert index['source_sha']=='b6fd4c21e308c4457e800941e24dc9fbc78a8cbc'
def inventory():
    result={}
    for path in source.rglob('*'):
        if path.is_file():
            descriptor=os.open(path,os.O_RDONLY|os.O_NOATIME|os.O_NOFOLLOW)
            with os.fdopen(descriptor,'rb') as stream: payload=stream.read()
            result[str(path.relative_to(source))]={'sha256':hashlib.sha256(payload).hexdigest(),'mode':path.stat().st_mode&0o777}
    return result
before=inventory(); assert {name:row['sha256'] for name,row in before.items()}==index['files']
assert {name:'100755' if row['mode']&0o111 else '100644' for name,row in before.items()}==index['modes']
environment=dict(os.environ,PYTEST_DISABLE_PLUGIN_AUTOLOAD='1',PYTHONDONTWRITEBYTECODE='1',GIT_DIR='/workspace/porota_rc6_convergence/.git/worktrees/ux-driver',GIT_WORK_TREE=str(source))
environment.pop('PYTHONPATH',None)
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,env=environment,text=True).strip()==index['source_sha']
command=[sys.executable,'-B','-m','pytest','-vv','-p','no:cacheprovider','tests/test_rc6_browser_product_ipc.py','tests/test_rc6_projection_large_browser.py','tests/test_rc6_projection_browser_diagnostic.py','--basetemp',str(raw/('pytest-'+suffix)),'--junitxml',str(raw/('ipc-'+suffix+'.xml'))]
receipt={'schema':'rc6.ux-ipc-functional-execution.v1','source_sha':index['source_sha'],'source_tree':index['source_tree'],'whole_source':str(source),'index':str(raw/'source.index.json'),'overlay_count':0,'driver_or_product_test_runtime':'LOCKED_PRODUCT_TEST_RUNTIME_157_NO_PLAYWRIGHT','sys_executable':sys.executable,'python':sys.version,'installed':installed,'installed_count':len(installed),'metadata_sha256':hashlib.sha256(json.dumps(installed,sort_keys=True,separators=(',',':')).encode()).hexdigest(),'native_scope':'IPC_AND_CANONICAL_SMALL_FIXTURES_NO_BIG_NO_CHROME','git_dir_readonly_control':environment['GIT_DIR'],'git_work_tree':str(source),'command':command,'started_at':time.time(),'launcher_pid':os.getpid(),'acceptance_complete':False,'browser_large_acceptance':'PENDING','source_before':before}
start=time.perf_counter()
with (raw/('ipc-'+suffix+'.stdout')).open('xb') as stdout, (raw/('ipc-'+suffix+'.stderr')).open('xb') as stderr:
    process=subprocess.Popen(command,cwd=source,env=environment,stdout=stdout,stderr=stderr)
    receipt['pytest_pid']=process.pid
    (raw/('ipc-'+suffix+'-started.json')).write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
    print(json.dumps({'event':'START','pytest_pid':process.pid,'launcher_pid':os.getpid(),'source_sha':index['source_sha'],'source_tree':index['source_tree'],'executable':sys.executable,'installed_count':len(installed),'command':command}),flush=True)
    code=process.wait()
after=inventory(); receipt.update({'returncode':code,'elapsed_seconds':time.perf_counter()-start,'finished_at':time.time(),'source_after':after,'source_hashes_and_modes_unchanged':before==after,'tracked_source_files':len(before)})
for name in ('stdout','stderr','xml'):
    path=raw/('ipc-'+suffix+'.'+name)
    if path.exists():receipt[name+'_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
(raw/('ipc-'+suffix+'-receipt.json')).write_text(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
print(json.dumps({'event':'END','pytest_pid':process.pid,'returncode':code,'source_hashes_and_modes_unchanged':before==after,'elapsed_seconds':receipt['elapsed_seconds'],'receipt':str(raw/('ipc-'+suffix+'-receipt.json'))}),flush=True)
raise SystemExit(code if before==after else 97)
