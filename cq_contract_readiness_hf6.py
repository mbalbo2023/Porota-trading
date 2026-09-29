"""Fail-closed Contract Evidence v2 readiness rules for PRODUCTION_PAPER.

This module only evaluates evidence. It cannot send orders and it cannot turn a
family on by itself. READY_PAPER remains an explicit integration decision after
contract, cost, sizing, simulator and regression tests all pass.
"""
from __future__ import annotations

# Requirement ownership lives exclusively in cq_family_contract_rules_hf6.
# This module is only a compatibility adapter for historical field names and
# statuses; keeping a second family matrix here previously allowed drift.

ALIASES = {
    "FCI": "FCI_LOCAL",
    "ETFS": "ETF",
    "OBLIGACIONES": "ON",
    "ON": "ON",
    "FONDOS": "FCI_LOCAL",
    "FONDOS_LOCAL": "FCI_LOCAL",
    "ACCIONES_EXTERIOR": "ACCIONES_USA",
    "OBLIGACIONES_NEGOCIABLES": "ON",
    "OBLIGACIONES-NEGOCIABLES": "ON",
    "ACCIONES-USA": "ACCIONES_USA",
    "FCI-EXTERIOR": "FCI_EXTERIOR",
    "FCI EXTERIOR": "FCI_EXTERIOR",
}


def canonical_family(family: str) -> str:
    key = str(family or "").upper().strip().replace("/", "_")
    key = ALIASES.get(key, key)
    key = key.replace(" ", "_")
    return ALIASES.get(key, key)


def _adapt_payload(raw):
    raw = dict(raw) if isinstance(raw, dict) else {}
    canonical = dict(raw)
    for old, new in FIELD_ALIASES.items():
        if canonical.get(new) in (None, "", [], {}) and raw.get(old) not in (None, "", [], {}):
            canonical[new] = raw[old]
    if raw.get("market") and canonical.get("trading_session") in (None, ""):
        canonical["trading_session"] = "BROKER_DEFINED"
    return canonical


FIELD_ALIASES = {
    "price_precision": "price_tick", "cost_model": "fee_schedule",
    "price_unit_nominals": "price_quote_unit", "maturity": "maturity_date",
    "lamina_minima": "quantity_min", "nominal_value": "quantity_step",
    "option_right": "put_call", "expiry": "expiry_at",
    "contract_lot": "lot_size", "annual_rate": "annual_rate_fraction",
    "principal_min": "minimum_principal", "fee_model": "fee_schedule",
    "settlement_rule": "settlement_method",
    "trading_hours": "trading_session", "nav": "nav_value",
    "nav_as_of": "nav_date", "subscription_minimum": "subscription_min",
    "cutoff": "cutoff_time",
}


def evaluate(family: str, evidence, *, simulator_ready=False,
             cost_ready=False, freshness_ok=False, source_conflict=False,
             profile="FULL", now=None):
    """Legacy facade over the single canonical family-rule evaluator.

    Old callers use historical field names.  This function adapts those names
    and integration flags, but owns no independent requirement list.
    """
    from datetime import datetime, timezone
    from cq_family_contract_rules_hf6 import evaluate_family

    key = canonical_family(family)
    key = {"FCI_LOCAL": "FCI", "ETF": "ETF"}.get(key, key)
    stamp = now or datetime.now(timezone.utc)
    if isinstance(evidence, (list, tuple)):
        records = []
        for item in evidence:
            if not isinstance(item, dict):
                continue
            records.append({**item, "evidence": _adapt_payload(item.get("evidence"))})
    else:
        canonical = _adapt_payload(evidence)
        # Only the historical dict API accepts the historical freshness flag.
        # Stored records must carry their own dynamic fields and timestamps.
        if freshness_ok:
            canonical.setdefault("operable", True)
            canonical.setdefault("market_session_state", "OPEN")
            canonical.setdefault("subscription_status", "AVAILABLE")
            canonical.setdefault("auction_status", "OPEN")
        records = [{"source_class": "PPI_STRUCTURED_API",
                    "observed_at": stamp.isoformat(), "evidence": canonical}]
    result = evaluate_family(key, records, profile=profile, now=stamp)

    if source_conflict:
        status, reason = "CONFLICT", "Fuentes contractuales contradictorias."
    elif result["status"] == "MISSING_CONTRACT":
        status, reason = "MISSING", "Faltan campos requeridos para ejecución PAPER."
    elif result["status"] in {"MISSING_DYNAMIC", "STALE_DYNAMIC"}:
        status, reason = "STALE", "Contrato completo pero dinámica no vigente."
    elif result["status"] not in {"READY_PAPER_CANDIDATE", "NOT_APPLICABLE"}:
        status, reason = result["status"], result.get("detail", "Fail closed.")
    elif not cost_ready:
        status, reason = "POROTA_INTEGRATION_REQUIRED", "Contrato completo pero modelo de costos no certificado."
    elif not simulator_ready:
        status, reason = "POROTA_INTEGRATION_REQUIRED", "Contrato completo pero simulador especializado no certificado."
    else:
        status, reason = result["status"], "Contrato/costo/simulador completos; requiere gate final de integración."
    return {
        "family": key, "profile": str(profile).upper(), "status": status,
        "missing": result.get("missing_contract", []) + result.get("missing_dynamic", []),
        "enrichment_missing": [], "event_missing": result.get("event_missing", []),
        "reason": reason, "canonical_result": result,
    }
