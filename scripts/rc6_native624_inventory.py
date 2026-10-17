"""Bounded passive host inventory. No service, DB or provider mutation."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

ROOT=Path('/opt/porota-trading')
def command(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=15)
    return p.returncode,p.stdout[:524288]
def memory():
    return {k:int(v.split()[0])*1024 for k,v in
        (line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
        if k in {'MemAvailable','MemTotal','SwapFree','SwapTotal'}}
def main():
    result={'schema':'rc6.native624.inventory.v1','read_only':True,'collected_at':time.time()}
    rc,raw=command(['systemctl','list-timers','--all','--no-pager','--output=json'])
    timers=json.loads(raw) if rc==0 else []
    result['timers']=[r for r in timers if 'porota' in r.get('unit','').lower() and 'ppi-watch' not in r.get('unit','').lower()]
    result['timer_properties']=[]
    for row in result['timers'][:100]:
        unit=row['unit'];rc,raw=command(['systemctl','show',unit,'-p','ActiveState','-p','SubState','-p','Unit','-p','LastTriggerUSec','-p','NextElapseUSecRealtime','-p','UnitFileState'])
        result['timer_properties'].append({'unit':unit,'returncode':rc,'properties':dict(x.split('=',1) for x in raw.splitlines() if '=' in x)})
    rc,raw=command(['docker','inspect','porota_production_observer','porota_production_dashboard'])
    containers=json.loads(raw) if rc==0 else []
    result['containers']=[{'name':r['Name'],'id':r['Id'],'image':r['Image'],'state':{k:r['State'].get(k) for k in ('Status','Pid','StartedAt','FinishedAt','OOMKilled','Restarting')},'restart_count':r.get('RestartCount'),'limits':{k:r['HostConfig'].get(k) for k in ('Memory','NanoCpus','CpuShares','MemorySwap')}} for r in containers]
    processes=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            args=(path/'cmdline').read_bytes().split(b'\0');cg=(path/'cgroup').read_text()
            if not any(r['Id'] in cg for r in containers):continue
            role='SHADOW' if b'--dynamic-shadow-worker' in args else 'SCANNER' if any(b'bf_production_paper_observer' in a or b'bv_paper_runtime' in a for a in args) else 'DASHBOARD' if any(b'dashboard' in a for a in args) else 'OTHER_OWNED'
            safe_args=[a.decode(errors='replace') for a in args if re.fullmatch(rb'(?:--dynamic-shadow-worker|(?:/app/)?[a-zA-Z0-9_./-]+\.py|python[0-9.]*)',a)]
            status=dict(x.split(':',1) for x in (path/'status').read_text().splitlines() if ':' in x)
            processes.append({'pid':int(path.name),'role':role,'safe_cmdline':safe_args,'rss':status.get('VmRSS','').strip(),'swap':status.get('VmSwap','').strip(),'cgroup':cg.strip(),'schedstat':(path/'schedstat').read_text().strip()})
        except (OSError,ValueError):continue
    result['processes']=processes
    roots=[ROOT,Path('/etc/systemd/system'),Path('/etc/cron.d'),Path('/usr/local/bin')]
    result['consumer_scan']={'roots':[str(p) for p in roots],'scanned_files':0,'scanned_bytes':0,'matches':[],'skipped':[],'complete_within_roots':True}
    scan=result['consumer_scan'];pattern=re.compile(rb'computed_report|computed-[^\s]{0,80}json|write_streamed_report|prune_computed_reports')
    for root in roots:
        for parent,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if d not in {'.git','.venv','venv','data','backups','logs','node_modules','__pycache__','artifacts'}]
            for name in files:
                path=Path(parent)/name
                if path.suffix not in {'.py','.sh','.service','.timer','.yml','.yaml','.json'}:continue
                try:
                    if path.is_symlink() or path.stat().st_size>2*1024**2:scan['skipped'].append(str(path));continue
                    raw=path.read_bytes();scan['scanned_files']+=1;scan['scanned_bytes']+=len(raw)
                    if scan['scanned_bytes']>32*1024**2:scan['complete_within_roots']=False;break
                    if pattern.search(raw):scan['matches'].append({'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'is_test':('/tests/' in str(path))})
                except OSError:scan['skipped'].append(str(path))
            if not scan['complete_within_roots']:break
    samples=[]
    for _ in range(18):
        sample={'monotonic':time.monotonic(),'memory':memory()}
        base=ROOT/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
        try:
            status=json.loads((base/'status.json').read_text())['payload']
            sample['shadow']={k:status.get(k) for k in ('as_of','status','generation_timing','runtime_sql_diagnostics')}
        except (OSError,KeyError,ValueError):sample['shadow_unavailable']=True
        samples.append(sample);time.sleep(5)
    result['samples']=samples;result['end_memory']=memory()
    print(json.dumps(result,sort_keys=True,separators=(',',':')))
if __name__=='__main__':main()
