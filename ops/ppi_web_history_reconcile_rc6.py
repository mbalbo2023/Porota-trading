#!/usr/bin/env python3
"""Reconcile authenticated PPI Web history into History Store v2.

Design:
- accepts sanitized per-identity captures produced by the trusted browser layer;
- maps only explicit provider aliases to the PPI-style validation schema;
- reuses cp_history_ingest_policy_hf6.validate_provider_history unchanged;
- never repairs, interpolates, forward-fills, or synthesizes OHLCV;
- stores valid rows as source PPI_WEB_HISTORY;
- History Store precedence guarantees PPI API canonical rows are never replaced;
- PPI Web may replace lower-authority fallbacks such as IOL only when appropriate.

Capture JSONL schema (one identity per line):
{
  "symbol":"GGAL", "instrument_type":"ACCIONES", "market":"BYMA",
  "settlement":"A-48HS", "source_url":"https://trading.portfoliopersonal.com/...",
  "requested_from":"2025-09-12", "requested_to":"2026-09-12",
  "rows":[{...provider row...}, ...]
}
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

import cp_history_ingest_policy_hf6 as policy
import cu_history_store_v2_hf6 as history_v2
from cv_history_store_adapter_hf6 import default_history_store

SOURCE = "PPI_WEB_HISTORY"

ALIASES = {
    "date": ("date", "fecha", "datetime", "timestamp", "day", "dia", "día"),
    "openingPrice": ("openingPrice", "open", "apertura"),
    "max": ("max", "high", "maximo", "máximo"),
    "min": ("min", "low", "minimo", "mínimo"),
    "price": ("price", "close", "cierre", "ultimo", "último"),
    "volume": ("volume", "volumen", "quantity", "cantidad", "monto"),
}


def _pick(row: Mapping[str, object], names: tuple[str, ...]):
    lower = {str(k).lower(): v for k, v in row.items()}
    for name in names:
        if name in row:
            return row[name]
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def normalize_web_row(row: Mapping[str, object]) -> dict[str, object]:
    """Map explicit field aliases only. Missing values remain missing and reject."""
    if not isinstance(row, Mapping):
        return {}
    return {target: _pick(row, aliases) for target, aliases in ALIASES.items()}


def normalize_payload(rows) -> list[dict[str, object]]:
    if not isinstance(rows, list):
        return []
    return [normalize_web_row(row) if isinstance(row, Mapping) else {} for row in rows]


def _date_bound(value):
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value)[:10]).date()


def classify(provider_rows: int, valid_rows: int, rejected_rows: int) -> str:
    if provider_rows == 0:
        return "DONE_EMPTY"
    if valid_rows == 0:
        return "DONE_EMPTY"
    if rejected_rows:
        return "DONE_PARTIAL"
    return "DONE_VALID"


def reconcile_capture(capture: Mapping[str, object], *, history_store=None, apply: bool = False) -> dict[str, object]:
    required = ("symbol", "instrument_type", "market", "settlement", "rows")
    missing = [k for k in required if capture.get(k) in (None, "")]
    if missing:
        raise ValueError("CAPTURE_IDENTITY_INCOMPLETE:" + ",".join(missing))

    symbol = str(capture["symbol"]).strip().upper()
    family = str(capture["instrument_type"]).strip().upper()
    market = str(capture["market"]).strip().upper()
    settlement = str(capture["settlement"]).strip().upper()
    if market == "UNKNOWN" or settlement == "UNKNOWN":
        raise ValueError("CAPTURE_IDENTITY_UNVERIFIED")

    payload = normalize_payload(capture["rows"])
    now = datetime.now(timezone.utc)
    result = policy.validate_provider_history(
        payload,
        as_of=now,
        date_from=_date_bound(capture.get("requested_from")),
        date_to=_date_bound(capture.get("requested_to")),
    )

    stored = {"versions_appended": 0, "canonical_updates": 0, "protected_by_precedence": 0}
    if apply and result.valid_rows:
        store = history_store or default_history_store()
        candles = [
            history_v2.Candle(
                symbol=symbol,
                instrument_type=family,
                market=market,
                settlement=settlement,
                date=str(row["date"])[:10],
                open=float(row["openingPrice"]),
                high=float(row["max"]),
                low=float(row["min"]),
                close=float(row["price"]),
                volume=float(row["volume"]),
                source=SOURCE,
                adjusted=False,
                observed_at=now.isoformat(),
                metadata={
                    "history_quality": "FULL_OHLC",
                    "provenance": "PPI_WEB_AUTHENTICATED_READONLY",
                    "ready_paper_implication": "NONE",
                    "execution_price_implication": "NONE",
                },
            )
            for row in result.valid_rows
        ]
        stored = history_v2.append_many(store, candles)

    reasons: dict[str, int] = {}
    for item in result.rejected_rows:
        reasons[item.reason] = reasons.get(item.reason, 0) + 1

    return {
        "symbol": symbol,
        "instrument_type": family,
        "market": market,
        "settlement": settlement,
        "source": SOURCE,
        "source_url": str(capture.get("source_url") or ""),
        "provider_rows": result.provider_rows,
        "valid_rows": result.valid_count,
        "rejected_rows": result.rejected_count,
        "state": classify(result.provider_rows, result.valid_count, result.rejected_count),
        "storage_quality": result.storage_quality,
        "context_state": result.context_state,
        "first_date": result.first_date,
        "last_date": result.last_date,
        "rejection_reasons": dict(sorted(reasons.items())),
        "versions_appended": int(stored["versions_appended"]),
        "canonical_updates": int(stored["canonical_updates"]),
        "protected_by_precedence": int(stored["protected_by_precedence"]),
        "applied": bool(apply),
        "ready_paper_implication": "NONE",
        "execution_price_implication": "NONE",
    }


def load_jsonl(path: Path) -> list[dict[str, object]]:
    rows = []
    seen = set()
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            obj = json.loads(line)
            key = tuple(str(obj.get(k, "")).strip().upper() for k in ("symbol", "instrument_type", "market", "settlement"))
            if key in seen:
                raise ValueError(f"DUPLICATE_CAPTURE_IDENTITY:{lineno}:{key}")
            seen.add(key)
            rows.append(obj)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--captures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Write only validated FULL_OHLC rows to History Store")
    args = parser.parse_args()

    captures = load_jsonl(args.captures)
    results = [reconcile_capture(row, apply=args.apply) for row in captures]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in results), encoding="utf-8")

    summary = {"identities": len(results), "applied": bool(args.apply), "states": {}, "valid_rows": 0, "canonical_updates": 0, "protected_by_precedence": 0}
    for row in results:
        summary["states"][row["state"]] = summary["states"].get(row["state"], 0) + 1
        summary["valid_rows"] += int(row["valid_rows"])
        summary["canonical_updates"] += int(row["canonical_updates"])
        summary["protected_by_precedence"] += int(row["protected_by_precedence"])
    summary["states"] = dict(sorted(summary["states"].items()))
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
