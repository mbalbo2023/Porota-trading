import json,subprocess,time,sqlite3
from pathlib import Path
from datetime import datetime,timezone
from urllib.request import urlopen
from contextlib import closing
ROOT=Path("/opt/porota-trading")
OWNED=("porota_production_observer","porota_production_dashboard")
IMAGE="sha256:77e9e14083dd3f018ff55892457b854a9a9f3d249cfcdd0b6f9a416e812fcb8e"
def command(args,timeout=30):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=timeout)
    if p.returncode:raise ValueError("READONLY_COMMAND_FAILED")
    return p.stdout
def inspect():
    result={}
    for name in OWNED:
        d=json.loads(command(["docker","inspect",name]))[0]
        result[name]={"id":d["Id"],"image":d["Image"],"status":d["State"]["Status"],"exit_code":d["State"]["ExitCode"],"oom_killed":d["State"]["OOMKilled"],"restart_count":d["RestartCount"]}
        if d["Image"]!=IMAGE or d["State"]["Status"]!="running" or d["State"]["OOMKilled"]:raise ValueError("CURRENT_RUNTIME_NOT_HEALTHY_EXACT619")
    return result
def db():
    path=ROOT/"data/paper_v17/observer_v17.db"
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=.2)) as c:
        c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON");c.execute("PRAGMA busy_timeout=200")
        end=time.monotonic()+2;c.set_progress_handler(lambda:int(time.monotonic()>end),1000)
        r=dict(c.execute("SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at FROM observer_state WHERE id=1").fetchone())
    assert r["mode"]=="PRODUCTION_PAPER" and type(r["real_orders_sent"]) is int and r["real_orders_sent"]==0
    r["heartbeat_age_seconds"]=(datetime.now(timezone.utc)-datetime.fromisoformat(r["heartbeat_at"])).total_seconds()
    assert 0<=r["heartbeat_age_seconds"]<=180 and r["process_state"]=="WAITING_MARKET" and r["session_state"] in ("CLOSED","MARKET_CLOSED")
    return r
def health():
    with urlopen("http://127.0.0.1:8000/health",timeout=5) as response:assert response.status==200
    return 200
report={"schema":"rc6.native619-health-gate-trace.v1","candidate_sha":"5baeae7b0478d61d2dea04e5dbee4e0c1a3af951","read_only":True,"ppi_watch_accessed":False,"services_changed":False,"runtime_restore_performed":False,"not_canonical_acceptance":True,"started_at":datetime.now(timezone.utc).isoformat()}
try:
    report["before"]=inspect();report["state_before"]=db();report["health_before"]=health()
    state=json.loads((ROOT/"data/deploy/CURRENT_STATE_V2.json").read_text())
    report["deploy_state"]={k:state.get(k) for k in ("candidate_sha","image_id","validation_status")}
    assert state.get("candidate_sha")==report["candidate_sha"]
    trace=json.loads(command(["docker","exec","-u","botuser","porota_production_dashboard","timeout","195","nice","-n","19","python","-B","-c","import json,time\nfrom pathlib import Path\nfrom datetime import datetime,timezone\nimport scripts.rc6_deploy_generation_gate as gate\nexpected=\"5baeae7b0478d61d2dea04e5dbee4e0c1a3af951\";tree=\"268009eb5fb2416fb92435ea828be7ad8d875965\"\nevents=[];last=None;original=gate.receipt\ndef record(kind,detail):\n    global last\n    identity=(kind,detail.get(\"sequence\"),detail.get(\"reason\"))\n    if identity!=last and len(events)<256:\n        events.append({\"at\":datetime.now(timezone.utc).isoformat(),\"kind\":kind,**detail});last=identity\ndef receipt(cut,health,sha,tree,at):\n    try:\n        value=original(cut,health,sha,tree,at)\n        context=gate._observation_context(cut,at)\n        record(\"VALID_CUT\",{\"sequence\":value[\"sequence\"],\"generation_id\":value[\"generation_id\"],**context})\n        return value\n    except Exception as error:\n        reason=str(error) if type(error) is ValueError and str(error) in gate._SAFE_FAILURE_REASONS else \"REDACTED\"\n        record(\"RECEIPT_REJECTED\",{\"reason\":reason,**gate._observation_context(cut,at)})\n        raise\ngate.receipt=receipt\noriginal_reader=gate.read_committed_generation\ndef reader(*args,**kwargs):\n    try:return original_reader(*args,**kwargs)\n    except Exception as error:\n        reason=str(error) if type(error) is ValueError and str(error) in gate._SAFE_FAILURE_REASONS else \"REDACTED\"\n        record(\"READER_REJECTED\",{\"reason\":reason,\"error_class\":type(error).__name__})\n        raise\nroot=Path(\"/app/data/paper_v17/artifacts/observer_v17.db/dynamic-shadow\")\nresult=gate.collect(root,expected,tree,timeout=180,reader=reader)\nprint(json.dumps({\"gate\":result,\"events\":events,\"read_only\":True,\"services_changed\":False,\"not_canonical_acceptance\":True},sort_keys=True))\n"],timeout=205))
    report["generation_trace"]=trace
    time.sleep(5)
    report["after"]=inspect();report["state_after"]=db();report["health_after"]=health()
    assert report["before"]==report["after"]
    assert datetime.fromisoformat(report["state_after"]["heartbeat_at"])>datetime.fromisoformat(report["state_before"]["heartbeat_at"])
    report["status"]="GREEN_CURRENT_HEALTH_ONLY"
except Exception as error:
    report["status"]="RED";report["error_class"]=type(error).__name__
    allowed={"READONLY_COMMAND_FAILED","CURRENT_RUNTIME_NOT_HEALTHY_EXACT619"}
    report["error_code"]=str(error) if type(error) is ValueError and str(error) in allowed else "READONLY_PROBE_FAILED"
report["finished_at"]=datetime.now(timezone.utc).isoformat()
print(json.dumps(report,sort_keys=True),flush=True)
raise SystemExit(0 if report["status"]=="GREEN_CURRENT_HEALTH_ONLY" else 2)
