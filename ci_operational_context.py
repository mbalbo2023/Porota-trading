"""Contexto observacional de mercado y sectores; nunca decide una operación."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _number(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("INVALID_PRICE") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("INVALID_PRICE")
    return result


def breadth_observation(rows, *, minimum_symbols=4):
    """Mide amplitud entre primera y última muestra; no la llama índice."""
    if type(minimum_symbols) is not int or minimum_symbols < 2:
        raise ValueError("INVALID_MINIMUM_SYMBOLS")
    observations = []
    for row in rows:
        try:
            first, last = _number(row.get("first_price")), _number(row.get("last_price"))
        except (ValueError, AttributeError):
            continue
        change = (last / first - 1) * 100
        observations.append({"symbol": str(row.get("symbol") or "").upper(),
                             "currency": str(row.get("currency") or "UNKNOWN").upper(),
                             "change_pct": change})
    count = len(observations)
    rising = sum(item["change_pct"] > 0 for item in observations)
    falling = sum(item["change_pct"] < 0 for item in observations)
    unchanged = count - rising - falling
    state = "INSUFFICIENT_SAMPLE"
    if count >= minimum_symbols:
        falling_fraction = Decimal(falling) / Decimal(count)
        state = "BEARISH_BREADTH" if falling_fraction >= Decimal("0.70") else "MIXED_OR_POSITIVE"
    return {
        "state": state, "symbols": count, "rising": rising, "falling": falling,
        "unchanged": unchanged, "minimum_symbols": minimum_symbols,
        "policy": "ALERT_ONLY", "binding": False,
        "method": "FIRST_LAST_TRADE_SAMPLE_BREADTH_NOT_AN_INDEX",
    }


def sector_observation(positions):
    """Cuenta sectores sólo cuando el catálogo aporta una etiqueta explícita."""
    counts, unmapped = {}, 0
    for position in positions:
        if not isinstance(position, dict):
            continue
        sector = str(position.get("sector") or "").strip()
        if not sector:
            unmapped += 1
            continue
        counts[sector] = counts.get(sector, 0) + 1
    maximum = max(counts.values(), default=0)
    return {
        "state": "OBSERVED" if counts else "MAP_UNAVAILABLE",
        "groups": [{"sector": key, "open_positions": value}
                   for key, value in sorted(counts.items())],
        "mapped_positions": sum(counts.values()), "unmapped_positions": unmapped,
        "maximum_observed_concentration": maximum,
        "policy": "OBSERVATION_ONLY", "limit": None, "binding": False,
    }
