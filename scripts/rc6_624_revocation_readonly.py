"""One bounded host read-only receipt for #624 native CURRENT revocation."""
import gzip,hashlib,json,os,re,sqlite3,stat,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path('/opt/porota-trading')
BASE=ROOT/'data/paper_v17/artifacts/observer_v17.db/dynamic-shadow'
ALLOW={'as_of','status','error_class','error_module','error_line','runtime_read_stage',
       'runtime_deadline_expired','real_orders_sent','real_routes','provider_requests',
       'generation_timing','report_digest','computed_audit_contract','computed_report_digest'}
AUDIT={'state','epoch','snapshot_id','manifest_sha256','source_as_of','configuration_fingerprint',
       'deadline_at','completed_at','failed_at','error_class','wall_seconds','cpu_seconds'}
def command(args,timeout=8):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if result.returncode:raise ValueError('HOST_READ_COMMAND_FAILED:'+args[0])
    return result.stdout

def read(path,max_bytes=131072,compressed=False):
    try:
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size>max_bytes:raise ValueError('TYPE_OR_SIZE')
        raw=path.read_bytes()
        envelope=json.loads(gzip.decompress(raw) if compressed else raw)
        if not isinstance(envelope,dict) or not isinstance(envelope.get('payload'),dict):
            raise ValueError('EVIDENCE_ENVELOPE_REQUIRED')
        value=envelope['payload']
        return {'present':True,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                'mtime_ns':info.st_mtime_ns,'inode':info.st_ino,'value':value}
    except FileNotFoundError:return {'present':False}
    except (OSError,ValueError,json.JSONDecodeError) as error:
        return {'present':True,'error_class':type(error).__name__}

def selected(row,fields):
    return {k:row[k] for k in fields if k in row}

def capture():
    result={'schema':'rc6.issue624.revocation-readonly.v1','at':datetime.now(timezone.utc).isoformat(),
            'read_only':True,'ppi_watch_accessed':False,'root_exists':BASE.is_dir()}
    containers=json.loads(command(['docker','inspect','porota_production_observer','porota_production_dashboard']))
    result['containers']=[]
    for item in containers:
        env=dict(x.split('=',1) for x in item['Config']['Env'] if '=' in x)
        result['containers'].append({'name':item['Name'],'id':item['Id'],'image':item['Image'],
            'status':item['State']['Status'],'oom':item['State']['OOMKilled'],
            'restarts':item['RestartCount'],'source_sha':env.get('POROTA_BUILD_SHA'),
            'source_tree':env.get('POROTA_CANDIDATE_TREE_SHA')})
    result['memory_available_bytes']=next(int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))
    result['free_disk_bytes']=os.statvfs(ROOT).f_bavail*os.statvfs(ROOT).f_frsize
    db=ROOT/'data/paper_v17/observer_v17.db'
    try:
        with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=2) as connection:
            connection.execute('PRAGMA query_only=ON')
            row=connection.execute('SELECT mode,real_orders_sent,heartbeat_at FROM observer_state WHERE id=1').fetchone()
            result['paper_safety']={'mode':row[0],'real_orders_sent':row[1],'heartbeat_at':row[2]}
    except (sqlite3.Error,TypeError,IndexError) as error:
        result['paper_safety']={'read_error_class':type(error).__name__}
    pointer=read(BASE/'CURRENT.json');live=read(BASE/'status.json')
    result['pointer']={k:v for k,v in pointer.items() if k!='value'}
    result['live_status']={k:v for k,v in live.items() if k!='value'}
    result['live_status']['selected']=selected(live.get('value',{}),ALLOW)
    current=pointer.get('value') or {};name=current.get('file')
    result['pointer']['selected']=selected(current,{'file','generation_id','sequence','manifest_sha256'})
    if isinstance(name,str) and re.fullmatch(r'cut-[0-9a-f]{64}\.json\.gz',name):
        cut=read(BASE/name,5*1024**2,compressed=True)
        result['cut']={k:v for k,v in cut.items() if k!='value'}
        value=cut.get('value') or {}
        result['cut']['selected_status']=selected(value.get('status') or {},ALLOW)
        result['cut']['computed_reference']=selected((value.get('report') or {}).get('computed_report') or {},
            {'schema','state','snapshot_id','manifest_sha256','deadline_at','source_as_of','configuration_fingerprint'})
    for name in ('runtime-health.json','publication.json'):
        value=read(BASE.parent/name if name=='runtime-health.json' else BASE/name)
        result[name]={k:v for k,v in value.items() if k!='value'}
        result[name]['selected']=selected(value.get('value') or {},
            {'status','as_of','at','error_class','generation_id','sequence','source_as_of','publish_completed_at','total_seconds','pointer_committed_monotonic','manifest_sha256'})
    cold=BASE/'cold-audit';result['cold_audit']={'exists':cold.is_dir(),'entries':[]}
    if cold.is_dir():
        for path in sorted(cold.iterdir(),key=lambda p:p.name)[:64]:
            info=path.lstat();row={'name':path.name,'bytes':info.st_size,'mtime_ns':info.st_mtime_ns,
                                   'inode':info.st_ino,'is_file':stat.S_ISREG(info.st_mode)}
            if stat.S_ISREG(info.st_mode) and path.suffix=='.json' and info.st_size<131072:
                data=read(path);row['sha256']=data.get('sha256')
                row['selected']=selected(data.get('value') or {},AUDIT)
            result['cold_audit']['entries'].append(row)
    log=subprocess.run(['docker','logs','--since','2026-10-18T16:40:00Z','--tail','2000',
        'porota_production_observer'],capture_output=True,text=True,timeout=15)
    if log.returncode:raise ValueError('HOST_LOG_READ_FAILED')
    raw=log.stdout+'\n'+log.stderr
    allow=('DYNAMIC_SHADOW_RUNTIME_FAIL_CLOSED:','DYNAMIC_SHADOW_STATUS_UNAVAILABLE',
           'DYNAMIC_SHADOW_RUNTIME status=','DYNAMIC_SHADOW_IDLE_MEMORY_UNAVAILABLE:')
    relevant=[line[-400:] for line in raw.splitlines() if any(marker in line for marker in allow)]
    result['worker_log_filter']={'allowlist':list(allow),'matching_lines':len(relevant),'last_lines':relevant[-100:]}
    result['status']='OBSERVED'
    print(json.dumps(result,sort_keys=True,separators=(',',':')))
if __name__=='__main__':capture()
