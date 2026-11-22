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
    symbol, family, _, _, _ = identity
    return [
        history_v2.Candle(
            symbol=symbol,
            instrument_type=family,
            market="BCBA",
            settlement="UNKNOWN",
            date=row[0],
            open=row[1], high=row[2], low=row[3], close=row[4], volume=row[5],
            source="DATA912",
            adjusted=False,
            currency="UNKNOWN",
            price_basis=history_v2.SOURCE_REPORTED,
            adjustment_basis=history_v2.source_reported_adjustment("DATA912"),
            volume_kind="UNKNOWN",
            observed_at=observed_at,
            metadata={
                "historical_only": True,
                "execution_allowed": False,
                "ready_paper_implication": "NONE",
                "reported_currency": getattr(row, "currency", "UNKNOWN"),
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
    request_cohort = {}
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
        if symbol and family in data912.OPERATIONAL_FAMILIES and market in {"BYMA", "BCBA"}:
            request_cohort.setdefault((symbol,family),set()).add(key)
        if (not symbol or family not in data912.OPERATIONAL_FAMILIES or market not in {"BYMA", "BCBA"} or
                not settlement or settlement == "UNKNOWN" or
                currency not in {"ARS","USD","USD_MEP","USD_CCL"} or key in seen):
            invalid_targets += int(key not in seen)
            continue
        seen.add(key)
        normalized.append(key)
    # The provider request carries ticker/family only. A cohort with multiple
    # monetary or settlement identities cannot be resolved by that request.
    # Check the complete cohort before applying a scheduling batch limit.
    ambiguous_requests={key for key,identities in request_cohort.items() if len(identities)>1}
    ambiguous_targets=[identity for identity in normalized if identity[:2] in ambiguous_requests]
    normalized=[identity for identity in normalized if identity[:2] not in ambiguous_requests]
    invalid_targets+=len(ambiguous_targets)
    if batch_limit is not None:
        normalized = normalized[:max(0, int(batch_limit))]

    started_at = datetime.now(timezone.utc).isoformat()
    successful = empty = failed = versions = canonical = protected = 0
    results = []

    if normalized:
        hstore = history_store if history_store is not None else default_history_store()
        if not hstore.source_reported_only:
            hstore = hstore.for_source_reported_history()
        if hstore.existing_history_identity is None:
            history_v2.init_schema(hstore)
        else:
            with hstore.connect() as connection:
                history_v2.require_current_history(connection)
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        client = data912._session()

    for identity in normalized:
        symbol, family, _, _, _ = identity
        reported_key = {"symbol": symbol, "instrument_type": family, "market": "BCBA",
                        "currency": "UNKNOWN", "settlement": "UNKNOWN", "requested_identity": list(identity)}
        try:
            path = data912.GROUPS[family][1].format(ticker=symbol)
            raw = data912._get_json(client, path)
            observed_at = datetime.now(timezone.utc).isoformat()
            rows,rejected = data912._normalize(raw, cutoff,as_of=observed_at,return_rejections=True)
            if not rows:
                empty += 1
                results.append({
                    **reported_key, "state":"EMPTY_OR_INVALID",
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
                    **reported_key,
                    "state":"HISTORICAL_V2_PARTIAL" if rejected else "HISTORICAL_V2_INGESTED",
                    "rejected_rows":len(rejected),"rejections":rejected,
                    **stored,
                })
        except Exception as exc:
            failed += 1
            allowed={'DATA912_TIMEOUT','DATA912_RATE_LIMIT','DATA912_HTTP_ERROR','DATA912_PAYLOAD_INVALID'}
            detail=str(exc) if str(exc) in allowed else 'HISTORICAL_PROVIDER_OR_VALIDATION_ERROR'
            results.append({
                **reported_key, "state":"ERROR",
                "error_class":type(exc).__name__,"detail":detail,
                "versions_appended":0,"canonical_updates":0,
                "protected_by_precedence":0,
            })
        time.sleep(hist.HIST_BATCH_SLEEP)

    return {
        "ok": not invalid_targets and failed == 0 and empty == 0 and not any(x.get('rejected_rows',0) for x in results),
        "source":"DATA912_HISTORICAL_BATCH_V2",
        "price_basis": history_v2.SOURCE_REPORTED,
        "selected":len(normalized),
        "invalid_targets":invalid_targets,
        "ambiguous_provider_requests":len(ambiguous_requests),
        "identity_rejections":[{'identity':list(identity),'reason':'HISTORY_PROVIDER_REQUEST_IDENTITY_AMBIGUOUS'}
                               for identity in ambiguous_targets],
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
