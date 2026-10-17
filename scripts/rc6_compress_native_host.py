#!/usr/bin/env python3
"""Bounded compression RCA after governed CLOSED/PAPER quiescence; exact restore."""
import argparse, base64, io, fcntl, hashlib, json, math, os, pathlib, re, shutil
import signal, sqlite3, subprocess, tarfile, time
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import urlopen
ROOT=Path("/opt/porota-trading").resolve(strict=True)
DATA=(ROOT/"data").resolve(strict=True)
IMAGE="sha256:bc406686ad8774469e72def0ad94d468e2320de56d790c830f90f20d2a7b6fed"
OWNED=("porota_production_observer","porota_production_dashboard")
MEMORY=64*1024**2
RESERVE=2*1024**3
def require(ok,code):
    if not ok:raise ValueError(code)
def command(args,timeout=30):
    value=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=timeout)
    require(value.returncode==0,"RCA_HOST_COMMAND_FAILED")
    return value.stdout.strip()
def available():
    return int(next(x.split()[1] for x in Path("/proc/meminfo").read_text().splitlines() if x.startswith("MemAvailable:")))*1024
def snapshot(name):
    fmt='{"Id":{{json .Id}},"Image":{{json .Image}},"RestartCount":{{json .RestartCount}},"State":{"Status":{{json .State.Status}},"StartedAt":{{json .State.StartedAt}},"FinishedAt":{{json .State.FinishedAt}},"ExitCode":{{json .State.ExitCode}},"OOMKilled":{{json .State.OOMKilled}}}}'
    return json.loads(command(["docker","inspect","--format",fmt,name]))
def runtime_snapshot():
    values={name:snapshot(name) for name in OWNED}
    require(all(x["Image"]==IMAGE and x["State"]["Status"]=="running" and type(x["RestartCount"]) is int for x in values.values()),"RCA_OWNED_RUNTIME_IDENTITY_INVALID")
    return values

def lifecycle(value):
    return {key: value.get('State', {}).get(key) for key in
            ('Status', 'StartedAt', 'FinishedAt', 'ExitCode', 'OOMKilled')}

def clean_stop(before, after):
    """Distinguish a retained historical OOM flag from this clean stop.

    A new/unknown OOM or any nonzero exit remains blocking. Docker may retain
    OOMKilled across starts; a fresh clean ExitCode alone does not erase its
    history. Accept that history only when the prior lifecycle was verified.
    """
    old, new = lifecycle(before), lifecycle(after)
    if before['Id'] != after['Id'] or before['Image'] != after['Image']:
        raise ValueError('QUIESCENCE_STOP_IDENTITY_CHANGED')
    if new['Status'] != 'exited': raise ValueError('QUIESCENCE_STOP_NOT_EXITED')
    if type(new['ExitCode']) is not int or new['ExitCode'] != 0:
        raise ValueError('QUIESCENCE_STOP_NONZERO_OR_UNKNOWN_EXIT')
    if new['OOMKilled'] is False: return 'CLEAN_EXIT'
    try:
        started = datetime.fromisoformat(old['StartedAt'].replace('Z', '+00:00'))
        previous_finish = datetime.fromisoformat(old['FinishedAt'].replace('Z', '+00:00'))
        finished = datetime.fromisoformat(new['FinishedAt'].replace('Z', '+00:00'))
        if (old['Status'] == 'running' and old['OOMKilled'] is True and new['OOMKilled'] is True
                and type(old['ExitCode']) is int and old['ExitCode'] == 0
                and new['StartedAt'] == old['StartedAt'] and previous_finish.year >= 2020
                and previous_finish < started <= finished <= datetime.now(timezone.utc)):
            return 'INHERITED_OOM_FLAG_CLEAN_EXIT'
    except (AttributeError, TypeError, ValueError): pass
    raise ValueError('QUIESCENCE_STOP_OOM_NEW_OR_UNVERIFIED')

def restored_state(before,current,image):
    if current['Id']!=before['Id'] or current['Image']!=image:
        raise ValueError('STRESS_RESTORE_IDENTITY_MISMATCH')
    if current['State']['Status']!='running':
        raise ValueError('STRESS_RESTORE_PROCESS_NOT_RUNNING')
    previous=before.get('RestartCount'); observed=current.get('RestartCount')
    if type(previous) is not int or type(observed) is not int or observed!=previous:
        raise ValueError('STRESS_RESTORE_RESTART_COUNT_CHANGED_OR_UNKNOWN')
    return dict(lifecycle(current),RestartCount=observed)

def database_state(path, *, require_waiting=True):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2)) as c:
        c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON');c.execute('PRAGMA busy_timeout=200')
        end=time.monotonic()+2;c.set_progress_handler(lambda:int(time.monotonic()>end),1000)
        row=dict(c.execute('SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at FROM observer_state WHERE id=1').fetchone())
        if row['mode']!='PRODUCTION_PAPER' or type(row['real_orders_sent']) is not int or row['real_orders_sent']!=0:
            raise ValueError('STRESS_PAPER_REQUIRED')
        if require_waiting:
            stamp=datetime.fromisoformat(row['heartbeat_at'])
            if (row['session_state'] not in {'CLOSED','MARKET_CLOSED'} or row['process_state']!='WAITING_MARKET'
                    or stamp.tzinfo is None or not 0<=(datetime.now(timezone.utc)-stamp).total_seconds()<=180):
                raise ValueError('STRESS_CLOSED_QUIESCENT_HEARTBEAT_REQUIRED')
        for table,status in (('paper_positions','OPEN'),('paper_cauciones','OPEN'),('paper_future_positions','ACTIVE')):
            if not c.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone():
                raise ValueError('STRESS_LEDGER_UNVERIFIED')
            if c.execute('SELECT 1 FROM '+table+' WHERE status=? LIMIT 1',(status,)).fetchone():
                raise ValueError('STRESS_OPEN_FINANCIAL_POSITION')
        return row



def envelope(path,limit=4*1024**2):
    import gzip
    require(path.is_file() and not path.is_symlink() and path.stat().st_nlink==1,"RCA_ENVELOPE_ALIAS")
    with (gzip.open(path,"rb") if path.name.endswith(".gz") else path.open("rb")) as source:
        raw=source.read(limit+1)
    require(len(raw)<=limit,"RCA_ENVELOPE_SIZE_LIMIT")
    value=json.loads(raw)
    payload=value["payload"]
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",",":"),default=str,allow_nan=False).encode()).hexdigest()
    require(digest==value.get("digest"),"RCA_ENVELOPE_DIGEST_MISMATCH")
    return payload

def source_descriptor(root):
    pointer=envelope(root/"CURRENT.json",131072)
    require(re.fullmatch(r"cut-[0-9a-f]{64}\.json\.gz",pointer.get("file","")),"RCA_CUT_NAME_INVALID")
    cut=envelope(root/pointer["file"])
    manifest=cut["manifest"]
    digest=lambda x:hashlib.sha256(json.dumps(x,sort_keys=True,separators=(",",":"),default=str,allow_nan=False).encode()).hexdigest()
    require(digest(manifest)==pointer.get("manifest_sha256")
        and manifest.get("generation_id")==pointer.get("generation_id")
        and manifest.get("sequence")==pointer.get("sequence"),"RCA_CUT_MANIFEST_MISMATCH")
    for role in ("report","status"):
        member=cut[role]
        require(digest(member)==manifest["files"][role]["payload_digest"]
            and member.get("generation_id")==pointer["generation_id"]
            and member.get("source_watermark")==manifest["source_watermark"]
            and member.get("configuration_fingerprint")==manifest["configuration_fingerprint"],
            "RCA_CUT_MEMBER_MISMATCH")
        require(type(member.get("real_orders_sent")) is int and member["real_orders_sent"]==0
            and member.get("real_routes")=="NOT_CALLED","RCA_CUT_AUTHORITY_INVALID")
    audit=cut["report"]["computed_report"]
    require(re.fullmatch(r"[0-9a-f]{64}",audit.get("sha256","")) is not None
        and audit.get("file")=="computed-"+audit["sha256"]+".json.gz","RCA_AUDIT_IDENTITY_INVALID")
    require(type(audit.get("raw_bytes")) is int and 0<audit["raw_bytes"]<=512*1024**2
        and type(audit.get("compressed_bytes")) is int and 0<audit["compressed_bytes"]<=128*1024**2,
        "RCA_AUDIT_SIZE_INVALID")
    return {"root_relative":str(root.relative_to(DATA)),"file":audit["file"],
        "raw_sha256":audit["sha256"],"raw_bytes":audit["raw_bytes"],
        "compressed_bytes":audit["compressed_bytes"],"generation_id":pointer["generation_id"],
        "manifest_sha256":pointer["manifest_sha256"]}

def await_idle_hint(root):
    for _ in range(31):
        try:
            status=envelope(root/"status.json",131072)
            stamp=datetime.fromisoformat(status["as_of"].replace("Z","+00:00"))
            tick=(status.get("generation_timing") or {}).get("tick_seconds")
            require(type(tick) in (int,float) and math.isfinite(tick) and 0<=tick<=1200
                and stamp.tzinfo is not None,"RCA_IDLE_CLOCK_INVALID")
            age=(datetime.now(timezone.utc)-stamp).total_seconds()-tick
            if (status.get("status") in {"SHADOW_OBSERVING","PREOPEN_SNAPSHOT_REQUIRED_DURING_SESSION"}
                    and 0<=age<=20):return
        except (ValueError,KeyError,FileNotFoundError):
            pass
        time.sleep(3)
    raise ValueError("RCA_IDLE_WINDOW_NOT_OBSERVED")

def safe_metrics(raw,descriptor):
    require(raw.get("schema")=="rc6.bounded-native-compression-rca.v1"
        and raw.get("context")=="READ_ONLY_NATIVE_COMPRESSION_RCA","RCA_METRICS_SCHEMA_INVALID")
    keys=("schema","status","context","nice","isal_version","raw_sha256","raw_bytes",
          "native_acceptance","full_session_stress","production_input_read_only","network",
          "provider_requests","real_orders_sent","payload_exported","threads",
          "legacy_reader_verified","stdlib_reader_verified")
    result={key:raw[key] for key in keys if key in raw}
    require(raw.get("raw_sha256")==descriptor["raw_sha256"] and raw.get("raw_bytes")==descriptor["raw_bytes"],"RCA_METRICS_SOURCE_INVALID")
    require(raw.get("nice")==19 and raw.get("native_acceptance") is False
        and raw.get("full_session_stress") is False and raw.get("payload_exported") is False,
        "RCA_METRICS_AUTHORITY_INVALID")
    rows=raw.get("measurements")
    require(isinstance(rows,list) and len(rows)<=6,"RCA_MEASUREMENTS_INVALID")
    numeric=("raw_bytes","compressed_bytes","compression_cpu_seconds","source_decode_cpu_seconds",
             "raw_hash_cpu_seconds","total_write_cpu_seconds","write_wall_seconds",
             "stdlib_readback_cpu_seconds","peak_rss_kib")
    result["measurements"]=[]
    for row in rows:
        require(row.get("variant") in {"stdlib1","isal0","isal1"} and row.get("raw_sha256")==descriptor["raw_sha256"],"RCA_MEASUREMENT_IDENTITY_INVALID")
        require(all(type(row.get(k)) in (int,float) and math.isfinite(row[k]) and 0<=row[k]<=1024**3 for k in numeric),"RCA_MEASUREMENT_NUMERIC_INVALID")
        require(row["peak_rss_kib"]<=MEMORY//1024,"RCA_PEAK_RSS_OVER_CONFIGURED_LIMIT")
        result["measurements"].append({"variant":row["variant"],"raw_sha256":row["raw_sha256"],**{k:row[k] for k in numeric}})
    if raw.get("status")=="GREEN":
        require(len(rows)==6 and raw.get("legacy_reader_verified") is True and raw.get("stdlib_reader_verified") is True,"RCA_NONEMPTY_GUARD_RED")
        for key in ("median_compression_cpu_seconds","median_total_write_cpu_seconds"):
            value=raw.get(key)
            require(isinstance(value,dict) and set(value)=={"stdlib1","isal0","isal1"}
                and all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=600 for v in value.values()),"RCA_MEDIANS_INVALID")
            result[key]=value
    else:
        code=raw.get("error_code")
        result["error_code"]=code if type(code) is str and re.fullmatch(r"RCA_[A-Z_0-9]{1,70}",code) else None
    return result

def run(args):
    require(re.fullmatch(r"[0-9]+-[0-9]+",args.key) is not None,"RCA_RUN_KEY_INVALID")
    require(re.fullmatch(r"[0-9a-f]{64}",args.packet_sha) is not None,"RCA_PACKET_HASH_INVALID")
    expected=Path("/tmp")/("rc6-native-compress-"+args.key+".tgz")
    packet=Path(args.packet)
    require(packet==expected and not packet.is_symlink() and packet.is_file()
        and packet.stat().st_nlink==1 and packet.stat().st_size<=16*1024**2,"RCA_PACKET_PATH_INVALID")
    with packet.open("rb") as source:packet_content=source.read(16*1024**2+1)
    require(len(packet_content)<=16*1024**2 and hashlib.sha256(packet_content).hexdigest()==args.packet_sha,"RCA_PACKET_HASH_MISMATCH")
    report={"schema":"rc6.readonly-compression-host.v1","status":"RED","run_key":args.key,
            "candidate_sha":"7bad982559f56323c00f555f0d8bb480dd00dfd0","image_id":IMAGE,
            "native_acceptance":False,"full_session_stress":False,"services_restarted":False,
            "production_input_read_only":True,"production_input_files":1,"payload_exported":False,
            "network":"none","memory_limit_bytes":MEMORY,"nano_cpus":250000000,
            "ppi_watch_accessed":False,"runtime_restore":"NOT_NEEDED_NO_SERVICE_ACTION","planned_closed_quiesce":True,
            "started_at":datetime.now(timezone.utc).isoformat()}
    work=ROOT/"data/deploy/diagnostics/compression"/args.key
    require(not any(x.is_symlink() for x in (work,*work.parents)),"RCA_WORK_PATH_ALIAS")
    work.mkdir(parents=True,exist_ok=False,mode=0o700)
    tool=work/"tool";tool.mkdir(mode=0o755)
    output=work/"output";output.mkdir(mode=0o700);os.chown(output,1000,1000)
    lock_fd=os.open(ROOT/"data/deploy/.readonly-compression-rca.lock",os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    require(os.fstat(lock_fd).st_nlink==1,"RCA_LOCK_ALIAS")
    stream=os.fdopen(lock_fd,"a+")
    container=None;before=None;process=None;samples=[];stopped=[]
    def interrupted(signum,frame):raise ValueError("RCA_INTERRUPTED")
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted);signal.signal(signal.SIGHUP,interrupted)
    try:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        report["phase"]="PRE_ADMISSION"
        report["pre_quiesce_resources"]={"memory_available":available(),"disk_free":shutil.disk_usage(ROOT).free}
        require(shutil.disk_usage(ROOT).free>=RESERVE,"RCA_DISK_ADMISSION_BLOCKED")
        db=DATA/"paper_v17/observer_v17.db"
        admitted=database_state(db)
        report["heartbeat_before"]=admitted["heartbeat_at"]
        before=runtime_snapshot()
        report["phase"]="PACKET"
        with tarfile.open(fileobj=io.BytesIO(packet_content),mode="r:gz") as archive:
            members=archive.getmembers()
            require(len(members)<=300 and sum(x.size for x in members)<=32*1024**2,"RCA_PACKET_EXPANSION_LIMIT")
            for member in members:
                path=pathlib.PurePosixPath(member.name)
                require(not path.is_absolute() and ".." not in path.parts and (member.isdir() or member.isfile()),"RCA_PACKET_ALIAS_INVALID")
                require(path.parts and (path.parts[0]=="isal-module" or member.name=="probe.py"),"RCA_PACKET_MEMBER_INVALID")
                target=tool/Path(*path.parts)
                if member.isdir():target.mkdir(parents=True,exist_ok=True,mode=0o755);continue
                target.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
                with archive.extractfile(member) as source,target.open("xb") as sink:shutil.copyfileobj(source,sink)
                os.chmod(target,0o444)
        report["phase"]="WAIT_IDLE"
        shadow_root=DATA/"paper_v17/artifacts/observer_v17.db/dynamic-shadow"
        require(not any(x.is_symlink() for x in (shadow_root,*shadow_root.parents)),"RCA_SHADOW_ROOT_ALIAS")
        await_idle_hint(shadow_root)
        database_state(db)
        require(runtime_snapshot()==before,"RCA_RUNTIME_CHANGED_BEFORE_QUIESCE")
        report["phase"]="QUIESCE"
        report["stop_evidence"]={}
        for name in OWNED:
            stopped.append(name)
            command(["docker","stop","--time","45",before[name]["Id"]],timeout=55)
            current=snapshot(name)
            classification=clean_stop(before[name],current)
            report["stop_evidence"][name]={"classification":classification,"exit_code":current["State"]["ExitCode"],"oom_killed":current["State"]["OOMKilled"]}
        database_state(db,require_waiting=False)
        report["runtime_restore"]="PENDING_EXACT_ORIGINAL_CONTAINERS"
        report["phase"]="ADMISSION_AFTER_QUIESCE"
        admitted_samples=[]
        for attempt in range(31):
            memory=available();disk=shutil.disk_usage(ROOT).free;admitted_samples.append(memory)
            report["admission"]={"memory_available":memory,"required_memory":MEMORY+128*1024**2,
                "disk_free":disk,"required_disk":RESERVE,"wait_seconds":attempt*2,
                "minimum_memory_seen":min(admitted_samples),"maximum_memory_seen":max(admitted_samples)}
            require(disk>=RESERVE,"RCA_DISK_ADMISSION_BLOCKED")
            if memory>=MEMORY+128*1024**2:break
            if attempt<30:time.sleep(2)
        else:raise ValueError("RCA_MEMORY_ADMISSION_BLOCKED")
        report["phase"]="SOURCE"
        descriptor=source_descriptor(shadow_root)
        relative=pathlib.PurePosixPath(descriptor["root_relative"])
        require(not relative.is_absolute() and ".." not in relative.parts,"RCA_INPUT_ROOT_INVALID")
        source=DATA/Path(*relative.parts)/descriptor["file"]
        require(not any(x.is_symlink() for x in (source,*source.parents))
            and source.is_file() and source.stat().st_nlink==1
            and source.resolve().is_relative_to(DATA),"RCA_INPUT_ALIAS_OR_RACE")
        require(source.stat().st_size==descriptor["compressed_bytes"],"RCA_INPUT_SIZE_MISMATCH")
        report["source"]={key:descriptor[key] for key in ("raw_sha256","raw_bytes","compressed_bytes","generation_id","manifest_sha256")}
        file_sizes={}
        for role,filename in (("full_audit",descriptor["file"]),("checkpoint","checkpoint.json.gz"),("selection","latest.json.gz")):
            path=source.parent/filename
            require(not path.is_symlink() and path.is_file() and path.stat().st_nlink==1,"RCA_MEMBER_ALIAS_OR_RACE")
            with path.open("rb") as value:
                require(value.read(2)==b"\x1f\x8b","RCA_GZIP_MEMBER_INVALID")
                value.seek(-4,os.SEEK_END);isize=int.from_bytes(value.read(4),"little")
                compressed_size=os.fstat(value.fileno()).st_size
            file_sizes[role]={"compressed_bytes":compressed_size,"gzip_isize_mod2_32":isize}
        report["private_file_size_metadata"]=file_sizes
        report["phase"]="PROBE"
        name="porota_rc6_compress_"+args.key.replace("-","_")
        create=["docker","create","--name",name,"--network","none","--user","1000:1000",
            "--read-only","--cap-drop","ALL","--security-opt","no-new-privileges",
            "--pids-limit","64","--memory",str(MEMORY),"--memory-swap",str(MEMORY),"--cpus","0.25",
            "--tmpfs","/tmp:rw,nosuid,nodev,size=32m",
            "--mount","type=bind,src="+str(source)+",dst=/input/report.json.gz,readonly",
            "--mount","type=bind,src="+str(tool)+",dst=/tool,readonly",
            "--mount","type=bind,src="+str(output)+",dst=/work",
            "--env","PYTHONPATH=/tool/isal-module","--env","PYTHONDONTWRITEBYTECODE=1",
            "--entrypoint","timeout",IMAGE,"--signal=TERM","--kill-after=10","600","python","/tool/probe.py","--input","/input/report.json.gz",
            "--expected-sha256",descriptor["raw_sha256"],"--raw-bytes",str(descriptor["raw_bytes"]),
            "--context","READ_ONLY_NATIVE_COMPRESSION_RCA"]
        container=command(create,timeout=35)
        require(re.fullmatch(r"[0-9a-f]{64}",container) is not None,"RCA_CONTAINER_ID_INVALID")
        fmt='{"Memory":{{json .HostConfig.Memory}},"MemorySwap":{{json .HostConfig.MemorySwap}},"NanoCpus":{{json .HostConfig.NanoCpus}},"NetworkMode":{{json .HostConfig.NetworkMode}},"ReadonlyRootfs":{{json .HostConfig.ReadonlyRootfs}}}'
        limits=json.loads(command(["docker","inspect","--format",fmt,container]))
        require(limits=={"Memory":MEMORY,"MemorySwap":MEMORY,"NanoCpus":250000000,"NetworkMode":"none","ReadonlyRootfs":True},"RCA_CONTAINER_LIMITS_INVALID")
        process=subprocess.Popen(["docker","start","--attach",container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+600
        while process.poll() is None:
            memory=available();disk=shutil.disk_usage(ROOT).free;samples.append((memory,disk))
            require(memory>=32*1024**2 and disk>=RESERVE and time.monotonic()<deadline,"RCA_RESOURCE_OR_TIME_ABORT")
            time.sleep(2)
        fmt='{"ExitCode":{{json .State.ExitCode}},"OOMKilled":{{json .State.OOMKilled}}}'
        child=json.loads(command(["docker","inspect","--format",fmt,container]))
        report["process_exit_code"]=child["ExitCode"];report["oom_killed"]=child["OOMKilled"]
        metrics_path=output/"metrics.json"
        require(metrics_path.is_file() and not metrics_path.is_symlink() and metrics_path.stat().st_size<=131072,"RCA_METRICS_MISSING")
        report["metrics"]=safe_metrics(json.loads(metrics_path.read_text()),descriptor)
        require(process.returncode==0 and child["ExitCode"]==0 and child["OOMKilled"] is False
                and report["metrics"]["status"]=="GREEN","RCA_PROBE_RED")
        report["status"]="GREEN"
    except Exception as error:
        report["error_class"]=type(error).__name__ if type(error).__name__ in {"ValueError","TimeoutExpired","FileNotFoundError","PermissionError","BlockingIOError"} else "RCA_UNAVAILABLE"
        value=str(error)
        report["error_code"]=value if type(error) is ValueError and re.fullmatch(r"(?:RCA|STRESS)_[A-Z_0-9]{1,75}",value) else None
    finally:
        signal.signal(signal.SIGTERM,signal.SIG_IGN);signal.signal(signal.SIGINT,signal.SIG_IGN);signal.signal(signal.SIGHUP,signal.SIG_IGN)
        if container and re.fullmatch(r"[0-9a-f]{64}",container):
            try:
                removed=subprocess.run(["docker","rm","--force",container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
                report["diagnostic_container_cleanup"]="GREEN" if removed.returncode==0 else "RED"
            except subprocess.TimeoutExpired:
                report["diagnostic_container_cleanup"]="RED_TIMEOUT"
            if report["diagnostic_container_cleanup"]!="GREEN":report["status"]="RED"
        if before is not None:
            failures=[]
            for name in stopped:
                try:
                    current=snapshot(name)
                    require(current["Id"]==before[name]["Id"] and current["Image"]==IMAGE,"RCA_RESTORE_IDENTITY_CHANGED")
                    if current["State"]["Status"]!="running":
                        command(["docker","start",before[name]["Id"]],timeout=35)
                        report["services_restarted"]=True
                    restored_state(before[name],snapshot(name),IMAGE)
                except Exception:
                    failures.append("RCA_RESTORE_PROCESS_OR_IDENTITY_UNVERIFIED")
            for _ in range(24):
                try:
                    after=runtime_snapshot()
                    for name in OWNED:restored_state(before[name],after[name],IMAGE)
                    if not stopped:require(after==before,"RCA_UNPLANNED_RUNTIME_CHANGE")
                    row=database_state(DATA/"paper_v17/observer_v17.db")
                    require(datetime.fromisoformat(row["heartbeat_at"])>datetime.fromisoformat(report["heartbeat_before"]),"RCA_HEARTBEAT_NOT_ADVANCED")
                    with urlopen("http://127.0.0.1:8000/health",timeout=3) as response:
                        require(response.status==200,"RCA_RESTORE_HEALTH_NOT_200")
                    report["heartbeat_after"]=row["heartbeat_at"];report["real_orders_sent"]=0
                    report["runtime_lifecycle_checks"]={name:{
                        "identity_sha256_before":hashlib.sha256(before[name]["Id"].encode()).hexdigest(),
                        "identity_sha256_after":hashlib.sha256(after[name]["Id"].encode()).hexdigest(),
                        "image_id_before":before[name]["Image"],"image_id_after":after[name]["Image"],
                        "restart_count_before":before[name]["RestartCount"],"restart_count_after":after[name]["RestartCount"],
                        "status_after":after[name]["State"]["Status"]} for name in OWNED}
                    report["exact_original_runtime_recovered"]=True
                    break
                except Exception:
                    time.sleep(5)
            else:failures.append("RCA_RESTORE_LIVENESS_OR_HEALTH_UNVERIFIED")
            report["runtime_restore"]=("GREEN" if not failures else "RED") if stopped else ("NOT_NEEDED_NO_SERVICE_ACTION" if not failures else "RED")
            if failures:report["restore_failures"]=failures;report["status"]="RED"
        if samples:
            report["minimum_host_memory_available"]=min(x[0] for x in samples)
            report["minimum_disk_free_bytes"]=min(x[1] for x in samples)
            report["resource_samples"]=len(samples)
        report["finished_at"]=datetime.now(timezone.utc).isoformat()
        packet.unlink(missing_ok=True)
        stream.close()
        saved=(json.dumps(report,sort_keys=True,indent=2,allow_nan=False)+"\n").encode()
        (work/"host-summary.json").write_bytes(saved)
        print("RC6_NATIVE_COMPRESSION_RCA="+json.dumps(report,sort_keys=True,separators=(",",":")),flush=True)
        print("RC6_NATIVE_COMPRESSION_RCA_FILE_SHA256="+hashlib.sha256(saved).hexdigest(),flush=True)
        print("RC6_NATIVE_COMPRESSION_RCA_FILE_BYTES="+str(len(saved)),flush=True)
        encoded=base64.b64encode(saved).decode()
        chunks=[encoded[n:n+32768] for n in range(0,len(encoded),32768)]
        for index,chunk in enumerate(chunks):
            print("RC6_NATIVE_COMPRESSION_RCA_FILE_B64_PART="+str(index)+"/"+str(len(chunks))+":"+chunk,flush=True)
    return 0 if report["status"]=="GREEN" else 2

if __name__=="__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--key",required=True)
    parser.add_argument("--packet",required=True);parser.add_argument("--packet-sha",required=True)
    raise SystemExit(run(parser.parse_args()))
