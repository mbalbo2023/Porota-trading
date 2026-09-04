"""Fail-closed Contract Evidence v2 readiness rules for PRODUCTION_PAPER.

This module only evaluates evidence. It cannot send orders and it cannot turn a
family on by itself. READY_PAPER remains an explicit integration decision after
contract, cost, sizing, simulator and regression tests all pass.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FamilyRequirements:
    execution_required: tuple[str, ...]
    enrichment_required: tuple[str, ...] = ()


COMMON_SPOT = (
    "instrument_id", "ticker", "market", "currency", "settlement",
    "quantity_min", "quantity_step", "price_precision", "cost_model",
)

REQUIREMENTS = {
    "ACCIONES": FamilyRequirements(COMMON_SPOT),
    "CEDEARS": FamilyRequirements(COMMON_SPOT + ("conversion_ratio",)),
    "BONOS": FamilyRequirements(COMMON_SPOT + (
        "price_unit_nominals", "nominal_value", "lamina_minima", "isin",
        "maturity", "coupon_terms", "amortization_terms",
    ), ("payment_schedule", "tir", "modified_duration", "parity", "accrued_interest")),
    "LETRAS": FamilyRequirements(COMMON_SPOT + (
        "price_unit_nominals", "nominal_value", "lamina_minima", "maturity",
    ), ("isin", "yield_context")),
    "ON": FamilyRequirements(COMMON_SPOT + (
        "price_unit_nominals", "nominal_value", "lamina_minima", "isin",
        "maturity", "coupon_terms", "amortization_terms", "payment_currency",
    ), ("payment_schedule", "tir", "modified_duration")),
    "CAUCIONES": FamilyRequirements((
        "instrument_id", "currency", "side", "term_days", "maturity",
        "annual_rate", "available_principal", "minimum_principal",
        "principal_step", "day_count_basis", "fee_model",
    )),
    "OPCIONES": FamilyRequirements((
        "instrument_id", "ticker", "market", "currency", "underlying",
        "option_right", "strike", "expiry", "contract_lot", "quantity_step",
        "price_tick", "price_per_option", "exercise_style", "settlement",
        "cost_model",
    ), ("open_interest", "implied_volatility", "greeks")),
    "FUTUROS": FamilyRequirements((
        "instrument_id", "ticker", "market", "currency", "underlying",
        "expiry", "contract_multiplier", "quantity_step", "price_tick",
        "tick_value", "initial_margin", "settlement_rule", "adjustment_rule",
        "trading_hours", "cost_model",
    ), ("maintenance_margin", "open_interest")),
    "FCI_LOCAL": FamilyRequirements((
        "fund_id", "currency", "nav", "nav_as_of", "subscription_minimum",
        "subscription_step", "cutoff", "redemption_term", "cost_model",
    ), ("redemption_minimum", "manager", "custodian", "benchmark", "risk")),
    "FCI_EXTERIOR": FamilyRequirements((
        "fund_id", "currency", "nav", "nav_as_of", "subscription_minimum",
        "subscription_step", "cutoff", "redemption_term", "cost_model",
        "jurisdiction", "restrictions",
    ), ("isin", "manager", "custodian", "benchmark", "risk")),
    "ETF": FamilyRequirements(COMMON_SPOT + ("exchange",)),
    "ACCIONES_USA": FamilyRequirements(COMMON_SPOT + (
        "exchange", "fractional_policy", "trading_hours",
    )),
    "LICITACIONES": FamilyRequirements((
        "auction_id", "instrument_id", "currency", "open_at", "close_at",
        "status", "minimum_amount", "amount_step", "settlement",
        "allocation_rule", "cost_model",
    ), ("maximum_amount", "competitive_rule", "noncompetitive_rule", "proration_rule")),
    "CANJES": FamilyRequirements((
        "event_id", "eligible_instrument", "target_instrument", "open_at",
        "close_at", "exchange_ratio", "quantity_min", "quantity_step",
        "settlement", "cost_model",
    )),
}

ALIASES = {
    "FCI": "FCI_LOCAL",
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


def evaluate(family: str, evidence: dict, *, simulator_ready=False,
             cost_ready=False, freshness_ok=False, source_conflict=False):
    """Return a deterministic readiness result without side effects."""
    key = canonical_family(family)
    req = REQUIREMENTS.get(key)
    if req is None:
        return {
            "family": key,
            "status": "FAIL_CLOSED",
            "missing": ["family_requirements"],
            "reason": "No existe contrato de readiness para esta familia.",
        }

    evidence = evidence if isinstance(evidence, dict) else {}
    missing = [name for name in req.execution_required
               if evidence.get(name) in (None, "", [], {})]

    if source_conflict:
        status = "CONFLICT"
        reason = "Fuentes contractuales contradictorias."
    elif missing:
        status = "MISSING"
        reason = "Faltan campos requeridos para ejecución PAPER."
    elif not freshness_ok:
        status = "STALE"
        reason = "Contrato completo pero fuera de TTL."
    elif not cost_ready:
        status = "POROTA_INTEGRATION_REQUIRED"
        reason = "Contrato completo pero modelo de costos no certificado."
    elif not simulator_ready:
        status = "POROTA_INTEGRATION_REQUIRED"
        reason = "Contrato completo pero simulador especializado no certificado."
    else:
        status = "READY_PAPER_CANDIDATE"
        reason = "Contrato/costo/simulador completos; requiere gate final de integración."

    return {
        "family": key,
        "status": status,
        "missing": missing,
        "enrichment_missing": [name for name in req.enrichment_required
                               if evidence.get(name) in (None, "", [], {})],
        "reason": reason,
    }
