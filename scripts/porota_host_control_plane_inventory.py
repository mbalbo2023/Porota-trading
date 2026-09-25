#!/usr/bin/env python3
"""Verify RC6 host-control-plane provenance from Git + complete lifecycle policy."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def is_rc6_unit(path: str) -> bool:
    return (
        (path.startswith("systemd/") or path.startswith("ops/systemd/"))
        and path.endswith((".service", ".timer"))
        and "rc6" in Path(path).name.lower()
    )


def build_inventory(tracked_paths: list[str], policy_paths: list[str] | set[str]) -> dict:
    tracked_units=sorted(p for p in set(tracked_paths) if is_rc6_unit(p))
    policy_units=sorted(set(policy_paths))
    missing=sorted(set(tracked_units)-set(policy_units))
    extra=sorted(set(policy_units)-set(tracked_units))
    return {
        "schema_version":2,
        "status":"GREEN" if not missing and not extra else "FAILED_POLICY_PROVENANCE",
        "tracked_rc6_units":tracked_units,
        "policy_rc6_units":policy_units,
        "missing_policy_units":missing,
        "extra_policy_units":extra,
        "counts":{
            "tracked_rc6_units":len(tracked_units),
            "policy_rc6_units":len(policy_units),
            "missing_policy_units":len(missing),
            "extra_policy_units":len(extra),
        },
    }


def git_tracked(repo_root: Path) -> list[str]:
    out=subprocess.check_output(["git","-C",str(repo_root),"ls-files","-z"],text=False)
    return sorted(x.decode("utf-8") for x in out.split(b"\0") if x)


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--repo-root",default=".")
    ap.add_argument("--policy",default="ops/policy/host-control-plane-reconciliation-v2.json")
    ap.add_argument("--json-out")
    args=ap.parse_args()
    root=Path(args.repo_root).resolve()
    policy=json.loads((root/args.policy).read_text(encoding="utf-8"))
    result=build_inventory(git_tracked(root),list(policy.get("units",{})))
    payload=json.dumps(result,indent=2,sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload+"\n",encoding="utf-8")
    print("POROTA_HOST_CONTROL_PLANE_PROVENANCE="+result["status"])
    return 0 if result["status"]=="GREEN" else 1


if __name__=="__main__":
    raise SystemExit(main())
