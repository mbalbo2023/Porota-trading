"""Fail-closed capacity screen for a measured RC6 candidate and host snapshot.

This does not certify candidate behavior or authorize a deploy. The caller must
bind the candidate measurement to the exact source and qualify its CPU envelope.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


def positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def screen(snapshot: object, *, peak_rss_bytes: int, qualified_cpu_count: int,
           reserve_bytes: int) -> dict:
    reasons: list[str] = []
    if not isinstance(snapshot, dict) or snapshot.get("schema") != "rc6.host-capacity-readonly.v1":
        reasons.append("HOST_SNAPSHOT_MISSING_OR_INVALID")
        snapshot = {}
    memory = snapshot.get("memory")
    if not isinstance(memory, dict):
        memory = {}
    total = memory.get("MemTotal_bytes")
    available = memory.get("MemAvailable_bytes")
    cpu = snapshot.get("cpu_affinity_count")
    if not (positive_int(total) and positive_int(available) and available <= total):
        reasons.append("HOST_MEMORY_MEASUREMENT_INCOMPLETE")
    if not positive_int(cpu):
        reasons.append("HOST_CPU_MEASUREMENT_INCOMPLETE")
    if not positive_int(peak_rss_bytes) or not positive_int(qualified_cpu_count):
        reasons.append("CANDIDATE_MEASUREMENT_INCOMPLETE")
    if type(reserve_bytes) is not int or reserve_bytes < 0:
        reasons.append("MEMORY_RESERVE_INVALID")

    if not reasons:
        if peak_rss_bytes + reserve_bytes > total:
            reasons.append("MEASURED_PEAK_AND_RESERVE_EXCEED_HOST_PHYSICAL_RAM")
        if peak_rss_bytes + reserve_bytes > available:
            reasons.append("MEASURED_PEAK_AND_RESERVE_EXCEED_CURRENT_AVAILABLE_RAM")
        if qualified_cpu_count > cpu:
            reasons.append("CANDIDATE_NOT_QUALIFIED_AT_HOST_CPU_COUNT")
    return {
        "schema": "rc6.host-capacity-screen.v1",
        "status": "BLOCKED" if reasons else "SNAPSHOT_SCREEN_PASSED_NOT_DEPLOY_APPROVAL",
        "reasons": reasons,
        "observed_utc": snapshot.get("observed_utc"),
        "host_total_bytes": total,
        "host_available_bytes": available,
        "host_cpu_affinity_count": cpu,
        "candidate_peak_rss_bytes": peak_rss_bytes,
        "candidate_qualified_cpu_count": qualified_cpu_count,
        "reserve_bytes": reserve_bytes,
        "candidate_exercised_on_host": False,
        "deploy_approved": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-snapshot", type=Path, required=True)
    parser.add_argument("--candidate-peak-rss-bytes", type=int, required=True)
    parser.add_argument("--candidate-source-sha", required=True)
    parser.add_argument("--peak-evidence-url", required=True)
    parser.add_argument("--qualified-cpu-count", type=int, required=True)
    parser.add_argument("--reserve-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        snapshot = json.loads(args.host_snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        snapshot = None
    report = screen(snapshot, peak_rss_bytes=args.candidate_peak_rss_bytes,
                    qualified_cpu_count=args.qualified_cpu_count,
                    reserve_bytes=args.reserve_bytes)
    report["candidate_source_sha"] = args.candidate_source_sha
    report["peak_evidence_url"] = args.peak_evidence_url
    if (not re.fullmatch(r"[0-9a-f]{40}", args.candidate_source_sha)
            or not args.peak_evidence_url.startswith(
                "https://github.com/mbalbo2023/Porota-trading/issues/471#issuecomment-")):
        report["status"] = "BLOCKED"
        report["reasons"].append("CANDIDATE_PEAK_PROVENANCE_NOT_PINNED")
    args.output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print("RC6_HOST_CAPACITY=" + report["status"])
    for reason in report["reasons"]:
        print("RC6_HOST_CAPACITY_REASON=" + reason)
    print("DEPLOY_APPROVAL=NO")
    return 1 if report["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
