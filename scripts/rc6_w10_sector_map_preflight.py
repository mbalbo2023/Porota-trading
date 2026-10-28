#!/usr/bin/env python3
"""RC6 W10 fail-closed preflight for the reviewed sector map.

This validator is intentionally read-only and network-free. W10 is BINDING for
new PAPER entries, so a release must not be considered deployable when the
versioned sector evidence needed by the configured RC6 focus is absent.
"""
from __future__ import annotations

import csv
from pathlib import Path

import ck_policy_gate_hf6 as gate
import porota_mode_manager as modes
from es_policy_context_rc6 import _explicit_sector_map

ROOT = Path(__file__).resolve().parents[1]
MAP = ROOT / "POROTA_SECTOR_MAP_V1.csv"
REQUIRED_COLUMNS = (
    "ticker", "family", "market", "currency", "settlement", "sector",
    "source", "author", "effective_at", "reviewed",
)

# Exact RC6 PAPER focus identities. AAPLD/AAPLC are the MEP/CCL Apple
# identities explicitly configured by porota_mode_manager; no identity is
# inferred from the ticker at runtime.
EXPECTED_FOCUS = {
    ("GGAL", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    ("YPFD", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    ("PAMP", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    ("BMA", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    ("BBAR", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    ("SUPV", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    ("CEPU", "ACCIONES", "BYMA", "ARS", "A-24HS"),
    ("AAPL", "CEDEARS", "BYMA", "ARS", "A-24HS"),
    ("AAPLD", "CEDEARS", "BYMA", "USD_MEP", "A-24HS"),
    ("AAPLC", "CEDEARS", "BYMA", "USD_CCL", "A-24HS"),
}


def _norm(row, name):
    return str(row.get(name) or "").strip()


def main() -> int:
    if not MAP.is_file():
        raise SystemExit("W10_SECTOR_MAP_NOT_READY: file missing")

    with MAP.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != REQUIRED_COLUMNS:
            raise SystemExit("W10_SECTOR_MAP_NOT_READY: schema mismatch")
        rows = list(reader)

    if not rows:
        raise SystemExit("W10_SECTOR_MAP_NOT_READY: zero reviewed rows")

    seen = set()
    for index, row in enumerate(rows, start=2):
        reviewed = _norm(row, "reviewed").lower() in {"1", "true", "yes", "si", "sí"}
        if not reviewed:
            raise SystemExit(f"W10_SECTOR_MAP_NOT_READY: row {index} not reviewed")
        for field in ("sector", "source", "author", "effective_at"):
            if not _norm(row, field):
                raise SystemExit(f"W10_SECTOR_MAP_NOT_READY: row {index} missing {field}")
        key = tuple(_norm(row, name).upper() for name in
                    ("ticker", "family", "market", "currency", "settlement"))
        if not all(key):
            raise SystemExit(f"W10_SECTOR_MAP_NOT_READY: row {index} incomplete identity")
        if key in seen:
            raise SystemExit(f"W10_SECTOR_MAP_NOT_READY: duplicate identity {key}")
        seen.add(key)

    missing = EXPECTED_FOCUS - seen
    if missing:
        formatted = ";".join("/".join(key) for key in sorted(missing))
        raise SystemExit("W10_SECTOR_MAP_NOT_READY: missing focus identities: " + formatted)

    loaded = _explicit_sector_map()
    if EXPECTED_FOCUS - set(loaded):
        raise SystemExit("W10_SECTOR_MAP_NOT_READY: provider rejected required evidence")

    if modes.PAPER_DEFAULTS["PAPER_SECTOR_CONCENTRATION_POLICY"] != "BINDING":
        raise SystemExit("W10_POLICY_NOT_BINDING: PAPER_DEFAULTS")
    if modes.RC6_FROZEN_PAPER_SETTINGS["PAPER_SECTOR_CONCENTRATION_POLICY"] != "BINDING":
        raise SystemExit("W10_POLICY_NOT_BINDING: RC6 frozen settings")
    if gate.active_policies()["sector_concentration"] != "BINDING":
        raise SystemExit("W10_POLICY_NOT_BINDING: active default")

    # Semantic proof: every mapped focus sector is evaluable below capacity;
    # an unmapped candidate still fails closed. This never routes an order.
    for key in sorted(EXPECTED_FOCUS):
        sector = loaded[key]["sector"]
        result = gate.evaluate(sectors={"groups": []}, candidate_sector=sector, sector_limit=2)
        if result["execute_block"]:
            raise SystemExit(f"W10_VALID_CANDIDATE_NOT_EVALUABLE: {key}: {result['verdict']}")
    unmapped = gate.evaluate(sectors={"groups": []}, candidate_sector=None, sector_limit=2)
    if not unmapped["execute_block"] or unmapped["verdict"] != "SECTOR_UNMAPPED_BINDING":
        raise SystemExit("W10_FAIL_CLOSED_REGRESSION")

    sectors = sorted({loaded[key]["sector"] for key in EXPECTED_FOCUS})
    print("W10_POLICY=BINDING")
    print(f"W10_SECTOR_MAP_ROWS={len(loaded)}")
    print(f"W10_FOCUS_COVERAGE={len(EXPECTED_FOCUS)}/{len(EXPECTED_FOCUS)}")
    print("W10_SECTORS=" + ",".join(sectors))
    print("W10_UNMAPPED_FAIL_CLOSED=GREEN")
    print("W10_VALID_CANDIDATES_EVALUABLE=GREEN")
    print("REAL_ORDER_ROUTES=NOT_CALLED")
    print("W10_PREFLIGHT=GREEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
