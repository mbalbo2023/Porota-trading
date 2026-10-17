"""Passive sustained acceptance with all owned production services running."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rc6_shadow_runtime.persistence import EvidenceFiles, read_committed_generation
from scripts.rc6_deploy_generation_gate import collect

OWNED=('porota_production_observer','porota_production_dashboard')
RAM_FLOOR=128*1024**2
DISK_FLOOR=2*1024**3

def owned_state():
    p=subprocess.run(['docker','inspect',*OWNED],capture_output=True,text=True,timeout=8,check=True)
    rows=json.loads(p.stdout)
    return [{'name':row['Name'],'id':row['Id'],'image':row['Image'],
             'status':row['State']['Status'],'oom':row['State']['OOMKilled'],
             'restarts':row['RestartCount']} for row in rows]

def resource_sample(root, baseline, *, state_fn=owned_state, memory_path=Path('/proc/meminfo')):
    memory={line.split(':',1)[0]:int(line.split()[1])*1024
            for line in memory_path.read_text().splitlines() if ':' in line}
    disk=shutil.disk_usage(root).free
    state=state_fn()
    if state!=baseline or any(r['status']!='running' or r['oom'] for r in state):
        raise ValueError('NATIVE_SOAK_SERVICE_CHANGED')
    if memory.get('MemAvailable',0)<RAM_FLOOR:
        raise ValueError('NATIVE_SOAK_RAM_RESERVE_BREACHED')
    if disk<DISK_FLOOR:
        raise ValueError('NATIVE_SOAK_DISK_RESERVE_BREACHED')
    return {'at':datetime.now(timezone.utc).isoformat(),'available_memory_bytes':memory['MemAvailable'],
            'free_disk_bytes':disk,'swap_used_bytes':memory.get('SwapTotal',0)-memory.get('SwapFree',0),
            'services_unchanged':True}

def stable_reader(root, **kwargs):
    # A status write can precede CURRENT by milliseconds. Retry only this
    # explicit publication race for <=0.5s, never stale/FAIL_CLOSED or safety.
    for attempt in range(11):
        try:
            cut = read_committed_generation(root,**kwargs)
        except ValueError as exc:
            if str(exc)!='GENERATION_REVOKED_OR_PUBLICATION_IN_PROGRESS' or attempt==10:raise
            path=Path(root)/'status.json'
            live=EvidenceFiles(root).read('status.json')
            if (not isinstance(live,dict) or live.get('status') not in
                    {'SHADOW_OBSERVING','PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION'}
                    or not 0<=time.time()-path.stat().st_mtime<=2):raise
            time.sleep(.05)
            continue
        if ((cut.get('status',{}).get('generation_timing') or {}).get('tick_measurement_boundary')
                == 'BEFORE_STATUS_AND_CURRENT' and not cut.get('publication')):
            if attempt==10 or not 0<=time.time()-(Path(root)/'CURRENT.json').stat().st_mtime<=2:
                raise ValueError('GENERATION_PUBLICATION_COST_UNVERIFIED')
            time.sleep(.05)
            continue
        if attempt:
            cut['reader_observation']={'publication_race_retries':attempt}
        return cut

def main():
    p=argparse.ArgumentParser();p.add_argument('--expected-sha',required=True)
    p.add_argument('--expected-tree',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--root',type=Path,default=Path('/opt/porota-trading'))
    p.add_argument('--timeout',type=int,default=1200);args=p.parse_args()
    root=args.root/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
    baseline=owned_state()
    if len(baseline)!=2 or len({r['image'] for r in baseline})!=1:
        raise ValueError('NATIVE_SOAK_IMAGE_MISMATCH')
    cached=[0,None]
    def sample():
        # Docker metadata every10s; memory/disk and cut validation each2s.
        if cached[1] is None or time.monotonic()-cached[0]>=10:
            try:
                cached[:]=[time.monotonic(),owned_state()]
            except (OSError,ValueError,subprocess.SubprocessError) as exc:
                raise ValueError('NATIVE_SOAK_SERVICE_UNAVAILABLE') from exc
        return resource_sample(args.root,baseline,state_fn=lambda:cached[1])
    result=collect(root,args.expected_sha,args.expected_tree,timeout=args.timeout,
                   minimum_generations=10,strict=True,sample=sample,reader=stable_reader)
    result.update(services=baseline,all_services_sampled_running=result['status']=='GREEN',
                  resource_sampling_seconds=2,service_sampling_seconds=10,
                  ram_floor_bytes=RAM_FLOOR,disk_floor_bytes=DISK_FLOOR)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    temporary=args.output.with_suffix('.tmp');temporary.write_text(json.dumps(result,sort_keys=True)+'\n');temporary.replace(args.output)
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status']=='GREEN' else 42

if __name__=='__main__':raise SystemExit(main())
