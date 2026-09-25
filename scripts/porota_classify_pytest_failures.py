#!/usr/bin/env python3
"""Classify pytest failures using Porota's explicit versioned triage registry."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

FAILED_RE = re.compile(r"^FAILED\s+(tests/[^\s]+::.+)$")
ERROR_RE = re.compile(r"^ERROR\s+(tests/[^\s]+(?:::.+)?)$")

def classify(log_text: str, registry: dict, pytest_exit_code: int) -> dict:
    known = {item["nodeid"]: item for item in registry.get("failures", [])}
    failed = []
    errors = []
    for raw in log_text.splitlines():
        line = raw.strip()
        m = FAILED_RE.match(line)
        if m:
            failed.append(m.group(1))
            continue
        m = ERROR_RE.match(line)
        if m:
            errors.append(m.group(1))

    # Preserve order while removing duplicates.
    failed = list(dict.fromkeys(failed))
    errors = list(dict.fromkeys(errors))
    counts = {
        "CURRENT_CONTRACT_RECONCILIATION_REQUIRED": 0,
        "ENVIRONMENT_HARNESS_FIXTURE": 0,
        "LEGACY_REGRESSION_STALE_EXPECTATION": 0,
        "UNCLASSIFIED": 0,
    }
    details = []
    for nodeid in failed:
        item = known.get(nodeid)
        classification = item.get("classification") if item else "UNCLASSIFIED"
        if classification not in counts:
            classification = "UNCLASSIFIED"
        counts[classification] += 1
        details.append({"nodeid": nodeid, "classification": classification})

    registry_nodes = set(known)
    observed_nodes = set(failed)
    return {
        "schema_version": 1,
        "registry_status": registry.get("status", "UNKNOWN"),
        "pytest_exit_code": pytest_exit_code,
        "failed_cases": len(failed),
        "error_nodes": errors,
        "counts": counts,
        "failures": details,
        "registry_failures_not_observed": sorted(registry_nodes - observed_nodes),
        "unclassified_failures": [x["nodeid"] for x in details if x["classification"] == "UNCLASSIFIED"],
    }

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--pytest-exit-code", type=int, required=True)
    ap.add_argument("--json-out")
    args = ap.parse_args()

    result = classify(
        Path(args.log).read_text(encoding="utf-8", errors="replace"),
        json.loads(Path(args.registry).read_text(encoding="utf-8")),
        args.pytest_exit_code,
    )
    payload = json.dumps(result, indent=2, sort_keys=True)
    print(payload)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")

    c = result["counts"]
    print(
        "POROTA_TEST_TRIAGE="
        f"CURRENT_CONTRACT:{c['CURRENT_CONTRACT_RECONCILIATION_REQUIRED']}|"
        f"HARNESS:{c['ENVIRONMENT_HARNESS_FIXTURE']}|"
        f"LEGACY:{c['LEGACY_REGRESSION_STALE_EXPECTATION']}|"
        f"UNCLASSIFIED:{c['UNCLASSIFIED']}|"
        f"ERRORS:{len(result['error_nodes'])}"
    )
    # Reporting only while registry is provisional. Gate severity remains pytest's.
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
