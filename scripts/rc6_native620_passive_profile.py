"""Bounded passive host measurements. No service action, source mutation or provider."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone

ROOT = Path('/opt/porota-trading')
SHA = '5baeae7b0478d61d2dea04e5dbee4e0c1a3af951'
OWNED = ('porota_production_observer', 'porota_production_dashboard')

def command(args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True, timeout=10).stdout

def lifecycle():
    result = {}
    for name in OWNED:
        v = json.loads(command(['docker', 'inspect', name]))[0]
        result[name] = {k:v[k] for k in ('Id','Image','RestartCount')}
        result[name].update({k:v['State'][k] for k in ('Status','OOMKilled','Pid')})
        assert v['State']['Status'] == 'running' and not v['State']['OOMKilled']
    assert len({v['Image'] for v in result.values()}) == 1
    return result

def envelope(path):
    assert not path.is_symlink() and path.stat().st_size <= 131072
    raw = path.read_bytes()
    v = json.loads(raw)
    digest = hashlib.sha256(json.dumps(v['payload'],sort_keys=True,separators=(',', ':'),
                                      default=str,allow_nan=False).encode()).hexdigest()
    assert digest == v['digest']
    return v['payload']

def counters(path):
    return {a.rstrip(':'):int(b) for line in path.read_text().splitlines()
            for a,b in [line.split()] if b.isdigit()}

def process(pid):
    p = Path('/proc') / str(pid)
    stat = p.joinpath('stat').read_text().rsplit(')',1)[1].split()
    io = counters(p/'io')
    memory = {line.split(':')[0]:int(line.split()[1])*1024
              for line in (p/'status').read_text().splitlines()
              if line.startswith(('VmSwap:','RssAnon:','RssFile:','VmRSS:'))}
    states = {}
    runtime = wait = slices = 0
    for task in p.joinpath('task').iterdir():
        try:
            s = task.joinpath('stat').read_text().rsplit(')',1)[1].split()[0]
            states[s] = states.get(s,0)+1
            a,b,c = map(int,task.joinpath('schedstat').read_text().split())
            runtime += a; wait += b; slices += c
        except FileNotFoundError:
            continue
    return {'pid':pid,'nice':int(stat[16]),'state':stat[0],
        'user_ticks':int(stat[11]),'system_ticks':int(stat[12]),
        'minor_faults':int(stat[7]),'major_faults':int(stat[9]),
        'rss_bytes':int(stat[21])*os.sysconf('SC_PAGE_SIZE'),
        'memory':memory,'thread_states':states,'sched_runtime_ns':runtime,'sched_wait_ns':wait,
        'sched_slices':slices,'io':{k:io.get(k) for k in ('read_bytes','write_bytes','rchar','wchar')}}

def sample(baseline):
    init = baseline[OWNED[0]]['Pid']
    pids = [int(x) for x in command(['docker','top',OWNED[0],'-eo','pid']).splitlines()[1:]]
    rows = []
    for pid in pids:
        try: rows.append(process(pid))
        except FileNotFoundError: pass
    cgroup = Path('/sys/fs/cgroup') / Path('/proc',str(init),'cgroup').read_text().split('0::',1)[1].strip().lstrip('/')
    mem = {line.split(':')[0]:int(line.split()[1])*1024
           for line in Path('/proc/meminfo').read_text().splitlines()}
    fs = os.statvfs(ROOT)
    r = {'at':datetime.now(timezone.utc).isoformat(),'monotonic':time.monotonic(),
        'processes':rows,'host_cpu_count':os.cpu_count(),'clock_ticks':os.sysconf('SC_CLK_TCK'),
        'host_load':list(os.getloadavg()),'host_mem_available':mem['MemAvailable'],
        'disk_free':fs.f_bavail*fs.f_frsize,'inodes_free':fs.f_favail,
        'host_cpu_counters':Path('/proc/stat').read_text().splitlines()[0],
        'cgroup_cpu':counters(cgroup/'cpu.stat'),
        'cgroup_memory_current':int((cgroup/'memory.current').read_text()),
        'cgroup_memory_events':counters(cgroup/'memory.events'),
        'cgroup_swap_current':int((cgroup/'memory.swap.current').read_text()),
        'host_swap_free':mem['SwapFree'],'host_swap_total':mem['SwapTotal'],
        'host_vmstat':{k:v for k,v in counters(Path('/proc/vmstat')).items()
                       if k in {'pgfault','pgmajfault','pswpin','pswpout','pgscan_kswapd','pgscan_direct'}},
        'host_memory_pressure':Path('/proc/pressure/memory').read_text(),
        'host_io_pressure':Path('/proc/pressure/io').read_text(),
        'cgroup_memory_pressure':(cgroup/'memory.pressure').read_text()}
    folder=ROOT/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    try:
        status=envelope(folder/'status.json'); pointer=envelope(folder/'CURRENT.json')
        r['status']={k:status.get(k) for k in ('as_of','status','generation_timing','runtime_sql_diagnostics',
                                             'configuration_fingerprint','computed_report_digest')}
        r['pointer']={k:pointer.get(k) for k in ('generation_id','sequence','manifest_sha256')}
        assert type(status['real_orders_sent']) is int and status['real_orders_sent']==0
        assert status['real_routes']=='NOT_CALLED'
    except (ValueError,KeyError,FileNotFoundError,AssertionError):
        r['read_error']='STATUS_OR_POINTER_NOT_VERIFIED'
    return r

report={'schema':'rc6.native620-passive-profile.v1','read_only':True,'services_changed':False,
        'ppi_watch_accessed':False,'candidate_sha':SHA,'not_acceptance':True,
        'started_at':datetime.now(timezone.utc).isoformat(),'samples':[]}
try:
    state=json.loads((ROOT/'data/deploy/CURRENT_STATE_V2.json').read_text())
    assert state['candidate_sha']==SHA
    before=lifecycle(); report['before']=before
    started=time.monotonic()
    while time.monotonic()-started<90:
        report['samples'].append(sample(before))
        time.sleep(5)
    report['after']=lifecycle()
    assert before==report['after']
    report['status']='GREEN_PASSIVE_MEASUREMENT_ONLY'
except Exception as error:
    report['status']='RED';report['error_class']=type(error).__name__
report['finished_at']=datetime.now(timezone.utc).isoformat()
print(json.dumps(report,sort_keys=True),flush=True)
raise SystemExit(0 if report['status']=='GREEN_PASSIVE_MEASUREMENT_ONLY' else 2)
