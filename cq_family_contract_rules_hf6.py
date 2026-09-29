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
        "market", "currency", "settlement",
    }),
    "CEDEARS": frozenset({
        "market", "currency", "settlement",
    }),
    "BONOS": frozenset({
        "market", "currency", "settlement", "cash_multiplier",
        "quantity_min", "quantity_step", "maturity_date", "payment_currency",
        "coupon_terms", "amortization_terms",
    }),
    "LETRAS": frozenset({
        "market", "currency", "settlement", "cash_multiplier",
        "quantity_min", "quantity_step", "maturity_date",
    }),
    "ON": frozenset({
        "market", "currency", "settlement", "cash_multiplier",
        "quantity_min", "quantity_step", "maturity_date", "payment_currency",
        "coupon_terms", "amortization_terms",
    }),
    "OBLIGACIONES": frozenset({
        "market", "currency", "settlement", "cash_multiplier",
        "quantity_min", "quantity_step", "maturity_date", "payment_currency",
        "coupon_terms", "amortization_terms",
    }),
    "CAUCIONES": frozenset({
        "market", "currency", "settlement", "side", "term_days",
        "start_date", "maturity_at", "minimum_principal", "principal_step",
        "day_count_basis", "fee_payment",
    }),
    "OPCIONES": frozenset({
        "market", "currency", "settlement", "underlying", "put_call",
        "strike", "expiry_at", "cash_multiplier", "exercise_style",
        "quantity_min", "quantity_step",
    }),
    "FUTUROS": frozenset({
        "market", "currency", "settlement", "underlying", "expiry_at",
        "cash_multiplier", "quantity_min", "quantity_step", "settlement_method",
    }),
    "FCI": frozenset({
        "currency", "subscription_min", "subscription_step", "cutoff_time",
        "redemption_term", "nav_unit", "manager", "custodian",
    }),
    "FCI_LOCAL": frozenset({
        "currency", "subscription_min", "subscription_step", "cutoff_time",
        "redemption_term", "nav_unit", "manager", "custodian",
    }),
    "LICITACIONES": frozenset({
        "currency", "instrument", "open_at", "close_at", "subscription_min",
        "subscription_max", "quantity_min", "quantity_step", "competitive_rules",
        "award_settlement", "proration_rules",
    }),
    "ETF": frozenset({
        "market", "currency", "settlement",
    }),
    "ACCIONES_USA": frozenset({
        "market", "currency", "settlement", "exchange", "fractional_allowed",
    }),
    "FCI_EXTERIOR": frozenset({
        "currency", "jurisdiction", "subscription_min", "subscription_step",
        "cutoff_time", "redemption_term", "nav_unit",
        "manager", "custodian", "restrictions",
    }),
    "CANJES": frozenset({
        "market", "currency", "eligible_species", "exchange_ratio", "open_at",
        "close_at", "settlement", "quantity_min", "quantity_step",
    }),
}

PROFILES = frozenset({"OPEN", "CLOSE", "EVENT", "FULL"})
EVENT_CONDITIONAL_FIELDS = {
    "BONOS": frozenset({"maturity_date", "payment_currency", "coupon_terms", "amortization_terms"}),
    "LETRAS": frozenset({"maturity_date"}),
    "ON": frozenset({"maturity_date", "payment_currency", "coupon_terms", "amortization_terms"}),
    "OBLIGACIONES": frozenset({"maturity_date", "payment_currency", "coupon_terms", "amortization_terms"}),
    "OPCIONES": frozenset({"exercise_style"}),
    "FUTUROS": frozenset({"settlement_method"}),
    "FCI": frozenset({"cutoff_time", "redemption_term", "nav_unit"}),
    "FCI_LOCAL": frozenset({"cutoff_time", "redemption_term", "nav_unit"}),
    "FCI_EXTERIOR": frozenset({"cutoff_time", "redemption_term", "nav_unit"}),
}

EVENT_DYNAMIC_FIELDS = {
    "FCI": frozenset({"nav_value", "nav_date"}),
    "FCI_LOCAL": frozenset({"nav_value", "nav_date"}),
    "FCI_EXTERIOR": frozenset({"nav_value", "nav_date"}),
}

# Broker-account controls protect real money.  They are intentionally outside
# every PAPER profile; contract margin and Porota's own paper cash/risk controls
# remain required where applicable.
REAL_ACCOUNT_ONLY_FIELDS = frozenset({
    "account_balance", "buying_power", "real_collateral",
    "broker_account_permission", "ppi_rofex_enabled",
})

# These descriptive/analytical terms are preserved as enrichment. They do not
# enter local spot certificate cashflows or subscription/redemption arithmetic.
# Lifecycle/event terms and every dynamic/risk gate remain required.
FAMILY_ENRICHMENT_FIELDS = {
    "ACCIONES": frozenset({"isin", "price_tick", "trading_session"}),
    "CEDEARS": frozenset({"conversion_ratio", "isin", "price_tick", "trading_session"}),
    "BONOS": frozenset({"isin", "price_quote_unit", "fee_schedule", "trading_session"}),
    "LETRAS": frozenset({"isin", "price_quote_unit", "fee_schedule", "trading_session"}),
    "ON": frozenset({"isin", "price_quote_unit", "fee_schedule", "trading_session"}),
    "OBLIGACIONES": frozenset({"isin", "price_quote_unit", "fee_schedule", "trading_session"}),
    "OPCIONES": frozenset({"lot_size", "contract_multiplier", "price_tick", "tick_value", "fee_schedule", "trading_session"}),
    "FUTUROS": frozenset({"contract_multiplier", "min_price_increment", "tick_value", "min_trade_volume", "round_lot", "fee_schedule", "trading_session"}),
    "CAUCIONES": frozenset({"fee_schedule", "trading_session"}),
    "FCI": frozenset({"manager", "custodian"}),
    "FCI_LOCAL": frozenset({"manager", "custodian"}),
    "FCI_EXTERIOR": frozenset({"manager", "custodian"}),
}
FAMILY_CONTRACT_FIELDS = {
    family: fields - FAMILY_ENRICHMENT_FIELDS.get(family, frozenset())
    for family, fields in FAMILY_CONTRACT_FIELDS.items()
}

# Condiciones dinámicas que no deben confundirse con contrato. Se exigen para
# READY_PAPER_CANDIDATE, pero se ingieren a otra frecuencia.
FAMILY_DYNAMIC_FIELDS = {
    # Spot/option quote freshness, book depth and market session are already
    # enforced by PaperBroker/Quote/PaperSessionPolicy. Requiring duplicate
    # Evidence-v2 flags here produced false blockers without adding safety.
    "ACCIONES": frozenset(),
    "CEDEARS": frozenset(),
    "BONOS": frozenset(),
    "LETRAS": frozenset(),
    "ON": frozenset(),
    "OBLIGACIONES": frozenset(),
    "CAUCIONES": frozenset({
        "operable", "market_session_state", "annual_rate_fraction",
        "available_principal", "quoted_at",
    }),
    "OPCIONES": frozenset(),
    # Argentina Clearing publishes one margin per contract/month position. A
    # second broker-style maintenance margin is not an independently published
    # term and has no distinct PAPER consumer: the simulator conservatively
    # maintains the full published requirement.
    "FUTUROS": frozenset({"margin_requirement"}),
    # PPI's exact primary-catalog AVAILABLE state already gates admission in
    # the catalog.  Requiring a second, unpublished subscription_status made
    # PAPER depend on a broker-account control with no independent consumer.
    # NAV remains event-only: it is required when applying/settling an event,
    # never to create a simulated subscription request.
    "FCI": frozenset({"nav_value", "nav_date"}),
    "FCI_LOCAL": frozenset({"nav_value", "nav_date"}),
    "LICITACIONES": frozenset({"auction_status"}),
    "ETF": frozenset(),
    "ACCIONES_USA": frozenset(),
    "FCI_EXTERIOR": frozenset({"nav_value", "nav_date"}),
    "CANJES": frozenset({"auction_status"}),
}

# TTL de condiciones dinámicas, en horas. Sólo aplica a FAMILY_DYNAMIC_FIELDS.
# 5 min = 1/12 h; 15 min = 1/4 h. NAV tiene ciclo diario.
DYNAMIC_TTL_HOURS = {
    "annual_rate_fraction": 1 / 12,
    "available_principal": 1 / 12,
    "quoted_at": 1 / 12,
    "auction_status": 1 / 12,
    "market_session_state": 1 / 12,
    "operable": 1 / 4,
    "initial_margin": 24.0,
    "maintenance_margin": 24.0,
    "margin_requirement": 24.0,
    "subscription_status": 1 / 4,
    "expiry_at": 1 / 4,
    "nav_value": 36.0,
    "nav_date": 36.0,
}

# Every blocking field must have a concrete PAPER consumer.  Fields that do
# not appear here are enrichment/event metadata and cannot block OPEN.
FIELD_CONSUMERS = {
    "market": "InstrumentContract.key and PaperBroker market executor gate",
    "currency": "cash ledger and InstrumentContract.key",
    "settlement": "cash availability and InstrumentContract.key",
    "cash_multiplier": "notional, cash, risk and P&L",
    "quantity_min": "InstrumentContract.quantity minimum",
    "quantity_step": "InstrumentContract.quantity rounding",
    "underlying": "long-option/future contract identity",
    "put_call": "long-option payoff direction",
    "strike": "long-option contract validation",
    "expiry_at": "derivative expiry/session guard",
    "side": "caucion placing-only policy",
    "term_days": "caucion term identity/date cross-check",
    "start_date": "caucion accrued-interest clock",
    "maturity_at": "caucion settlement clock",
    "minimum_principal": "CaucionOffer.economics lower bound",
    "principal_step": "CaucionOffer.economics rounding",
    "day_count_basis": "caucion interest calculation",
    "fee_payment": "caucion cash timing",
    "annual_rate_fraction": "caucion interest calculation",
    "available_principal": "caucion depth/participation",
    "quoted_at": "caucion executable freshness",
    "initial_margin": "future PAPER margin reserve",
    "maintenance_margin": "future PAPER deficit gate",
    "margin_requirement": "future PAPER reserve and conservative deficit gate",
    "subscription_min": "FCI PAPER subscription validation",
    "subscription_step": "FCI PAPER subscription rounding",
    "subscription_status": "FCI subscription admission",
    "nav_value": "FCI NAV_APPLIED ledger event",
    "nav_date": "FCI NAV freshness",
    "maturity_date": "fixed-income EVENT maturity guard",
    "payment_currency": "fixed-income EVENT cashflow ledger",
    "coupon_terms": "fixed-income EVENT coupon cashflow",
    "amortization_terms": "fixed-income EVENT nominal reduction",
    "exercise_style": "option EVENT exercise handling",
    "settlement_method": "future EVENT close/expiry settlement",
    "cutoff_time": "FCI EVENT NAV assignment window",
    "redemption_term": "FCI EVENT settlement date",
    "nav_unit": "FCI EVENT cuotaparte conversion",
    "operable": "specialized caucion admission",
    "market_session_state": "specialized caucion session admission",
    "exchange": "external-equity venue executor selection",
    "fractional_allowed": "external-equity quantity validation",
    "jurisdiction": "external-fund lifecycle policy",
    "restrictions": "external-fund subscription policy",
    "instrument": "auction instrument identity",
    "open_at": "auction/event admission window",
    "close_at": "auction/event admission window",
    "subscription_max": "auction/fund upper amount validation",
    "competitive_rules": "auction PAPER allocation model",
    "award_settlement": "auction award cash ledger",
    "proration_rules": "auction PAPER allocation model",
    "auction_status": "auction/event admission",
    "eligible_species": "exchange-event eligibility",
    "exchange_ratio": "exchange-event quantity conversion",
}

POSITIVE_CONTRACT_FIELDS = frozenset({
    "cash_multiplier", "quantity_min", "quantity_step", "strike",
    "term_days", "minimum_principal", "principal_step", "subscription_min",
    "subscription_step", "subscription_max", "exchange_ratio",
})


def _positive_number(value):
    from decimal import Decimal, InvalidOperation
    try:
        x = Decimal(str(value))
        return x.is_finite() and x > 0
    except (InvalidOperation, TypeError, ValueError):
        return False


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
            if field in evidence_v2.PROVENANCE_ONLY_FIELDS:
                continue
            if not _present(value):
                continue
            current = candidates.get(field)
            item = (
                rank, source, observed_at, value,
                payload.get("provider_timestamp"),
                payload.get("freshness_basis"),
            )
            if current is None or rank < current[0]:
                candidates[field] = item

    merged = {field: item[3] for field, item in candidates.items()}
    provenance = {
        field: {
            "source_class": item[1], "observed_at": item[2],
            "provider_timestamp": item[4], "freshness_basis": item[5],
        }
        for field, item in candidates.items()
    }
    return merged, provenance, conflicts


def _stale_dynamic(fields, provenance, now):
    stale = []
    for field in fields:
        detail = provenance.get(field, {})
        if detail.get("freshness_basis") != "PROVIDER_TIMESTAMP":
            stale.append(field)
            continue
        at = _parse_at(detail.get("provider_timestamp"))
        if at is None:
            stale.append(field)
            continue
        age = (now - at.astimezone(timezone.utc)).total_seconds() / 3600
        ttl = float(DYNAMIC_TTL_HOURS.get(field, 0.25))
        if age < 0 or age > ttl:
            stale.append(field)
    return sorted(stale)


def _missing_dynamic(fields, merged, provenance):
    """Return absent/invalid dynamics, including values lacking provider time.

    Capture/collection time is provenance for the observation, not evidence of
    when the provider produced a dynamic quote, margin, status or NAV.
    """
    missing = []
    for field in fields:
        value = merged.get(field)
        invalid_value = (
            not _present(value)
            or (field == "operable" and value is not True)
            or (field in {"market_session_state", "subscription_status", "auction_status"}
                and str(value).upper() not in {"OPEN", "ACTIVE", "AVAILABLE"})
            or (field in {"nav_value", "annual_rate_fraction", "available_principal",
                          "initial_margin", "maintenance_margin", "margin_requirement"}
                and not _positive_number(value))
        )
        detail = provenance.get(field, {})
        provider_time_missing = (
            detail.get("freshness_basis") != "PROVIDER_TIMESTAMP"
            or _parse_at(detail.get("provider_timestamp")) is None
        )
        if invalid_value or provider_time_missing:
            missing.append(field)
    return sorted(missing)


def _profile_fields(family, profile):
    contract = FAMILY_CONTRACT_FIELDS[family]
    events = EVENT_CONDITIONAL_FIELDS.get(family, frozenset())
    dynamic = FAMILY_DYNAMIC_FIELDS[family]
    dynamic_events = EVENT_DYNAMIC_FIELDS.get(family, frozenset())
    if profile in {"OPEN", "CLOSE"}:
        return contract - events, events, dynamic - dynamic_events, dynamic_events
    if profile == "EVENT":
        return events, frozenset(), dynamic_events, frozenset()
    return contract, frozenset(), dynamic, frozenset()


def evaluate_family(family: str, records, *, profile="FULL", now=None) -> dict:
    """Evaluate contract + dynamic completeness without granting READY_PAPER."""
    family = str(family or "").upper()
    profile = str(profile or "FULL").upper()
    contract_fields = FAMILY_CONTRACT_FIELDS.get(family)
    dynamic_fields = FAMILY_DYNAMIC_FIELDS.get(family)
    if profile not in PROFILES:
        return {
            "family": family, "profile": profile, "status": "FAIL_CLOSED",
            "missing_contract": [], "missing_dynamic": [], "conflicts": {},
            "detail": "PROFILE_NO_SOPORTADO",
        }
    if contract_fields is None or dynamic_fields is None:
        return {
            "family": family, "profile": profile,
            "status": "FAIL_CLOSED",
            "missing_contract": [],
            "missing_dynamic": [],
            "conflicts": {},
            "detail": "FAMILIA_SIN_REGLA_CONTRACTUAL_V2",
        }

    required_contract, event_fields, required_dynamic, event_dynamic = _profile_fields(family, profile)
    if profile == "EVENT" and not required_contract and not required_dynamic:
        return {
            "family": family, "profile": profile, "status": "NOT_APPLICABLE",
            "missing_contract": [], "missing_dynamic": [], "conflicts": {},
            "event_missing": [], "detail": "SIN_EVENTO_CONTRACTUAL_ESPECIAL",
        }
    merged, provenance, conflicts = merge_evidence(records)
    ignored_account_fields = sorted(REAL_ACCOUNT_ONLY_FIELDS & set(merged))
    invalid_contract = sorted(
        field for field in required_contract
        if _present(merged.get(field)) and field in POSITIVE_CONTRACT_FIELDS
        and not _positive_number(merged.get(field))
    )
    missing_contract = sorted(
        field for field in required_contract
        if not _present(merged.get(field)) or field in invalid_contract
    )
    event_missing = sorted(
        field for field in event_fields | event_dynamic if not _present(merged.get(field))
    )
    missing_dynamic = _missing_dynamic(required_dynamic, merged, provenance)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stale_dynamic = _stale_dynamic(
        set(required_dynamic) - set(missing_dynamic), provenance, now)
    if conflicts:
        return {
            "family": family, "profile": profile,
            "status": "CONFLICT",
            "missing_contract": missing_contract,
            "missing_dynamic": missing_dynamic,
            "stale_dynamic": stale_dynamic,
            "invalid_contract": invalid_contract,
            "conflicts": conflicts,
            "evidence": merged,
            "provenance": provenance,
            "event_missing": event_missing,
            "ignored_real_account_fields": ignored_account_fields,
            "detail": "Fuentes permitidas discrepan; revisión obligatoria.",
        }
    if missing_contract:
        return {
            "family": family, "profile": profile,
            "status": "MISSING_CONTRACT",
            "missing_contract": missing_contract,
            "missing_dynamic": missing_dynamic,
            "stale_dynamic": stale_dynamic,
            "invalid_contract": invalid_contract,
            "conflicts": {},
            "evidence": merged,
            "provenance": provenance,
            "event_missing": event_missing,
            "ignored_real_account_fields": ignored_account_fields,
            "detail": f"Faltan {len(missing_contract)} término(s) contractuales.",
        }

    if missing_dynamic:
        return {
            "family": family, "profile": profile,
            "status": "MISSING_DYNAMIC",
            "missing_contract": [],
            "missing_dynamic": missing_dynamic,
            "invalid_contract": [],
            "conflicts": {},
            "evidence": merged,
            "provenance": provenance,
            "event_missing": event_missing,
            "ignored_real_account_fields": ignored_account_fields,
            "detail": f"Contrato completo; faltan {len(missing_dynamic)} condición(es) dinámicas.",
        }

    if stale_dynamic:
        return {
            "family": family, "profile": profile,
            "status": "STALE_DYNAMIC",
            "missing_contract": [],
            "missing_dynamic": [],
            "stale_dynamic": stale_dynamic,
            "invalid_contract": [],
            "conflicts": {},
            "evidence": merged,
            "provenance": provenance,
            "event_missing": event_missing,
            "ignored_real_account_fields": ignored_account_fields,
            "detail": "Contrato completo pero condición dinámica vencida según TTL por campo.",
        }

    return {
        "family": family, "profile": profile,
        "status": "READY_PAPER_CANDIDATE",
        "missing_contract": [],
        "missing_dynamic": [],
        "stale_dynamic": [],
        "invalid_contract": [],
        "conflicts": {},
        "evidence": merged,
        "provenance": provenance,
        "event_missing": event_missing,
        "event_classification": "EVENT_CONDITIONAL" if event_missing else "COMPLETE",
        "ignored_real_account_fields": ignored_account_fields,
        "legacy_global_ttl_ignored": True,
        "detail": (
            "Contrato completo y dinámica fresca. Aún requiere adaptador, simulador, "
            "tests y revisión de integración antes de READY_PAPER."
        ),
    }
