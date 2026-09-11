"""HF6-v2: requisitos fail-closed por familia, separando contrato y dinámica.

Una ficha contractual (ISIN, multiplicador, lámina, tick, etc.) no caduca con
la misma frecuencia que una TNA, un margen o el estado de una licitación.
Por eso HF6-v2 evalúa dos capas:

1. CONTRACT: términos versionados/effective-dated. Se vigilan por snapshot/hash
   y eventos de cambio; no se invalidan sólo porque el observed_at tenga 48 h.
2. DYNAMIC: condiciones necesarias para simular/operar ahora. Tienen TTL corto
   por campo y deben refrescarse desde APIs/endpoints estructurados.

La salida máxima automática es READY_PAPER_CANDIDATE. Este módulo nunca
habilita trading por sí mismo. READY_PAPER requiere además adaptador de sizing,
simulador/exit específico, tests, evidencia fresca y aprobación de integración.
"""
from __future__ import annotations

from datetime import datetime, timezone

import cp_contract_evidence_v2_hf6 as evidence_v2


FAMILY_CONTRACT_FIELDS = {
    "ACCIONES": frozenset({
        "market", "currency", "settlement", "quantity_min", "quantity_step",
        "price_tick", "fee_schedule", "trading_session",
    }),
    "CEDEARS": frozenset({
        "market", "currency", "settlement", "quantity_min", "quantity_step",
        "price_tick", "conversion_ratio", "fee_schedule", "trading_session",
    }),
    "BONOS": frozenset({
        "market", "currency", "settlement", "isin", "price_quote_unit",
        "quantity_min", "quantity_step", "maturity_date", "payment_currency",
        "coupon_terms", "amortization_terms", "fee_schedule", "trading_session",
    }),
    "LETRAS": frozenset({
        "market", "currency", "settlement", "isin", "price_quote_unit",
        "quantity_min", "quantity_step", "maturity_date", "fee_schedule",
        "trading_session",
    }),
    "ON": frozenset({
        "market", "currency", "settlement", "isin", "price_quote_unit",
        "quantity_min", "quantity_step", "maturity_date", "payment_currency",
        "coupon_terms", "amortization_terms", "fee_schedule", "trading_session",
    }),
    "OBLIGACIONES": frozenset({
        "market", "currency", "settlement", "isin", "price_quote_unit",
        "quantity_min", "quantity_step", "maturity_date", "payment_currency",
        "coupon_terms", "amortization_terms", "fee_schedule", "trading_session",
    }),
    "CAUCIONES": frozenset({
        "market", "currency", "settlement", "side", "term_days",
        "principal_min", "principal_step", "day_count_basis",
        "fee_schedule", "trading_session",
    }),
    "OPCIONES": frozenset({
        "market", "currency", "settlement", "underlying", "put_call",
        "strike", "expiry_at", "lot_size", "contract_multiplier", "price_tick",
        "tick_value", "exercise_style", "quantity_min", "quantity_step",
        "fee_schedule", "trading_session",
    }),
    "FUTUROS": frozenset({
        "market", "currency", "settlement", "underlying", "expiry_at",
        "contract_multiplier", "min_price_increment", "tick_value",
        "min_trade_volume", "round_lot", "settlement_method", "fee_schedule",
        "trading_session",
    }),
    "FCI": frozenset({
        "currency", "subscription_min", "subscription_step", "cutoff_time",
        "redemption_term", "nav_unit", "fee_schedule", "manager", "custodian",
    }),
    "FCI_LOCAL": frozenset({
        "currency", "subscription_min", "subscription_step", "cutoff_time",
        "redemption_term", "nav_unit", "fee_schedule", "manager", "custodian",
    }),
    "LICITACIONES": frozenset({
        "currency", "instrument", "open_at", "close_at", "subscription_min",
        "subscription_max", "quantity_min", "quantity_step", "competitive_rules",
        "award_settlement", "proration_rules", "fee_schedule",
    }),
    "ETF": frozenset({
        "market", "currency", "settlement", "isin", "quantity_min",
        "quantity_step", "price_tick", "fee_schedule", "trading_session",
    }),
    "ACCIONES_USA": frozenset({
        "market", "currency", "settlement", "exchange", "isin",
        "quantity_min", "quantity_step", "fractional_allowed", "fee_schedule",
        "trading_session",
    }),
    "FCI_EXTERIOR": frozenset({
        "currency", "jurisdiction", "subscription_min", "subscription_step",
        "cutoff_time", "redemption_term", "nav_unit", "fee_schedule",
        "manager", "custodian", "restrictions",
    }),
    "CANJES": frozenset({
        "market", "currency", "eligible_species", "exchange_ratio", "open_at",
        "close_at", "settlement", "quantity_min", "quantity_step", "fee_schedule",
    }),
}

# Condiciones dinámicas que no deben confundirse con contrato. Se exigen para
# READY_PAPER_CANDIDATE, pero se ingieren a otra frecuencia.
FAMILY_DYNAMIC_FIELDS = {
    "ACCIONES": frozenset({"operable", "market_session_state"}),
    "CEDEARS": frozenset({"operable", "market_session_state"}),
    "BONOS": frozenset({"operable", "market_session_state"}),
    "LETRAS": frozenset({"operable", "market_session_state"}),
    "ON": frozenset({"operable", "market_session_state"}),
    "OBLIGACIONES": frozenset({"operable", "market_session_state"}),
    "CAUCIONES": frozenset({
        "operable", "market_session_state", "tna", "available_principal", "expiry_at",
    }),
    "OPCIONES": frozenset({"operable", "market_session_state"}),
    "FUTUROS": frozenset({
        "operable", "market_session_state", "margin_requirement", "available_to_operate",
    }),
    "FCI": frozenset({"subscription_status", "nav_value", "nav_date"}),
    "FCI_LOCAL": frozenset({"subscription_status", "nav_value", "nav_date"}),
    "LICITACIONES": frozenset({"auction_status"}),
    "ETF": frozenset({"operable", "market_session_state"}),
    "ACCIONES_USA": frozenset({"operable", "market_session_state"}),
    "FCI_EXTERIOR": frozenset({"subscription_status", "nav_value", "nav_date"}),
    "CANJES": frozenset({"auction_status"}),
}

# TTL de condiciones dinámicas, en horas. Sólo aplica a FAMILY_DYNAMIC_FIELDS.
# 5 min = 1/12 h; 15 min = 1/4 h. NAV tiene ciclo diario.
DYNAMIC_TTL_HOURS = {
    "tna": 1 / 12,
    "available_principal": 1 / 12,
    "auction_status": 1 / 12,
    "market_session_state": 1 / 12,
    "operable": 1 / 4,
    "margin_requirement": 1 / 4,
    "available_to_operate": 1 / 4,
    "subscription_status": 1 / 4,
    "expiry_at": 1 / 4,
    "nav_value": 36.0,
    "nav_date": 36.0,
}


def _present(value) -> bool:
    return value not in (None, "", [], {})


def _parse_at(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def merge_evidence(records):
    """Merge only when sources agree; highest-ranked source wins provenance.

    Contract Evidence v2 stores ticker/market/settlement as normalized identity
    columns. Explicit non-placeholder identity values are therefore admissible
    readiness evidence; wildcards/UNKNOWN remain fail-closed. This is a wiring
    bridge only, never an inference of economic terms.
    """
    records = [r for r in (records or []) if isinstance(r, dict)]
    conflicts = evidence_v2.source_conflict(records)
    if conflicts:
        return {}, {}, conflicts

    candidates = {}
    for record in records:
        source = record.get("source_class")
        if source not in evidence_v2.SOURCE_RANK:
            continue
        rank = evidence_v2.SOURCE_RANK[source]
        observed_at = record.get("observed_at")
        payload = evidence_v2._readiness_evidence(record)
        if not isinstance(payload, dict):
            continue
        for field, value in payload.items():
            if not _present(value):
                continue
            current = candidates.get(field)
            item = (rank, source, observed_at, value)
            if current is None or rank < current[0]:
                candidates[field] = item

    merged = {field: item[3] for field, item in candidates.items()}
    provenance = {
        field: {"source_class": item[1], "observed_at": item[2]}
        for field, item in candidates.items()
    }
    return merged, provenance, {}


def _stale_dynamic(fields, provenance, now):
    stale = []
    for field in fields:
        at = _parse_at(provenance.get(field, {}).get("observed_at"))
        if at is None:
            stale.append(field)
            continue
        age = (now - at.astimezone(timezone.utc)).total_seconds() / 3600
        ttl = float(DYNAMIC_TTL_HOURS.get(field, 0.25))
        if age > ttl:
            stale.append(field)
    return sorted(stale)


def evaluate_family(family: str, records, *, now=None) -> dict:
    """Evaluate contract + dynamic completeness without granting READY_PAPER."""
    family = str(family or "").upper()
    contract_fields = FAMILY_CONTRACT_FIELDS.get(family)
    dynamic_fields = FAMILY_DYNAMIC_FIELDS.get(family)
    if contract_fields is None or dynamic_fields is None:
        return {
            "family": family,
            "status": "FAIL_CLOSED",
            "missing_contract": [],
            "missing_dynamic": [],
            "conflicts": {},
            "detail": "FAMILIA_SIN_REGLA_CONTRACTUAL_V2",
        }

    merged, provenance, conflicts = merge_evidence(records)
    if conflicts:
        return {
            "family": family,
            "status": "CONFLICT",
            "missing_contract": [],
            "missing_dynamic": [],
            "conflicts": conflicts,
            "detail": "Fuentes permitidas discrepan; revisión obligatoria.",
        }

    missing_contract = sorted(
        field for field in contract_fields if not _present(merged.get(field))
    )
    if missing_contract:
        return {
            "family": family,
            "status": "MISSING_CONTRACT",
            "missing_contract": missing_contract,
            "missing_dynamic": [],
            "conflicts": {},
            "evidence": merged,
            "provenance": provenance,
            "detail": f"Faltan {len(missing_contract)} término(s) contractuales.",
        }

    missing_dynamic = sorted(
        field for field in dynamic_fields if not _present(merged.get(field))
    )
    if missing_dynamic:
        return {
            "family": family,
            "status": "MISSING_DYNAMIC",
            "missing_contract": [],
            "missing_dynamic": missing_dynamic,
            "conflicts": {},
            "evidence": merged,
            "provenance": provenance,
            "detail": f"Contrato completo; faltan {len(missing_dynamic)} condición(es) dinámicas.",
        }

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stale_dynamic = _stale_dynamic(dynamic_fields, provenance, now)
    if stale_dynamic:
        return {
            "family": family,
            "status": "STALE_DYNAMIC",
            "missing_contract": [],
            "missing_dynamic": [],
            "stale_dynamic": stale_dynamic,
            "conflicts": {},
            "evidence": merged,
            "provenance": provenance,
            "detail": "Contrato completo pero condición dinámica vencida según TTL por campo.",
        }

    return {
        "family": family,
        "status": "READY_PAPER_CANDIDATE",
        "missing_contract": [],
        "missing_dynamic": [],
        "stale_dynamic": [],
        "conflicts": {},
        "evidence": merged,
        "provenance": provenance,
        "detail": (
            "Contrato completo y dinámica fresca. Aún requiere adaptador, simulador, "
            "tests y revisión de integración antes de READY_PAPER."
        ),
    }
