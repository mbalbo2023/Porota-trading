from pathlib import Path
import hashlib,json,os,signal,subprocess,time

os.umask(0o022)
plan_path=Path('/tmp/rc6_archive_v3_maximum_member_source_plan_d9e7fb7d.json')
plan_raw=plan_path.read_bytes();plan=json.loads(plan_raw)
probe=Path(plan['external_probe']['path'])
assert hashlib.sha256(probe.read_bytes()).hexdigest()==plan['external_probe']['sha256']
raw=Path('/tmp/rc6-native-maximum-member-d9e7fb7d-raw')
fixture=Path('/workspace/rc6-native-maximum-member-d9e7fb7d')
assert not raw.exists() and not fixture.exists()
raw.mkdir(mode=0o700)
command=plan['execution_command_template']
before={'schema':'rc6.maximum-archive-member-native-child-wrapper.v1','source_sha':plan['reviewed_source_sha'],
        'source_tree':plan['reviewed_source_tree'],'command':command,'cwd':plan['frozen_whole_source']['source_root'],
        'source_index_sha256':plan['frozen_whole_source']['index_sha256'],'probe_sha256':plan['external_probe']['sha256'],
        'source_plan_sha256':hashlib.sha256(plan_raw).hexdigest(),'umask':'022','uid':os.geteuid(),
        'scope':'NARROW_CANONICAL_PUBLISHER_ARCHIVE_MEMORY_ONLY_NO_BUSINESS_TICKS_HORIZON_IMAGE_RUNTIME',
        'outer_timeout_seconds':330,'executed':False}
with (raw/'wrapper-before.json').open('x') as f:f.write(json.dumps(before,indent=2,sort_keys=True)+'\n')
start=time.monotonic();timed_out=False
with (raw/'native.log').open('xb') as log:
    process=subprocess.Popen(command,cwd=before['cwd'],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},
                             stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    print(json.dumps({'START_PID':process.pid,'source_sha':before['source_sha'],'command':command}),flush=True)
    try:
        code=process.wait(timeout=330)
    except subprocess.TimeoutExpired:
        timed_out=True
        os.killpg(process.pid,signal.SIGTERM)
        try:code=process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid,signal.SIGKILL);code=process.wait(timeout=3)
final={**before,'executed':True,'returncode':code,'outer_timed_out':timed_out,
       'outer_elapsed_wall_seconds':time.monotonic()-start,
       'log_sha256':hashlib.sha256((raw/'native.log').read_bytes()).hexdigest()}
for name in ('native-receipt.json','child-resource.txt'):
    p=raw/name
    if p.exists():final[name+'_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
with (raw/'wrapper-final.json').open('x') as f:f.write(json.dumps(final,indent=2,sort_keys=True)+'\n')
print(json.dumps({k:final[k] for k in ('source_sha','returncode','outer_timed_out','outer_elapsed_wall_seconds')}),flush=True)
raise SystemExit(code or int(timed_out))
