"""RC6 shadow diagnostics for published costs, settlement and take profit.

Personal taxes are intentionally out of scope. This module only reports
published broker/market costs already represented by au_fee_schedule.
"""
from __future__ import annotations

from decimal import Decimal


def published_cost_diagnostic(gross_gain):
    return {
        "state": "NOT_APPLICABLE",
        "rate": None,
        "amount": "0",
        "effect": "PERSONAL_TAXES_OUT_OF_SCOPE",
        "published_costs_source": "au_fee_schedule",
    }


def net_take_profit(entry, target, quantity, factor, entry_cost, exit_cost,
                    gross_gain=None):
    entry = Decimal(str(entry))
    target = Decimal(str(target))
    quantity = Decimal(str(quantity))
    factor = Decimal(str(factor))
    entry_cost = Decimal(str(entry_cost))
    exit_cost = Decimal(str(exit_cost))
    gross = ((target - entry) * quantity * factor
             if gross_gain is None else Decimal(str(gross_gain)))
    published = published_cost_diagnostic(gross)
    return {
        "gross_pnl": str(gross),
        "known_costs": str(entry_cost + exit_cost),
        "published_costs_note": published,
        "net_pnl_after_published_costs": str(gross - entry_cost - exit_cost),
        "state": "PARTIAL",
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
