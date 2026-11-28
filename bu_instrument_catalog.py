"""Catálogo financiero derivado de payloads PPI, con procedencia explícita.

Las etiquetas de moneda se contrastaron con el diagnóstico del 27/08/2026.
SearchInstrument no prueba multiplicador, margen ni vencimiento estructurado.
"""

import json
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from bs_instrument_contracts import InstrumentContract, cash_currency, family_name, contract_from_metadata
from rc6_multisource_discovery import canonical_family, canonical_market, canonical_settlement

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")
SOURCE_PRECEDENCE = ("PPI_PRIMARY", "IOL_COMPLEMENTARY", "BYMA_PUBLIC_COMPLEMENTARY")
PAPER_PPI_IDENTITY_LKG_SECONDS = 14 * 86400
US_MARKET_WIDE_CEDEAR_HOLIDAYS = {
    "2026-09-07": "US_LABOR_DAY",
}


@dataclass(frozen=True)
class PaperCaucionCapability:
    family: str
    market: str


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
        CREATE INDEX IF NOT EXISTS idx_shadow_catalog_order
          ON financial_instrument_catalog(instrument_type,market,currency,ticker,
            settlement,status,capability);
        -- The audit projection includes source/clocks, unlike the SHADOW
        -- identity index. Build once during schema preparation, never in a
        -- bounded read-only HOT query. Large metadata_json stays out of it.
        CREATE INDEX IF NOT EXISTS idx_catalog_public_metadata_v1
          ON financial_instrument_catalog(instrument_type,market,currency,ticker,
            settlement,status,capability,settlement_source,last_seen_at);
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
                 run_id=run_id, status="OBSERVED_SHADOW" if kind == "INDICES" else "AVAILABLE",
                 capability="", raw=raw)
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
    metadata=dict(raw.get("raw") if isinstance(raw.get("raw"),dict) else {})
    # Preserve normalized complementary contract evidence at top level. It is
    # considered only when no PPI-primary identity already exists.
    for key,value in raw.items():
        if key not in {"raw"} and value not in (None,""):
            metadata[key]=value
    metadata["_discovery_source"]=source
    if raw.get("units_per_lot") not in (None,""): metadata["_iol_units_per_lot"]=raw.get("units_per_lot")
    status="AVAILABLE" if currency!="UNKNOWN" and market!="UNKNOWN" and settlement!="UNKNOWN" else "OBSERVED_SHADOW"
    source_seen = raw.get("observed_at") or raw.get("provider_observed_at") or observed_at
    value=dict(ticker=ticker,instrument_type=kind,market=market,currency=currency,settlement=settlement,
               settlement_source=source,description=str(raw.get("description") or ""),last_seen_at=source_seen,
               run_id=run_id,status=status,capability="",raw=metadata)
    value["capability"]=capability(value)
    if status!="AVAILABLE" and str(value["capability"]).startswith("READY_PAPER_"):
        value["capability"]="DISCOVERED_COMPLEMENTARY_METADATA_INCOMPLETE"
    return value

def _option_iol_contract_unverified(raw):
    """Detect an already persisted option contract derived from IOL alone."""
    if not isinstance(raw,dict):
        return False
    contract=raw.get("financial_contract_v17")
    if not isinstance(contract,dict) or not contract:
        return False
    origin=str(raw.get("_contract_complement_source") or "").upper()
    discovery=str(raw.get("_discovery_source") or "").upper()
    declared=str(contract.get("metadata_source") or "").upper()
    if (origin=="IOL_COMPLEMENTARY" or discovery=="IOL_COMPLEMENTARY"
            or declared.startswith("IOL_OPTIONS_CHAIN")):
        return True
    provenance=contract.get("field_provenance")
    return (isinstance(provenance,dict)
            and any(isinstance(p,dict) and p.get("source_class")=="IOL_STRUCTURED_API"
                    for p in provenance.values()))


def contract_for(record):
    if record["currency"] == "UNKNOWN" or record["market"] == "UNKNOWN":
        raise ValueError("MISSING_CURRENCY_OR_MARKET")
    family = family_name(record["instrument_type"])
    raw = record.get("raw", {})
    if family == "OPCIONES" and _option_iol_contract_unverified(raw):
        raise ValueError("OPTION_IOL_SERIES_CONTRACT_UNVERIFIED")
    from rc6_contract_bridge import hard_blocked_bridge
    if hard_blocked_bridge(raw.get("_contract_bridge")):
        raise ValueError("CONTRACT_EVIDENCE_REVIEW_REQUIRED")
    if raw.get("_contract_conflicts"):
        raise ValueError("CONTRACT_SOURCE_CONFLICT")
    if family == "FCI" and raw.get("paper_family_contract_v1"):
        from rc6_paper_family_lifecycle import fund_terms_from_metadata
        return fund_terms_from_metadata(record["ticker"], raw["paper_family_contract_v1"])
    if family == "CAUCIONES" and raw.get("paper_caucion_contract_v1"):
        return PaperCaucionCapability(family="CAUCIONES", market=record["market"])
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
    if record["instrument_type"] == "INDICES":
        return "INDEX_REFERENCE_ONLY"
    try:
        spec = contract_for(record)
    except ValueError as exc:
        return str(exc)
    if spec.family == "FUTUROS":
        return "READY_PAPER_FUTURES" if spec.market in {"A3","ROFEX"} else "NEEDS_MARKET_EXECUTOR"
    if spec.family == "FCI":
        # FCI PAPER subscriptions are internal amount-ledger lifecycle events;
        # the PPI catalogue commonly labels their venue BYMA.  No real broker
        # route is selected from this field.
        return ("READY_PAPER_FCI_SUBSCRIPTION"
                if spec.market in {"FCI", "BYMA"} else "NEEDS_MARKET_EXECUTOR")
    if spec.family == "CAUCIONES":
        return ("READY_PAPER_CAUCION_PLACING"
                if spec.market == "BYMA" else "NEEDS_MARKET_EXECUTOR")
    if spec.market != "BYMA":
        return "NEEDS_MARKET_EXECUTOR"
    if spec.family in {"ACCIONES", "CEDEARS", "ETFS", "BONOS", "LETRAS", "OBLIGACIONES"}:
        return "READY_PAPER_SPOT"
    if spec.family == "OPCIONES":
        # Long options now have a dedicated PAPER risk path: explicit contract
        # lot/expiry/strike/right, full-premium maximum-loss sizing, T+0
        # settlement and expiry-day session cutoff. This never authorizes a
        # short option or a real broker order.
        return "READY_PAPER_OPTION_LONG" if spec.market == "BYMA" else "NEEDS_MARKET_EXECUTOR"
    return "NEEDS_SPECIALIZED_EXECUTOR"


def complementary_is_fresh(complementary, *, max_age_seconds=86400, now=None):
    """Contract metadata may be slower-moving than quotes, but must be dated."""
    if not isinstance(complementary, dict):
        return False
    stamp = complementary.get("observed_at") or complementary.get("provider_observed_at")
    if not stamp:
        return False
    try:
        at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        if at.tzinfo is None:
            at = at.replace(tzinfo=ZoneInfo("UTC"))
        ref = now or datetime.now(ZoneInfo("UTC"))
        if ref.tzinfo is None:
            ref = ref.replace(tzinfo=ZoneInfo("UTC"))
        age = (ref.astimezone(ZoneInfo("UTC")) - at.astimezone(ZoneInfo("UTC"))).total_seconds()
        return 0 <= age <= int(max_age_seconds)
    except (TypeError, ValueError, OverflowError):
        return False



def complement_source(complementary):
    source=str((complementary or {}).get("source") or "COMPLEMENTARY").strip().upper()
    return source


def complement_matches_primary(primary, complementary):
    """Match a complement without letting it redefine the PPI identity."""
    if not isinstance(primary, dict) or not isinstance(complementary, dict):
        return False
    family=canonical_family(complementary.get("instrument_type") or
                            complementary.get("asset_type") or complementary.get("family"))
    ticker=str(complementary.get("ticker") or complementary.get("symbol") or "").strip().upper()
    if ticker != str(primary.get("ticker") or "").strip().upper():
        return False
    if family != canonical_family(primary.get("instrument_type")):
        return False

    evidence=complementary.get("identity_evidence")
    evidence=evidence if isinstance(evidence,dict) else {}
    market_explicit=evidence.get("market_explicit")
    currency_explicit=evidence.get("currency_explicit")
    settlement_explicit=evidence.get("settlement_explicit")
    if market_explicit is None:
        market_explicit=bool(complementary.get("market"))
    if currency_explicit is None:
        currency_explicit=bool(complementary.get("currency"))
    if settlement_explicit is None:
        settlement_explicit=bool(complementary.get("settlement") or complementary.get("term"))

    if market_explicit:
        if canonical_market(complementary.get("market")) != canonical_market(primary.get("market")):
            return False
    if currency_explicit:
        try:
            observed_currency=cash_currency(complementary.get("currency"))
        except ValueError:
            return False
        if observed_currency != str(primary.get("currency") or "").upper():
            return False
    if settlement_explicit:
        observed_settlement=canonical_settlement(
            complementary.get("settlement") or complementary.get("term"), family)
        expected_settlement=canonical_settlement(
            primary.get("settlement"), primary.get("instrument_type"))
        if observed_settlement != expected_settlement:
            return False
    return True


def _source_timestamp(complementary):
    return ((complementary or {}).get("observed_at")
            or (complementary or {}).get("provider_observed_at"))


def _max_timestamp(*values):
    parsed=[]
    for value in values:
        if not value:
            continue
        try:
            item=datetime.fromisoformat(str(value).replace("Z","+00:00"))
            if item.tzinfo is None:
                item=item.replace(tzinfo=ZoneInfo("UTC"))
            parsed.append((item.astimezone(ZoneInfo("UTC")),str(value)))
        except (TypeError,ValueError,OverflowError):
            continue
    return max(parsed)[1] if parsed else None


def complete_with_complement(record, complementary):
    """Apply complements strictly as PPI -> IOL -> BYMA gap filling."""
    if record is None or not isinstance(complementary, dict):
        return record
    primary=dict(record)
    raw=dict(primary.get("raw") or {})
    if not complement_matches_primary(primary, complementary):
        return primary

    source=complement_source(complementary)
    if complementary.get("contract_bridge"):
        raw["_contract_bridge"] = complementary["contract_bridge"]
    contract=complementary.get("financial_contract_v17")
    existing_contract=raw.get("financial_contract_v17")
    if isinstance(contract,dict) and contract and not existing_contract:
        raw["financial_contract_v17"]=contract
        raw["_contract_complement_source"]=source
    elif isinstance(contract,dict) and contract and existing_contract != contract:
        ignored=list(raw.get("_ignored_lower_priority_contract_sources") or [])
        if source not in ignored:
            ignored.append(source)
        raw["_ignored_lower_priority_contract_sources"]=ignored
        essential = {"currency", "market", "settlement", "cash_multiplier", "quantity_step", "minimum_quantity", "expires_at", "underlying", "strike", "option_right", "initial_margin", "maintenance_margin"}
        numeric = {"cash_multiplier", "quantity_step", "minimum_quantity", "strike", "initial_margin", "maintenance_margin"}
        def same_term(k):
            try:
                return Decimal(str(contract[k])) == Decimal(str(existing_contract[k])) if k in numeric else str(contract[k]) == str(existing_contract[k])
            except (ValueError, ArithmeticError):
                return False
        changed = sorted(k for k in essential if k in contract and k in existing_contract and not same_term(k))
        if changed:
            raw["_contract_conflicts"] = {"fields": changed, "source": source, "incoming": contract, "existing": existing_contract}
    family_contract = complementary.get("paper_family_contract_v1")
    existing_family_contract = raw.get("paper_family_contract_v1")
    if isinstance(family_contract, dict) and family_contract and not existing_family_contract:
        raw["paper_family_contract_v1"] = family_contract
        raw["_contract_complement_source"] = source
    elif (isinstance(family_contract, dict) and family_contract
          and existing_family_contract != family_contract):
        from rc6_contract_bridge import same_bound_contract_version
        if (canonical_family(primary["instrument_type"]) == "FCI"
                and raw.get("_contract_complement_source") == source == "CONTRACT_EVIDENCE_V2"
                and same_bound_contract_version(existing_family_contract, family_contract)):
            raw["paper_family_contract_v1"] = family_contract
        else:
            raw["_contract_conflicts"] = {
                "fields": ["paper_family_contract_v1"], "source": source,
                "incoming": family_contract, "existing": existing_family_contract,
            }
    caucion_contract = complementary.get("paper_caucion_contract_v1")
    existing_caucion_contract = raw.get("paper_caucion_contract_v1")
    if isinstance(caucion_contract, dict) and caucion_contract and not existing_caucion_contract:
        raw["paper_caucion_contract_v1"] = caucion_contract
        raw["_contract_complement_source"] = source
    elif (isinstance(caucion_contract, dict) and caucion_contract
          and existing_caucion_contract != caucion_contract):
        from rc6_contract_bridge import is_static_caucion_policy, same_caucion_policy_version
        if (raw.get("_contract_complement_source") == source == "CONTRACT_EVIDENCE_V2"
                and is_static_caucion_policy(complementary)
                and same_caucion_policy_version(existing_caucion_contract, caucion_contract)):
            raw["paper_caucion_contract_v1"] = caucion_contract
        else:
            raw["_contract_conflicts"] = {
                "fields": ["paper_caucion_contract_v1"], "source": source,
                "incoming": caucion_contract, "existing": existing_caucion_contract,
            }

    stamp=_source_timestamp(complementary)
    freshness=dict(raw.get("_freshness_by_source") or {})
    if stamp:
        freshness[source]=str(stamp)
        raw["_freshness_by_source"]=freshness
        raw["_effective_observed_at"]=_max_timestamp(
            primary.get("last_seen_at"), *freshness.values())

    applied=list(raw.get("_applied_complement_sources") or [])
    if source not in applied:
        applied.append(source)
    applied.sort(key=lambda value: (
        SOURCE_PRECEDENCE.index(value) if value in SOURCE_PRECEDENCE else len(SOURCE_PRECEDENCE),
        value))
    raw["_applied_complement_sources"]=applied
    raw["_source_precedence"]="PPI_PRIMARY>IOL_COMPLEMENTARY>BYMA_PUBLIC_COMPLEMENTARY"

    primary["raw"]=raw
    primary["capability"]=capability(primary)
    if str(primary["capability"]).startswith("READY_PAPER_"):
        primary["status"]="AVAILABLE"
        chain=["PPI_PRIMARY"] + [s for s in applied if s != "PPI_PRIMARY"]
        raw["_availability_source"]="+".join(chain)
    return primary


def _candidate_timestamp_is_fresh(value, checked_at, max_age_seconds):
    try:
        observed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        current = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=ZoneInfo("UTC"))
        if current.tzinfo is None:
            current = current.replace(tzinfo=ZoneInfo("UTC"))
        age = (current.astimezone(ZoneInfo("UTC"))
               - observed.astimezone(ZoneInfo("UTC"))).total_seconds()
        return age >= 0 and (max_age_seconds is None or age <= int(max_age_seconds))
    except (TypeError, ValueError, OverflowError):
        return False


def _candidate_has_ppi_primary(settlement_source, metadata_json):
    try:
        metadata = json.loads(str(metadata_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        metadata = {}
    metadata = metadata if isinstance(metadata, dict) else {}
    discovery = str(metadata.get("_discovery_source") or "").upper()
    availability = str(metadata.get("_availability_source") or "").upper()
    source = str(settlement_source or "").upper()
    return (discovery in {"PPI_PRIMARY", "LEGACY_CATALOG"}
            or availability.startswith("PPI_PRIMARY")
            or source in {"PPI_FIELD", "REQUEST_CANDIDATE"})


def ppi_full_key_evidence_reason(record, *, database=None, now=None):
    """Distinguish PPI discovery from an attested executable five-key.

    SearchInstrument may omit settlement. Preserve that raw provenance.
    A live, signed Gateway Current/Book pair can bind an exact requested
    settlement to an unambiguous PPI-primary catalog identity.
    """
    if not isinstance(record, dict):
        return "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"
    source = str(record.get("settlement_source") or "").upper()
    if source not in {"PPI_FIELD", "REQUEST_CANDIDATE"}:
        return "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"
    raw = record.get("raw")
    if not isinstance(raw, dict):
        try:
            raw = json.loads(record.get("metadata_json") or "{}")
        except (ValueError, TypeError, json.JSONDecodeError):
            raw = {}
    if source == "REQUEST_CANDIDATE":
        from rc6_ppi_native_request_receipt import FIELD, verified_receipt

        proof = raw.get(FIELD) if isinstance(raw, dict) else None
        if proof is None:
            return "PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED"
        key = tuple(record.get(name) for name in (
            "ticker", "instrument_type", "market", "currency", "settlement"))
        try:
            four = (str(raw.get("ticker") or raw.get("symbol") or "").strip().upper(),
                    canonical_family(raw.get("type") or raw.get("instrumentType")),
                    canonical_market(raw.get("market")), cash_currency(raw.get("currency")))
            valid_primary = (raw.get("_discovery_source") == "PPI_PRIMARY"
                             and raw.get("settlement") in (None, "")
                             and four == key[:4]
                             and canonical_settlement(key[4], key[1]) == key[4])
        except (ValueError, TypeError):
            valid_primary = False
        if valid_primary and verified_receipt(proof, key, database=database, now=now,
                                             maximum_age_seconds=PAPER_PPI_IDENTITY_LKG_SECONDS):
            return ""
        return "PPI_NATIVE_SETTLEMENT_RECEIPT_INVALID"
    if not isinstance(raw, dict) or str(raw.get("_discovery_source") or "").upper() != "PPI_PRIMARY":
        return "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"
    literal = (raw.get("ticker") or raw.get("symbol"),
               raw.get("type") or raw.get("instrumentType"),
               raw.get("market"), raw.get("currency"), raw.get("settlement"))
    if any(value in (None, "") for value in literal):
        return "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"
    try:
        observed = (str(literal[0]).strip().upper(), canonical_family(literal[1]),
                    canonical_market(literal[2]), cash_currency(literal[3]),
                    canonical_settlement(literal[4], literal[1]))
        expected = (str(record.get("ticker") or "").strip().upper(),
                    canonical_family(record.get("instrument_type")),
                    canonical_market(record.get("market")),
                    str(record.get("currency") or "").strip().upper(),
                    canonical_settlement(record.get("settlement"), record.get("instrument_type")))
    except (ValueError, TypeError):
        return "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"
    return "" if observed == expected else "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"


def retain_ppi_settlement_witness(previous, incoming):
    """Keep a verified PPI witness when a search only omits settlement.

    SearchInstrument queries overlap and can later replay the same instrument
    without settlement. Retention preserves the original provider record and
    receipt clock. New literal data or material changes follow normal publication.
    """
    if (incoming.get("settlement_source") != "REQUEST_CANDIDATE" or not previous
            or previous.get("status") != "AVAILABLE" or incoming.get("status") != "AVAILABLE"
            or incoming.get("capability") in {"TYPE_NOT_ENUMERATED", "MARKET_NOT_ENUMERATED"}
            or ppi_full_key_evidence_reason(previous)
            or not _candidate_timestamp_is_fresh(previous.get("last_seen_at"),
                    incoming.get("last_seen_at"), PAPER_PPI_IDENTITY_LKG_SECONDS)):
        return False
    coordinates = ("ticker", "instrument_type", "market", "currency", "settlement")
    key = tuple(incoming[field] for field in coordinates)
    raw = incoming["raw"]
    if (tuple(previous[field] for field in coordinates) != key
            or raw.get("_discovery_source") != "PPI_PRIMARY"
            or raw.get("settlement") not in (None, "")
            or canonical_family(raw.get("_provider_instrument_type")) != key[1]):
        return False
    from rc6_ppi_global_budget import assert_native_ppi_response_identity
    try:
        assert_native_ppi_response_identity(raw, key)
    except ValueError:
        return False
    witness = previous.get("raw")
    if not isinstance(witness, dict):
        witness = json.loads(previous["metadata_json"])
    search_fields = {"ticker", "symbol", "type", "instrumentType", "market", "currency",
                     "settlement", "_discovery_source", "_provider_instrument_type"}
    if key[1] != "OPCIONES":
        search_fields.add("description")
    return all(field in search_fields or (field in witness and witness[field] == value)
               for field, value in raw.items())


def scanner_diagnostic_aliases_sql(connection):
    """Recognize obsolete Spot placeholders without relaxing request uniqueness.

    Keep the catalog and ledger rows intact for diagnostics. Only a unique,
    incomplete complementary sibling that both views have retired can leave the census.
    Any known coordinate must agree with an available, PPI-attested five-key.
    Older snapshots without provenance columns still count all aliases.
    """
    columns = {row[1] for row in connection.execute("PRAGMA table_info(financial_instrument_catalog)")}
    if not {"metadata_json", "settlement_source"} <= columns:
        return "SELECT NULL ticker,NULL instrument_type,NULL market,NULL currency,NULL settlement WHERE 0"

    def attested(ticker, family, market, currency, settlement, settlement_source, metadata_json):
        return not ppi_full_key_evidence_reason({
            "ticker": ticker, "instrument_type": family, "market": market, "currency": currency,
            "settlement": settlement, "settlement_source": settlement_source, "metadata_json": metadata_json,
        })

    connection.create_function("rc6_scanner_ppi_primary", 2, _candidate_has_ppi_primary, deterministic=True)
    connection.create_function("rc6_scanner_ppi_attested", 7, attested, deterministic=True)
    return """SELECT h.ticker,h.instrument_type,h.market,h.currency,h.settlement
      FROM financial_instrument_catalog h JOIN candidate_identity_v2 p
        USING(ticker,instrument_type,market,currency,settlement)
      WHERE h.instrument_type IN ('ACCIONES','CEDEARS','ETFS')
        AND h.status='STALE' AND h.capability='MISSING_CURRENCY_OR_MARKET'
        AND p.status='STALE' AND p.can_simulate=0
        AND (h.market IN ('','UNKNOWN','NO_VERIFICADO') OR h.currency IN ('','UNKNOWN','NO_VERIFICADO'))
        AND CASE WHEN json_valid(h.metadata_json) THEN
          UPPER(h.settlement_source) IN ('IOL_COMPLEMENTARY','BYMA_PUBLIC_COMPLEMENTARY')
          AND UPPER(json_extract(h.metadata_json,'$._discovery_source'))=UPPER(h.settlement_source)
          AND NOT rc6_scanner_ppi_primary(h.settlement_source,h.metadata_json)
        ELSE 0 END
        AND (SELECT COUNT(*) FROM financial_instrument_catalog d
          WHERE (d.ticker,d.instrument_type,d.market,d.currency,d.settlement)=
                (h.ticker,h.instrument_type,h.market,h.currency,h.settlement))=1
        AND (SELECT COUNT(*) FROM candidate_identity_v2 d
          WHERE (d.ticker,d.instrument_type,d.market,d.currency,d.settlement)=
                (h.ticker,h.instrument_type,h.market,h.currency,h.settlement))=1
        AND EXISTS (SELECT 1 FROM financial_instrument_catalog f
          WHERE f.ticker=h.ticker AND f.instrument_type=h.instrument_type AND f.settlement=h.settlement
            AND f.status='AVAILABLE' AND f.capability='READY_PAPER_SPOT'
            AND (h.market IN ('','UNKNOWN','NO_VERIFICADO') OR h.market=f.market)
            AND (h.currency IN ('','UNKNOWN','NO_VERIFICADO') OR h.currency=f.currency)
            AND rc6_scanner_ppi_attested(f.ticker,f.instrument_type,f.market,f.currency,
                f.settlement,f.settlement_source,f.metadata_json))"""


def ppi_option_full_key_evidence_reason(record):
    """OPTION-only alias consistency, including its underlying's identity.

    Keep the shared guard unchanged: external Watch/services may import it and
    are separately owned. Only option-policy callers opt into this pure wrapper.
    """
    reason = ppi_full_key_evidence_reason(record)
    if reason:
        return reason
    raw = record.get("raw")
    if not isinstance(raw, dict):
        raw = json.loads(record.get("metadata_json") or "{}")
    try:
        for names, normalize, wanted in (
            (("ticker", "symbol"), lambda value: value.strip().upper(),
             str(record.get("ticker") or "").strip().upper()),
            (("type", "instrumentType", "_provider_instrument_type"), canonical_family,
             canonical_family(record.get("instrument_type"))),
        ):
            for name in names:
                value = raw.get(name)
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                if not isinstance(value, str) or normalize(value) != wanted:
                    return "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"
    except (ValueError, TypeError):
        return "PPI_FULL_KEY_NOT_PROVIDER_ATTESTED"
    return ""


def _candidate_retry_ambiguities(connection, only_instrument=None):
    table = connection.execute("""SELECT 1 FROM sqlite_master
      WHERE type='table' AND name='complementary_contract_retry'""").fetchone()
    if not table:
        return set()
    columns = {
        str(row[1]) for row in connection.execute(
            "PRAGMA table_info(complementary_contract_retry)"
        ).fetchall()
    }
    required = {"ticker", "instrument_type", "market", "currency", "settlement", "reason"}
    if not required.issubset(columns):
        return set()
    where = "" if only_instrument is None else " WHERE ticker=? AND instrument_type=?"
    return {
        tuple(str(value or "").strip().upper() for value in row[:5])
        for row in connection.execute("""SELECT ticker,instrument_type,market,currency,
          settlement,reason FROM complementary_contract_retry""" + where,
          only_instrument or ())
        if "AMBIG" in str(row[5] or "").upper()
    }


def sync_candidate_universe(connection, checked_at, *,
                            freshness_seconds=PAPER_PPI_IDENTITY_LKG_SECONDS,
                            bounded_batch=None, only_instrument=None, database=None):
    """Atomically rebuild the legacy summary without projecting ambiguity to READY.

    ``candidate_universe`` intentionally remains keyed by ticker/family/market for
    compatibility.  It is therefore only simulation-ready when that summary maps
    to exactly one complete, fresh PPI-primary catalog identity.  Every other
    projection fails closed.
    """
    if only_instrument is not None and bounded_batch is not None:
        raise ValueError("PARTIAL_INSTRUMENT_REQUIRES_ATOMIC_PUBLICATION")
    where = "" if only_instrument is None else " WHERE ticker=? AND instrument_type=?"
    params = () if only_instrument is None else only_instrument
    rows = connection.execute("""SELECT ticker,instrument_type,market,currency,
      settlement,settlement_source,status,capability,last_seen_at,metadata_json
      FROM financial_instrument_catalog""" + where + """
      ORDER BY ticker,instrument_type,market,currency,settlement,last_seen_at""", params).fetchall()
    wire_aliases = {}
    for row in rows:
        if (row[6] in {"AVAILABLE", "STALE"}
                and all(row[i] not in ("", "UNKNOWN", "NO_VERIFICADO", None) for i in (2, 3))):
            wire_aliases.setdefault(tuple(row[:2]), set()).add(tuple(row[2:4]))
    grouped = {}
    for row in rows:
        ticker, family, market = (str(row[index] or "").strip().upper()
                                  for index in (0, 1, 2))
        grouped.setdefault((ticker, family, market), []).append(row)
    retry_ambiguities = _candidate_retry_ambiguities(connection, only_instrument)

    def explicit_projection(family, reasons, fallback_status, detail):
        """Classify WS15 residual families without a generic pending bucket."""
        family = str(family or "").upper()
        if not reasons:
            return "AVAILABLE", detail
        ambiguous = any("AMBIG" in str(reason).upper() for reason in reasons)
        if family == "OPCIONES":
            code = "OPTION_CONTRACT_UNRESOLVED"
        elif family == "FUTUROS":
            code = "FUTURES_CONTRACT_OR_PAPER_MARGIN_UNRESOLVED"
        elif family in {"ON", "OBLIGACIONES"}:
            code = ("ON_IDENTITY_AMBIGUOUS" if ambiguous
                    else "ON_CONTRACT_UNRESOLVED")
        else:
            return str(fallback_status or "STALE"), detail
        rendered = ";".join(str(reason) for reason in reasons)
        return "PAUSED_EXPLICIT", "PAUSED_EXPLICIT:" + code + ";" + rendered

    def identity_reasons(row):
        identity_key = tuple(str(value or "").strip().upper()
                             for value in row[:5])
        reasons = []
        has_ppi_discovery = _candidate_has_ppi_primary(row[5], row[9])
        if not has_ppi_discovery:
            reasons.append("PPI_PRIMARY_IDENTITY_NOT_VERIFIED")
        full_key_reason = ppi_full_key_evidence_reason({
            "ticker": row[0], "instrument_type": row[1], "market": row[2],
            "currency": row[3], "settlement": row[4],
            "settlement_source": row[5], "metadata_json": row[9]},
            database=database, now=checked_at)
        if has_ppi_discovery and full_key_reason:
            reasons.append(full_key_reason)
        if (row[5] == "REQUEST_CANDIDATE" and not full_key_reason
                and len(wire_aliases.get(tuple(row[:2]), ())) != 1):
            reasons.append("PPI_NATIVE_SETTLEMENT_IDENTITY_AMBIGUOUS")
        if any(value in {"", "UNKNOWN", "NO_VERIFICADO"}
               for value in identity_key):
            reasons.append("IDENTITY_INCOMPLETE")
        if not _candidate_timestamp_is_fresh(row[8], checked_at, freshness_seconds):
            reasons.append("PPI_FRESHNESS_STALE")
        if row[6] != "AVAILABLE":
            reasons.append("CATALOG_STATUS:" + str(row[6]))
        if not str(row[7]).startswith("READY_PAPER_"):
            reasons.append("CAPABILITY:" + str(row[7]))
        if str(row[1]).upper() == "OPCIONES":
            try:
                stored_raw=json.loads(row[9] or "{}")
            except (TypeError,ValueError):
                stored_raw={}
            if _option_iol_contract_unverified(stored_raw):
                reasons.append("OPTION_IOL_SERIES_CONTRACT_UNVERIFIED")
        if identity_key in retry_ambiguities:
            reasons.append("RETRY_IDENTITY_AMBIGUOUS")
        return list(dict.fromkeys(reasons))

    projected = []
    for key, identities in sorted(grouped.items()):
        ticker, family, market = key
        primary_identities = [
            row for row in identities
            if _candidate_has_ppi_primary(row[5], row[9])
        ]
        ready_identities = [row for row in primary_identities
                            if not identity_reasons(row)]
        # The legacy row may represent one uniquely READY full identity even
        # when blocked siblings share its short ticker.  Full-key v2 remains
        # authoritative; two READY siblings are a real unresolved selection.
        representative = max(ready_identities or primary_identities or identities,
                             key=lambda row: str(row[8] or ""))
        currency, settlement, settlement_source = representative[3:6]
        status, capability, last_seen, metadata_json = representative[6:10]
        reasons = identity_reasons(representative)
        if len(ready_identities) > 1:
            reasons.insert(0, "IDENTITY_AMBIGUOUS")
        elif not ready_identities and len(primary_identities) > 1:
            reasons.insert(0, "IDENTITY_AMBIGUOUS")
        reasons = list(dict.fromkeys(reasons))
        ready = not reasons
        detail = (str(capability) if ready or not str(capability or "").startswith("READY_PAPER_")
                  else ";".join(reasons))
        projected_status, detail = explicit_projection(
            family, reasons, status, detail)
        projected.append((
            ticker, family, str(settlement or "NO_VERIFICADO"), market,
            int(ready), projected_status, detail, checked_at,
        ))

    # Compute both projections before taking a writer lock in bounded mode.
    full = []
    for row in rows:
        key = tuple(row[:5])
        reasons = identity_reasons(row)
        base_detail = ";".join(reasons) or row[7]
        projected_status, detail = explicit_projection(
            row[1], reasons, row[6], base_detail)
        full.append((*key,int(not reasons),projected_status,detail,checked_at))

    connection.execute("""CREATE TABLE IF NOT EXISTS candidate_identity_v2(
        ticker TEXT NOT NULL,instrument_type TEXT NOT NULL,market TEXT NOT NULL,
        currency TEXT NOT NULL,settlement TEXT NOT NULL,can_simulate INTEGER NOT NULL,
        status TEXT NOT NULL,detail TEXT NOT NULL,checked_at TEXT NOT NULL,
        PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")

    if bounded_batch is None:
        # Existing callers retain the original atomic full rebuild contract.
        connection.execute("DELETE FROM candidate_universe" + where, params)
        connection.executemany(
            "INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)", projected)
        connection.execute("DELETE FROM candidate_identity_v2" + where, params)
        connection.executemany(
            "INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)", full)
        return
    if not 1 <= bounded_batch <= 128 or connection.in_transaction:
        raise ValueError("BOUNDED_CANDIDATE_REBUILD_REQUIRES_IDLE_CONNECTION")
    # The bounded publication deliberately spans commits. Expose that interval
    # so a stability audit can distinguish a partial generation from a durable
    # loss of ready identities without ignoring worker or order-route failures.
    connection.execute("""CREATE TABLE IF NOT EXISTS candidate_refresh_state(
        id INTEGER PRIMARY KEY CHECK(id=1),state TEXT NOT NULL,
        checked_at TEXT NOT NULL)""")
    connection.execute("""INSERT INTO candidate_refresh_state(id,state,checked_at)
        VALUES(1,'RUNNING',?) ON CONFLICT(id) DO UPDATE SET
        state=excluded.state,checked_at=excluded.checked_at""", (checked_at,))
    connection.commit()
    # Keep prior READY identities that remain verified. Only identities whose
    # new projection is absent or blocked must close before publishing batches.
    # This prevents a periodic refresh from showing a mostly empty universe to
    # the dashboard while still failing closed for removed/stale identities.
    desired_legacy = {(row[0], row[1], row[3]): row for row in projected}
    desired_full = {tuple(row[:5]): row for row in full}
    legacy_close = []
    for ticker, family, market, ready in connection.execute(
            "SELECT ticker,instrument_type,market,can_simulate FROM candidate_universe"):
        key = (ticker, family, market)
        if key not in desired_legacy or (ready and not desired_legacy[key][4]):
            legacy_close.append(key)
    full_close = []
    for ticker, family, market, currency, settlement, ready in connection.execute(
            "SELECT ticker,instrument_type,market,currency,settlement,can_simulate "
            "FROM candidate_identity_v2"):
        key = (ticker, family, market, currency, settlement)
        if key not in desired_full or (ready and not desired_full[key][5]):
            full_close.append(key)
    for start in range(0, max(len(legacy_close), len(full_close)), bounded_batch):
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            "DELETE FROM candidate_universe WHERE ticker=? AND instrument_type=? AND market=?",
            legacy_close[start:start + bounded_batch])
        connection.executemany(
            "DELETE FROM candidate_identity_v2 WHERE ticker=? AND instrument_type=? "
            "AND market=? AND currency=? AND settlement=?",
            full_close[start:start + bounded_batch])
        connection.commit()
        time.sleep(.005)
    for start in range(0, max(len(projected), len(full)), bounded_batch):
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            "INSERT OR REPLACE INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)",
            projected[start:start + bounded_batch])
        connection.executemany(
            "INSERT OR REPLACE INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",
            full[start:start + bounded_batch])
        connection.commit()
        # Give other PAPER writers a chance to claim the WAL between batches.
        time.sleep(.005)
    connection.execute("UPDATE candidate_refresh_state SET state='COMPLETE' WHERE id=1")
    connection.commit()


def persist(c, record):
    c.execute("""INSERT OR REPLACE INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (record["ticker"], record["instrument_type"], record["market"], record["currency"],
               record["settlement"], record["settlement_source"], record["description"],
               record["last_seen_at"], record["run_id"], record["status"], record["capability"],
               json.dumps(record["raw"], ensure_ascii=False, default=str)))


def _unique_native_catalog_scope(con, ticker, family, market, currency):
    keys = con.execute("""SELECT DISTINCT market,currency
      FROM financial_instrument_catalog
      WHERE ticker=? AND instrument_type=? AND status IN ('AVAILABLE','STALE')
        AND market NOT IN ('','UNKNOWN','NO_VERIFICADO')
        AND currency NOT IN ('','UNKNOWN','NO_VERIFICADO') LIMIT 2""",
        (ticker, family)).fetchall()
    return len(keys) == 1 and tuple(keys[0]) == (market, currency)


def publish_native_settlement_receipt(store, identity, proof, checked_at):
    """Publish a signed proof and its instrument's exact eligibility together."""
    from contextlib import closing
    from rc6_ppi_native_request_receipt import FIELD, verified_receipt

    if not verified_receipt(proof, identity, database=store.path, now=checked_at,
                            require_active=True):
        return False
    ticker, family, market, currency, settlement = identity
    with closing(store.connect()) as con, con:
        con.execute("PRAGMA busy_timeout=250")
        con.execute("BEGIN IMMEDIATE")
        state = con.execute("SELECT mode,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        publication = con.execute("SELECT state FROM candidate_refresh_state WHERE id=1").fetchone()
        if (state is None or tuple(state) != ("PRODUCTION_PAPER", 0)
                or publication is None or publication[0] != "COMPLETE"):
            return False
        if not _unique_native_catalog_scope(con, ticker, family, market, currency):
            return False
        rows = con.execute("""SELECT * FROM financial_instrument_catalog
          WHERE ticker=? AND instrument_type=? AND settlement=?
            AND status IN ('AVAILABLE','STALE')
            AND market NOT IN ('','UNKNOWN','NO_VERIFICADO')
            AND currency NOT IN ('','UNKNOWN','NO_VERIFICADO') LIMIT 2""",
            (ticker, family, settlement)).fetchall()
        if len(rows) != 1:
            return False
        record = dict(rows[0])
        if ((record["market"], record["currency"], record["status"], record["settlement_source"])
                != (market, currency, "AVAILABLE", "REQUEST_CANDIDATE")
                or not _candidate_timestamp_is_fresh(
                    record["last_seen_at"], checked_at, PAPER_PPI_IDENTITY_LKG_SECONDS)):
            return False
        raw = json.loads(record["metadata_json"])
        if not isinstance(raw, dict):
            return False
        previous = raw.get(FIELD)
        if (verified_receipt(previous, identity, database=store.path, now=checked_at)
                and previous["book"]["received_at"] >= proof["book"]["received_at"]):
            return False
        raw[FIELD] = proof
        record["raw"] = raw
        if ppi_full_key_evidence_reason(record, database=store.path, now=checked_at):
            return False
        persist(con, record)
        sync_candidate_universe(con, checked_at, only_instrument=(ticker, family), database=store.path)
        return True


def lookup(store, symbol, kind, settlement, *, market=None, currency=None):
    """Una identidad ambigua no se resuelve eligiendo la primera fila."""
    ready_full_keys = set()
    with store.connect() as c:
        rows = c.execute("""SELECT * FROM financial_instrument_catalog
          WHERE ticker=? AND instrument_type=? AND settlement=?""", (symbol, kind, settlement)).fetchall()
        if (market is None) != (currency is None):
            raise ValueError("CATALOG_PARTIAL_MONETARY_IDENTITY")
        if market is not None:
            rows = [r for r in rows if (r["market"], r["currency"]) == (market, currency)]
        if (len(rows) > 1 and c.execute("""SELECT 1 FROM sqlite_master
              WHERE type='table' AND name='candidate_identity_v2'""").fetchone()):
            ready_full_keys = {
                (str(row[0]).upper(), str(row[1]).upper())
                for row in c.execute("""SELECT market,currency
                  FROM candidate_identity_v2
                  WHERE ticker=? AND instrument_type=? AND settlement=?
                    AND can_simulate=1 AND status='AVAILABLE'""",
                  (symbol, kind, settlement)).fetchall()
            }
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
            if market is not None:
                candidates = [r for r in candidates if (r["market"],r["currency"]) == (market,currency)]
                return candidates[0] if len(candidates) == 1 else None
            return candidates[0] if len(keys) == 1 else None
    primaries = [r for r in rows if _candidate_has_ppi_primary(r["settlement_source"], r["metadata_json"])]
    rows = primaries or rows
    available = [r for r in rows if r["status"] == "AVAILABLE"]
    rows = available or rows
    if len(rows) > 1 and ready_full_keys:
        rows = [row for row in rows
                if (str(row["market"]).upper(), str(row["currency"]).upper())
                in ready_full_keys]
    if len(rows) != 1:
        return None
    record = dict(rows[0])
    record["raw"] = json.loads(record.pop("metadata_json"))
    # Recheck independently of a READY projection from before a rebuild.
    full_key_reason = ppi_full_key_evidence_reason(record, database=getattr(store, "path", None))
    with store.connect() as c:
        ledger = []
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_identity_v2'").fetchone():
            ledger = c.execute("SELECT can_simulate,status,detail FROM candidate_identity_v2 WHERE ticker=? AND instrument_type=? AND market=? AND currency=? AND settlement=?",
                (symbol,kind,record["market"],record["currency"],settlement)).fetchall()
        elif c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_universe'").fetchone():
            ledger = c.execute("SELECT can_simulate,status,detail FROM candidate_universe WHERE ticker=? AND instrument_type=? AND market=? AND settlement=?",
                (symbol,kind,record["market"],settlement)).fetchall()
        if (not full_key_reason and record["settlement_source"] == "REQUEST_CANDIDATE"
                and not _unique_native_catalog_scope(c, symbol, kind, record["market"], record["currency"])):
            full_key_reason = "PPI_NATIVE_SETTLEMENT_IDENTITY_AMBIGUOUS"
    if len(ledger) != 1 or not ledger[0][0] or ledger[0][1] != "AVAILABLE":
        record["_admission_reason"] = "CANDIDATE_GATE:" + (str(ledger[0][2]) if len(ledger)==1 else "NO_UNIQUE_CANDIDATE")
    if full_key_reason:
        record["_admission_reason"] = full_key_reason
    return record


def _iol_spot_primary(record):
    raw = record["raw"]
    if not isinstance(raw, dict) or raw.get("_discovery_source") != "PPI_PRIMARY":
        return False
    source = record["settlement_source"]
    if source == "PPI_FIELD":
        return not ppi_full_key_evidence_reason(record)
    if source != "REQUEST_CANDIDATE" or raw.get("settlement") not in (None, ""):
        return False
    try:
        return (str(raw.get("ticker") or raw.get("symbol") or "").strip().upper(),
                canonical_family(raw.get("type") or raw.get("instrumentType")),
                canonical_market(raw.get("market")), cash_currency(raw.get("currency"))) == tuple(
                    record[key] for key in ("ticker", "instrument_type", "market", "currency"))
    except (ValueError, TypeError):
        return False


def _iol_local_etf_reference(key):
    from rc6_performance.costs import MERV_LISTING_KNOWN_AT

    ticker, family, market, currency, settlement = key
    return {
        "ticker": ticker, "instrument_type": family, "market": market,
        "currency": currency, "settlement": settlement, "status": "OBSERVED_SHADOW",
        "settlement_source": "BYMA_PUBLIC_COMPLEMENTARY",
        "capability": "IOL_ETF_EXECUTION_TARIFF_UNVERIFIED",
        "description": "gForce MSCI Merval FCIA ETF",
        "last_seen_at": MERV_LISTING_KNOWN_AT, "run_id": "BYMA_FIMA_MERV_REFERENCE",
        "raw": {"_discovery_source": "BYMA_PUBLIC_COMPLEMENTARY", "iol_local_etf_reference": True},
    }


def persist_iol_etf_observation(store, record, observation):
    """Preserve direct market proof beside venue metadata, with no admission effect."""
    from contextlib import closing
    from rc6_performance.costs import local_etf_reference_keys

    key = observation.quote.contract.key
    if (key not in local_etf_reference_keys(observation.quote.observed_at)
            or observation.current is None or not observation.quote.iol_trade_evidence
            or record != _iol_local_etf_reference(key)):
        raise ValueError("IOL_ETF_REFERENCE_IDENTITY_MISMATCH")
    observed = dict(record, last_seen_at=observation.quote.observed_at)
    observed["raw"] = dict(record["raw"], iol_current_sha256=observation.current.data_sha256,
                           iol_book_sha256=observation.book.data_sha256)
    with closing(store.connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute("""SELECT settlement_source,metadata_json
            FROM financial_instrument_catalog
            WHERE (ticker,instrument_type,market,currency,settlement)=(?,?,?,?,?)""", key).fetchone()
        if previous is None or (previous[0] == record["settlement_source"] and
                                json.loads(previous[1]).get("iol_local_etf_reference") is True):
            persist(connection, observed)


def iol_spot_catalog(store, *, now, identity=None, include_contracts=False):
    """Select exact stored keys for requalification against IOL market data."""
    from contextlib import closing
    from rc6_dynamic_universe.routing import EQUITY_FAMILIES
    from rc6_performance.costs import local_etf_reference_keys

    families = set(EQUITY_FAMILIES)
    references = ()
    if include_contracts:
        from rc6_fixed_income_paper_strategy import FAMILIES
        families.update(FAMILIES)
        families.add("OPCIONES")
        references = tuple(key for key in local_etf_reference_keys(now)
                           if identity is None or tuple(identity) == key)
    slots = ",".join("?" for _ in families)
    scope = ""
    parameters = tuple(sorted(families))
    instrument = None
    if identity is not None:
        scope = "AND (f.ticker,f.instrument_type,f.market,f.currency,f.settlement)=(?,?,?,?,?)"
        parameters += tuple(identity)
        instrument = tuple(identity[:2])
    with closing(store.connect()) as c:
        has_ledger = c.execute("""SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='candidate_identity_v2'""").fetchone() is not None
        rows = c.execute(f"""SELECT f.*, p.can_simulate AS ledger_allowed, p.status AS ledger_status,
            p.detail AS ledger_detail FROM financial_instrument_catalog f
            JOIN candidate_identity_v2 p USING(ticker,instrument_type,market,currency,settlement)
            WHERE f.instrument_type IN ({slots}) AND f.status='AVAILABLE'
              AND (f.capability='READY_PAPER_SPOT' OR
                  (f.instrument_type='OPCIONES' AND f.capability='READY_PAPER_OPTION_LONG'
                   AND f.settlement='INMEDIATA'))
              AND f.market='BYMA'
              AND f.currency IN ('ARS','USD') AND f.settlement IN ('INMEDIATA','A-24HS')
              AND NOT EXISTS (SELECT 1 FROM financial_instrument_catalog other
                WHERE other.ticker=f.ticker AND other.instrument_type=f.instrument_type
                  AND other.status IN ('AVAILABLE','STALE')
                  AND other.market NOT IN ('','UNKNOWN','NO_VERIFICADO')
                  AND other.currency NOT IN ('','UNKNOWN','NO_VERIFICADO')
                  AND (other.market!=f.market OR other.currency!=f.currency))
              {scope} ORDER BY f.instrument_type,f.ticker,f.settlement""", parameters).fetchall() if has_ledger else ()
        ambiguities = _candidate_retry_ambiguities(c, instrument)
        if references and c.execute("""SELECT 1 FROM financial_instrument_catalog
                    WHERE ticker=? AND status IN ('AVAILABLE','STALE')
                      AND market NOT IN ('','UNKNOWN','NO_VERIFICADO')
                      AND currency NOT IN ('','UNKNOWN','NO_VERIFICADO')
                      AND (market<>? OR currency<>?) LIMIT 1""",
                                           (references[0][0], *references[0][2:4])).fetchone():
            references = ()
    result = {}
    replaceable = {"PPI_SETTLEMENT_NOT_PROVIDER_ATTESTED", "PPI_NATIVE_SETTLEMENT_RECEIPT_INVALID",
                   "PPI_FRESHNESS_STALE"}
    for row in rows:
        record = dict(row)
        key = tuple(record[field] for field in ("ticker", "instrument_type", "market", "currency", "settlement"))
        # The old catalog date identifies the origin of this key. IOL checks
        # the current asset, currency and settlement at each market read.
        if key in ambiguities or not _candidate_timestamp_is_fresh(
                record["last_seen_at"], now.isoformat(), None):
            continue
        try:
            record["raw"] = json.loads(record.pop("metadata_json"))
        except (ValueError, TypeError):
            continue
        if not _iol_spot_primary(record):
            continue
        ledger_proved = record["ledger_status"] == "AVAILABLE" and record["ledger_allowed"] == 1
        reasons = set(str(record["ledger_detail"] or "").split(";"))
        awaiting_iol = (key[1] in EQUITY_FAMILIES and
                        record["settlement_source"] in {"PPI_FIELD", "REQUEST_CANDIDATE"} and
                        record["ledger_status"] == "AVAILABLE" and record["ledger_allowed"] == 0 and
                        bool(reasons) and reasons <= replaceable)
        if ledger_proved or awaiting_iol:
            result[key] = record
    for key in references:
        if key not in ambiguities:
            result.setdefault(key, _iol_local_etf_reference(key))
    return result


def quote_terms(record, *, now=None, iol_live_settlement=False):
    if record is None:
        return {"currency": None, "market": None, "metadata_source": None,
                "opening_block_reason": "Falta catálogo confirmado o la identidad es ambigua"}
    if iol_live_settlement and record.get("raw", {}).get("iol_local_etf_reference") is True:
        contract = InstrumentContract(record["ticker"], "ETFS", record["currency"], record["market"],
                                      record["settlement"], Decimal(1), Decimal(1),
                                      "BYMA_FIMA_MERV_REFERENCE", minimum_quantity=Decimal(1))
        return {"currency": contract.currency, "market": contract.market, "contract": contract,
                "metadata_source": contract.metadata_source, "opening_block_reason": record["capability"]}
    spec = None
    try:
        spec = contract_for(record)
    except ValueError as exc:
        contract_error = str(exc)
    else:
        contract_error = ""
    reason = contract_error or ("" if str(record["capability"]).startswith("READY_PAPER_") else record["capability"])
    if record["status"] != "AVAILABLE":
        reason = "Catálogo no confirmado en la última actualización"
    if record.get("_admission_reason"):
        reason = record["_admission_reason"]
    if not _candidate_has_ppi_primary(record.get("settlement_source"), json.dumps(record.get("raw") or {})):
        reason = "PPI_PRIMARY_IDENTITY_NOT_VERIFIED"
    full_key_reason = ("" if iol_live_settlement and _iol_spot_primary(record)
                       else ppi_full_key_evidence_reason(record))
    if full_key_reason:
        reason = full_key_reason
    checked = now or datetime.now(ZoneInfo("UTC"))
    raw = record.get("raw") or {}
    bridge = raw.get("_contract_bridge") or {}
    static_v2_contract = (
        record.get("instrument_type") != "CAUCIONES"
        and any(isinstance(raw.get(name), dict)
                and raw[name].get("metadata_source") == "CONTRACT_EVIDENCE_V2_BOUND"
                for name in ("financial_contract_v17", "paper_family_contract_v1"))
    )
    if (bridge and not static_v2_contract
            and not _candidate_timestamp_is_fresh(
                bridge.get("observed_at"), checked.isoformat(), 86400)):
        reason = "CONTRACT_EVIDENCE_STALE"
    identity_age_limit = None if iol_live_settlement else PAPER_PPI_IDENTITY_LKG_SECONDS
    if not _candidate_timestamp_is_fresh(record.get("last_seen_at"), checked.isoformat(),
                                         identity_age_limit):
        reason = "IOL_CATALOG_LINEAGE_CLOCK_INVALID" if iol_live_settlement else "PPI_FRESHNESS_STALE"
    holiday_reason = rc6_underlying_opening_block(record["ticker"], record["instrument_type"])
    if holiday_reason:
        reason = holiday_reason if not reason else f"{reason}; {holiday_reason}"
    return {"currency": record["currency"], "market": record["market"], "contract": spec,
            "metadata_source": f"PPI_CATALOG:{record['last_seen_at']}", "opening_block_reason": reason}
