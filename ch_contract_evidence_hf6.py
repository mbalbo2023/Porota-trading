"""HF6 contract evidence registry.

This module never invents financial contract data. It records exactly what a
provider returned, which fields are still missing, and who must resolve the
gap. It is safe to run repeatedly and does not promote a financial instrument
by itself.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone


SCHEMA = "porota-contract-evidence-v1"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def init_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS contract_evidence(
          instrument_type TEXT NOT NULL,
          ticker TEXT NOT NULL,
          market TEXT NOT NULL,
          status TEXT NOT NULL,
          owner TEXT NOT NULL,
          source TEXT NOT NULL,
          checked_at TEXT NOT NULL,
          missing_fields_json TEXT NOT NULL,
          evidence_json TEXT NOT NULL,
          detail TEXT NOT NULL,
          PRIMARY KEY(instrument_type,ticker,market)
        );
        CREATE TABLE IF NOT EXISTS contract_evidence_runs(
          run_id TEXT PRIMARY KEY,
          started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL,
          total INTEGER NOT NULL,
          verified INTEGER NOT NULL,
          blocked_porota INTEGER NOT NULL,
          blocked_provider INTEGER NOT NULL,
          detail TEXT NOT NULL
        );
        """)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _save(store, *, family, ticker, market, status, owner, source,
          missing, evidence, detail, checked_at=None):
    checked_at = checked_at or now_iso()
    row = (
        str(family or "UNKNOWN").upper(),
        str(ticker or "*").upper(),
        str(market or "UNKNOWN").upper(),
        str(status), str(owner), str(source), checked_at,
        _json(list(missing or [])), _json(evidence or {}), str(detail)[:2000],
    )
    with store.connect() as c:
        c.execute("""INSERT INTO contract_evidence VALUES(?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(instrument_type,ticker,market) DO UPDATE SET
          status=excluded.status,owner=excluded.owner,source=excluded.source,
          checked_at=excluded.checked_at,missing_fields_json=excluded.missing_fields_json,
          evidence_json=excluded.evidence_json,detail=excluded.detail""", row)


def _catalog_rows(store):
    with store.connect() as c:
        rows = c.execute("""SELECT ticker,instrument_type,market,currency,settlement,
          capability,metadata_json,last_seen_at FROM financial_instrument_catalog
          WHERE status='AVAILABLE' ORDER BY instrument_type,ticker,market,currency,settlement""").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except Exception:
            item["metadata"] = {}
        result.append(item)
    return result


def _family_coverage(store):
    with store.connect() as c:
        return [dict(r) for r in c.execute("SELECT * FROM catalog_family_coverage ORDER BY instrument_type")]


def _bond_estimate(reader, record):
    """Collect documented PPI evidence only; never derives a multiplier."""
    if not hasattr(reader, "estimate_bond"):
        return None, "POROTA_READER_METHOD_MISSING"
    try:
        current = reader.current(record["ticker"], record["instrument_type"], record["settlement"])
        if not isinstance(current, dict):
            return None, "PPI_CURRENT_INVALID_SHAPE"
        price = current.get("price")
        if price is None or float(price) <= 0:
            return None, "PPI_CURRENT_PRICE_MISSING"
        payload = reader.estimate_bond(record["ticker"], price=float(price), quantity=1)
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            return {"request_price": price, "response": payload}, "PPI_BOND_ESTIMATE_INVALID_SHAPE"
        return {"request_price": price, "response": payload}, ""
    except Exception as exc:
        return None, f"{type(exc).__name__}:{str(exc)[:300]}"


def collect(reader, store, *, run_id):
    """Refresh evidence using only currently documented/read-only sources.

    READY status here means only that the existing HF6 contract source was
    already accepted for PAPER. For every other family this module remains
    fail-closed until the exact required fields are provider-backed.
    """
    init_schema(store)
    started = now_iso()
    total = verified = blocked_porota = blocked_provider = 0
    seen_families = set()

    for record in _catalog_rows(store):
        family = str(record["instrument_type"]).upper()
        ticker = str(record["ticker"]).upper()
        market = str(record["market"]).upper()
        seen_families.add(family)
        raw = record.get("metadata") or {}
        generic = {
            "catalog_checked_at": record.get("last_seen_at"),
            "currency": record.get("currency"),
            "settlement": record.get("settlement"),
            "catalog_capability": record.get("capability"),
            "ppi_fields": sorted(map(str, raw.keys())) if isinstance(raw, dict) else [],
            "nominalInPrice": raw.get("nominalInPrice") if isinstance(raw, dict) else None,
            "cajaValoresCode": raw.get("cajaValoresCode") if isinstance(raw, dict) else None,
            "isin": raw.get("isin") if isinstance(raw, dict) else None,
        }
        total += 1

        if family in {"ACCIONES", "CEDEARS"} and record.get("capability") == "READY_PAPER_SPOT":
            verified += 1
            _save(store, family=family, ticker=ticker, market=market,
                  status="VERIFIED_EXISTING_PAPER_CONTRACT", owner="NONE",
                  source="PPI_SEARCH_INSTRUMENT + HF6_BYMA_SPOT_UNIT_POLICY",
                  missing=[], evidence=generic,
                  detail="Contrato PAPER ya habilitado por la política HF6 existente; este registro no cambia su semántica.")
            continue

        if family == "BONOS":
            estimate, error = _bond_estimate(reader, record)
            evidence = dict(generic)
            evidence["ppi_bonds_estimate"] = estimate
            missing = [
                "official_semantics_of_nominalInPrice",
                "verified_cash_quantity_relation_for_execution",
                "explicit_order_cost_budget_for_paper_economics",
            ]
            if error:
                blocked_porota += 1 if error == "POROTA_READER_METHOD_MISSING" else 0
                blocked_provider += 0 if error == "POROTA_READER_METHOD_MISSING" else 1
                owner = "POROTA" if error == "POROTA_READER_METHOD_MISSING" else "PPI_OR_MARKET_DATA"
                status = "COLLECTION_INCOMPLETE"
                detail = "Bonds/Estimate no pudo quedar certificado: " + error
            else:
                blocked_provider += 1
                owner = "PPI_SUPPORT"
                status = "PPI_DOCUMENTED_ESTIMATE_COLLECTED_SEMANTICS_PENDING"
                detail = ("PPI Bonds/Estimate fue recolectado como evidencia, pero Porota no infiere "
                          "un multiplicador desde amountToInvest/quantityTitles ni desde nominalInPrice. "
                          "Falta definición oficial de la unidad de cotización/nominal para ejecución.")
            _save(store, family=family, ticker=ticker, market=market, status=status,
                  owner=owner, source="PPI_SEARCH_INSTRUMENT + PPI_MARKETDATA_BONDS_ESTIMATE",
                  missing=missing, evidence=evidence, detail=detail)
            continue

        if family in {"LETRAS", "ON", "LEBAC", "NOBAC"}:
            blocked_provider += 1
            _save(store, family=family, ticker=ticker, market=market,
                  status="PPI_FIELD_PRESENT_SEMANTICS_UNDOCUMENTED", owner="PPI_SUPPORT",
                  source="PPI_SEARCH_INSTRUMENT",
                  missing=["official_semantics_of_nominalInPrice",
                           "verified_cash_multiplier", "verified_quantity_step"],
                  evidence=generic,
                  detail=("PPI entrega nominalInPrice en SearchInstrument pero la documentación pública "
                          "consultada no define su semántica. No se transforma en multiplicador por nombre."))
            continue

        if family == "OPCIONES":
            blocked_provider += 1
            _save(store, family=family, ticker=ticker, market=market,
                  status="PPI_CONTRACT_FIELDS_NOT_RETURNED", owner="PPI_SUPPORT",
                  source="PPI_SEARCH_INSTRUMENT",
                  missing=["underlying", "option_right", "strike", "expiry", "contract_lot_or_multiplier"],
                  evidence=generic,
                  detail="SearchInstrument identifica la serie pero no devuelve los campos contractuales requeridos por HF6.")
            continue

        if family == "FUTUROS":
            blocked_provider += 1
            _save(store, family=family, ticker=ticker, market=market,
                  status="PPI_CONTRACT_FIELDS_NOT_RETURNED", owner="PPI_SUPPORT_OR_VERIFIED_ROFEX_SOURCE",
                  source="PPI_SEARCH_INSTRUMENT",
                  missing=["contract_multiplier", "expiry", "initial_margin", "maintenance_margin"],
                  evidence=generic,
                  detail=("PPI identifica el futuro pero el payload genérico no contiene multiplicador, "
                          "vencimiento ni márgenes. El adaptador ROFEX sólo puede destrabarlo con datos reales del proveedor."))
            continue

        blocked_porota += 1
        _save(store, family=family, ticker=ticker, market=market,
              status="SPECIALIZED_INTEGRATION_REQUIRED", owner="POROTA",
              source="PPI_SEARCH_INSTRUMENT",
              missing=["family_specific_contract_and_executor"], evidence=generic,
              detail="Familia observada pero sin adaptador contractual/ejecutor PAPER especializado certificado.")

    # Families declared by PPI but with no instruments in the last catalog run.
    for row in _family_coverage(store):
        family = str(row.get("instrument_type") or "UNKNOWN").upper()
        if family in seen_families:
            continue
        total += 1
        discovery = str(row.get("discovery_status") or "")
        if family == "CAUCIONES" and discovery == "EMPTY_FILTER_RESULTS":
            blocked_provider += 1
            status = "PPI_SEARCH_HTTP200_EMPTY_SUPPORT_REQUIRED"
            owner = "PPI_SUPPORT"
            missing = ["official_discovery_endpoint_or_identifier", "currency", "term",
                       "rate", "placement_side", "available_principal", "minimum_principal",
                       "principal_step", "day_count_basis", "maturity", "explicit_cost_budget"]
            detail = ("PPI Configuration declara CAUCIONES y COLOCAR-CAUCION, pero la búsqueda "
                      "documentada usada por HF6 no devuelve instrumentos. Repetir la misma ingesta no completa estos términos.")
        elif discovery == "DECLARED_NO_QUERY":
            blocked_porota += 1
            status = "POROTA_DISCOVERY_NOT_IMPLEMENTED"
            owner = "POROTA"
            missing = ["documented_discovery_query", "family_specific_contract_and_executor"]
            detail = "PPI declara la familia, pero HF6 todavía no ejecuta una consulta de descubrimiento para ella."
        elif discovery == "EMPTY_FILTER_RESULTS":
            blocked_porota += 1
            status = "DISCOVERY_FILTER_EMPTY_REVIEW_REQUIRED"
            owner = "POROTA_THEN_PPI_SUPPORT"
            missing = ["correct_discovery_market_or_identifier"]
            detail = "La consulta documentada respondió sin coincidencias; revisar primero mercado/filtro antes de atribuirlo a PPI."
        else:
            blocked_provider += 1
            status = "DISCOVERY_UNRESOLVED"
            owner = "POROTA_THEN_PPI_SUPPORT"
            missing = ["verified_instrument_identity"]
            detail = "La familia no quedó resuelta en el catálogo actual."
        _save(store, family=family, ticker="*", market="*", status=status,
              owner=owner, source="PPI_CONFIGURATION + CATALOG_QUERY_RESULTS",
              missing=missing, evidence={"coverage": row}, detail=detail)

    finished = now_iso()
    with store.connect() as c:
        c.execute("INSERT OR REPLACE INTO contract_evidence_runs VALUES(?,?,?,?,?,?,?,?)",
                  (str(run_id), started, finished, total, verified,
                   blocked_porota, blocked_provider,
                   f"schema={SCHEMA}; no automatic promotion; provider-backed evidence only"))
    return {
        "schema": SCHEMA, "run_id": str(run_id), "total": total,
        "verified": verified, "blocked_porota": blocked_porota,
        "blocked_provider": blocked_provider, "finished_at": finished,
    }
