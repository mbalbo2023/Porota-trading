#!/usr/bin/env python3
"""One bounded read-only compression probe. No service start/stop/restart."""
import argparse, base64, fcntl, hashlib, json, math, os, pathlib, re, shutil
import signal, sqlite3, subprocess, tarfile, time
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
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
    fmt='{"Id":{{json .Id}},"Image":{{json .Image}},"RestartCount":{{json .RestartCount}},"Status":{{json .State.Status}},"StartedAt":{{json .State.StartedAt}}}'
    return json.loads(command(["docker","inspect","--format",fmt,name]))
def runtime_snapshot():
    values={name:snapshot(name) for name in OWNED}
    require(all(x["Image"]==IMAGE and x["Status"]=="running" and type(x["RestartCount"]) is int for x in values.values()),"RCA_OWNED_RUNTIME_IDENTITY_INVALID")
    return values

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


DESCRIPTOR_CODE="import json,os,re\nfrom pathlib import Path\nfrom rc6_shadow_runtime.persistence import read_committed_generation\ndb=Path(os.getenv(\"PAPER_V17_DB_PATH\",\"/app/data/paper_v17/observer_v17.db\"))\nroot=db.parent/\"artifacts\"/db.name/\"dynamic-shadow\"\ncut=read_committed_generation(root,payload_limit=4*1024**2,require_current_status=False)\naudit=cut[\"report\"][\"computed_report\"]\nassert re.fullmatch(r\"computed-[0-9a-f]{64}\\.json\\.gz\",audit[\"file\"])\nassert re.fullmatch(r\"[0-9a-f]{64}\",audit[\"sha256\"])\nassert type(audit[\"raw_bytes\"]) is int and 0<audit[\"raw_bytes\"]<=512*1024**2\nassert type(audit[\"compressed_bytes\"]) is int and 0<audit[\"compressed_bytes\"]<=128*1024**2\nprint(json.dumps({\"root_relative\":str(root.relative_to(\"/app/data\")),\n \"file\":audit[\"file\"],\"raw_sha256\":audit[\"sha256\"],\"raw_bytes\":audit[\"raw_bytes\"],\n \"compressed_bytes\":audit[\"compressed_bytes\"],\"generation_id\":cut[\"pointer\"][\"generation_id\"],\n \"manifest_sha256\":cut[\"pointer\"][\"manifest_sha256\"]},sort_keys=True))\n"

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
    require(hashlib.sha256(packet.read_bytes()).hexdigest()==args.packet_sha,"RCA_PACKET_HASH_MISMATCH")
    report={"schema":"rc6.readonly-compression-host.v1","status":"RED","run_key":args.key,
            "candidate_sha":"7bad982559f56323c00f555f0d8bb480dd00dfd0","image_id":IMAGE,
            "native_acceptance":False,"full_session_stress":False,"services_restarted":False,
            "production_input_read_only":True,"production_input_files":1,"payload_exported":False,
            "network":"none","memory_limit_bytes":MEMORY,"nano_cpus":250000000,
            "ppi_watch_accessed":False,"runtime_restore":"NOT_NEEDED_NO_SERVICE_ACTION",
            "started_at":datetime.now(timezone.utc).isoformat()}
    work=ROOT/"data/deploy/diagnostics/compression"/args.key
    require(not any(x.is_symlink() for x in (work,*work.parents)),"RCA_WORK_PATH_ALIAS")
    work.mkdir(parents=True,exist_ok=False,mode=0o700)
    tool=work/"tool";tool.mkdir(mode=0o755)
    output=work/"output";output.mkdir(mode=0o700);os.chown(output,1000,1000)
    lock_fd=os.open(ROOT/"data/deploy/.readonly-compression-rca.lock",os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    require(os.fstat(lock_fd).st_nlink==1,"RCA_LOCK_ALIAS")
    stream=os.fdopen(lock_fd,"a+")
    container=None;before=None;process=None;samples=[]
    def interrupted(signum,frame):raise ValueError("RCA_INTERRUPTED")
    signal.signal(signal.SIGTERM,interrupted);signal.signal(signal.SIGINT,interrupted)
    try:
        fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        report["phase"]="ADMISSION"
        admitted_samples=[]
        for attempt in range(31):
            memory=available();disk=shutil.disk_usage(ROOT).free
            admitted_samples.append(memory)
            report["admission"]={"memory_available":memory,"required_memory":MEMORY+128*1024**2,
                "disk_free":disk,"required_disk":RESERVE,"wait_seconds":attempt*2,
                "minimum_memory_seen":min(admitted_samples),"maximum_memory_seen":max(admitted_samples)}
            require(disk>=RESERVE,"RCA_DISK_ADMISSION_BLOCKED")
            if memory>=MEMORY+128*1024**2:break
            if attempt<30:time.sleep(2)
        else:raise ValueError("RCA_MEMORY_ADMISSION_BLOCKED")
        db=DATA/"paper_v17/observer_v17.db"
        admitted=database_state(db)
        report["heartbeat_before"]=admitted["heartbeat_at"]
        before=runtime_snapshot()
        report["phase"]="PACKET"
        with tarfile.open(packet,"r:gz") as archive:
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
        report["phase"]="SOURCE"
        descriptor=json.loads(command(["docker","exec",OWNED[1],"python","-c",DESCRIPTOR_CODE],timeout=25))
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
        signal.signal(signal.SIGTERM,signal.SIG_IGN);signal.signal(signal.SIGINT,signal.SIG_IGN)
        if container and re.fullmatch(r"[0-9a-f]{64}",container):
            try:
                removed=subprocess.run(["docker","rm","--force",container],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=20)
                report["diagnostic_container_cleanup"]="GREEN" if removed.returncode==0 else "RED"
            except subprocess.TimeoutExpired:
                report["diagnostic_container_cleanup"]="RED_TIMEOUT"
            if report["diagnostic_container_cleanup"]!="GREEN":report["status"]="RED"
        if before is not None:
            try:
                after=runtime_snapshot()
                require(after==before,"RCA_OWNED_RUNTIME_CHANGED")
                for _ in range(12):
                    row=database_state(DATA/"paper_v17/observer_v17.db")
                    if datetime.fromisoformat(row["heartbeat_at"])>datetime.fromisoformat(report["heartbeat_before"]):break
                    time.sleep(5)
                else:raise ValueError("RCA_HEARTBEAT_NOT_ADVANCED")
                report["owned_runtime_unchanged"]=True
                report["heartbeat_after"]=row["heartbeat_at"]
                report["real_orders_sent"]=0
            except Exception:
                report["owned_runtime_unchanged"]=False;report["status"]="RED"
                report["runtime_after_check"]="UNVERIFIED"
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
