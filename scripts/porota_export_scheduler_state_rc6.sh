#!/usr/bin/env bash
set -Eeuo pipefail
umask 027
ROOT="${POROTA_ROOT:-/opt/porota-trading}"
OUTDIR="${POROTA_SCHEDULER_DIR:-$ROOT/data/scheduler}"
OUT="$OUTDIR/systemd_timers.json"
LOCK="/run/lock/porota-scheduler-export-rc6.lock"
mkdir -p "$OUTDIR"
exec 9>"$LOCK"
flock -n 9 || exit 0
python3 - "$OUT" <<'PY'
from __future__ import annotations
import json,subprocess,sys
from datetime import datetime,timezone
from pathlib import Path
out=Path(sys.argv[1])
def run(*args):
    p=subprocess.run(args,text=True,capture_output=True,timeout=12,check=False)
    return p.returncode,p.stdout,p.stderr
def props(unit,names):
    cmd=["systemctl","show",unit,"--no-pager"]
    for name in names: cmd += ["-p",name]
    rc,stdout,_=run(*cmd)
    result={}
    if rc not in (0,1): return result
    for line in stdout.splitlines():
        if "=" in line:
            k,v=line.split("=",1); result[k]=v
    return result
rc,stdout,stderr=run("systemctl","list-unit-files","--type=timer","--no-legend","--no-pager")
timers=[]
if rc==0:
    for line in stdout.splitlines():
        parts=line.split()
        if not parts: continue
        unit=parts[0]
        if not unit.startswith("porota-") or not unit.endswith(".timer"): continue
        t=props(unit,["Id","Description","ActiveState","SubState","UnitFileState","LastTriggerUSec","NextElapseUSecRealtime","Triggers"])
        service=unit[:-6]+".service"
        s=props(service,["Id","Description","ActiveState","SubState","Result","ExecMainCode","ExecMainStatus","ActiveEnterTimestamp","InactiveExitTimestamp"])
        timers.append({"unit":unit,"description":t.get("Description") or "",
            "active_state":t.get("ActiveState") or "unknown","sub_state":t.get("SubState") or "unknown",
            "enabled_state":t.get("UnitFileState") or (parts[1] if len(parts)>1 else "unknown"),
            "last_trigger":t.get("LastTriggerUSec") or None,"next_elapse":t.get("NextElapseUSecRealtime") or None,
            "triggers":t.get("Triggers") or service,"service":service,
            "service_description":s.get("Description") or "","service_active_state":s.get("ActiveState") or "unknown",
            "service_sub_state":s.get("SubState") or "unknown","last_result":s.get("Result") or "unknown",
            "exec_main_code":s.get("ExecMainCode") or None,"exec_main_status":s.get("ExecMainStatus") or None,
            "service_active_enter":s.get("ActiveEnterTimestamp") or None,"service_inactive_exit":s.get("InactiveExitTimestamp") or None})
payload={"schema":"porota-scheduler-systemd-v1","recorded_at":datetime.now(timezone.utc).isoformat(),
         "state":"OK" if rc==0 else "ERROR","error":"" if rc==0 else (stderr.strip()[:500] or "systemctl list-unit-files failed"),
         "timers":sorted(timers,key=lambda x:x["unit"])}
tmp=out.with_name("."+out.name+".tmp")
tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
tmp.chmod(0o640); tmp.replace(out)
PY
chmod 0640 "$OUT"
