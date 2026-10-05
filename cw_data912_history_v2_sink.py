"""Data912 batch sink para History Store v2.

Este módulo reutiliza únicamente el cliente histórico de ba_data912_history y
persiste en ``history_versions_v2/history_canonical_v2``. No toca el esquema
legado ``market_historical_ohlcv``.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import al_historical_ingest as hist
import ba_data912_history as data912
import cu_history_store_v2_hf6 as history_v2
from cv_history_store_adapter_hf6 import default_history_store


def _candles(identity, rows, observed_at):
    symbol, family, market, currency, settlement = identity
    for row in rows:
        supplied=getattr(row,'currency','UNKNOWN')
        if supplied not in {'UNKNOWN','',currency}:
            raise ValueError('HISTORY_CURRENCY_CONFLICT')
    return [
        history_v2.Candle(
            symbol=symbol,
            instrument_type=family,
            market=market,
            settlement=settlement,
            date=row[0],
            open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5],
            source="DATA912_POROTA_BATCH",
            adjusted=False,
            currency=currency,
            price_basis="RAW",
            volume_kind=getattr(row,"volume_kind","UNKNOWN"),
            observed_at=observed_at,
            metadata={
                "historical_only": True,
                "execution_allowed": False,
                "ready_paper_implication": "NONE",
                "provider_currency_status":"EXPLICIT" if getattr(row,'currency','UNKNOWN')==currency else "REQUESTED_IDENTITY_ONLY",
            },
        )
        for row in rows
    ]


def refresh_identities(targets: Iterable[tuple[str, str, str, str, str]],
                       days: int = hist.HIST_DEFAULT_DAYS,
                       *, batch_limit: int | None = None,
                       history_store=None) -> dict:
    """Fetch Data912 only for Porota-selected identities and write History v2."""
    if data912.DATA912_EXECUTION_ALLOWED:
        raise RuntimeError("DATA912_EXECUTION_INVARIANT_BROKEN")
    if not data912.DATA912_REFRESH_ENABLED:
        return {"ok": False, "disabled": True, "execution_allowed": False,
                "reason": "DATA912_REFRESH_ENABLED=false"}

    normalized = []
    seen = set()
    invalid_targets = 0
    for raw in targets:
        if len(raw) != 5:
            invalid_targets += 1
            continue
        symbol, family, market, currency, settlement = (
            str(raw[0] or "").strip().upper(),
            str(raw[1] or "").strip().upper(),
            str(raw[2] or "").strip().upper(),
            str(raw[3] or "").strip().upper(),
            str(raw[4] or "").strip().upper(),
        )
        key = (symbol, family, market, currency, settlement)
        if (not symbol or family not in data912.OPERATIONAL_FAMILIES or not market or
                market == "UNKNOWN" or not settlement or settlement == "UNKNOWN" or
                currency not in {"ARS","USD","USD_MEP","USD_CCL"} or key in seen):
            invalid_targets += int(key not in seen)
            continue
        seen.add(key)
        normalized.append(key)
    if batch_limit is not None:
        normalized = normalized[:max(0, int(batch_limit))]

    hstore = history_store or default_history_store()
    history_v2.init_schema(hstore)
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    client = data912._session()
    started_at = datetime.now(timezone.utc).isoformat()
    successful = empty = failed = versions = canonical = protected = 0
    results = []

    for identity in normalized:
        symbol, family, market, currency, settlement = identity
        try:
            path = data912.GROUPS[family][1].format(ticker=symbol)
            raw = data912._get_json(client, path)
            observed_at = datetime.now(timezone.utc).isoformat()
            rows,rejected = data912._normalize(raw, cutoff,as_of=observed_at,return_rejections=True)
            if not rows:
                empty += 1
                results.append({
                    "symbol":symbol,"instrument_type":family,"market":market,
                    "currency":currency,"settlement":settlement,"state":"EMPTY_OR_INVALID",
                    "rejected_rows":len(rejected),"rejections":rejected,
                    "versions_appended":0,"canonical_updates":0,
                    "protected_by_precedence":0,
                })
            else:
                stored = history_v2.append_many(
                    hstore, _candles(identity, rows, observed_at)
                )
                successful += 1
                versions += stored["versions_appended"]
                canonical += stored["canonical_updates"]
                protected += stored["protected_by_precedence"]
                results.append({
                    "symbol":symbol,"instrument_type":family,"market":market,
                    "currency":currency,"settlement":settlement,
                    "state":"HISTORICAL_V2_PARTIAL" if rejected else "HISTORICAL_V2_INGESTED",
                    "rejected_rows":len(rejected),"rejections":rejected,
                    **stored,
                })
        except Exception as exc:
            failed += 1
            allowed={'DATA912_TIMEOUT','DATA912_RATE_LIMIT','DATA912_HTTP_ERROR','DATA912_PAYLOAD_INVALID'}
            detail=str(exc) if str(exc) in allowed else 'HISTORICAL_PROVIDER_OR_VALIDATION_ERROR'
            results.append({
                "symbol":symbol,"instrument_type":family,"market":market,
                "currency":currency,"settlement":settlement,"state":"ERROR",
                "error_class":type(exc).__name__,"detail":detail,
                "versions_appended":0,"canonical_updates":0,
                "protected_by_precedence":0,
            })
        time.sleep(hist.HIST_BATCH_SLEEP)

    return {
        "ok": not invalid_targets and failed == 0 and empty == 0 and not any(x.get('rejected_rows',0) for x in results),
        "source":"DATA912_HISTORICAL_BATCH_V2",
        "selected":len(normalized),
        "invalid_targets":invalid_targets,
        "successful":successful,
        "without_history":empty,
        "failed":failed,
        "versions_appended":versions,
        "canonical_updates":canonical,
        "protected_rows":protected,
        "started_at":started_at,
        "finished_at":datetime.now(timezone.utc).isoformat(),
        "execution_allowed":False,
        "results":results,
    }
