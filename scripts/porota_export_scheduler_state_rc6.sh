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
PYTHONPATH="$ROOT" python3 - "$OUT" <<'PY'
from __future__ import annotations
import json,os,re,stat,subprocess,sys,time
from datetime import datetime,timezone
from pathlib import Path
from rc6_scheduler_snapshot import CLOSED_REUSE_SECONDS, execution_state, instant, monotonic_target, snapshot_phase
out=Path(sys.argv[1])
# Use the existing timer cadence. In CLOSED, reuse a recent complete snapshot.
# Unknown calendar, missing/untrusted output or clock skew takes the original
# full export path. No provider or trading source is accessed here.
try:
    now=datetime.now(timezone.utc)
    info=out.stat()
    age=time.time()-info.st_mtime
    if (not out.is_symlink() and stat.S_ISREG(info.st_mode) and info.st_size <= 1024*1024 and
            snapshot_phase(now)=='CLOSED' and 0 <= age < CLOSED_REUSE_SECONDS):
        prior=json.loads(out.read_text(encoding='utf-8'))
        captured = instant(prior.get('recorded_at'))
        if (prior.get('schema')=='porota-scheduler-systemd-v1' and
                captured is not None and 0 <= (now-captured).total_seconds() < CLOSED_REUSE_SECONDS and
                snapshot_phase(captured)=='CLOSED' and
                prior.get('state')=='OK' and isinstance(prior.get('timers'),list) and
                len(prior['timers'])>=20):
            sys.exit(0)
except (OSError, ValueError, TypeError, ImportError, AttributeError):
    pass
def run(*args):
    p=subprocess.run(args,text=True,capture_output=True,timeout=12,check=False,
                     env={**os.environ, "LC_ALL":"C", "TZ":"UTC"})
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
        t=props(unit,["Id","Description","ActiveState","SubState","UnitFileState","LastTriggerUSec","NextElapseUSecRealtime","NextElapseUSecMonotonic","WakeSystem","Unit","Triggers"])
        observed=datetime.now(timezone.utc)
        tick_clock = time.CLOCK_BOOTTIME if t.get("WakeSystem")=="yes" else time.CLOCK_MONOTONIC
        elapsed_deadline = monotonic_target(t.get("NextElapseUSecMonotonic"),
                                           wall=observed,monotonic=time.clock_gettime(tick_clock))
        targets = [t.get("Unit", ""), *t.get("Triggers", "").split()]
        service = next((target for target in targets
                        if re.fullmatch(r"porota-[A-Za-z0-9_.@-]+\.service", target)),
                       unit[:-6] + ".service")
        s=props(service,["Id","Description","LoadState","ActiveState","SubState","Result","ExecMainCode","ExecMainStatus","ExecMainStartTimestamp","ExecMainExitTimestamp","ActiveEnterTimestamp","InactiveExitTimestamp"])
        trigger=t.get("LastTriggerUSec") or None
        execution=execution_state(s, trigger)
        completed=execution=="COMPLETED"
        timers.append({"unit":unit,"description":t.get("Description") or "",
            "active_state":t.get("ActiveState") or "unknown","sub_state":t.get("SubState") or "unknown",
            "enabled_state":t.get("UnitFileState") or (parts[1] if len(parts)>1 else "unknown"),
            "last_trigger":trigger,"next_elapse":t.get("NextElapseUSecRealtime") or None,
            "next_elapse_monotonic":t.get("NextElapseUSecMonotonic") or None,
            "next_elapse_monotonic_at":elapsed_deadline.isoformat() if elapsed_deadline else None,
            "triggers":t.get("Triggers") or service,"service":service,
            "service_description":s.get("Description") or "","service_active_state":s.get("ActiveState") or "unknown",
            "service_sub_state":s.get("SubState") or "unknown",
            "execution_state":execution,
            "last_result":(s.get("Result") or "unknown") if completed else "unknown",
            "exec_main_code":s.get("ExecMainCode") if completed else None,
            "exec_main_status":s.get("ExecMainStatus") if completed else None,
            "service_started_at":s.get("ExecMainStartTimestamp") or None,
            "service_finished_at":s.get("ExecMainExitTimestamp") or None,
            "service_active_enter":s.get("ActiveEnterTimestamp") or None,"service_inactive_exit":s.get("InactiveExitTimestamp") or None})
payload={"schema":"porota-scheduler-systemd-v1","recorded_at":datetime.now(timezone.utc).isoformat(),
         "state":"OK" if rc==0 else "ERROR","error":"" if rc==0 else (stderr.strip()[:500] or "systemctl list-unit-files failed"),
         "timers":sorted(timers,key=lambda x:x["unit"])}
tmp=out.with_name("."+out.name+".tmp")
tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
tmp.chmod(0o640); tmp.replace(out)
PY
chmod 0640 "$OUT"
