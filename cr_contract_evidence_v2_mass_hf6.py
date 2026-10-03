"""Mass PPI catalogue -> Contract Evidence v2 ingestion for PAPER.

The catalogue is the PPI-primary identity authority.  This module reads every
current AVAILABLE full-key row and writes stable, append-only v2 evidence.  It
does not call a broker, infer order terms from ticker text, or authorize real
execution.  The only derived contract emitted here is the already-audited BYMA
unit convention for spot shares, CEDEARs and ETFs.
"""
from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal, InvalidOperation

import cp_contract_evidence_v2_hf6 as evidence_v2
from bu_instrument_catalog import _candidate_has_ppi_primary
from rc6_multisource_discovery import canonical_family, canonical_market, canonical_settlement
import rc6_ppi_option_contract_policy as option_policy\nimport rc6_ppi_future_contract_policy as future_policy


SCHEMA = "rc6-contract-evidence-v2-mass-catalog-v1"
JOB_KEY = "CONTRACT_EVIDENCE_V2_MASS_PPI_CATALOG"
MAX_CATALOG_ROWS = 20_000
SPOT_UNIT_FAMILIES = frozenset({"ACCIONES", "CEDEARS", "ETFS"})
FIXED_INCOME_FAMILIES = frozenset({"BONOS", "LETRAS", "OBLIGACIONES"})
CAUCION_PAPER_LIVE_BOOK_POLICY = "LIVE_PPI_BID_PARTICIPATION_CAP"
UNKNOWN = frozenset({"", "*", "UNKNOWN", "NO_VERIFICADO"})


def _catalog_rows(store):
    with store.connect() as connection:
        rows = connection.execute("""SELECT ticker,instrument_type,market,currency,
          settlement,settlement_source,status,capability,last_seen_at,description,metadata_json
          FROM financial_instrument_catalog
          WHERE UPPER(COALESCE(status,''))='AVAILABLE'
          ORDER BY instrument_type,ticker,market,currency,settlement""").fetchall()
    if len(rows) > MAX_CATALOG_ROWS:
        raise RuntimeError("PPI_CATALOG_BOUND_EXCEEDED")
    return [dict(row) for row in rows]


def _raw(row):
    try:
        value = json.loads(row.get("metadata_json") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _full_identity(row):
    family = canonical_family(row.get("instrument_type"))
    return {
        "family": family,
        "ticker": str(row.get("ticker") or "").strip().upper(),
        "market": canonical_market(row.get("market")),
        "currency": str(row.get("currency") or "").strip().upper(),
        "settlement": canonical_settlement(row.get("settlement"), family),
    }


def planned_records(rows):
    """Return stable source records and explicit skip reasons."""
    records = []
    skipped = Counter()
    identities = Counter()
    option_underlyings = option_policy.underlying_family_index(rows)
    for row in rows:
        raw = _raw(row)
        if not _candidate_has_ppi_primary(row.get("settlement_source"),
                                          json.dumps(raw, ensure_ascii=False)):
            skipped["NOT_PPI_PRIMARY"] += 1
            continue
        identity = _full_identity(row)
        if any(value in UNKNOWN for value in identity.values()):
            skipped["FULL_IDENTITY_INCOMPLETE"] += 1
            continue
        family = identity["family"]
        provider_family = str(raw.get("_provider_instrument_type") or
                              raw.get("type") or row.get("instrument_type") or "").strip().upper()
        payload = {
            "market": identity["market"],
            "currency": identity["currency"],
            "settlement": identity["settlement"],
            "ppi_identity_verified": True,
            "provider_instrument_type": provider_family,
            "provider_catalog_status": "AVAILABLE",
            "evidence_scope": "FULL_PPI_AVAILABLE_CATALOG",
            "readiness_guard": "PPI_IDENTITY_ONLY_UNLESS_EXPLICIT_TERM_PRESENT",
        }
        nominal = raw.get("nominalInPrice")
        if nominal not in (None, ""):
            # Preserve PPI's literal structured field.  It is quote-basis
            # evidence, not an order increment and not by itself a multiplier.
            payload["price_quote_unit"] = nominal
            payload["ppi_nominal_in_price"] = nominal
            payload["semantic_guard"] = "NOT_QUANTITY_STEP"
        records.append({
            **identity,
            "source_class": "PPI_STRUCTURED_API",
            "source_ref": "PPI_API:/api/1.0/marketdata/searchinstrument",
            "observed_at": row.get("last_seen_at"),
            "evidence": payload,
        })
        identities[family] += 1

        if family in SPOT_UNIT_FAMILIES and identity["market"] == "BYMA":
            records.append({
                **identity,
                "source_class": "DERIVED_OFFICIAL_RULE",
                "source_ref": "BYMA_SPOT_UNIT_CONVENTION:PAPER_OPEN",
                "observed_at": row.get("last_seen_at"),
                "evidence": {
                    "cash_multiplier": "1",
                    "quantity_min": "1",
                    "quantity_step": "1",
                    "derivation_rule": "one traded unit equals one PAPER quantity unit",
                    "evidence_scope": "BYMA_SPOT_ONLY",
                    "readiness_guard": "NOT_APPLICABLE_TO_FIXED_INCOME_OR_DERIVATIVES",
                },
            })
        elif family in FIXED_INCOME_FAMILIES and identity["market"] == "BYMA":
            # PPI's nominalInPrice is an explicit quotation basis.  For the
            # PAPER nominal engine, one simulated quantity is one nominal and
            # cash is price / quote-basis.  The unit min/step below is Porota's
            # internal simulator policy; it is deliberately not a broker term.
            try:
                quote_basis = Decimal(str(nominal))
            except (InvalidOperation, TypeError, ValueError):
                quote_basis = Decimal(0)
            if quote_basis.is_finite() and quote_basis > 0:
                records.append({
                    **identity,
                    "source_class": "DERIVED_OFFICIAL_RULE",
                    "source_ref": "PPI_QUOTE_BASIS+POROTA_PAPER_NOMINAL_POLICY:v1",
                    "observed_at": row.get("last_seen_at"),
                    "evidence": {
                        "paper_cash_multiplier": str(Decimal(1) / quote_basis),
                        "paper_quantity_min": "1",
                        "paper_quantity_step": "1",
                        "broker_minimum_quantity": "NO_VERIFICADO",
                        "broker_quantity_step": "NO_VERIFICADO",
                        "paper_quantity_policy": "ONE_NOMINAL_SIMULATION_UNIT",
                        "derivation_rule": "cash = quoted_price * nominals / ppi_nominal_in_price",
                        "provenance_class": "DERIVED_FROM_PROVIDER_QUOTE_BASIS",
                        "policy_scope": "PRODUCTION_PAPER_SIMULATION_ONLY",
                        "readiness_guard": "PPI_EXACT_IDENTITY+NOMINAL_IN_PRICE_POSITIVE",
                    },
                })
        elif family == "CAUCIONES" and identity["market"] == "BYMA" and identity["currency"] == "ARS":
            # Static simulator policy only. Rate/depth/timestamp remain dynamic
            # and are collected separately from PPI book near EOD.
            try:
                from rc6_cauciones_contract import parse_ticker
                caucion_identity = parse_ticker(identity["ticker"])
            except ValueError:
                skipped["CAUCION_TICKER_POLICY_UNSUPPORTED"] += 1
            else:
                if caucion_identity.currency_prefix != "PESOS":
                    skipped["CAUCION_CURRENCY_POLICY_UNSUPPORTED"] += 1
                else:
                    from au_fee_schedule import CAUCION_PAPER_FEE_AUTHORITY
                    records.append({
                        **identity,
                        "source_class": "DERIVED_OFFICIAL_RULE",
                        "source_ref": "PPI_CAUCION_TICKER+POROTA_PAPER_LIVE_BOOK_POLICY:v1",
                        "observed_at": row.get("last_seen_at"),
                        "evidence": {
                            "side": "COLOCADORA",
                            "term_days": str(caucion_identity.term_days),
                            "minimum_principal": "100000",
                            "paper_principal_step": "1",
                            "day_count_basis": "365",
                            "fee_payment": "UPFRONT",
                            "paper_fill_policy": CAUCION_PAPER_LIVE_BOOK_POLICY,
                            "fee_authority": CAUCION_PAPER_FEE_AUTHORITY,
                            "policy_scope": "PRODUCTION_PAPER_SIMULATION_ONLY",
                            "dynamic_execution_gate": "FRESH_PPI_PLACING_BID_REQUIRED",
                            "readiness_guard": "PPI_EXACT_IDENTITY+SUPPORTED_TICKER+LIVE_BID_AT_EXECUTION",
                        },
                    })
        elif family == "OPCIONES":
            option_evidence = option_policy.standard_long_option_evidence(
                row, option_underlyings)
            if option_evidence:
                records.append({
                    **identity,
                    "source_class": "DERIVED_OFFICIAL_RULE",
                    "source_ref": option_policy.OPTION_DERIVED_SOURCE_REF,
                    "observed_at": row.get("last_seen_at"),
                    "evidence": option_evidence,
                })
            elif str(row.get("capability") or "") == "NEEDS_OPTION_CONTRACT":
                skipped["OPTION_STANDARD_CONTRACT_POLICY_UNRESOLVED"] += 1
        elif family == "FUTUROS":
            future_evidence = future_policy.standard_dlr_future_evidence(row)
            if future_evidence:
                records.append({
                    **identity,
                    "source_class": "DERIVED_OFFICIAL_RULE",
                    "source_ref": future_policy.A3_DLR_SOURCE_REF,
                    "observed_at": row.get("last_seen_at"),
                    "evidence": future_evidence,
                })
            elif str(row.get("capability") or "") == "NEEDS_FUTURES_MARGIN_AND_CONTRACT":
                skipped["FUTURE_STANDARD_DLR_POLICY_UNRESOLVED"] += 1
        elif family == "FCI":
            records.append({
                **identity,
                "source_class": "DERIVED_OFFICIAL_RULE",
                "source_ref": "POROTA_PAPER_FCI_RISK_BUDGET_POLICY:v1",
                "observed_at": row.get("last_seen_at"),
                "evidence": {
                    "paper_subscription_policy": "INTERNAL_RISK_BUDGET_BY_AMOUNT",
                    "paper_subscription_min": "1000",
                    "broker_subscription_min": "NO_VERIFICADO",
                    "broker_subscription_step": "NO_VERIFICADO",
                    "paper_amount_unit": "0.01",
                    "policy_scope": "PRODUCTION_PAPER_SIMULATION_ONLY",
                    "readiness_guard": "PPI_EXACT_AVAILABLE_IDENTITY+POSITIVE_INTERNAL_RISK_BUDGET",
                },
            })
    return records, dict(skipped), dict(identities)


def collect(store, *, run_id):
    evidence_v2.start_run(
        store, run_id=run_id, job_key=JOB_KEY,
        detail="PPI full AVAILABLE catalogue; PAPER/SHADOW only; no broker routes")
    try:
        rows = _catalog_rows(store)
        records, skipped, identities = planned_records(rows)
        results = evidence_v2.record_snapshots(store, records)
        changed = sum(bool(item.get("changed")) for item in results)
        first_seen = sum(bool(item.get("first_seen")) for item in results)
        by_family = Counter(item["family"] for item in results)
        detail = json.dumps({
            "schema": SCHEMA,
            "catalog_available": len(rows),
            "identities_written": identities,
            "evidence_rows": dict(by_family),
            "first_seen": first_seen,
            "skipped": skipped,
            "paper_only": True,
            "real_routes_used": [],
        }, ensure_ascii=False, sort_keys=True)
        evidence_v2.finish_run(
            store, run_id=run_id, state="OK", records=len(results),
            changed=changed, conflicts=changed, detail=detail)
        return json.loads(detail)
    except Exception as exc:
        evidence_v2.finish_run(
            store, run_id=run_id, state="ERROR", detail=type(exc).__name__)
        raise


def assert_read_only_invariants():
    assert MAX_CATALOG_ROWS > 0
    assert SPOT_UNIT_FAMILIES == {"ACCIONES", "CEDEARS", "ETFS"}
    assert FIXED_INCOME_FAMILIES == {"BONOS", "LETRAS", "OBLIGACIONES"}
    assert "order" not in JOB_KEY.lower()
