"""Execute a bounded dashboard observation through the existing Actions transport."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from browser_audit import run
from scripts.porota_actions_host import connection, ssh_run

DEPLOYMENT = "bad732511a7cc60981c730699e8527be03f7aa5b"
IMAGE = "sha256:8e59367f73d632363c73fa3e7ce814aad1f6752223c779e54effe164abcc9d4c"
SOURCE = "c328a21bc585f6a3fa9f1d28add156616b17bfe3"
PORT = "18085"
BASE = "http://127.0.0.1:" + PORT
REMOTE = r"""
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

def observe(args, timeout=4):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        return ""
    return result.stdout.strip()

def record(path, maximum=1048576):
    found = {}
    try:
        status = path.stat()
        found["mtime"] = datetime.datetime.fromtimestamp(status.st_mtime, datetime.timezone.utc).isoformat()
        if not path.is_file() or status.st_size > maximum:
            return {"state": "UNREADABLE", **found}, {}
        data = json.loads(path.read_text())
        return {"state": "AVAILABLE", **found}, data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {"state": "ABSENT"}, {}
    except (ValueError,OSError):
        return {"state": "UNREADABLE", **found}, {}

def instant(value):
    try:
        text = str(value or "")
        date = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return date.astimezone(datetime.timezone.utc).isoformat() if date.tzinfo else None
    except ValueError:
        return None

def text(value, allowed):
    return value if isinstance(value, str) and value in allowed else None

def systemd(names):
    properties = "LoadState,ActiveState,SubState,UnitFileState,Result,ExecMainStatus,LastTriggerUSec,NextElapseUSecRealtime,NextElapseUSecMonotonic,TimersMonotonic,ExecMainExitTimestamp"
    rows = {}
    for unit in names:
        value = observe(["systemctl","show","--no-pager","--property="+properties,unit])
        parts = dict(line.split("=",1) for line in value.splitlines() if "=" in line)
        rows[unit] = {k:v for k,v in parts.items() if re.fullmatch(r"[-_a-zA-Z0-9 :./{}+=,;\[\]]{1,220}", v)}
    return rows

def disk_age(raw):
    try:
        parsed = datetime.datetime.fromisoformat(raw)
        return round((datetime.datetime.now(datetime.timezone.utc) - parsed).total_seconds(),1)
    except (TypeError, ValueError):
        return None

deployment, image, candidate, mode = sys.argv[1:]
uid = os.getuid()
assert uid == os.geteuid() > 0
root = Path("/opt/porota-trading/data")
marker_meta, marker = record(root / "deploy" / "CURRENT_STATE_V2.json")
assert marker.get("deployment_sha") == deployment and marker.get("image_id") == image
assert marker.get("candidate_sha") == candidate and marker.get("validation_status") == "VALIDATED_RUNTIME"
assert marker.get("mode") == "PRODUCTION_PAPER" and marker.get("real_order_capability") == "BLOCKED"
docker = ["/usr/bin/docker","--host",f"unix:///run/user/{uid}/porota-rc6-docker.sock"]
containers = {}
for role in ("dashboard","observer"):
    rows = observe([*docker,"ps","--no-trunc","--filter","label=io.porota.project=rc6",
                    "--filter","label=io.porota.role="+role,"--format","{{.ID}}"]).splitlines()
    assert len(rows) == 1 and re.fullmatch("[0-9a-f]{64}",rows[0])
    cid = rows[0]
    identity = observe([*docker,"container","inspect","--format",
                        "{{.Image}}|{{.Path}}|{{join .Args \" \"}}",cid]).split("|")
    assert len(identity) == 3 and identity[0] == image
    expected = "o_dashboard.py" if role == "dashboard" else "bv_paper_runtime.py"
    assert identity[2] == expected
    containers[role] = {"cid":cid,"entrypoint":expected}
receipt = {"captured_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"source_sha":candidate,
           "deployment_sha":deployment,"image_id":image,"containers":containers}
if mode == "before":
    # One ephemeral, read-only process in the already-running dashboard.
    # Output is captured only inside the Actions Python process.
    token = observe([*docker,"container","exec",containers["dashboard"]["cid"],
                     "printenv","DASHBOARD_ACCESS_TOKEN"],timeout=5)
    assert len(token) >= 24 and not any(c.isspace() for c in token)
    receipt["token"] = token
    projection_meta, projection = record(root / "validation" / "validation_milestones_rc6.json")
    stats = projection.get("milestones")
    milestones = {}
    if isinstance(stats,dict):
        for key in ("M0","M1","M2","M3","M4","M11"):
            item = stats.get(key)
            if isinstance(item,dict):
                pct = item.get("compliance_pct")
                milestones[key] = {
                    "pct": pct if type(pct) is int and 0 <= pct <= 100 else None,
                    "state":text(item.get("state"), {"GREEN","YELLOW","RED","GRAY"}),
                    "claim":text(item.get("claim_status"), {"PENDING","VERIFIED_CURRENT","VERIFIED_HISTORICAL","STALE","BLOCKED_BY_POLICY"}),
                    "evidence_at":instant(item.get("evidence_at")),
                    "evaluated_at":instant(item.get("evaluated_at")),
                }
    ledger = root / "validation" / "validation_campaign_rc6.jsonl"
    try:
        ledger_meta = {"state":"AVAILABLE","size_bytes":ledger.stat().st_size,
                       "mtime":datetime.datetime.fromtimestamp(ledger.stat().st_mtime,datetime.timezone.utc).isoformat()}
    except FileNotFoundError:
        ledger_meta = {"state":"ABSENT"}
    except OSError:
        ledger_meta = {"state":"UNREADABLE"}
    receipt["milestones"] = {**projection_meta,
        "generated_at":instant(projection.get("generated_at")),
        "source":text(projection.get("source"), {"rc6-validation-projection-worker"}),
        "audit_kind":text(projection.get("audit_kind"), {"DAILY_READ_ONLY","DEEP_READ_ONLY","LIMITED_PROJECTION"}),
        "ledger_status":text(projection.get("ledger_status"), {"DAILY_COMPACT","FULLY_VERIFIED","VERIFY_ERROR"}),
        "ledger":ledger_meta,"rows":milestones}
    action_meta, action4 = record(root / "paper_v17" / "artifacts" / "observer_v17.db" / "reports" /
                                  "rc6_action4_auditoria_latest.json")
    action_report = action4.get("dashboard_daily_report") or {}
    period = action_report.get("periodo") if isinstance(action_report,dict) else None
    receipt["action4"] = {**action_meta, "generated_at":instant(action4.get("generated_at")),
                          "period": period if isinstance(period,str) and re.fullmatch("[0-9 /T:Z.+–-]{1,125}",period) else None}
    snap_meta, snap = record(root / "scheduler" / "systemd_timers.json")
    rows = snap.get("timers")
    receipt["scheduler_snapshot"] = {**snap_meta, "generated_at":instant(snap.get("recorded_at")),
        "age_seconds":disk_age(instant(snap.get("recorded_at"))),
        "rows":len(rows) if isinstance(rows,list) else None}
    names = ("porota-scheduler-export-rc6.timer","porota-scheduler-export-rc6.service",
             "porota-runtime-evidence-rc6.timer","porota-runtime-evidence-rc6.service",
             "porota-contract-evidence-hf6.timer","porota-contract-evidence-rc6.timer",
             "porota-candle-integrity-rc6.timer","porota-1016-github-verify.timer",
             "porota-a3-history-daily-rc6.timer")
    receipt["units"] = systemd(names)
    existing = observe(["systemctl","list-unit-files","--no-legend","--no-pager",
                        "--type=timer","porota-*"],timeout=5)
    receipt["validation_named_timers"] = [line.split()[0] for line in existing.splitlines() if
                                          re.fullmatch(r"porota-[a-z0-9-]*(?:validation|validacion|action4|maintenance)[a-z0-9-]*\.timer",line.split()[0])]
print(json.dumps(receipt))
"""


def host(ssh, phase):
    process = ssh_run(ssh, ["python3", "-B", "-", DEPLOYMENT, IMAGE, SOURCE, phase],
                      data=REMOTE.encode(), timeout=25)
    if process.returncode != 0:
        # Never print raw SSH stderr/stdout. In particular stdout may contain a bearer.
        raise SystemExit("RC6_ROOTLESS_DASHBOARD_PROBE_FAILED_" + phase.upper())
    try:
        data = json.loads(process.stdout)
    except ValueError:
        raise SystemExit("RC6_HOST_RECEIPT_INVALID") from None
    return data


def main():
    output = Path(os.environ["RUNNER_TEMP"]) / "porota-dashboard-audit"
    output.mkdir()
    with connection(os.environ) as (ssh, _, target):
        facts = host(ssh, "before")
        token = facts.pop("token")
        path = output / "host-facts.json"
        path.write_text(json.dumps(facts, indent=2) + "\n")
        forwarding = subprocess.Popen(
            [*ssh[:-1], "-o", "ExitOnForwardFailure=yes", "-N", "-L",
             f"127.0.0.1:{PORT}:127.0.0.1:8000", target],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(15):
                if forwarding.poll() is not None:
                    raise SystemExit("RC6_DASHBOARD_FORWARD_FAILED")
                try:
                    with urlopen(Request(BASE + "/health", headers={"Authorization": "Bearer " + token}),
                                 timeout=2) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    time.sleep(.3)
            else:
                raise SystemExit("RC6_DASHBOARD_HEALTH_UNAVAILABLE")
            run(BASE, token, output, live=True)
        finally:
            forwarding.terminate()
            try:
                forwarding.wait(timeout=5)
            except subprocess.TimeoutExpired:
                forwarding.kill()
                forwarding.wait()
        after = host(ssh, "after")
        facts["checked_again_at"] = after["captured_at"]
        facts["same_runtime"] = facts["containers"] == after["containers"]
        path.write_text(json.dumps(facts, indent=2) + "\n")
        if not facts["same_runtime"]:
            raise SystemExit("RC6_RUNTIME_CHANGED_BETWEEN_OBSERVATIONS")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Avoid exception messages and command lines which could contain inputs.
        raise SystemExit("RC6_DASHBOARD_AUDIT_FAILED_" + type(error).__name__) from None
