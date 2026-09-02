"""Read-only extension for the documented PPI Bonds/Estimate endpoint.

No account or order APIs are exposed. The endpoint is GET-only and is added to
the existing fail-closed production allowlist. This module deliberately does
not interpret nominalInPrice or derive a trading multiplier.
"""
from __future__ import annotations

from datetime import datetime, timezone

import bd_ppi_readonly_guard as readonly


BONDS_ESTIMATE_PATH = "/api/1.0/marketdata/bonds/estimate"


def _estimate_bond(self, ticker: str, *, price: float, quantity: float = 1.0,
                   quantity_type: str = "PAPELES", at=None):
    ticker = str(ticker or "").strip().upper()
    if not ticker:
        raise ValueError("PPI_BOND_ESTIMATE_TICKER_REQUIRED")
    try:
        price = float(price)
        quantity = float(quantity)
    except (TypeError, ValueError) as exc:
        raise ValueError("PPI_BOND_ESTIMATE_NUMERIC_INPUT_REQUIRED") from exc
    if price <= 0 or quantity <= 0:
        raise ValueError("PPI_BOND_ESTIMATE_POSITIVE_INPUT_REQUIRED")
    quantity_type = str(quantity_type or "").strip().upper()
    if quantity_type != "PAPELES":
        # HF6 uses this endpoint only as evidence for quoted-paper semantics.
        # Other quantity modes are not required and remain closed.
        raise ValueError("PPI_BOND_ESTIMATE_ONLY_PAPELES_EVIDENCE_ALLOWED")
    from ppi_client.models.estimate_bonds import EstimateBonds
    when = at or datetime.now(timezone.utc)
    request = EstimateBonds(
        ticker=ticker,
        date=when,
        quantityType=quantity_type,
        quantity=quantity,
        price=price,
    )
    return self._market().estimate_bonds(request)


def install():
    readonly._GET_PATHS.add(BONDS_ESTIMATE_PATH)
    if not hasattr(readonly.ProductionMarketReader, "estimate_bond"):
        readonly.ProductionMarketReader.estimate_bond = _estimate_bond
    return True


install()
