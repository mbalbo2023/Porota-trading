#!/usr/bin/env python3
"""Apply the complete RC6 systemd lifecycle policy on the deployment host.

This tool is intentionally scoped to Git-tracked porota-*rc6 units declared in
ops/policy/host-control-plane-reconciliation-v2.json.  It never discovers or
touches PPI Watch, unrelated systemd units, data, databases, Docker volumes or
secrets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

UNIT_RE = re.compile(r"^porota-[A-Za-z0-9_.@-]*rc6[A-Za-z0-9_.@-]*\.(?:service|timer)$")


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT)


def _sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_plan(policy: dict) -> list[dict]:
    result=[]
    for source_path,row in sorted(policy.get("units", {}).items()):
        name=Path(source_path).name
        if not UNIT_RE.fullmatch(name):
            raise ValueError(f"UNSAFE_UNIT_NAME:{name}")
        if "ppi" in name.lower() and "watch" in name.lower():
            raise ValueError("PPI_WATCH_FORBIDDEN")
        role=str(row.get("role") or "")
        result.append({
            "source_path":source_path,
            "unit_name":name,
            "role":role,
            "install":bool(row.get("install")),
            "enabled":row.get("enabled"),
            "active":row.get("active"),
            "masked":bool(row.get("masked")),
            "remove_on_deploy":bool(row.get("remove_on_deploy")),
        })
    return result


def apply(root: Path, policy: dict, systemd_root: Path) -> dict:
    plan=build_plan(policy)
    changed=[]
    for item in plan:
        src=(root / item["source_path"]).resolve()
        target=systemd_root / item["unit_name"]
        if item["install"]:
            if not src.is_file():
                raise RuntimeError(f"HOST_UNIT_SOURCE_MISSING:{item['source_path']}")
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.is_symlink():
                target.unlink()
            shutil.copy2(src,target)
            os.chmod(target,0o644)
            changed.append({"unit":item["unit_name"],"action":"INSTALL","sha256":_sha256(target)})
        elif item["role"].startswith("RETIRED_"):
            _run("systemctl","disable","--now",item["unit_name"],check=False)
            if target.exists() or target.is_symlink():
                target.unlink()
            changed.append({"unit":item["unit_name"],"action":"RETIRE"})
        elif item["role"]=="QUARANTINED_TIMER":
            _run("systemctl","disable","--now",item["unit_name"],check=False)
            if target.exists() or target.is_symlink():
                target.unlink()
            changed.append({"unit":item["unit_name"],"action":"QUARANTINE"})

    _run("systemctl","daemon-reload")

    for item in plan:
        name=item["unit_name"]
        role=item["role"]
        if role=="ACTIVE_TIMER":
            _run("systemctl","unmask",name,check=False)
            _run("systemctl","enable","--now",name)
        elif role=="QUARANTINED_TIMER":
            _run("systemctl","mask",name)
            _run("systemctl","stop",name,check=False)
        elif role=="RETIRED_TIMER":
            _run("systemctl","disable","--now",name,check=False)

    verification=[]
    for item in plan:
        name=item["unit_name"]
        role=item["role"]
        if role=="ACTIVE_TIMER":
            enabled=_run("systemctl","is-enabled",name).stdout.strip()
            active=_run("systemctl","is-active",name).stdout.strip()
            if enabled!="enabled" or active!="active":
                raise RuntimeError(f"ACTIVE_TIMER_VERIFY_FAILED:{name}:{enabled}:{active}")
            verification.append({"unit":name,"state":"enabled|active"})
        elif role=="QUARANTINED_TIMER":
            enabled=_run("systemctl","is-enabled",name,check=False).stdout.strip()
            active=_run("systemctl","is-active",name,check=False).stdout.strip()
            if enabled!="masked" or active=="active":
                raise RuntimeError(f"QUARANTINE_VERIFY_FAILED:{name}:{enabled}:{active}")
            verification.append({"unit":name,"state":f"{enabled}|{active}"})
        elif role.startswith("RETIRED_"):
            active=_run("systemctl","is-active",name,check=False).stdout.strip()
            if active=="active":
                raise RuntimeError(f"RETIRED_UNIT_ACTIVE:{name}")
            verification.append({"unit":name,"state":"retired|inactive"})
        elif item["install"]:
            target=systemd_root / name
            if not target.is_file():
                raise RuntimeError(f"SERVICE_INSTALL_VERIFY_FAILED:{name}")
            verification.append({"unit":name,"state":"installed"})
    return {"status":"GREEN","changed":changed,"verification":verification,"count":len(plan)}


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=".")
    ap.add_argument("--policy",default="ops/policy/host-control-plane-reconciliation-v2.json")
    ap.add_argument("--systemd-root",default="/etc/systemd/system")
    ap.add_argument("--json-out")
    args=ap.parse_args()
    root=Path(args.root).resolve()
    policy=json.loads((root / args.policy).read_text(encoding="utf-8"))
    result=apply(root,policy,Path(args.systemd_root))
    payload=json.dumps(result,indent=2,sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload+"\n",encoding="utf-8")
    print(f"POROTA_HOST_CONTROL_PLANE_APPLY=GREEN|units={result['count']}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
