#!/usr/bin/env python3
"""Select a deterministic representative canary from the FINAL PPI API residual.

This module never chooses symbols before a real manifest exists and performs no
network, browser, broker, or database writes. Same manifest + options => same
selection.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

CLASS_PRIORITY = {
    "PROVIDER_INVALID": 0,
    "PARTIAL_VALID": 1,
    "NO_PROVIDER_ROWS": 2,
    "HARD_PROVIDER_ERROR": 3,
}
# Ordering only. Families not listed are appended alphabetically.
FAMILY_PRIORITY = [
    "ACCIONES", "CEDEARS", "BONOS", "LETRAS", "ON",
    "OPCIONES", "FUTUROS", "ETF", "FCI", "CAUCIONES",
    "LICITACIONES", "INDICES",
]


def norm(value: object) -> str:
    return str(value or "").strip().upper()


def identity(row: dict) -> tuple[str, str, str, str]:
    return tuple(norm(row.get(k)) for k in ("symbol", "instrument_type", "market", "settlement"))


def load_manifest(path: Path) -> list[dict]:
    rows=[]; seen=set()
    with path.open("r", encoding="utf-8") as fh:
        for lineno,line in enumerate(fh,1):
            if not line.strip(): continue
            row=json.loads(line)
            key=identity(row)
            if not all(key): raise ValueError(f"incomplete identity line {lineno}: {key}")
            cls=norm(row.get("residual_class"))
            if cls not in CLASS_PRIORITY: raise ValueError(f"invalid residual_class line {lineno}: {cls}")
            if key in seen: raise ValueError(f"duplicate identity line {lineno}: {key}")
            seen.add(key)
            rows.append({**row,"symbol":key[0],"instrument_type":key[1],"market":key[2],"settlement":key[3],"residual_class":cls})
    if not rows: raise RuntimeError("EMPTY_RESIDUAL_MANIFEST")
    return rows


def stable_rank(row: dict) -> tuple:
    key="|".join(identity(row))
    digest=hashlib.sha256(key.encode()).hexdigest()
    return (CLASS_PRIORITY[norm(row["residual_class"])], digest, key)


def select_canary(rows: list[dict], *, max_total: int=8, max_per_family: int=1) -> list[dict]:
    if max_total <= 0 or max_per_family <= 0: raise ValueError("limits must be > 0")
    grouped: dict[str,list[dict]]={}
    for row in rows: grouped.setdefault(norm(row["instrument_type"]),[]).append(row)
    ordered=[f for f in FAMILY_PRIORITY if f in grouped]
    ordered += sorted(f for f in grouped if f not in set(ordered))
    out=[]
    for family in ordered:
        for row in sorted(grouped[family], key=stable_rank)[:max_per_family]:
            out.append(row)
            if len(out) >= max_total: return out
    return out


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r,ensure_ascii=False,sort_keys=True)+"\n" for r in rows),encoding="utf-8")


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument("--manifest",type=Path,required=True)
    ap.add_argument("--output",type=Path,required=True)
    ap.add_argument("--max-total",type=int,default=8)
    ap.add_argument("--max-per-family",type=int,default=1)
    args=ap.parse_args()
    rows=load_manifest(args.manifest)
    chosen=select_canary(rows,max_total=args.max_total,max_per_family=args.max_per_family)
    write_jsonl(args.output,chosen)
    print(json.dumps({"mode":"DETERMINISTIC_FINAL_RESIDUAL_CANARY","manifest_rows":len(rows),"selected":len(chosen),"families":[r["instrument_type"] for r in chosen],"output":str(args.output)},sort_keys=True))
    return 0

if __name__ == "__main__": raise SystemExit(main())
