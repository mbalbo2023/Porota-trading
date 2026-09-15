"""RC6 shadow diagnostics for taxes, settlement and take profit.

No function in this module changes a paper position or routes an order.
"""
from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation


def configured_gain_tax_rate():
    raw = os.getenv("PAPER_GAIN_TAX_RATE", "").strip()
    if not raw:
        return None
    try:
        rate = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return rate if Decimal("0") <= rate <= Decimal("1") else None


def tax_diagnostic(gross_gain):
    rate = configured_gain_tax_rate()
    if rate is None:
        return {"state": "UNKNOWN", "rate": None, "amount": None,
                "effect": "NO_BINDING_TAX_ASSUMPTION"}
    gain = max(Decimal("0"), Decimal(str(gross_gain)))
    return {"state": "CONFIGURED", "rate": str(rate),
            "amount": str(gain * rate), "effect": "SHADOW_ONLY"}


def net_take_profit(entry, target, quantity, factor, entry_cost, exit_cost,
                    gross_gain=None):
    entry = Decimal(str(entry)); target = Decimal(str(target))
    quantity = Decimal(str(quantity)); factor = Decimal(str(factor))
    entry_cost = Decimal(str(entry_cost)); exit_cost = Decimal(str(exit_cost))
    gross = (target - entry) * quantity * factor if gross_gain is None else Decimal(str(gross_gain))
    tax = tax_diagnostic(gross)
    tax_amount = Decimal(tax["amount"]) if tax["amount"] is not None else Decimal("0")
    return {
        "gross_pnl": str(gross),
        "known_costs": str(entry_cost + exit_cost),
        "fiscal_tax": tax,
        "net_pnl_known": str(gross - entry_cost - exit_cost),
        "net_pnl_with_configured_tax": str(gross - entry_cost - exit_cost - tax_amount),
        "state": "CONFIGURED" if tax["state"] == "CONFIGURED" else "PARTIAL",
        "effect": "SHADOW_ONLY",
    }


def settlement_diagnostic(settlement, available_at, basis, validated):
    if validated is not None:
        return {"state": "AVAILABLE", "available_at": str(validated),
                "basis": basis, "effect": "DIAGNOSTIC"}
    if str(basis) == "PENDING_CONFIRMATION":
        return {"state": "PENDING_CONFIRMATION", "available_at": None,
                "basis": basis, "effect": "FAIL_CLOSED"}
    return {"state": "UNRESOLVED", "available_at": None,
            "basis": basis, "effect": "FAIL_CLOSED"}
