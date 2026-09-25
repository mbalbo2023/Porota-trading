"""RC6 historical and candle features for SHADOW evaluation.

This module is deliberately non-binding: it calculates features only from
data available at as_of and never opens, closes, or blocks a paper trade.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation


def _dt(value):
    raw = str(value or "").replace("Z", "+00:00")
    return datetime.fromisoformat(raw)


def _d(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() and result > 0 else None


def _valid_ohlc(row):
    if not isinstance(row, dict):
        return None
    aliases = {
        "open": ("open", "openingPrice"),
        "high": ("high", "max"),
        "low": ("low", "min"),
        "close": ("close", "price"),
    }
    values = {}
    for target, names in aliases.items():
        value = next((row.get(name) for name in names if row.get(name) not in (None, "")), None)
        values[target] = _d(value)
    if any(value is None for value in values.values()):
        return None
    if not values["low"] <= min(values["open"], values["close"]) <= max(values["open"], values["close"]) <= values["high"]:
        return None
    return values


def _mean(values):
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def _trend(closes, span):
    if len(closes) < span:
        return None
    first, last = closes[-span], closes[-1]
    return last / first - Decimal("1") if first > 0 else None


def _history_features(store, q, at):
    rows = []
    try:
        with store.connect() as connection:
            item = connection.execute(
                """SELECT payload_json FROM production_history
                   WHERE symbol=? AND instrument_type=? AND settlement=?
                   ORDER BY downloaded_at DESC LIMIT 1""",
                (q.symbol, q.asset_class, q.settlement),
            ).fetchone()
        if item:
            payload = json.loads(item["payload_json"] or "[]")
            for raw in payload if isinstance(payload, list) else []:
                try:
                    stamp = _dt(raw.get("date"))
                    if stamp <= at:
                        ohlc = _valid_ohlc(raw)
                        if ohlc:
                            rows.append((stamp, ohlc))
                except (TypeError, ValueError):
                    continue
    except Exception:
        return {"state": "HISTORY_UNAVAILABLE", "observations": 0}
    rows.sort(key=lambda item: item[0])
    closes = [item[1]["close"] for item in rows]
    ranges = [item[1]["high"] / item[1]["low"] - Decimal("1") for item in rows]
    returns = [
        closes[index] / closes[index - 1] - Decimal("1")
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]
    trend20 = _trend(closes, 20)
    trend50 = _trend(closes, 50)
    return {
        "state": "READY" if len(closes) >= 20 else "HISTORY_INSUFFICIENT",
        "observations": len(closes),
        "trend_20": str(trend20) if trend20 is not None else None,
        "trend_50": str(trend50) if trend50 is not None else None,
        "avg_range": str(_mean(ranges)) if ranges else None,
        "avg_abs_return": str(_mean([abs(value) for value in returns])) if returns else None,
    }


def _candle_features(store, q, at):
    try:
        with store.connect() as connection:
            rows = connection.execute(
                """SELECT v.body_json
                   FROM candle_versions v
                   JOIN candle_series s ON s.series_id=v.series_id
                   WHERE json_extract(s.identity_json,'$.symbol')=?
                     AND json_extract(s.identity_json,'$.asset_class')=?
                     AND json_extract(s.identity_json,'$.market')=?
                     AND json_extract(s.identity_json,'$.currency')=?
                     AND json_extract(s.identity_json,'$.settlement')=?
                     AND json_extract(s.identity_json,'$.resolution')='5m'
                     AND v.bar_end<=? AND v.known_at<=?
                   ORDER BY v.bar_start DESC LIMIT 60""",
                (q.symbol, q.asset_class, q.market, q.currency, q.settlement,
                 at.isoformat(), at.isoformat()),
            ).fetchall()
    except Exception:
        return {"state": "CANDLES_UNAVAILABLE", "observations": 0}
    bars = []
    for row in reversed(rows):
        try:
            body = json.loads(row["body_json"])
            if body.get("quality") not in {"COMPLETE", "SAMPLED"} or body.get("synthetic"):
                continue
            close, high, low = _d(body.get("close")), _d(body.get("high")), _d(body.get("low"))
            if close and high and low and low <= close <= high:
                bars.append((close, high / low - Decimal("1")))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    closes = [item[0] for item in bars]
    ranges = [item[1] for item in bars]
    short = _mean(closes[-3:]) if len(closes) >= 3 else None
    long = _mean(closes[-15:]) if len(closes) >= 15 else None
    momentum = short / long - Decimal("1") if short and long else None
    return {
        "state": "READY" if len(closes) >= 15 else "CANDLE_INSUFFICIENT",
        "observations": len(closes),
        "momentum_3v15": str(momentum) if momentum is not None else None,
        "avg_range_5m": str(_mean(ranges)) if ranges else None,
        "last_bar_close": str(closes[-1]) if closes else None,
    }


def collect(store, q, at):
    """Collect point-in-time historical/candle features for SHADOW only."""
    # RC6 vigente es multifamilia. Esta capa no habilita por familia: intenta
    # leer evidencia para la identidad exacta y, si no existe, devuelve
    # INSUFFICIENT_DATA/HISTORY_UNAVAILABLE sin fabricar datos ni bloquear
    # otras fuentes.
    if not isinstance(at, datetime):
        at = _dt(at)
    history = _history_features(store, q, at)
    candles = _candle_features(store, q, at)
    ready = history["state"] == "READY" and candles["state"] == "READY"
    shadow_delta = Decimal("0")
    if ready:
        trend = Decimal(history["trend_20"] or "0")
        momentum = Decimal(candles["momentum_3v15"] or "0")
        shadow_delta = max(Decimal("-0.15"), min(Decimal("0.15"),
            trend * Decimal("0.20") + momentum * Decimal("10")))
    return {
        "mode": "SHADOW",
        "state": "READY" if ready else "INSUFFICIENT_DATA",
        "identity": [q.symbol, q.asset_class, q.market, q.currency, q.settlement],
        "as_of": at.isoformat(),
        "history": history,
        "candles_5m": candles,
        "shadow_score_delta": str(shadow_delta),
        "decision_effect": "OBSERVE_ONLY",
        "lookahead_protection": "bar_end_and_known_at<=as_of",
        "feature_version": "rc6-historical-candle-shadow-v1",
    }
