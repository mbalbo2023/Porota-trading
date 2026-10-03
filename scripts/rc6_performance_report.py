#!/usr/bin/env python3
"""Export bounded evidence report without opening the trading database."""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rc6_performance.common import canonical
from rc6_performance.report import evidence_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.evidence.resolve() == args.out.resolve():
        raise ValueError("REPORT_CANNOT_OVERWRITE_EVIDENCE")
    report = evidence_report(args.evidence)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.out.with_name(args.out.name+".tmp")
    temporary.write_text(canonical(report)+"\n")
    os.replace(temporary, args.out)
    print(canonical({"mode": report["mode"], "selection": report["selection"],
                     "economic_edge_validated": False}))


if __name__ == "__main__":
    main()
