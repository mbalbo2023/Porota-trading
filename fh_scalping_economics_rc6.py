"""Economía de costos para scalping PAPER RC6.

El scalping abre y cierra la misma identidad de mercado/moneda/liquidación en
la misma rueda. Para familias elegibles del mercado local PPI bonifica el 100%
del arancel de la operación de menor valor; los derechos de mercado y su IVA
no quedan bonificados. La fuente canónica de tarifas sigue siendo
``au_fee_schedule``.

Este módulo es puro: no usa red, DB ni rutas de órdenes.
"""
from __future__ import annotations

from decimal import Decimal

import au_fee_schedule


SLIPPAGE_BUFFER = Decimal("0.0004")


def modeled_roundtrip_cost(family: str, spread, *, slippage=SLIPPAGE_BUFFER) -> dict:
    """Devuelve el costo redondo conservador para un scalp PAPER.

    Para familias alcanzadas por la bonificación intradiaria usa una punta
    completa + una punta bonificada. Para familias no alcanzadas conserva dos
    puntas completas. El spread se paga una vez y se suma un buffer explícito
    de slippage.
    """
    full_leg = Decimal(str(au_fee_schedule.costo_por_tramo(family)))
    spread_fraction = Decimal(str(spread))
    slippage_fraction = Decimal(str(slippage))
    if spread_fraction < 0 or slippage_fraction < 0:
        raise ValueError("SCALPING_COST_NEGATIVE_COMPONENT")
    try:
        discounted_leg = Decimal(str(au_fee_schedule.costo_por_tramo_bonificado(family)))
        fee_model = "PPI_INTRADAY_BONUS"
    except ValueError:
        discounted_leg = full_leg
        fee_model = "FULL_BOTH_LEGS"
    total = full_leg + discounted_leg + spread_fraction + slippage_fraction
    return {
        "modeled_roundtrip_fraction": total,
        "full_leg_fee_fraction": full_leg,
        "discounted_leg_fee_fraction": discounted_leg,
        "spread_fraction": spread_fraction,
        "slippage_buffer_fraction": slippage_fraction,
        "fee_model": fee_model,
    }


def assert_no_execution_capability():
    # Guard documental/ejecutable para tests de safety.
    assert not hasattr(au_fee_schedule, "send_order")
