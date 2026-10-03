#!/usr/bin/env python3
"""Offline financial audit. Input is a local read-only master artifact, never a broker."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_performance.common import canonical
from rc6_performance.metrics import audit_master, historical_sessions
from rc6_performance.counterfactuals import extracted_replays


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--replays", type=Path, help="Optional attached read-only extracted replay folder")
    args = parser.parse_args()
    if args.master.resolve() == args.out.resolve():
        parser.error("Output must not overwrite the source")
    master = json.loads(args.master.read_text())
    report = audit_master(master, sessions=historical_sessions())
    if args.replays:
        report["counterfactuals"] = extracted_replays(master, args.replays, sessions=historical_sessions())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(canonical(report)+"\n")
    print(canonical({k: report[k] for k in ("n_sessions", "closed_count", "fills_count", "by_currency")}))


if __name__ == "__main__":
    main()
