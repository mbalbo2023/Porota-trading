"""RC6 sanitized normalization of authenticated PPI GET/XHR evidence.

Only explicit provider fields are retained. Decimal precision is not converted
into quantity step/price tick and account-specific available quantity is
excluded. This module has no order capability.
"""
from __future__ import annotations


def _currency(row):
    value = row.get("moneda") if isinstance(row, dict) else None
    if isinstance(value, dict):
        nested = value.get("moneda") if isinstance(value.get("moneda"), dict) else None
        src = nested or value
        return {"description": src.get("descripcion"), "symbol": src.get("simbolo"),
                "provider_id": src.get("id")}
    return None


def _fee_schedule(row):
    """Retain explicit PPI fee components without deriving or estimating new values."""
    if not isinstance(row, dict):
        return None
    fields = {
        "commission_minimum": row.get("comisionMontoMinimo"),
        "commission_rate_estimated": row.get("porcentajeComisionEstimado"),
        "commission_vat_rate_estimated": row.get("porcentajeIVAComisionEstimado"),
        "market_fee_rate": row.get("porcentajeDerechoMercadoYBolsa"),
    }
    out = {k: v for k, v in fields.items() if v not in (None, "")}
    return out or None


def instrumentos_operables(payload):
    rows = (payload or {}).get("payload") if isinstance(payload, dict) else None
    if isinstance(rows, dict):
        rows = rows.get("payload")
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        out.append({
            "instrument_id": row.get("itemId"), "ticker": ticker,
            "provider_name": row.get("nombre"), "currency": _currency(row),
            "operable_auction": row.get("operableSubasta"),
            "commission_minimum": row.get("comisionMontoMinimo"),
            "commission_rate_estimated": row.get("porcentajeComisionEstimado"),
            "commission_vat_rate_estimated": row.get("porcentajeIVAComisionEstimado"),
            "market_fee_rate": row.get("porcentajeDerechoMercadoYBolsa"),
            "fee_schedule": _fee_schedule(row),
            "quantity_decimal_places": row.get("cantidadDecimales"),
            "price_decimal_places": row.get("cantidadDecimalesPrecio"),
            "derived_instrument_count": len(row.get("instrumentosDerivados") or []),
            "quantity_step": None, "price_tick": None,
            "semantic_guard": "DECIMAL_PLACES_ARE_NOT_STEP_OR_TICK",
        })
    return out


def option_underlyings(payload):
    rows = (payload or {}).get("payload") if isinstance(payload, dict) else None
    if isinstance(rows, dict):
        rows = rows.get("payload")
    if not isinstance(rows, list):
        return []
    return [{"underlying_id": r.get("id"), "ticker": str(r.get("ticker") or "").upper(),
             "description": r.get("descripcion")}
            for r in rows if isinstance(r, dict) and r.get("ticker")]


def bond_technical(payload):
    row = (payload or {}).get("payload") if isinstance(payload, dict) else None
    if isinstance(row, dict) and "payload" in row and isinstance(row.get("payload"), dict):
        row = row["payload"]
    if not isinstance(row, dict):
        return {}
    keys = ("id","ticker","nombre","emisor","legislacion","isin","laminaMinima","multiploMinimo",
            "fechaEmision","fechaVencimiento","intereses","amortizacion","pagosPorAnio","tir",
            "modifiedDuration","paridad","interesesCorridos","valorTecnico","valorResidual")
    result = {k: row.get(k) for k in keys if row.get(k) not in (None, "")}
    result["currency"] = _currency(row)

    # Canonical aliases are only direct semantic renames of explicit PPI fields.
    # No order unit, quantity step, price tick or payment currency is inferred.
    if row.get("fechaVencimiento") not in (None, ""):
        result["maturity_date"] = row.get("fechaVencimiento")
    if row.get("intereses") not in (None, "", [], {}):
        result["coupon_terms"] = row.get("intereses")
    if row.get("amortizacion") not in (None, "", [], {}):
        result["amortization_terms"] = row.get("amortizacion")

    result["provider_nominales_en_precio"] = row.get("nominalesEnPrecio")
    result["provider_quantity_decimal_places"] = row.get("cantidadDecimales")
    result["provider_price_decimal_places"] = row.get("cantidadDecimalesPrecio")
    result["order_quantity_step"] = None
    result["order_price_tick"] = None
    result["price_unit_nominals"] = None
    result["semantic_guard"] = "TECHNICAL_FIELDS_DO_NOT_DEFINE_ORDER_UNIT_OR_STEP"
    return result
