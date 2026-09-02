"""HF6-v2: requisitos contractuales fail-closed por familia financiera.

La salida máxima automática es READY_PAPER_CANDIDATE. Este módulo nunca
habilita trading por sí mismo. READY_PAPER requiere además adaptador de sizing,
simulador/exit específico, tests, evidencia fresca y aprobación de integración.
"""
from __future__ import annotations

from datetime import datetime, timezone

import cp_contract_evidence_v2_hf6 as evidence_v2


FAMILY_REQUIRED_FIELDS = {
    "ACCIONES": frozenset({
        "market", "currency", "settlement", "quantity_min", "quantity_step",
        "price_tick", "fee_schedule",
    }),
    "CEDEARS": frozenset({
        "market", "currency", "settlement", "quantity_min", "quantity_step",
        "price_tick", "conversion_ratio", "fee_schedule",
    }),
    "BONOS": frozenset({
        "market", "currency", "settlement", "isin", "price_quote_unit",
        "quantity_min", "quantity_step", "maturity_date", "payment_currency",
        "coupon_terms", "amortization_terms", "fee_schedule",
    }),
    "LETRAS": frozenset({
        "market", "currency", "settlement", "isin", "price_quote_unit",
        "quantity_min", "quantity_step", "maturity_date", "fee_schedule",
    }),
    "ON": frozenset({
        "market", "currency", "settlement", "isin", "price_quote_unit",
        "quantity_min", "quantity_step", "maturity_date", "payment_currency",
        "coupon_terms", "amortization_terms", "fee_schedule",
    }),
    "OBLIGACIONES": frozenset({
        "market", "currency", "settlement", "isin", "price_quote_unit",
        "quantity_min", "quantity_step", "maturity_date", "payment_currency",
        "coupon_terms", "amortization_terms", "fee_schedule",
    }),
    "CAUCIONES": frozenset({
        "market", "currency", "settlement", "side", "term_days", "tna",
        "principal_min", "principal_step", "day_count_basis", "expiry_at",
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
        "min_trade_volume", "round_lot", "margin_requirement",
        "collateral_rules", "settlement_method", "fee_schedule", "trading_session",
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

# No se exige un TTL artificialmente corto a términos estáticos; observed_at se
# controla por snapshot y hash diario. Horarios/catálogos dinámicos pueden usar
# TTL más estricto en el scheduler que los recolecta.
DEFAULT_MAX_AGE_HOURS = 48


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
    """Merge only when sources agree; highest-ranked source wins provenance."""
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
        payload = record.get("evidence") or {}
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


def evaluate_family(family: str, records, *, now=None,
                    max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> dict:
    """Evaluate contract completeness without granting READY_PAPER."""
    family = str(family or "").upper()
    required = FAMILY_REQUIRED_FIELDS.get(family)
    if required is None:
        return {
            "family": family,
            "status": "FAIL_CLOSED",
            "missing": [],
            "conflicts": {},
            "detail": "FAMILIA_SIN_REGLA_CONTRACTUAL_V2",
        }

    merged, provenance, conflicts = merge_evidence(records)
    if conflicts:
        return {
            "family": family,
            "status": "CONFLICT",
            "missing": [],
            "conflicts": conflicts,
            "detail": "Fuentes oficiales/permitidas discrepan; revisión obligatoria.",
        }

    missing = sorted(field for field in required if not _present(merged.get(field)))
    if missing:
        return {
            "family": family,
            "status": "MISSING",
            "missing": missing,
            "conflicts": {},
            "evidence": merged,
            "provenance": provenance,
            "detail": f"Faltan {len(missing)} campo(s) contractuales obligatorios.",
        }

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stale = []
    for field in required:
        at = _parse_at(provenance.get(field, {}).get("observed_at"))
        if at is None:
            stale.append(field)
            continue
        age = (now - at.astimezone(timezone.utc)).total_seconds() / 3600
        if age > max_age_hours:
            stale.append(field)
    if stale:
        return {
            "family": family,
            "status": "STALE",
            "missing": [],
            "stale": sorted(stale),
            "conflicts": {},
            "evidence": merged,
            "provenance": provenance,
            "detail": "Contrato completo pero evidencia vencida según TTL v2.",
        }

    return {
        "family": family,
        "status": "READY_PAPER_CANDIDATE",
        "missing": [],
        "stale": [],
        "conflicts": {},
        "evidence": merged,
        "provenance": provenance,
        "detail": (
            "Contrato completo/fresco. Aún requiere adaptador, simulador, tests "
            "y revisión de integración antes de READY_PAPER."
        ),
    }
