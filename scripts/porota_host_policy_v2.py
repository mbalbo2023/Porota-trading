#!/usr/bin/env python3
"""Validate explicit reconciliation of RC6 host-control-plane units."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


ALLOWED_ROLES = {
    "ACTIVE_SERVICE",
    "ACTIVE_TIMER",
    "QUARANTINED_SERVICE",
    "QUARANTINED_TIMER",
    "RETIRED_SERVICE",
    "RETIRED_TIMER",
}


def validate_policy(manifest: dict, policy: dict) -> dict:
    unmanaged = {
        row["source_path"]
        for row in manifest.get("units", [])
        if not row.get("managed_by_canonical_deploy")
    }
    entries = policy.get("units", {})
    policy_paths = set(entries)
    missing = sorted(unmanaged - policy_paths)
    extra = sorted(policy_paths - unmanaged)
    invalid = []
    for path, row in sorted(entries.items()):
        role = row.get("role")
        if role not in ALLOWED_ROLES:
            invalid.append({"path": path, "reason": "INVALID_ROLE"})
            continue
        if role == "ACTIVE_TIMER":
            if not (row.get("install") is True and row.get("enabled") is True and row.get("active") is True):
                invalid.append({"path": path, "reason": "ACTIVE_TIMER_CONTRACT"})
        elif role == "QUARANTINED_TIMER":
            if not (
                row.get("install") is False
                and row.get("enabled") is False
                and row.get("active") is False
                and row.get("masked") is True
            ):
                invalid.append({"path": path, "reason": "QUARANTINED_TIMER_CONTRACT"})
        elif role == "RETIRED_TIMER":
            if not (row.get("install") is False and row.get("enabled") is False and row.get("active") is False and row.get("remove_on_deploy") is True):
                invalid.append({"path": path, "reason": "RETIRED_TIMER_CONTRACT"})
        elif role == "RETIRED_SERVICE":
            if not (row.get("install") is False and row.get("remove_on_deploy") is True):
                invalid.append({"path": path, "reason": "RETIRED_SERVICE_CONTRACT"})
        elif role in {"ACTIVE_SERVICE", "QUARANTINED_SERVICE"}:
            if row.get("install") is not True:
                invalid.append({"path": path, "reason": "SERVICE_INSTALL_CONTRACT"})
        if not str(row.get("reason") or "").strip():
            invalid.append({"path": path, "reason": "MISSING_REASON"})
    status = "GREEN" if not (missing or extra or invalid) else "FAILED"
    return {
        "schema_version": 1,
        "status": status,
        "tracked_unmanaged_units": len(unmanaged),
        "policy_units": len(entries),
        "missing_policy_units": missing,
        "extra_policy_units": extra,
        "invalid_policy_units": invalid,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--policy", default="ops/policy/host-control-plane-reconciliation-v2.json")
    ap.add_argument("--json-out")
    args = ap.parse_args()
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    policy = json.loads(Path(args.policy).read_text(encoding="utf-8"))
    result = validate_policy(manifest, policy)
    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
    print("POROTA_HOST_POLICY_V2=" + result["status"])
    return 0 if result["status"] == "GREEN" else 1


if __name__ == "__main__":
    raise SystemExit(main())
