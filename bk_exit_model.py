"""Modelo canónico de barreras de salida para la estrategia PAPER spot.

El precio de entrada de PAPER se protege con un stop y un objetivo porcentuales
fijos, versionados y persistibles. El módulo es puro: no lee entorno, no hace
I/O y no ejecuta órdenes. Backtests/replays que pretendan comparar resultados
con PAPER deben usar esta misma función o declararse explícitamente como
estrategias candidatas no comparables.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from bs_instrument_contracts import decimal_value


PAPER_EXIT_MODEL = "PAPER_FIXED_PERCENT_V1"


@dataclass(frozen=True)
class ExitBarriers:
    model: str
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal
    stop_loss_pct: Decimal
    target_gain_pct: Decimal


def fixed_percent_barriers(entry_price, *, stop_loss_pct, target_gain_pct) -> ExitBarriers:
    """Calcula las barreras PAPER sin redondear el riesgo hacia abajo.

    El redondeo de precios ejecutables pertenece al adaptador de mercado. La
    estrategia conserva los valores exactos en el ledger y el supervisor sólo
    compara contra puntas realmente observadas.
    """
    entry = decimal_value(entry_price, "precio de entrada", positive=True)
    stop_pct = decimal_value(stop_loss_pct, "stop", positive=True)
    target_pct = decimal_value(target_gain_pct, "objetivo", positive=True)
    if stop_pct >= 1 or target_pct >= 1:
        raise ValueError("Las barreras porcentuales deben ser menores a 100%")
    return ExitBarriers(
        model=PAPER_EXIT_MODEL,
        entry_price=entry,
        stop_price=entry * (Decimal("1") - stop_pct),
        target_price=entry * (Decimal("1") + target_pct),
        stop_loss_pct=stop_pct,
        target_gain_pct=target_pct,
    )
