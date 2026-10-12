"""RC6 historical and candle features for SHADOW evaluation.

This module is deliberately non-binding: it calculates features only from
data available at as_of and never opens, closes, or blocks a paper trade.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import nullcontext
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from cp_history_ingest_policy_hf6 import OPERATIONAL_HISTORY_FAMILIES
from cu_history_store_v2_hf6 import instant, read_as_of, source_rank, utc
from rc6_audit_evidence.sqlite_snapshot import readonly_copy, SnapshotError


def _dt(value):
    return instant(value)


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
        # A dedicated HistoricalStore opens WAL in its write adapter. A reader
        # uses only its path and opens SQLite on a bounded verified private copy.
        # Callers may reuse an already query-only snapshot during one cycle.
        if isinstance(store, sqlite3.Connection):
            if store.execute('PRAGMA query_only').fetchone()[0] != 1:
                return {'state':'HISTORY_READONLY_SNAPSHOT_REQUIRED','observations':0}
            context = nullcontext(store)
        else:
            context = readonly_copy(store.path, deadline=time.monotonic()+1.0, validate=False)
        with context as connection:
            columns = {r[1] for r in connection.execute("PRAGMA table_info(history_versions_v2)")}
            if not {"currency", "price_basis", "version_known_at"} <= columns:
                # The former three-field payload store cannot establish the
                # quote's market/currency or immutable as-of revision.
                return {"state": "HISTORY_IDENTITY_UNVERIFIED", "observations": 0,
                        "authority": "LEGACY_ADVISORY_ONLY", "price_basis": "RAW"}
            selected = read_as_of(connection, symbol=q.symbol, instrument_type=q.asset_class,
                market=q.market, currency=q.currency, settlement=q.settlement, as_of=at,
                price_basis="RAW")
        for raw in selected:
            ohlc = _valid_ohlc(raw)
            if ohlc:
                rows.append((_dt(raw["date"] + "T00:00:00+00:00"), ohlc))
    except SnapshotError as exc:
        return {'state':'HISTORY_COPY_UNAVAILABLE','observations':0,'reason':str(exc)}
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
        "source": "history_versions_v2.latest_as_of", "price_basis": "RAW",
        "known_cut": utc(at), "identity": [q.symbol,q.asset_class,q.market,q.currency,q.settlement],
    }


def _candle_features(store, q, at):
    try:
        with store.connect() as connection:
            rows = connection.execute(
                """SELECT v.*,s.identity_json
                   FROM candle_versions v
                   JOIN candle_series s ON s.series_id=v.series_id
                   WHERE json_extract(s.identity_json,'$.symbol')=?
                     AND json_extract(s.identity_json,'$.asset_class')=?
                     AND json_extract(s.identity_json,'$.market')=?
                     AND json_extract(s.identity_json,'$.currency')=?
                     AND json_extract(s.identity_json,'$.settlement')=?
                     AND json_extract(s.identity_json,'$.resolution')='5m'
                   ORDER BY v.series_id,v.bar_start,v.id LIMIT 100001""",
                (q.symbol, q.asset_class, q.market, q.currency, q.settlement),
            ).fetchall()
        if len(rows)>100000:
            return {'state':'CANDLE_READER_ROW_BUDGET_EXHAUSTED','observations':0}
    except Exception:
        return {"state": "CANDLES_UNAVAILABLE", "observations": 0}
    latest = {}
    identities = {}
    for row in rows:
        try:
            series = json.loads(row["identity_json"])
            if series.get("adjustment") != "RAW" or series.get("adjustment_basis") in {None,"","UNKNOWN"}:
                continue
            if series.get("price_kind") not in {"PROVIDER_OHLC", "TRADE_SAMPLES"}:
                continue
            known, end = _dt(row["known_at"]), _dt(row["bar_end"])
            if known > at or end > at:
                continue
            key = (row["series_id"], utc(row["bar_start"]))
            prior = latest.get(key)
            if prior is None or (known,row["id"]) > (_dt(prior["known_at"]),prior["id"]):
                latest[key] = row
                identities[row["series_id"]] = series
        except (TypeError,ValueError,json.JSONDecodeError):
            continue
    # Choose one explicit series before filtering quality. A latest conflict
    # revokes its bar; neither an older revision nor another series rescues it.
    if not identities:
        return {"state":"CANDLE_INSUFFICIENT","observations":0,"price_basis":"RAW"}
    selected_series = min(identities, key=lambda key: (
        source_rank(identities[key]["source"]),
        0 if identities[key]["price_kind"] == "PROVIDER_OHLC" else 1,
        identities[key]["source"],key))
    bars = []
    selected = sorted((row for (series,_),row in latest.items() if series==selected_series),
                      key=lambda row:_dt(row["bar_start"]))[-60:]
    for row in selected:
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
        "series_id": selected_series,"series": identities[selected_series],"known_cut":utc(at),
    }


def collect(store, q, at, *, history_store=None):
    """Collect point-in-time historical/candle features for SHADOW only."""
    asset_class = str(getattr(q, "asset_class", "") or "").upper()
    canonical = {
        "ACCION": "ACCIONES", "CEDEAR": "CEDEARS", "ETF": "ETFS",
        "ON": "OBLIGACIONES", "FCIS": "FCI",
    }.get(asset_class, asset_class)
    if canonical not in OPERATIONAL_HISTORY_FAMILIES:
        return {
            "mode": "SHADOW",
            "state": "OUT_OF_SCOPE",
            "identity": [getattr(q, "symbol", None), getattr(q, "asset_class", None),
                         getattr(q, "market", None), getattr(q, "currency", None),
                         getattr(q, "settlement", None)],
            "decision_effect": "OBSERVE_ONLY",
            "reason": f"{canonical or 'UNKNOWN'} no pertenece a OPERATIONAL_HISTORY_FAMILIES",
            "feature_version": "rc6-historical-candle-shadow-v2",
        }
    if not isinstance(at, datetime):
        at = _dt(at)
    else:
        at = instant(at)
    history = _history_features(history_store if history_store is not None else store, q, at)
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
        "lookahead_protection": "full_identity_latest_revision_then_quality;version_known_at_and_bar_end<=as_of",
        "feature_version": "rc6-historical-candle-shadow-v3",
    }
