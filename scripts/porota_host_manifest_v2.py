#!/usr/bin/env python3
"""Build the RC6 host manifest from Git-tracked unit bytes + lifecycle policy."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def sha256(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def tracked_modes(repo_root: Path) -> dict[str,str]:
    raw=subprocess.check_output(["git","-C",str(repo_root),"ls-files","-s","-z"])
    result={}
    for record in raw.split(b"\0"):
        if not record:
            continue
        left,path=record.decode("utf-8").split("\t",1)
        result[path]=left.split()[0]
    return result


def _is_rc6_unit(path: str) -> bool:
    return (
        (path.startswith("systemd/") or path.startswith("ops/systemd/"))
        and path.endswith((".service",".timer"))
        and "rc6" in Path(path).name.lower()
    )


def build_manifest(repo_root: Path, policy: dict, source_sha: str, *, modes: dict[str,str] | None=None) -> dict:
    modes=tracked_modes(repo_root) if modes is None else dict(modes)
    tracked=sorted(p for p in modes if _is_rc6_unit(p))
    policy_units=policy.get("units",{})
    missing=sorted(set(tracked)-set(policy_units))
    extra=sorted(set(policy_units)-set(tracked))
    entries=[]
    for path in tracked:
        row=policy_units.get(path) or {}
        name=Path(path).name
        install=bool(row.get("install"))
        src=repo_root/path
        entries.append({
            "category":"HOST_CONTROL_PLANE",
            "source_path":path,
            "source_sha256":sha256(src),
            "source_git_mode":modes[path],
            "source_git_sha":source_sha,
            "unit_name":name,
            "unit_kind":"timer" if name.endswith(".timer") else "service",
            "role":row.get("role"),
            "install":install,
            "install_target":f"/etc/systemd/system/{name}" if install else None,
            "expected_enabled":row.get("enabled"),
            "expected_active":row.get("active"),
            "expected_masked":bool(row.get("masked")),
            "remove_on_deploy":bool(row.get("remove_on_deploy")),
            "reason":row.get("reason"),
        })
    status="GREEN" if not missing and not extra else "FAILED_POLICY_PROVENANCE"
    return {
        "schema_version":2,
        "status":status,
        "source_git_sha":source_sha,
        "lifecycle_policy":"ops/policy/host-control-plane-reconciliation-v2.json",
        "counts":{
            "tracked_rc6_units":len(tracked),
            "policy_rc6_units":len(policy_units),
            "missing_policy_units":len(missing),
            "extra_policy_units":len(extra),
        },
        "missing_policy_units":missing,
        "extra_policy_units":extra,
        "units":entries,
    }


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo-root",default=".")
    ap.add_argument("--policy",default="ops/policy/host-control-plane-reconciliation-v2.json")
    ap.add_argument("--source-sha",default="")
    ap.add_argument("--json-out")
    args=ap.parse_args()
    root=Path(args.repo_root).resolve()
    source_sha=args.source_sha or subprocess.check_output(
        ["git","-C",str(root),"rev-parse","HEAD"],text=True).strip()
    policy=json.loads((root/args.policy).read_text(encoding="utf-8"))
    result=build_manifest(root,policy,source_sha)
    payload=json.dumps(result,indent=2,sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload+"\n",encoding="utf-8")
    print("POROTA_HOST_MANIFEST_V2="+result["status"])
    return 0 if result["status"]=="GREEN" else 1


if __name__=="__main__":
    raise SystemExit(main())
