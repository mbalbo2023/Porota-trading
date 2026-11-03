#!/usr/bin/env python3
"""Single RC6 morning BYMA pipeline.

Order is intentional and invariant:
1. refresh the shared structured public-source snapshot (the only BYMA scraper);
2. fingerprint that exact BYMA snapshot plus official authority pages;
3. persist the morning diff consumed by preopen.

No second instrument scraper exists here.  A degraded structured capture or
authority read returns non-zero so systemd and preopen expose the failure.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.rc6_public_source_capture import capture
from scripts.rc6_byma_morning_watch import collect, persist

DEFAULT_MARKET_ROOT = Path("/opt/porota-trading/data/market")


def run(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    structured = root / "rc6_public_sources_latest.json"

    public = capture(structured)
    byma = next(
        (item for item in public.get("sources", [])
         if str(item.get("source") or "").upper() == "BYMA"),
        None,
    )
    if not isinstance(byma, dict):
        raise RuntimeError("BYMA_STRUCTURED_BLOCK_MISSING_AFTER_CAPTURE")

    current = collect(structured_snapshot=structured)
    payload = persist(root, current)
    payload["pipeline"] = {
        "structured_snapshot": str(structured),
        "byma_scrape_status": byma.get("status"),
        "byma_record_count": int(byma.get("record_count") or 0),
        "scrape_method": byma.get("scrape_method"),
        "single_structured_scraper": True,
        "authority_watch_reuses_structured_snapshot": True,
    }
    # Re-persist enriched latest atomically; append-only history was already
    # written by persist() and intentionally stays compact.
    latest = root / "byma_morning_watch_latest.json"
    tmp = latest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp.replace(latest)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(DEFAULT_MARKET_ROOT))
    args = parser.parse_args()
    payload = run(Path(args.root))
    summary = {
        "state": payload.get("state"),
        "changed_components": payload.get("changed_components", []),
        "errors": payload.get("errors", []),
        "observed_at": payload.get("observed_at"),
        "pipeline": payload.get("pipeline", {}),
        "decision_effect": "OBSERVE_ONLY",
        "real_money_authorized": False,
    }
    print("BYMA_MORNING_PIPELINE=" + json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 2 if payload.get("state") == "DEGRADED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
