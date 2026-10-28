#!/usr/bin/env python3
"""Exporta metadatos públicos ya guardados: sin red, credenciales ni órdenes.

Uso en Termius: python3 v17_diagnostico_instrumentos.py --clipboard
El JSON también se imprime si OSC 52 no está habilitado en el terminal.
"""

import argparse
import base64
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


PUBLIC_FIELDS = {
    "ticker", "symbol", "description", "name", "instrumenttype", "type",
    "market", "settlement", "currency", "currencycode", "moneda",
    "term", "termdays", "term_days", "days", "plazo", "maturitydate",
    "maturity", "expirationdate", "expiry", "expiration", "duedate",
    "underlying", "underlyingticker", "strike", "strikeprice", "optiontype",
    "right", "contractmultiplier", "multiplier", "lotsize", "lot",
    "minquantity", "minimumquantity", "quantitystep", "pricefactor",
    "priceunit", "nominalvalue", "initialmargin", "maintenancemargin",
    "collateral", "rate", "rateunit", "rate_unit", "annualrate", "tna",
    "daycountbasis", "day_count_basis", "operation", "operationtype",
}


def public_metadata(raw):
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items()
            if str(k).lower() in PUBLIC_FIELDS and isinstance(v, (str, int, float, bool, type(None)))}


def collect(path):
    report = {"diagnostic": "POROTA_V17_INSTRUMENTS", "generated_at": datetime.now(timezone.utc).isoformat(),
              "scope": "Solo catálogo público persistido. Sin red ni lectura de cuentas o credenciales.",
              "database": str(path), "families": [], "samples": [], "warnings": []}
    if not path.is_file():
        report["warnings"].append("No se encontró la base indicada. Usar --db con la ruta del observador.")
        return report
    uri = "file:" + quote(str(path.resolve()), safe="/") + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as c:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA query_only=ON")
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "instrument_catalog" not in tables:
                report["warnings"].append("Esta base todavía no contiene instrument_catalog.")
                return report
            report["families"] = [dict(r) for r in c.execute("""SELECT instrument_type,
                COUNT(*) instruments, MAX(downloaded_at) last_download
                FROM instrument_catalog GROUP BY instrument_type ORDER BY instrument_type""")]
            for family in report["families"]:
                rows = c.execute("""SELECT ticker,instrument_type,market,settlement,downloaded_at,raw_json
                    FROM instrument_catalog WHERE instrument_type=? ORDER BY ticker LIMIT 3""",
                    (family["instrument_type"],)).fetchall()
                for row in rows:
                    item = {k: row[k] for k in row.keys() if k != "raw_json"}
                    try:
                        raw = json.loads(row["raw_json"] or "{}")
                    except (TypeError, ValueError):
                        raw = {}
                    item["public_metadata"] = public_metadata(raw)
                    report["samples"].append(item)
            if "candidate_universe" in tables:
                report["discovery"] = [dict(r) for r in c.execute("""SELECT instrument_type,status,
                    COUNT(*) candidates FROM candidate_universe GROUP BY instrument_type,status
                    ORDER BY instrument_type,status""")]
    except sqlite3.Error as exc:
        report["warnings"].append(f"No se pudo leer el catálogo: {type(exc).__name__}: {exc}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("/opt/porota-trading/data/observer/observer_production.db"))
    parser.add_argument("--clipboard", action="store_true", help="Copia el JSON con OSC 52 en terminales compatibles")
    args = parser.parse_args()
    result = json.dumps(collect(args.db), ensure_ascii=False, indent=2)
    print(result)
    if args.clipboard:
        encoded = base64.b64encode(result.encode("utf-8")).decode("ascii")
        sys.stdout.write("\033]52;c;" + encoded + "\a")
        sys.stdout.flush()
        print("\nResultado enviado al portapapeles por OSC 52; depende de que Termius lo permita.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
