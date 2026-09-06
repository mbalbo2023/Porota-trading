#!/usr/bin/env python3
"""RC5/A3: diagnóstico read-only de alineación PPI ↔ A3 CEM.

Objetivo: explicar por qué A3 History no debe activarse todavía si no existe una
identidad demostrable entre los símbolos PPI y A3. No escribe DB, no autentica,
no usa reMarkets privado y no posee order routing.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from cg_paper_workspace import database_path
from cz_a3_cem_public_history_hf6 import A3CEMPublicReadOnlyClient

DB = Path(os.getenv("POROTA_PAPER_DB", str(database_path())))
FAMILIES = {"FUTUROS", "OPCIONES", "FUTURE", "OPTION"}


def canon(value: Any) -> str:
    return str(value or "").strip().upper()


def loose(value: Any) -> str:
    # Sólo diagnóstico de candidatos; NUNCA se usa para auto-mapear.
    return re.sub(r"[^A-Z0-9]", "", canon(value))


def extract_symbols(payload: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower() in {"symbol", "ticker", "instrument", "security"} and isinstance(value, str):
                if canon(value):
                    out.add(canon(value))
            else:
                out |= extract_symbols(value)
    elif isinstance(payload, list):
        for item in payload:
            out |= extract_symbols(item)
    return out


def ppi_symbols() -> set[str]:
    if not DB.exists():
        return set()
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "financial_instrument_catalog" in tables:
            rows = conn.execute(
                "SELECT DISTINCT ticker,instrument_type FROM financial_instrument_catalog "
                "WHERE UPPER(instrument_type) IN ('FUTUROS','OPCIONES','FUTURE','OPTION')"
            ).fetchall()
            return {canon(r[0]) for r in rows if canon(r[0])}
        return set()
    finally:
        conn.close()


def main() -> int:
    ppi = ppi_symbols()
    result = {
        "schema": "POROTA_A3_CATALOG_ALIGNMENT_RC5_V1",
        "read_only": True,
        "a3_order_routing": False,
        "ppi_derivative_symbols": len(ppi),
        "a3_symbols": 0,
        "exact_overlap": 0,
        "loose_candidates": 0,
        "status": "RED",
        "reason": "NOT_RUN",
    }
    try:
        payload = A3CEMPublicReadOnlyClient().symbols()
        a3 = extract_symbols(payload)
    except Exception as exc:
        result["reason"] = f"A3_CEM_READ_FAILED:{type(exc).__name__}"
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 2

    exact = sorted(ppi & a3)
    a3_loose: dict[str, list[str]] = {}
    for symbol in a3:
        a3_loose.setdefault(loose(symbol), []).append(symbol)
    candidates = []
    for symbol in sorted(ppi):
        matches = sorted(a3_loose.get(loose(symbol), []))
        if symbol not in a3 and matches:
            candidates.append({"ppi": symbol, "a3_candidates": matches[:8]})

    result.update({
        "a3_symbols": len(a3),
        "exact_overlap": len(exact),
        "exact_examples": exact[:30],
        "loose_candidates": len(candidates),
        "candidate_examples": candidates[:30],
    })

    if not ppi:
        result["reason"] = "PPI_DERIVATIVE_CATALOG_EMPTY_OR_NOT_PRESENT"
    elif exact:
        result["status"] = "GREEN"
        result["reason"] = "EXACT_IDENTITY_OVERLAP_AVAILABLE_FOR_HISTORY_PILOT"
    elif candidates:
        result["status"] = "YELLOW"
        result["reason"] = "FORMAT_ALIGNMENT_REQUIRED_NO_AUTOMAP"
    else:
        result["reason"] = "ZERO_IDENTITY_OVERLAP"

    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["status"] == "GREEN" else 1 if result["status"] == "YELLOW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
