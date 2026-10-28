"""Métricas empíricas sobre fills PAPER cerrados, sin autoajuste ni pronóstico."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


ZERO = Decimal("0")


def _number(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("INVALID_NET_PNL") from exc
    if not number.is_finite():
        raise ValueError("INVALID_NET_PNL")
    return number


def _text(value):
    return format(value.quantize(Decimal("0.0001")), "f")


def empirical_expectancy(positions, *, minimum_sample=30):
    """Resume resultados observados por moneda; no estima causalidad futura.

    E = p(win)*ganancia_media - p(loss)*pérdida_media. Con toda la muestra
    coincide algebraicamente con el PnL medio, pero la forma descompuesta deja
    visibles frecuencia y magnitud. Los ceros se informan por separado.
    """
    if type(minimum_sample) is not int or minimum_sample < 2:
        raise ValueError("INVALID_MINIMUM_SAMPLE")
    groups = {}
    for position in positions:
        if not isinstance(position, dict) or str(position.get("status", "")).upper() != "CLOSED":
            continue
        currency = str(position.get("currency") or "ARS").strip().upper()
        if not currency:
            raise ValueError("INVALID_CURRENCY")
        groups.setdefault(currency, []).append(_number(position.get("net_pnl")))
    result = []
    for currency, values in sorted(groups.items()):
        wins = [value for value in values if value > 0]
        losses = [-value for value in values if value < 0]
        zeros = len(values) - len(wins) - len(losses)
        count = len(values)
        probability_win = Decimal(len(wins)) / Decimal(count)
        probability_loss = Decimal(len(losses)) / Decimal(count)
        average_win = sum(wins, ZERO) / len(wins) if wins else ZERO
        average_loss = sum(losses, ZERO) / len(losses) if losses else ZERO
        expectancy = probability_win * average_win - probability_loss * average_loss
        gross_win = sum(wins, ZERO)
        gross_loss = sum(losses, ZERO)
        result.append({
            "currency": currency,
            "samples": count,
            "wins": len(wins),
            "losses": len(losses),
            "zeros": zeros,
            "win_rate_pct": _text(probability_win * 100),
            "average_win": _text(average_win),
            "average_loss": _text(average_loss),
            "empirical_expectancy": _text(expectancy),
            "net_total": _text(sum(values, ZERO)),
            "profit_factor": (_text(gross_win / gross_loss) if gross_loss else None),
            "sample_state": "OBSERVATIONAL" if count >= minimum_sample else "INSUFFICIENT_SAMPLE",
            "minimum_sample": minimum_sample,
            "binding": False,
        })
    return result
