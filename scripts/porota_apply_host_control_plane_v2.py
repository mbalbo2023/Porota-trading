#!/usr/bin/env python3
"""Apply the complete RC6 systemd lifecycle policy on the deployment host.

Canonical Git-tracked RC6 units are managed from the versioned units map.
Historical host residue may be retired only through the separate exact-name
legacy_retire_units allowlist. No discovery-based deletion is permitted.

The tool never touches PPI Watch, unknown host units, data, databases, Docker
volumes or secrets. Independently managed external control-plane units are
explicitly preserved by policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

UNIT_RE = re.compile(r"^porota-[A-Za-z0-9_.@-]*rc6[A-Za-z0-9_.@-]*\.(?:service|timer)$")
LEGACY_RETIRE_UNIT_RE = re.compile(r"^porota-[A-Za-z0-9_.@-]+\.(?:service|timer)$")


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
        source=Path(source_path)
        name=source.name
        if (
            source.is_absolute()
            or ".." in source.parts
            or not (source_path.startswith("systemd/") or source_path.startswith("ops/systemd/"))
            or not UNIT_RE.fullmatch(name)
        ):
            raise ValueError(f"UNSAFE_UNIT_SOURCE:{source_path}")
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


def build_legacy_retire_plan(policy: dict) -> list[str]:
    """Return the exact legacy host-unit retirement allowlist.

    This deliberately does not discover units.  Only names explicitly versioned
    in policy may be touched, PPI Watch is always forbidden, and independently
    managed external control-plane units cannot be included.
    """
    external=set((policy.get("external_host_units") or {}).keys())
    residual_review=set((policy.get("residual_host_units") or {}).keys())
    result=[]
    seen=set()
    for raw in policy.get("legacy_retire_units") or []:
        name=str(raw or "").strip()
        if not LEGACY_RETIRE_UNIT_RE.fullmatch(name):
            raise ValueError(f"UNSAFE_LEGACY_RETIRE_UNIT:{name}")
        if "ppi" in name.lower() and "watch" in name.lower():
            raise ValueError("PPI_WATCH_FORBIDDEN")
        if name in external:
            raise ValueError(f"EXTERNAL_HOST_UNIT_FORBIDDEN:{name}")
        if name in residual_review:
            raise ValueError(f"RESIDUAL_REVIEW_UNIT_FORBIDDEN:{name}")
        if name in seen:
            raise ValueError(f"DUPLICATE_LEGACY_RETIRE_UNIT:{name}")
        seen.add(name)
        result.append(name)
    return result


def apply(root: Path, policy: dict, systemd_root: Path, *, keep_active_timers_paused: bool = False) -> dict:
    plan=build_plan(policy)
    legacy_retire=build_legacy_retire_plan(policy)
    operator_preopen_paused=False
    if any(item['role']=='ACTIVE_TIMER' and
           item['unit_name']=='porota-preopen-rc6.timer' for item in plan):
        current=_run('systemctl','is-enabled','porota-preopen-rc6.timer',check=False).stdout.strip()
        if current=='disabled':
            # A125 operator stop has independent authority until the end of
            # 2026-10-27 ART. Reinstalling unit files must not enable it.
            # The exact proof is local, read-only and shared with the
            # scheduler. Every unrelated disabled-timer condition stays RED.
            if root.is_symlink() or not (root/'scripts/rc6_deploy_scheduler_suspend.py').is_file():
                raise RuntimeError('HOST_OPERATOR_PAUSE_HELPER_MISSING')
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            from scripts.rc6_deploy_scheduler_suspend import (
                exact_operator_preopen_pause, operator_preopen_timer_disabled)
            exact_operator_preopen_pause()
            if not operator_preopen_timer_disabled():
                raise RuntimeError('HOST_OPERATOR_PREOPEN_STATE_CHANGED')
            if not keep_active_timers_paused:
                raise RuntimeError('HOST_OPERATOR_PREOPEN_REARM_FORBIDDEN')
            operator_preopen_paused=True
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

    for name in legacy_retire:
        target=systemd_root / name
        _run("systemctl","disable","--now",name,check=False)
        if target.exists() or target.is_symlink():
            target.unlink()
        _run("systemctl","reset-failed",name,check=False)
        changed.append({"unit":name,"action":"RETIRE_EXACT_LEGACY"})

    _run("systemctl","daemon-reload")

    for item in plan:
        name=item["unit_name"]
        role=item["role"]
        if role=="ACTIVE_TIMER":
            _run("systemctl","unmask",name,check=False)
            if keep_active_timers_paused:
                # The deploy owns an exact scheduler suspension receipt. Unit
                # reconciliation must not fire these timers during COLD2 or
                # contract evidence materialization.
                if not (operator_preopen_paused and name=='porota-preopen-rc6.timer'):
                    _run("systemctl","enable",name)
            else:
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
            enabled=_run("systemctl","is-enabled",name,
                         check=not (operator_preopen_paused and
                                    name=='porota-preopen-rc6.timer')).stdout.strip()
            active=_run("systemctl","is-active",name,check=False).stdout.strip()
            expected_active = "inactive" if keep_active_timers_paused else "active"
            expected_enabled = ("disabled" if operator_preopen_paused and
                                name=='porota-preopen-rc6.timer' else "enabled")
            if enabled!=expected_enabled or active!=expected_active:
                raise RuntimeError(f"ACTIVE_TIMER_VERIFY_FAILED:{name}:{enabled}:{active}")
            verification.append({"unit":name,"state":f"{expected_enabled}|{expected_active}"})
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

    for name in legacy_retire:
        active=_run("systemctl","is-active",name,check=False).stdout.strip()
        target=systemd_root / name
        if active=="active":
            raise RuntimeError(f"LEGACY_RETIRED_UNIT_ACTIVE:{name}")
        if target.exists() or target.is_symlink():
            raise RuntimeError(f"LEGACY_RETIRED_UNIT_FILE_PRESENT:{name}")
        verification.append({"unit":name,"state":"legacy-retired|inactive|absent"})

    return {
        "status":"GREEN",
        "changed":changed,
        "verification":verification,
        "count":len(plan),
        "legacy_retired_count":len(legacy_retire),
    }


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",default=".")
    ap.add_argument("--policy",default="ops/policy/host-control-plane-reconciliation-v2.json")
    ap.add_argument("--systemd-root",default="/etc/systemd/system")
    ap.add_argument("--json-out")
    ap.add_argument("--keep-active-timers-paused", action="store_true")
    args=ap.parse_args()
    root=Path(args.root).resolve()
    policy=json.loads((root / args.policy).read_text(encoding="utf-8"))
    result=apply(root,policy,Path(args.systemd_root),
                 keep_active_timers_paused=args.keep_active_timers_paused)
    payload=json.dumps(result,indent=2,sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload+"\n",encoding="utf-8")
    print(f"POROTA_HOST_CONTROL_PLANE_APPLY=GREEN|units={result['count']}")
    print(f"POROTA_HOST_LEGACY_RETIREMENT=GREEN|units={result['legacy_retired_count']}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
