"""Read-only discovery probes for PPI-declared families missing from HF6 catalog.

Queries are candidates only. A returned record is persisted as evidence and
never inserted into the trading catalog or promoted to READY_PAPER here.
"""
from __future__ import annotations

import json

import ch_contract_evidence_hf6 as evidence


# These are search candidates already present in HF6's own discovery/watchlist
# logic or literal family words. They are NOT contract data and never enable a
# trade without a provider response.
PROBES = {
    "CAUCIONES": {
        "markets": ("BYMA",),
        "queries": (("CAUCION", "CAUCION"), ("PESOS", "PESOS"), ("DOLAR", "DOLAR")),
    },
    "ETF": {
        "markets": ("NYSE", "NASDAQ", "BYMA"),
        "queries": tuple((x, x) for x in ("SPY", "DIA", "QQQ", "IWM")),
    },
    "ACCIONES-USA": {
        "markets": ("NYSE", "NASDAQ"),
        "queries": tuple((x, x) for x in (
            "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
            "JPM", "BAC", "V", "MA", "XOM", "CVX"
        )),
    },
    "FCI": {
        "markets": ("BYMA", "OTC"),
        "queries": (("FONDO", "FONDO"), ("FCI", "FCI")),
    },
    "FCI-EXTERIOR": {
        "markets": ("OTC", "NYSE", "NASDAQ"),
        "queries": (("FONDO", "FONDO"), ("FCI", "FCI")),
    },
    "LEBAC": {"markets": ("BYMA",), "queries": (("LEBAC", "LEBAC"),)},
    "NOBAC": {"markets": ("BYMA",), "queries": (("NOBAC", "NOBAC"),)},
    "LICITACIONES": {"markets": ("BYMA",), "queries": (("LICITACION", "LICITACION"),)},
}


def _configuration(store):
    with store.connect() as c:
        rows = c.execute("SELECT name,payload_json FROM broker_market_configuration").fetchall()
    result = {}
    for name, payload in rows:
        try:
            result[str(name)] = json.loads(payload or "[]")
        except Exception:
            result[str(name)] = []
    return result


def probe(reader, store):
    config = _configuration(store)
    declared = set(config.get("instrument_types") or [])
    markets_available = set(config.get("markets") or [])
    summary = {}

    for family, spec in PROBES.items():
        if family not in declared:
            continue
        attempts = []
        found = []
        for market in spec["markets"]:
            if market not in markets_available:
                continue
            for ticker, name in spec["queries"]:
                try:
                    rows = reader.search_instruments(ticker, family, name=name, market=market)
                    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                        raise ValueError("PPI_SEARCH_INVALID_SHAPE")
                    attempts.append({"ticker": ticker, "name": name, "market": market,
                                     "status": "OK", "count": len(rows)})
                    for row in rows:
                        copy = dict(row)
                        copy["_query_ticker"] = ticker
                        copy["_query_name"] = name
                        copy["_query_market"] = market
                        found.append(copy)
                except Exception as exc:
                    attempts.append({"ticker": ticker, "name": name, "market": market,
                                     "status": type(exc).__name__, "count": 0,
                                     "detail": str(exc)[:240]})

        # Deduplicate provider identities without interpreting their fields.
        identities = {}
        for row in found:
            key = (
                str(row.get("ticker") or row.get("symbol") or ""),
                str(row.get("type") or row.get("instrumentType") or ""),
                str(row.get("market") or ""),
                str(row.get("currency") or ""),
                str(row.get("isin") or ""),
            )
            identities[key] = row
        found = list(identities.values())

        if found:
            status = "PPI_DISCOVERY_CONFIRMED_SPECIALIZED_EXECUTOR_PENDING"
            owner = "POROTA"
            missing = ["family_specific_contract_validation", "family_specific_paper_executor"]
            detail = (f"PPI devolvió {len(found)} identidad(es) en las sondas ampliadas. "
                      "Esto corrige la ausencia de discovery, pero no convierte la familia en operable sin contrato/ejecutor especializado.")
        else:
            status = ("PPI_SEARCH_HTTP200_EMPTY_SUPPORT_REQUIRED" if family == "CAUCIONES"
                      else "PPI_MULTI_MARKET_DISCOVERY_EMPTY_SUPPORT_REQUIRED")
            owner = "PPI_SUPPORT"
            missing = (["official_discovery_endpoint_or_identifier", "currency", "term", "rate",
                        "placement_side", "available_principal", "minimum_principal", "principal_step",
                        "day_count_basis", "maturity", "explicit_cost_budget"]
                       if family == "CAUCIONES" else ["official_discovery_endpoint_or_identifier"])
            detail = ("Las consultas de descubrimiento documentadas y explícitas no devolvieron instrumentos. "
                      "Esperar más tiempo no cambia el contrato de consulta; se requiere confirmación de PPI si no existe otro endpoint/identificador.")

        evidence._save(
            store, family=family, ticker="*", market="MULTI" if len(spec["markets"]) > 1 else spec["markets"][0],
            status=status, owner=owner, source="PPI_SEARCH_INSTRUMENT_READONLY_PROBE",
            missing=missing,
            evidence={"attempts": attempts, "provider_records": found},
            detail=detail,
        )
        summary[family] = {"status": status, "owner": owner,
                           "attempts": len(attempts), "records": len(found)}
    return summary
