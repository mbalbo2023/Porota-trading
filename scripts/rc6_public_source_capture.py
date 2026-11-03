#!/usr/bin/env python3
"""One-shot read-only capture of public BYMA, CNV and A3 pages.

This is intentionally not scheduled every minute. It records only what the
public response actually contains; HTML/JavaScript landing pages remain
REFERENCE_ONLY and never become instrument evidence by inference.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rc6_source_consolidation import collect_public_sources


def capture(output: str | Path) -> dict:
    """Capture the single structured public-source snapshot used by RC6.

    BYMA discovery happens here and nowhere in the morning authority watcher.
    The watcher consumes this exact atomic file so instrument discovery and
    authority-change detection cannot diverge into two independent scrapers.
    """
    result = collect_public_sources()
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.chmod(temporary, 0o644)
    temporary.replace(target)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=os.getenv(
        "POROTA_PUBLIC_SOURCE_CAPTURE_PATH",
        "/opt/porota-trading/data/market/rc6_public_sources_latest.json",
    ))
    args = parser.parse_args()
    result = capture(args.output)
    target = Path(args.output)
    print("PUBLIC_SOURCE_CAPTURE=" + json.dumps({
        "schema": result.get("schema"),
        "sources": [
            {"source": item.get("source"), "status": item.get("status"),
             "http_status": item.get("http_status"),
             "record_count": item.get("record_count", 0)}
            for item in result.get("sources", [])
        ],
        "output": str(target),
        "decision_effect": "OBSERVE_ONLY",
        "real_money_authorized": False,
    }, ensure_ascii=False, sort_keys=True))
    for item in result.get("sources", []):
        if item.get("source") != "BYMA":
            continue
        case = next((row for row in item.get("records", [])
                     if str(row.get("symbol", "")).upper() == "AAPL"), None)
        if case:
            print("PUBLIC_SOURCE_CASE=" + json.dumps({
                "source": "BYMA", "symbol": "AAPL",
                "fields": {key: case.get(key) for key in (
                    "currency", "bid", "ask", "last", "variation_pct",
                    "volume", "cash_volume", "vwap", "timestamp") if case.get(key) is not None},
                "status": item.get("status"), "observed_at": item.get("observed_at"),
                "decision_effect": "OBSERVE_ONLY",
            }, ensure_ascii=False, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
