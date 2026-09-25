#!/usr/bin/env python3
"""Inventory Porota RC6 host control-plane provenance.

This is PREDEPLOY/read-only tooling. It compares Git-tracked RC6 systemd units
with every systemd unit referenced by the canonical production workflow.
A workflow reference to a non-tracked unit is a provenance failure: the deploy
must not depend on residual host files.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

UNIT_RE = re.compile(
    r"(?:ops/)?systemd/porota-[A-Za-z0-9_.@-]+\.(?:service|timer)"
)


def is_rc6_unit(path: str) -> bool:
    return (
        (path.startswith("systemd/") or path.startswith("ops/systemd/"))
        and path.endswith((".service", ".timer"))
        and "rc6" in Path(path).name.lower()
    )


def build_inventory(tracked_paths: list[str], workflow_text: str) -> dict:
    tracked = set(tracked_paths)
    tracked_units = sorted(p for p in tracked if is_rc6_unit(p))
    referenced_units = sorted(set(UNIT_RE.findall(workflow_text)))
    referenced_tracked = sorted(p for p in referenced_units if p in tracked)
    untracked_workflow_inputs = sorted(p for p in referenced_units if p not in tracked)
    tracked_not_referenced = sorted(p for p in tracked_units if p not in referenced_units)

    return {
        "schema_version": 1,
        "status": "GREEN" if not untracked_workflow_inputs else "FAILED_UNTRACKED_WORKFLOW_INPUT",
        "tracked_rc6_units": tracked_units,
        "workflow_referenced_units": referenced_units,
        "workflow_referenced_tracked_units": referenced_tracked,
        "untracked_workflow_inputs": untracked_workflow_inputs,
        "tracked_rc6_units_not_referenced_by_canonical_deploy": tracked_not_referenced,
        "counts": {
            "tracked_rc6_units": len(tracked_units),
            "workflow_referenced_units": len(referenced_units),
            "workflow_referenced_tracked_units": len(referenced_tracked),
            "untracked_workflow_inputs": len(untracked_workflow_inputs),
            "tracked_rc6_units_not_referenced_by_canonical_deploy": len(tracked_not_referenced),
        },
    }


def git_tracked(repo_root: Path) -> list[str]:
    out = subprocess.check_output(
        ["git", "-C", str(repo_root), "ls-files", "-z"], text=False
    )
    return sorted(x.decode("utf-8") for x in out.split(b"\0") if x)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument(
        "--workflow",
        default=".github/workflows/rc6-pr69-isolated-transactional-deploy-20260915.yml",
    )
    ap.add_argument("--json-out")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve()
    workflow_path = root / args.workflow
    result = build_inventory(
        git_tracked(root),
        workflow_path.read_text(encoding="utf-8"),
    )
    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")

    if result["status"] == "GREEN":
        print("POROTA_HOST_CONTROL_PLANE_PROVENANCE=GREEN")
        return 0

    print(
        "POROTA_HOST_CONTROL_PLANE_PROVENANCE=FAILED_UNTRACKED_WORKFLOW_INPUT",
        flush=True,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
