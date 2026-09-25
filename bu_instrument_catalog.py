"""Catálogo financiero derivado de payloads PPI, con procedencia explícita.

Las etiquetas de moneda se contrastaron con el diagnóstico del 27/08/2026.
SearchInstrument no prueba multiplicador, margen ni vencimiento estructurado.
"""

import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from bs_instrument_contracts import InstrumentContract, cash_currency, family_name, contract_from_metadata
from rc6_multisource_discovery import canonical_family, canonical_market, canonical_settlement

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
US_MARKET_WIDE_CEDEAR_HOLIDAYS = {
    "2026-09-07": "US_LABOR_DAY",
}


def rc6_underlying_opening_block(ticker, instrument_type, now=None):
    """Bloquea sólo nuevas aperturas PAPER de CEDEAR ante cierre total de EE.UU.

    Fail-safe RC6: hasta contar con un mapping autoritativo CEDEAR -> mercado
    subyacente, un cierre total del mercado estadounidense bloquea toda nueva
    apertura CEDEAR. Cotizaciones, persistencia y observación continúan activas;
    este texto llega a Quote.opening_block_reason y sólo convierte la decisión
    de apertura en HOLD. El ticker se conserva por compatibilidad de interfaz.
    """
    local = (now or datetime.now(AR_TZ)).astimezone(AR_TZ)
    kind = str(instrument_type or "").strip().upper()
    event = US_MARKET_WIDE_CEDEAR_HOLIDAYS.get(local.date().isoformat())
    if kind in {"CEDEARS", "CEDEAR"} and event:
        return f"UNDERLYING_MARKET_CLOSED: {event}"
    return ""


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
        CREATE TABLE IF NOT EXISTS catalog_family_coverage(
          instrument_type TEXT PRIMARY KEY, run_id TEXT NOT NULL,
          declared INTEGER NOT NULL, queries INTEGER NOT NULL,
          observed_count INTEGER NOT NULL, ready_paper_count INTEGER NOT NULL,
          discovery_status TEXT NOT NULL, checked_at TEXT NOT NULL);
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


def validate_configuration(value):
    """Conserva literalmente los enums; no los convierte en permisos.

    No acepta un snapshot parcial ni mezcla una respuesta inválida con la
    configuración de un ciclo anterior. Vacío es distinto de no disponible.
    """
    names = {'instrument_types', 'markets', 'settlements', 'quantity_types',
             'operation_terms', 'operation_types', 'operations'}
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError('PPI_CONFIGURATION_INVALID_SHAPE')
    for items in value.values():
        if (not isinstance(items, list) or len(items) > 100
                or any(not isinstance(s, str) or not s.strip() or len(s) > 100 for s in items)
                or len(set(items)) != len(items)):
            raise ValueError('PPI_CONFIGURATION_INVALID_SHAPE')
    return {key: list(items) for key, items in value.items()}


def persist_family_coverage(c, configuration, query_results, records, run_id, observed_at):
    """Inventario completo de familias declaradas, aunque no haya semillas.

    declared: 1 devuelta, 0 no enumerada en snapshot válido, -1 desconocida.
    Cuenta identidades, no filtros ni permisos; únicamente la última ejecución.
    Mantiene familias retiradas y las conocidas si falla la configuración.
    """
    records = list(records)
    declared = ({canonical_family(value) for value in configuration['instrument_types']}
                if configuration is not None else None)
    families = {r[0] for r in c.execute('SELECT instrument_type FROM catalog_family_coverage')}
    families.update(declared or ())
    families.update(q[3] for q in query_results)
    families.update(r['instrument_type'] for r in records)
    for kind in sorted(families):
        queries = [q for q in query_results if q[3] == kind
                   and q[5] not in {'TYPE_NOT_ENUMERATED', 'MARKET_NOT_ENUMERATED'}]
        observed = [r for r in records if r['instrument_type'] == kind]
        errors = any(q[5] in {'ERROR', 'INVALID_METADATA'} for q in queries)
        listed = -1 if declared is None else int(kind in declared)
        if listed == 0:
            state = 'NOT_ENUMERATED'
        elif observed:
            state = 'OBSERVED_WITH_ERRORS' if errors else 'INSTRUMENTS_OBSERVED'
        elif errors:
            state = 'QUERY_ERROR'
        elif queries:
            state = 'EMPTY_FILTER_RESULTS'
        else:
            state = 'DECLARED_NO_QUERY' if listed == 1 else 'CONFIGURATION_UNAVAILABLE'
        c.execute('INSERT OR REPLACE INTO catalog_family_coverage VALUES(?,?,?,?,?,?,?,?)',
            (kind, run_id, listed, len(queries), len(observed),
             sum(str(r['capability']).startswith('READY_PAPER_') for r in observed), state, observed_at))


def normalize_record(raw, settlement_hint, observed_at, run_id):
    if not isinstance(raw, dict):
        raise ValueError("El registro de instrumento debe ser un objeto")
    ticker = str(raw.get("ticker") or raw.get("symbol") or "").strip().upper()
    provider_kind = str(raw.get("type") or raw.get("instrumentType") or "").strip().upper()
    kind = canonical_family(provider_kind)
    if not ticker or not kind:
        raise ValueError("Registro sin ticker o clase real del instrumento")
    market = canonical_market(raw.get("market") or "UNKNOWN")
    try:
        currency = cash_currency(raw.get("currency"))
    except ValueError:
        currency = "UNKNOWN"
    settlement = canonical_settlement(raw.get("settlement") or settlement_hint, kind)
    raw = dict(raw)
    raw.setdefault("_provider_instrument_type", provider_kind)
    raw.setdefault("_discovery_source", "PPI_PRIMARY")
    value = dict(ticker=ticker, instrument_type=kind, market=market, currency=currency,
                 settlement=settlement, settlement_source="PPI_FIELD" if raw.get("settlement") else "REQUEST_CANDIDATE",
                 description=str(raw.get("description") or ""), last_seen_at=observed_at,
                 run_id=run_id, status="AVAILABLE", capability="", raw=raw)
    value["capability"] = capability(value)
    return value


def normalize_complementary_record(raw, observed_at, run_id):
    """Persist IOL/BYMA discovery without inventing missing PPI metadata."""
    if not isinstance(raw, dict):
        raise ValueError("Complementary record must be a dict")
    ticker=str(raw.get("ticker") or raw.get("symbol") or "").strip().upper()
    kind=canonical_family(raw.get("instrument_type") or raw.get("asset_type") or raw.get("family"))
    market=canonical_market(raw.get("market") or "UNKNOWN")
    settlement=canonical_settlement(raw.get("settlement") or raw.get("term"),kind) or "UNKNOWN"
    if not ticker or not kind:
        raise ValueError("Complementary record without identity")
    try: currency=cash_currency(raw.get("currency"))
    except ValueError: currency="UNKNOWN"
    source=str(raw.get("source") or "COMPLEMENTARY").strip().upper()
    metadata=dict(raw.get("raw") if isinstance(raw.get("raw"),dict) else raw)
    metadata["_discovery_source"]=source
    if raw.get("units_per_lot") not in (None,""): metadata["_iol_units_per_lot"]=raw.get("units_per_lot")
    status="AVAILABLE" if currency!="UNKNOWN" and market!="UNKNOWN" and settlement!="UNKNOWN" else "OBSERVED_SHADOW"
    value=dict(ticker=ticker,instrument_type=kind,market=market,currency=currency,settlement=settlement,
               settlement_source=source,description=str(raw.get("description") or ""),last_seen_at=observed_at,
               run_id=run_id,status=status,capability="",raw=metadata)
    value["capability"]=capability(value)
    if status!="AVAILABLE" and str(value["capability"]).startswith("READY_PAPER_"):
        value["capability"]="DISCOVERED_COMPLEMENTARY_METADATA_INCOMPLETE"
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
    if spec.family == "FUTUROS":
        return "READY_PAPER_FUTURES" if spec.market in {"A3","ROFEX"} else "NEEDS_MARKET_EXECUTOR"
    if spec.market != "BYMA":
        return "NEEDS_MARKET_EXECUTOR"
    if spec.family in {"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES"}:
        return "READY_PAPER_SPOT"
    if spec.family == "OPCIONES":
        return "READY_PAPER_OPTIONS"
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
    reason = "" if str(record["capability"]).startswith("READY_PAPER_") else record["capability"]
    if record["status"] != "AVAILABLE":
        reason = "Catálogo no confirmado en la última actualización"
    holiday_reason = rc6_underlying_opening_block(record["ticker"], record["instrument_type"])
    if holiday_reason:
        reason = holiday_reason if not reason else f"{reason}; {holiday_reason}"
    return {"currency": record["currency"], "market": record["market"], "contract": spec,
            "metadata_source": f"PPI_CATALOG:{record['last_seen_at']}", "opening_block_reason": reason}
