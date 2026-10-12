#!/usr/bin/env python3
"""Manual control-plane benchmark; no SSH, production files or runtime writes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from co_market_sessions_hf6 import byma_paper_spot_phase
from rc6_dynamic_universe.benchmark import BATCHES, ENDPOINTS, run_benchmark
from rc6_dynamic_universe.promotion import (POLICY_PATH, build_recommendation,
    read_json, resolve_capacity_policy)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, help="JSON array of explicit full PPI spot identities")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batches", default=",".join(map(str, BATCHES)))
    parser.add_argument("--endpoints", default=",".join(ENDPOINTS))
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--cadence-seconds", type=float, default=60)
    parser.add_argument("--max-requests", type=int, default=2701)
    parser.add_argument("--max-runtime-seconds", type=float, default=1800)
    parser.add_argument("--capacity-policy", type=Path, default=POLICY_PATH)
    parser.add_argument("--recommendation-output", type=Path,
        help="Separate sanitized recommendation artifact; never approval or runtime config")
    parser.add_argument("--validate-report", type=Path,
        help="Offline review/config gate validation; no reader, credentials or provider")
    parser.add_argument("--validate-recommendation", type=Path)
    parser.add_argument("--validate-approval", type=Path)
    args = parser.parse_args(argv)
    # SDK logger can include raw responses; this command exports sanitized
    # numeric evidence only. It never persists tokens, secrets or payloads.
    logging.disable(logging.CRITICAL)
    if args.validate_report:
        report = read_json(args.validate_report)
        recommendation = read_json(args.validate_recommendation) if args.validate_recommendation else None
        approval = read_json(args.validate_approval) if args.validate_approval else None
        review = resolve_capacity_policy(read_json(args.capacity_policy), recommendation, report, approval)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            os.chmod(args.output, 0o600)
            json.dump(review, stream, ensure_ascii=False, indent=2, allow_nan=False)
        print(json.dumps({"status": review["status"], "reason_codes": review["reason_codes"],
            "real_orders_sent": 0, "runtime_modified": False}, sort_keys=True))
        return 0
    records = []
    key = secret = ""
    phase = byma_paper_spot_phase(datetime.now(timezone.utc))
    if phase == "OPEN":
        key, secret = os.getenv("PPI_API_KEY", ""), os.getenv("PPI_API_SECRET", "")
        if key and secret and args.candidates:
            if args.candidates.stat().st_size > 100_000:
                raise ValueError("CANDIDATE_MANIFEST_TOO_LARGE")
            records = json.loads(args.candidates.read_text(encoding="utf-8"))
            if not isinstance(records, list) or len(records) > 100:
                raise ValueError("CANDIDATE_MANIFEST_INVALID")
    report = run_benchmark(records, api_key=key, api_secret=secret,
        batches=tuple(int(value) for value in args.batches.split(",")),
        endpoints=tuple(value.strip() for value in args.endpoints.split(",")),
        cycles=args.cycles, cadence_seconds=args.cadence_seconds,
        max_requests=args.max_requests, max_runtime_seconds=args.max_runtime_seconds)
    recommendation = build_recommendation(report, policy=read_json(args.capacity_policy))
    if args.recommendation_output:
        if args.recommendation_output.resolve() in {args.output.resolve(), args.capacity_policy.resolve(),
                args.candidates.resolve() if args.candidates else None}:
            raise ValueError("RECOMMENDATION_OUTPUT_MUST_BE_SEPARATE")
        args.recommendation_output.parent.mkdir(parents=True, exist_ok=True)
        with args.recommendation_output.open("x", encoding="utf-8") as stream:
            os.chmod(args.recommendation_output, 0o600)
            json.dump(recommendation, stream, ensure_ascii=False, indent=2, allow_nan=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        os.chmod(args.output, 0o600)
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "market_phase": report["market_phase"],
                      "stop_reason": report["stop_reason"], "real_orders_sent": 0,
                      "recommendation_status": recommendation["status"], "automatic_activation": False,
                      "evidence_digest": report["evidence_digest"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, TypeError, KeyError):
        # No arbitrary exception/response string leaks into Actions logs.
        print("BENCHMARK_INPUT_OR_CONFIGURATION_INVALID", file=sys.stderr)
        raise SystemExit(2)
