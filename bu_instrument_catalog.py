"""Catálogo financiero derivado de payloads PPI, con procedencia explícita.

Las etiquetas de moneda se contrastaron con el diagnóstico del 27/08/2026.
SearchInstrument no prueba multiplicador, margen ni vencimiento estructurado.
"""

import json
from decimal import Decimal

from bs_instrument_contracts import InstrumentContract, cash_currency, family_name, contract_from_metadata


def init_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS financial_instrument_catalog(
          ticker TEXT NOT NULL, instrument_type TEXT NOT NULL, market TEXT NOT NULL,
          currency TEXT NOT NULL, settlement TEXT NOT NULL, settlement_source TEXT NOT NULL,
          description TEXT NOT NULL, last_seen_at TEXT NOT NULL, run_id TEXT NOT NULL,
          status TEXT NOT NULL, capability TEXT NOT NULL, metadata_json TEXT NOT NULL,
          PRIMARY KEY(ticker,instrument_type,market,currency,settlement));
        CREATE TABLE IF NOT EXISTS catalog_query_results(
          run_id TEXT NOT NULL, ticker_query TEXT NOT NULL, name_query TEXT NOT NULL,
          instrument_type TEXT NOT NULL, market TEXT NOT NULL, status TEXT NOT NULL,
          record_count INTEGER NOT NULL, detail TEXT NOT NULL,
          PRIMARY KEY(run_id,ticker_query,instrument_type,market));
        CREATE TABLE IF NOT EXISTS broker_market_configuration(
          name TEXT PRIMARY KEY, checked_at TEXT NOT NULL, payload_json TEXT NOT NULL);
        """)
        legacy_exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='instrument_catalog'").fetchone()
        if legacy_exists and not c.execute("SELECT 1 FROM financial_instrument_catalog LIMIT 1").fetchone():
            for row in c.execute("SELECT * FROM instrument_catalog").fetchall():
                try:
                    record = normalize_record(json.loads(row["raw_json"]), row["settlement"], row["downloaded_at"], "LEGACY_CATALOG")
                except (TypeError, ValueError):
                    continue
                record["status"] = "STALE"
                persist(c, record)


def normalize_record(raw, settlement_hint, observed_at, run_id):
    if not isinstance(raw, dict):
        raise ValueError("El registro de instrumento debe ser un objeto")
    ticker = str(raw.get("ticker") or raw.get("symbol") or "").strip().upper()
    kind = str(raw.get("type") or raw.get("instrumentType") or "").strip().upper()
    if not ticker or not kind:
        raise ValueError("Registro sin ticker o clase real del instrumento")
    market = str(raw.get("market") or "UNKNOWN").strip().upper()
    try:
        currency = cash_currency(raw.get("currency"))
    except ValueError:
        currency = "UNKNOWN"
    settlement = str(raw.get("settlement") or settlement_hint)
    value = dict(ticker=ticker, instrument_type=kind, market=market, currency=currency,
                 settlement=settlement, settlement_source="PPI_FIELD" if raw.get("settlement") else "REQUEST_CANDIDATE",
                 description=str(raw.get("description") or ""), last_seen_at=observed_at,
                 run_id=run_id, status="AVAILABLE", capability="", raw=raw)
    value["capability"] = capability(value)
    return value


def contract_for(record):
    if record["currency"] == "UNKNOWN" or record["market"] == "UNKNOWN":
        raise ValueError("MISSING_CURRENCY_OR_MARKET")
    family = family_name(record["instrument_type"])
    raw = record.get("raw", {})
    if raw.get("financial_contract_v17"):
        spec = contract_from_metadata(record["ticker"], family, raw["financial_contract_v17"])
        if (spec.currency, spec.market, spec.settlement) != (record["currency"], record["market"], record["settlement"]):
            raise ValueError("CONTRADICTORY_CONTRACT")
        return spec
    if family in {"ACCIONES", "CEDEARS", "ETFS"} and record["market"] == "BYMA":
        # Convención de unidad negociada; no afirmar que SearchInstrument
        # devolvió estos campos. No extrapolar a VN, derivados ni FCI.
        return InstrumentContract(record["ticker"], family, record["currency"], record["market"],
                                  record["settlement"], Decimal(1), Decimal(1),
                                  "PPI_SEARCH_INSTRUMENT + BYMA_SPOT_UNIT_CONVENTION")
    raise ValueError({"BONOS": "NEEDS_NOMINAL_UNITS", "LETRAS": "NEEDS_NOMINAL_UNITS",
                      "OBLIGACIONES": "NEEDS_NOMINAL_UNITS", "OPCIONES": "NEEDS_OPTION_CONTRACT",
                      "FUTUROS": "NEEDS_FUTURES_MARGIN_AND_CONTRACT", "CAUCIONES": "NEEDS_CAUCION_TERMS",
                      "FCI": "NEEDS_FUND_SETTLEMENT"}.get(family, "UNSUPPORTED_FAMILY"))


def capability(record):
    try:
        spec = contract_for(record)
    except ValueError as exc:
        return str(exc)
    if spec.market != "BYMA":
        return "NEEDS_MARKET_EXECUTOR"
    if spec.family in {"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES"}:
        return "READY_PAPER_SPOT"
    return "NEEDS_SPECIALIZED_EXECUTOR"


def persist(c, record):
    c.execute("""INSERT OR REPLACE INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (record["ticker"], record["instrument_type"], record["market"], record["currency"],
               record["settlement"], record["settlement_source"], record["description"],
               record["last_seen_at"], record["run_id"], record["status"], record["capability"],
               json.dumps(record["raw"], ensure_ascii=False, default=str)))


def lookup(store, symbol, kind, settlement):
    """Una identidad ambigua no se resuelve eligiendo la primera fila."""
    with store.connect() as c:
        rows = c.execute("""SELECT * FROM financial_instrument_catalog
          WHERE ticker=? AND instrument_type=? AND settlement=?""", (symbol, kind, settlement)).fetchall()
        if not rows:
            # Compatibilidad con el catálogo ya persistido en el Droplet.
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='instrument_catalog'").fetchone():
                return None
            legacy = c.execute("SELECT * FROM instrument_catalog WHERE ticker=?", (symbol,)).fetchall()
            candidates = []
            for row in legacy:
                try:
                    record = normalize_record(json.loads(row["raw_json"]), settlement, row["downloaded_at"], "LEGACY_CATALOG")
                except (ValueError, TypeError):
                    continue
                if record["instrument_type"] == kind:
                    record["status"] = "STALE"
                    candidates.append(record)
            keys = {(r["market"], r["currency"]) for r in candidates}
            return candidates[0] if len(keys) == 1 else None
    available = [r for r in rows if r["status"] == "AVAILABLE"]
    rows = available or rows
    if len(rows) != 1:
        return None
    record = dict(rows[0])
    record["raw"] = json.loads(record.pop("metadata_json"))
    return record


def quote_terms(record):
    if record is None:
        return {"currency": None, "market": None, "metadata_source": None,
                "opening_block_reason": "Falta catálogo confirmado o la identidad es ambigua"}
    spec = None
    try:
        spec = contract_for(record)
    except ValueError:
        pass
    reason = "" if record["capability"] == "READY_PAPER_SPOT" else record["capability"]
    if record["status"] != "AVAILABLE":
        reason = "Catálogo no confirmado en la última actualización"
    return {"currency": record["currency"], "market": record["market"], "contract": spec,
            "metadata_source": f"PPI_CATALOG:{record['last_seen_at']}", "opening_block_reason": reason}
