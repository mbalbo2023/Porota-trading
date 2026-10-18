"""Bounded READ_ONLY RCA after native RAM-reserve rejection; never acceptance."""
import argparse,hashlib,json,os,re,shutil,sqlite3,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path('/opt/porota-trading');sys.path.insert(0,str(ROOT))
from rc6_shadow_runtime.persistence import read_committed_generation
FLAGS={'--exit-reader':'EXITS','--notification-worker':'NOTIFICATIONS','--candle-worker':'CANDLES','--performance-worker':'PERFORMANCE','--dynamic-shadow-worker':'SHADOW','--intraday-scalping-worker':'SCALPING','--caucion-cash-sweep-worker':'TREASURY'}
OWNED=['porota_production_observer','porota_production_dashboard']

SEMANTIC_KEYS=('Env','Entrypoint','Cmd','User','WorkingDir','StopSignal','Healthcheck','ExposedPorts','Volumes','Labels','OnBuild','Shell')
def semantic_config(config):
    result={}
    for key in SEMANTIC_KEYS:
        value=config.get(key)
        if key in {'Env','Entrypoint','Cmd','OnBuild','Shell'}:value=value or []
        elif key in {'Healthcheck','ExposedPorts','Volumes','Labels'}:value=value or {}
        else:value=value or ''
        result[key]=value
    raw=json.dumps(result,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
    return hashlib.sha256(raw).hexdigest()

def docker_inspect(kind,values):
    p=subprocess.run(['docker',kind,'inspect',*values] if kind=='image' else ['docker','inspect',*values],capture_output=True,text=True,timeout=8)
    if p.returncode:raise ValueError('PROFILE_DOCKER_READ_FAILED')
    return json.loads(p.stdout)
def containers():
    rows=[]
    for value in docker_inspect('container',OWNED):
        env=dict(item.split('=',1) for item in value['Config']['Env'] if '=' in item)
        rows.append({'name':value['Name'],'id':value['Id'],'image':value['Image'],'status':value['State']['Status'],'root_pid':value['State']['Pid'],'oom':value['State']['OOMKilled'],'restarts':value['RestartCount'],'source_sha':env.get('POROTA_BUILD_SHA'),'source_tree':env.get('POROTA_CANDIDATE_TREE_SHA'),'limits':{k:value['HostConfig'].get(k) for k in ('Memory','MemorySwap','NanoCpus','CpuShares')}})
    return rows
def keyed(path,names=None):
    result={}
    for line in path.read_text().splitlines():
        if ':' not in line:continue
        key,value=line.split(':',1)
        if names is not None and key not in names:continue
        part=value.split()
        if part and part[0].isdigit():result[key]=int(part[0])*(1024 if len(part)>1 and part[1]=='kB' else 1)
    return result
def processes(ids):
    result=[]
    for folder in Path('/proc').iterdir():
        if not folder.name.isdigit():continue
        try:
            if not any(ident in (folder/'cgroup').read_text() for ident in ids):continue
            args=(folder/'cmdline').read_bytes().decode(errors='replace').split('\0')
            role=next((label for flag,label in FLAGS.items() if flag in args),None)
            if role is None:role='OBSERVER' if any(v.endswith('bf_production_paper_observer.py') for v in args) else 'DASHBOARD' if any('dashboard.py' in v for v in args) else 'COORDINATOR'
            safe=[v for v in args if v in FLAGS or re.fullmatch(r'(?:/app/)?[a-zA-Z0-9_./-]+\.py|python[0-9.]*',v)]
            raw=(folder/'stat').read_text();fields=raw[raw.rfind(')')+2:].split()
            sched=[int(v) for v in (folder/'schedstat').read_text().split()]
            row={'pid':int(folder.name),'role':role,'safe_cmdline':safe,'status':keyed(folder/'status',{'VmRSS','VmHWM','VmSwap','Threads'}),'stat':{'utime_ticks':int(fields[11]),'stime_ticks':int(fields[12]),'major_faults':int(fields[9]),'nice':int(fields[16]),'start_ticks':int(fields[19])},'schedstat':{'cpu_nanoseconds':sched[0],'runqueue_wait_nanoseconds':sched[1],'timeslices':sched[2]}}
            try:row['io']=keyed(folder/'io')
            except OSError:row['io_unavailable']=True
            try:row['smaps_rollup']=keyed(folder/'smaps_rollup',{'Rss','Pss','Swap','Private_Dirty','Anonymous'})
            except OSError:row['smaps_unavailable']=True
            result.append(row)
        except (OSError,ValueError,IndexError):continue
    return result
def host_counters(rows):
    wanted={'pswpin','pswpout','pgmajfault','pgfault','pgscan_kswapd','pgsteal_kswapd'}
    vm={k:int(v) for k,v in (line.split() for line in Path('/proc/vmstat').read_text().splitlines()) if k in wanted}
    cpu=next(line.split()[1:] for line in Path('/proc/stat').read_text().splitlines() if line.startswith('cpu '))
    result={'vmstat':vm,'cpu_ticks':dict(zip(('user','nice','system','idle','iowait','irq','softirq','steal','guest','guest_nice'),map(int,cpu))),'pressure':{n:Path('/proc/pressure',n).read_text().strip() for n in ('cpu','memory','io')},'cgroups':[]}
    base=Path('/sys/fs/cgroup').resolve()
    for row in rows:
        group={'container_id':row['id']}
        try:
            paths=[line.split(':',2)[2] for line in Path('/proc',str(row['root_pid']),'cgroup').read_text().splitlines() if line.startswith('0::')]
            if len(paths)!=1:raise ValueError('CGROUP_UNAVAILABLE')
            folder=(base/paths[0].lstrip('/')).resolve();folder.relative_to(base);group['path']=str(folder)
            for name in ('memory.current','memory.peak','memory.swap.current','memory.events','cpu.stat','io.stat'):
                try:group[name]=(folder/name).read_text().strip()
                except OSError:group[name]='UNAVAILABLE'
        except (OSError,ValueError):group['unavailable']=True
        result['cgroups'].append(group)
    return result
def safety():
    path=ROOT/'data/paper_v17/observer_v17.db'
    with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.1) as conn:
        conn.execute('PRAGMA query_only=ON');deadline=time.monotonic()+2
        conn.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        row=conn.execute('SELECT mode,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1').fetchone()
        if not row or row[0]!='PRODUCTION_PAPER' or type(row[1]) is not int or row[1]!=0:raise ValueError('PROFILE_PAPER_SAFETY_UNVERIFIED')
        return {'mode':row[0],'real_orders_sent':row[1],'heartbeat_at':row[2]}
def main():
    p=argparse.ArgumentParser();p.add_argument('--sha',required=True);p.add_argument('--tree',required=True);a=p.parse_args()
    assert re.fullmatch('[0-9a-f]{40}',a.sha) and re.fullmatch('[0-9a-f]{40}',a.tree)
    rows=containers()
    assert len(rows)==2 and all(r['source_sha']==a.sha and r['source_tree']==a.tree and r['status']=='running' and not r['oom'] for r in rows)
    assert len({r['image'] for r in rows})==1
    image=docker_inspect('image',[rows[0]['image']])[0]
    result={'schema':'rc6.native624.passive-profile.v2','purpose':'RCA_AFTER_RAM_RED_39091864291','read_only':True,'started_at':datetime.now(timezone.utc).isoformat(),'source_sha':a.sha,'source_tree':a.tree,'containers':rows,'clock_ticks_per_second':os.sysconf('SC_CLK_TCK'),'page_size_bytes':os.sysconf('SC_PAGE_SIZE'),'logical_cpu_count':os.cpu_count(),'samples':[],'loaded_image':{'id':image['Id'],'architecture':image.get('Architecture'),'os':image.get('Os'),'rootfs_diff_ids':image.get('RootFS',{}).get('Layers'),'semantic_config_sha256':semantic_config(image['Config'])}}
    for name in ('CURRENT_STATE_V2.json','porota-frozen-candidate.json'):
        path=ROOT/'data/deploy'/name
        value=json.loads(path.read_text())
        result[name]={k:value.get(k) for k in ('candidate_sha','candidate_tree_sha','deploy_sha','image_id','image_tar_sha256','mode','real_orders_sent','validation_status','real_order_routes','ppi_watch_untouched','build_once','dashboard_health_http','deployment_strategy')}
    root=ROOT/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow';ids=[r['id'] for r in rows]
    try:
        for _ in range(18):
            sample={'at':datetime.now(timezone.utc).isoformat(),'monotonic':time.monotonic(),'memory':keyed(Path('/proc/meminfo'),{'MemTotal','MemAvailable','SwapTotal','SwapFree'}),'free_disk_bytes':shutil.disk_usage(ROOT).free,'host':host_counters(rows),'processes':processes(ids),'safety':safety()}
            try:
                cut=read_committed_generation(root,payload_limit=4*1024**2,require_current_status=True)
                sample.update(generation={k:cut['pointer'][k] for k in ('generation_id','sequence','manifest_sha256')},source_as_of=cut['status']['as_of'],worker_status=cut['status']['status'],timing=cut['status'].get('generation_timing'),publication=cut.get('publication'))
            except (OSError,ValueError,KeyError) as error:
                sample.update(generation_read_race_or_failure=True,generation_error_class=type(error).__name__)
                if str(error) in {'GENERATION_REVOKED_OR_PUBLICATION_IN_PROGRESS','COMMITTED_GENERATION_MISSING','SHADOW_EVIDENCE_DIGEST_MISMATCH','GENERATION_ENVELOPE_MISMATCH','GENERATION_SAFETY_UNVERIFIED'}:sample['generation_error_code']=str(error)
            result['samples'].append(sample);time.sleep(5)
        result['containers_after']=containers()
        if result['containers_after']!=rows:raise ValueError('PROFILE_CONTAINERS_CHANGED')
        result['status']='OBSERVATION_COMPLETE'
    except Exception as error:
        result.update(status='RED_OBSERVATION',error_class=type(error).__name__)
        if str(error) in {'PROFILE_CONTAINERS_CHANGED','PROFILE_PAPER_SAFETY_UNVERIFIED'}:result['error_code']=str(error)
    result['finished_at']=datetime.now(timezone.utc).isoformat()
    print(json.dumps(result,sort_keys=True,separators=(',',':')))
    return 0 if result['status']=='OBSERVATION_COMPLETE' else 2
if __name__=='__main__':raise SystemExit(main())
