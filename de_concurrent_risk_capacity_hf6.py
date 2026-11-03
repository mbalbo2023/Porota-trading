"""Dynamic concurrent-risk capacity for HF6 PAPER.

Financial policy:
- normal admission is NOT governed by a small fixed number of open positions;
- the normal concurrent-risk budget is derived from the PAPER daily soft stop;
- realized losses already consumed today reduce capacity; realized gains never
  increase it;
- open-position risk is measured conservatively from entry to modeled stop,
  including modeled exit friction supplied by the caller;
- unrealized daily PnL is not subtracted a second time because the full open
  stop risk already reserves that downside;
- a separate high emergency position cap may exist only as a runaway/bug guard.

Pure calculations only. No broker/network access and no order routing.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")
ONE_HUNDRED = Decimal("100")


def D(value, label: str, *, nonnegative: bool = False, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{label} inválido") from exc
    if not result.is_finite():
        raise ValueError(f"{label} no finito")
    if positive and result <= 0:
        raise ValueError(f"{label} debe ser positivo")
    if nonnegative and result < 0:
        raise ValueError(f"{label} debe ser no negativo")
    return result


@dataclass(frozen=True)
class ConcurrentRiskSnapshot:
    baseline_equity: Decimal
    soft_stop_pct: Decimal
    soft_stop_budget: Decimal
    realized_loss_consumed: Decimal
    open_stop_risk: Decimal
    candidate_stop_risk: Decimal
    remaining_before_candidate: Decimal
    remaining_after_candidate: Decimal
    admitted: bool
    reason: str


def full_trade_stop_risk(*, entry_price, modeled_stop_fill, quantity, cash_multiplier=1,
                         entry_cost=0, modeled_exit_cost=0) -> Decimal:
    """Conservative full-trade loss from entry through modeled stop.

    This is deliberately based on the original trade risk, not on favorable
    unrealized PnL. A gap can still be worse than the modeled stop fill, so the
    existing liquidity/exposure/daily-loss protections remain mandatory.
    """
    entry = D(entry_price, "precio de entrada", positive=True)
    stop = D(modeled_stop_fill, "fill de stop modelado", nonnegative=True)
    qty = D(quantity, "cantidad", positive=True)
    multiplier = D(cash_multiplier, "multiplicador", positive=True)
    buy_cost = D(entry_cost, "costo de entrada", nonnegative=True)
    sell_cost = D(modeled_exit_cost, "costo de salida", nonnegative=True)
    price_loss = max(ZERO, entry - stop) * qty * multiplier
    return price_loss + buy_cost + sell_cost


def capacity(*, baseline_equity, soft_stop_pct, realized_loss_consumed,
             open_stop_risk, candidate_stop_risk) -> ConcurrentRiskSnapshot:
    """Admission capacity anchored to the daily soft-stop budget.

    `realized_loss_consumed` is already the non-negative sum of realized losing
    fills/closures for the local day. Winning realizations are intentionally not
    netted against that amount, so gains can never expand the risk budget.
    Open stop risk is reserved in full, so unrealized loss is not deducted
    separately and cannot be counted twice.
    """
    baseline = D(baseline_equity, "baseline", positive=True)
    soft_pct = D(soft_stop_pct, "soft stop %", positive=True)
    if soft_pct > ONE_HUNDRED:
        raise ValueError("soft stop % fuera de rango")
    realized_loss = D(realized_loss_consumed, "pérdida realizada consumida", nonnegative=True)
    open_risk = D(open_stop_risk, "riesgo abierto", nonnegative=True)
    candidate = D(candidate_stop_risk, "riesgo candidato", nonnegative=True)

    soft_budget = baseline * soft_pct / ONE_HUNDRED
    remaining_before = max(ZERO, soft_budget - realized_loss - open_risk)
    remaining_after = remaining_before - candidate
    admitted = candidate > ZERO and remaining_after >= ZERO
    if candidate <= ZERO:
        reason = "CANDIDATE_RISK_NOT_POSITIVE"
    elif remaining_before <= ZERO:
        reason = "CONCURRENT_RISK_BUDGET_EXHAUSTED"
    elif not admitted:
        reason = "CANDIDATE_EXCEEDS_REMAINING_CONCURRENT_RISK"
    else:
        reason = "CONCURRENT_RISK_CAPACITY_AVAILABLE"
    return ConcurrentRiskSnapshot(
        baseline_equity=baseline,
        soft_stop_pct=soft_pct,
        soft_stop_budget=soft_budget,
        realized_loss_consumed=realized_loss,
        open_stop_risk=open_risk,
        candidate_stop_risk=candidate,
        remaining_before_candidate=remaining_before,
        remaining_after_candidate=max(ZERO, remaining_after),
        admitted=admitted,
        reason=reason,
    )


def derive_emergency_position_cap(*, soft_stop_pct, risk_per_trade_fraction, configured="AUTO") -> tuple[int, str]:
    """Derive the technical runaway cap from the same risk parameters as PAPER.

    The normal admission authority remains ``capacity``.  This cap is only a
    last-resort guard if the financial admission path misbehaves.  ``AUTO``
    avoids embedding an arbitrary count such as 12 or the historical 50.
    An explicit positive integer is permitted as a visible operator override.
    """
    raw = str(configured if configured is not None else "AUTO").strip().upper()
    if raw not in {"", "AUTO", "DERIVED"}:
        try:
            cap = int(raw)
        except Exception as exc:
            raise ValueError("cap técnico explícito inválido") from exc
        if cap < 1:
            raise ValueError("cap técnico explícito debe ser positivo")
        return cap, "OVERRIDE"

    soft_pct = D(soft_stop_pct, "soft stop %", positive=True)
    risk_fraction = D(risk_per_trade_fraction, "riesgo por trade", positive=True)
    if soft_pct > ONE_HUNDRED or risk_fraction > Decimal("1"):
        raise ValueError("parámetros de riesgo fuera de rango")
    soft_fraction = soft_pct / ONE_HUNDRED
    # Ceiling without float conversion. With 1.5% / 0.2% this yields 8.
    ratio = soft_fraction / risk_fraction
    cap = int(ratio.to_integral_value(rounding="ROUND_CEILING"))
    return max(1, cap), "DERIVED"


def emergency_position_guard(open_count, *, emergency_cap) -> str:
    """Runaway protection only; never the normal financial admission rule."""
    count = int(open_count)
    cap = int(emergency_cap)
    if count < 0 or cap < 1:
        raise ValueError("contador/cap técnico inválido")
    return "EMERGENCY_POSITION_CAP" if count >= cap else ""
