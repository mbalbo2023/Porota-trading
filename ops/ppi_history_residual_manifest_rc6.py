#!/usr/bin/env python3
"""Generate the exact PPI Web residual manifest after PPI API ingestion closes.

This tool is read-only against SQLite. It fails closed while any API task remains
PENDING/RETRYABLE/RUNNING, so the Web stage cannot start early.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

RUN_ID = "PPI-HIST-20260912-001"
ACTIVE_STATES = {"PENDING", "RETRYABLE", "RUNNING"}


def classify_task(state: str, provider_rows: int, valid_rows: int) -> str | None:
    state = str(state or "").upper()
    provider_rows = int(provider_rows or 0)
    valid_rows = int(valid_rows or 0)
    if state == "ERROR":
        return "HARD_PROVIDER_ERROR"
    if state == "DONE_PARTIAL":
        return "PARTIAL_VALID"
    if state == "DONE_EMPTY":
        return "NO_PROVIDER_ROWS" if provider_rows == 0 else "PROVIDER_INVALID"
    return None


def build_manifest(rows: list[tuple]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for row in rows:
        symbol, family, market, settlement, state, provider_rows, valid_rows, last_error, result = row
        key = tuple(str(x or "").strip().upper() for x in (symbol, family, market, settlement))
        if key in seen:
            raise RuntimeError(f"duplicate task identity: {key}")
        seen.add(key)
        residual_class = classify_task(str(state), int(provider_rows or 0), int(valid_rows or 0))
        if residual_class is None:
            continue
        out.append({
            "symbol": key[0],
            "instrument_type": key[1],
            "market": key[2],
            "settlement": key[3],
            "residual_class": residual_class,
            "api_state": str(state),
            "provider_rows": int(provider_rows or 0),
            "valid_rows": int(valid_rows or 0),
            "api_result": str(result or ""),
            "api_error": str(last_error or ""),
        })
    return out


def load_from_runtime() -> list[dict[str, object]]:
    import bf_production_paper_observer as observer
    store = observer.runtime_store()
    with store.connect() as c:
        state_rows = c.execute(
            "SELECT state,count(*) FROM ppi_history_ingest_tasks WHERE run_id=? GROUP BY state",
            (RUN_ID,),
        ).fetchall()
        state_counts = {str(s).upper(): int(n) for s, n in state_rows}
        active = sum(state_counts.get(s, 0) for s in ACTIVE_STATES)
        if active:
            raise RuntimeError(f"API ingestion still active: {active} tasks remain")
        rows = c.execute(
            """SELECT symbol,instrument_type,market,settlement,state,
                      provider_rows,valid_rows,last_error,result
               FROM ppi_history_ingest_tasks
               WHERE run_id=?
               ORDER BY instrument_type,symbol,market,settlement""",
            (RUN_ID,),
        ).fetchall()
    return build_manifest(list(rows))


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(payload, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = load_from_runtime()
    write_jsonl(args.output, rows)
    by_class: dict[str, int] = {}
    for row in rows:
        cls = str(row["residual_class"])
        by_class[cls] = by_class.get(cls, 0) + 1
    print(json.dumps({
        "run_id": RUN_ID,
        "residual_identities": len(rows),
        "by_class": dict(sorted(by_class.items())),
        "output": str(args.output),
        "mode": "READ_ONLY_MANIFEST_GENERATION",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
