#!/usr/bin/env python3
"""Generate the exact residual manifest after PPI Web reconciliation.

Input is the reconciliation JSONL emitted by ppi_web_history_reconcile_rc6.py.
Only identities still unresolved after authenticated PPI Web are forwarded to IOL.
DONE_VALID is considered resolved for the requested provider window. PARTIAL,
EMPTY and ERROR remain residual. This tool never performs network or DB writes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

UNRESOLVED = {"DONE_PARTIAL", "DONE_EMPTY", "ERROR"}
RESOLVED = {"DONE_VALID"}


def classify_for_iol(row: dict) -> str | None:
    state = str(row.get("state") or "").strip().upper()
    if state == "DONE_PARTIAL":
        return "PPI_WEB_PARTIAL_VALID"
    if state == "DONE_EMPTY":
        provider_rows = int(row.get("provider_rows") or 0)
        return "PPI_WEB_NO_ROWS" if provider_rows == 0 else "PPI_WEB_PROVIDER_INVALID"
    if state == "ERROR":
        return "PPI_WEB_ERROR"
    if state in RESOLVED:
        return None
    raise ValueError(f"UNKNOWN_WEB_STATE:{state}")


def build_manifest(rows: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for row in rows:
        key = tuple(str(row.get(k) or "").strip().upper() for k in ("symbol", "instrument_type", "market", "settlement"))
        if not all(key):
            raise ValueError(f"INCOMPLETE_IDENTITY:{key}")
        if key in seen:
            raise ValueError(f"DUPLICATE_IDENTITY:{key}")
        seen.add(key)
        reason = classify_for_iol(row)
        if reason is None:
            continue
        out.append({
            "symbol": key[0],
            "instrument_type": key[1],
            "market": key[2],
            "settlement": key[3],
            "fallback_source": "IOL",
            "residual_class": reason,
            "ppi_web_state": str(row.get("state") or "").upper(),
            "ppi_web_provider_rows": int(row.get("provider_rows") or 0),
            "ppi_web_valid_rows": int(row.get("valid_rows") or 0),
            "ppi_web_rejected_rows": int(row.get("rejected_rows") or 0),
            "ppi_web_first_date": row.get("first_date"),
            "ppi_web_last_date": row.get("last_date"),
            "ready_paper_implication": "NONE",
            "execution_price_implication": "NONE",
        })
    return out


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                obj = json.loads(line)
                if not isinstance(obj, dict):
                    raise ValueError("JSONL_ROW_NOT_OBJECT")
                rows.append(obj)
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in rows), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-results", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    source = load_jsonl(args.web_results)
    manifest = build_manifest(source)
    write_jsonl(args.output, manifest)
    by_class: dict[str, int] = {}
    for row in manifest:
        cls = str(row["residual_class"])
        by_class[cls] = by_class.get(cls, 0) + 1
    print(json.dumps({
        "input_identities": len(source),
        "resolved_by_ppi_web": len(source) - len(manifest),
        "iol_residual_identities": len(manifest),
        "by_class": dict(sorted(by_class.items())),
        "mode": "READ_ONLY_POSTWEB_RESIDUAL",
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
