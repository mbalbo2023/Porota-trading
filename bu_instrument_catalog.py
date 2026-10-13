"""Catálogo financiero derivado de payloads PPI, con procedencia explícita.

Las etiquetas de moneda se contrastaron con el diagnóstico del 27/08/2026.
SearchInstrument no prueba multiplicador, margen ni vencimiento estructurado.
"""

import json
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

def contract_for(record):
    if record["currency"] == "UNKNOWN" or record["market"] == "UNKNOWN":
        raise ValueError("MISSING_CURRENCY_OR_MARKET")
    family = family_name(record["instrument_type"])
    raw = record.get("raw", {})
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
        return 0 <= age <= int(max_age_seconds)
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


def _candidate_retry_ambiguities(connection):
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
    return {
        tuple(str(value or "").strip().upper() for value in row[:5])
        for row in connection.execute("""SELECT ticker,instrument_type,market,currency,
          settlement,reason FROM complementary_contract_retry""").fetchall()
        if "AMBIG" in str(row[5] or "").upper()
    }


def sync_candidate_universe(connection, checked_at, *,
                            freshness_seconds=PAPER_PPI_IDENTITY_LKG_SECONDS):
    """Atomically rebuild the legacy summary without projecting ambiguity to READY.

    ``candidate_universe`` intentionally remains keyed by ticker/family/market for
    compatibility.  It is therefore only simulation-ready when that summary maps
    to exactly one complete, fresh PPI-primary catalog identity.  Every other
    projection fails closed.
    """
    rows = connection.execute("""SELECT ticker,instrument_type,market,currency,
      settlement,settlement_source,status,capability,last_seen_at,metadata_json
      FROM financial_instrument_catalog
      ORDER BY ticker,instrument_type,market,currency,settlement,last_seen_at""").fetchall()
    grouped = {}
    for row in rows:
        ticker, family, market = (str(row[index] or "").strip().upper()
                                  for index in (0, 1, 2))
        grouped.setdefault((ticker, family, market), []).append(row)
    retry_ambiguities = _candidate_retry_ambiguities(connection)

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
        if not _candidate_has_ppi_primary(row[5], row[9]):
            reasons.append("PPI_PRIMARY_IDENTITY_NOT_VERIFIED")
        if any(value in {"", "UNKNOWN", "NO_VERIFICADO"}
               for value in identity_key):
            reasons.append("IDENTITY_INCOMPLETE")
        if not _candidate_timestamp_is_fresh(row[8], checked_at, freshness_seconds):
            reasons.append("PPI_FRESHNESS_STALE")
        if row[6] != "AVAILABLE":
            reasons.append("CATALOG_STATUS:" + str(row[6]))
        if not str(row[7]).startswith("READY_PAPER_"):
            reasons.append("CAPABILITY:" + str(row[7]))
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

    # DELETE + INSERT runs inside the caller's transaction.  Removed/reclassified
    # catalog identities cannot survive as stale candidate ghosts.
    connection.execute("DELETE FROM candidate_universe")
    connection.executemany(
        "INSERT INTO candidate_universe VALUES(?,?,?,?,?,?,?,?)", projected
    )

    # Persist the independent gate at the catalog's full key.  Short-ticker
    # selection ambiguity belongs only to the legacy projection above.
    connection.execute("""CREATE TABLE IF NOT EXISTS candidate_identity_v2(
        ticker TEXT NOT NULL,instrument_type TEXT NOT NULL,market TEXT NOT NULL,
        currency TEXT NOT NULL,settlement TEXT NOT NULL,can_simulate INTEGER NOT NULL,
        status TEXT NOT NULL,detail TEXT NOT NULL,checked_at TEXT NOT NULL,
        PRIMARY KEY(ticker,instrument_type,market,currency,settlement))""")
    full = []
    for row in rows:
        key = tuple(row[:5])
        reasons = identity_reasons(row)
        base_detail = ";".join(reasons) or row[7]
        projected_status, detail = explicit_projection(
            row[1], reasons, row[6], base_detail)
        full.append((*key,int(not reasons),projected_status,detail,checked_at))
    connection.execute("DELETE FROM candidate_identity_v2")
    connection.executemany("INSERT INTO candidate_identity_v2 VALUES(?,?,?,?,?,?,?,?,?)",full)


def persist(c, record):
    c.execute("""INSERT OR REPLACE INTO financial_instrument_catalog VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
              (record["ticker"], record["instrument_type"], record["market"], record["currency"],
               record["settlement"], record["settlement_source"], record["description"],
               record["last_seen_at"], record["run_id"], record["status"], record["capability"],
               json.dumps(record["raw"], ensure_ascii=False, default=str)))


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
    with store.connect() as c:
        ledger = []
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_identity_v2'").fetchone():
            ledger = c.execute("SELECT can_simulate,status,detail FROM candidate_identity_v2 WHERE ticker=? AND instrument_type=? AND market=? AND currency=? AND settlement=?",
                (symbol,kind,record["market"],record["currency"],settlement)).fetchall()
        elif c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='candidate_universe'").fetchone():
            ledger = c.execute("SELECT can_simulate,status,detail FROM candidate_universe WHERE ticker=? AND instrument_type=? AND market=? AND settlement=?",
                (symbol,kind,record["market"],settlement)).fetchall()
    if len(ledger) != 1 or not ledger[0][0] or ledger[0][1] != "AVAILABLE":
        record["_admission_reason"] = "CANDIDATE_GATE:" + (str(ledger[0][2]) if len(ledger)==1 else "NO_UNIQUE_CANDIDATE")
    return record


def quote_terms(record, *, now=None):
    if record is None:
        return {"currency": None, "market": None, "metadata_source": None,
                "opening_block_reason": "Falta catálogo confirmado o la identidad es ambigua"}
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
    if not _candidate_timestamp_is_fresh(
            record.get("last_seen_at"), checked.isoformat(),
            PAPER_PPI_IDENTITY_LKG_SECONDS):
        reason = "PPI_FRESHNESS_STALE"
    holiday_reason = rc6_underlying_opening_block(record["ticker"], record["instrument_type"])
    if holiday_reason:
        reason = holiday_reason if not reason else f"{reason}; {holiday_reason}"
    return {"currency": record["currency"], "market": record["market"], "contract": spec,
            "metadata_source": f"PPI_CATALOG:{record['last_seen_at']}", "opening_block_reason": reason}
