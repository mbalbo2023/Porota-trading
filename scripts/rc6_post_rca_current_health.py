#!/usr/bin/env python3
"""One-shot READ_ONLY current health after an unverified RCA restoration."""
import base64,hashlib,json,math,sqlite3,subprocess,time
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError
IMAGE="sha256:df464efbca8a4b4f35bcdfa71635815b35d70a5bdd522920aaf18ce796f1706d"
IDENTITIES={"porota_production_observer":"dccef189dd8049b5a722a316954c7c2dfbe2316559dd7dd2f66bb4fb6b95ff57","porota_production_dashboard":"deee5816d77b9bbfcb1fb5965400fa0060d9a1111622e7ccce58579d35b86731"}
BEFORE=datetime.fromisoformat("2026-10-17T12:19:59.070186+00:00")
def sample():
    now=datetime.now(timezone.utc)
    out={"captured_at":now.isoformat(),"containers":{},"failures":[]}
    for name,expected in IDENTITIES.items():
        try:
            fmt='{"Id":{{json .Id}},"Image":{{json .Image}},"RestartCount":{{json .RestartCount}},"State":{"Status":{{json .State.Status}},"StartedAt":{{json .State.StartedAt}},"FinishedAt":{{json .State.FinishedAt}},"ExitCode":{{json .State.ExitCode}},"OOMKilled":{{json .State.OOMKilled}}}}'
            p=subprocess.run(["docker","inspect","--format",fmt,name],capture_output=True,text=True,timeout=3,check=False)
            if p.returncode or len(p.stdout)>8192:raise ValueError("INSPECT_UNAVAILABLE")
            v=json.loads(p.stdout)
            digest=hashlib.sha256(v["Id"].encode()).hexdigest()
            out["containers"][name]={"id_sha256":digest,"identity_matches":digest==expected,
                "image_matches":v["Image"]==IMAGE,"image_id":v["Image"],"restart_count":v["RestartCount"],"lifecycle":v["State"]}
            for ok,code in ((digest==expected,"IDENTITY_MISMATCH"),(v["Image"]==IMAGE,"IMAGE_MISMATCH"),
                (v["State"]["Status"]=="running","PROCESS_NOT_RUNNING"),
                (type(v["RestartCount"]) is int and v["RestartCount"]==0,"RESTART_COUNT_CHANGED"),
                (v["State"]["OOMKilled"] is False,"OOM_FLAG_PRESENT")):
                if not ok:out["failures"].append(code+":"+name)
        except (OSError,ValueError,KeyError,TypeError,subprocess.TimeoutExpired) as e:
            out["failures"].append("CONTAINER_UNVERIFIED:"+name+":"+type(e).__name__)
    try:
        path=Path("/opt/porota-trading/data/paper_v17/observer_v17.db")
        with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=.2)) as c:
            c.row_factory=sqlite3.Row;c.execute("PRAGMA query_only=ON");c.execute("PRAGMA busy_timeout=200")
            end=time.monotonic()+2;c.set_progress_handler(lambda:int(time.monotonic()>end),1000)
            r=dict(c.execute("SELECT mode,real_orders_sent,process_state,session_state,heartbeat_at FROM observer_state WHERE id=1").fetchone())
        stamp=datetime.fromisoformat(r["heartbeat_at"].replace("Z","+00:00"))
        if stamp.tzinfo is None:raise ValueError("HEARTBEAT_CLOCK_INVALID")
        age=(now-stamp).total_seconds()
        out["observer"]={"mode":r["mode"] if r["mode"] in {"PRODUCTION_PAPER","PAPER"} else "UNREVIEWED_MODE",
            "process_state":r["process_state"] if r["process_state"] in {"RUNNING","WAITING_MARKET","COOLDOWN","DEGRADED","STARTING","FAILED","STOPPED"} else "UNREVIEWED_STATE",
            "session_state":r["session_state"] if r["session_state"] in {"CLOSED","MARKET_CLOSED","OPEN","PREOPEN","POSTCLOSE"} else "UNREVIEWED_STATE",
            "real_orders_sent":r["real_orders_sent"] if type(r["real_orders_sent"]) is int else None,
            "heartbeat_at":stamp.isoformat(),"heartbeat_age_seconds":round(age,3),
            "heartbeat_advanced_since_rca":stamp>BEFORE}
        for ok,code in ((r["mode"]=="PRODUCTION_PAPER","PAPER_MODE_UNVERIFIED"),
            (type(r["real_orders_sent"]) is int and r["real_orders_sent"]==0,"REAL_ORDER_COUNT_UNVERIFIED"),
            (r["process_state"]=="WAITING_MARKET","WAITING_MARKET_UNVERIFIED"),
            (r["session_state"] in {"CLOSED","MARKET_CLOSED"},"CLOSED_SESSION_UNVERIFIED"),
            (0<=age<=180,"HEARTBEAT_STALE_OR_FUTURE"),(stamp>BEFORE,"HEARTBEAT_NOT_ADVANCED")):
            if not ok:out["failures"].append(code)
    except (OSError,sqlite3.Error,ValueError,TypeError,KeyError) as e:
        out["failures"].append("DATABASE_STATE_UNVERIFIED:"+type(e).__name__)
    try:
        with urlopen("http://127.0.0.1:8000/health",timeout=4) as response:code=response.status
        out["health_http"]=code
        if code!=200:out["failures"].append("HEALTH_HTTP_NOT_200")
    except HTTPError as e:
        out["health_http"]=e.code;out["failures"].append("HEALTH_HTTP_NOT_200")
    except (OSError,ValueError) as e:
        out["failures"].append("HEALTH_UNAVAILABLE:"+type(e).__name__)
    return out
result={"schema":"rc6.post-rca-current-health.v1","read_only":True,"native_acceptance":False,
    "original_rca_restore":"RED_UNVERIFIED_WITHIN_120_SECONDS","ppi_watch_accessed":False,
    "provider_requests":0,"payload_exported":False,"notification_mode":"read_only",
    "services_stopped_or_restarted":False,"samples":[]}
for i in range(2):
    result["samples"].append(sample())
    if i==0:time.sleep(3)
result["status"]="GREEN" if all(not x["failures"] for x in result["samples"]) else "RED"
raw=(json.dumps(result,sort_keys=True,indent=2)+"\n").encode()
print("RC6_POST_RCA_CURRENT_HEALTH="+json.dumps(result,sort_keys=True,separators=(",",":")))
print("RC6_POST_RCA_RECEIPT="+json.dumps({"bytes":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"base64":base64.b64encode(raw).decode()},separators=(",",":")))
raise SystemExit(0 if result["status"]=="GREEN" else 2)
