"""HF6-v2: salva filas PPI históricas válidas sin aceptar filas defectuosas.

Las velas FULL_OHLC válidas se escriben en History Store v2. Desde la candidata
RC4-HF2, las filas rechazadas por FULL_OHLC pueden conservar sólo fecha+close
en una serie paralela explícita, sin reparar ni sintetizar open/high/low/volumen.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import cp_history_ingest_policy_hf6 as policy
import cq_history_attempt_ledger_hf6 as ledger
import cu_history_store_v2_hf6 as history_v2
import ea_history_close_series_hf2 as close_series
from cv_history_store_adapter_hf6 import default_history_store


def _date_bound(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError) as exc:
        raise ValueError("HISTORY_REQUEST_BOUND_INVALID") from exc


def _as_of(value) -> datetime:
    if value in (None, ""):
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("HISTORY_ATTEMPTED_AT_INVALID") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _init_rejections(store) -> None:
    with store.connect() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS history_row_rejections_v2(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          attempt_id INTEGER NOT NULL,
          symbol TEXT NOT NULL,
          instrument_type TEXT NOT NULL,
          market TEXT NOT NULL,
          settlement TEXT NOT NULL,
          row_index INTEGER NOT NULL,
          reason TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        )""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_history_rejections_v2_identity
          ON history_row_rejections_v2(symbol,instrument_type,market,settlement,attempt_id)""")


def _v2_candles(*, symbol, instrument_type, market, settlement,
                rows, observed_at):
    return [
        history_v2.Candle(
            symbol=str(symbol).upper(),
            instrument_type=str(instrument_type).upper(),
            market=str(market).upper(),
            settlement=str(settlement).upper(),
            date=str(row["date"])[:10],
            open=float(row["openingPrice"]),
            high=float(row["max"]),
            low=float(row["min"]),
            close=float(row["price"]),
            volume=float(row["volume"]),
            source="PPI_PRODUCTION_HISTORY",
            adjusted=False,
            observed_at=observed_at,
            metadata={"ready_paper_implication":"NONE","history_quality":"FULL_OHLC"},
        )
        for row in rows
    ]


def ingest_ppi_payload(store, *, symbol: str, instrument_type: str,
                       market: str, settlement: str, payload,
                       requested_from=None, requested_to=None,
                       attempted_at=None, history_store=None) -> dict:
    """Persist full OHLC and, separately, safe close-only evidence.

    ``store`` is observer evidence/readiness storage. ``history_store`` defaults
    to the dedicated historical DB. No historical row grants trading readiness.
    A close-only row never enters ``history_canonical_v2`` and therefore cannot
    degrade or replace a FULL_OHLC canonical candle.
    """
    attempted_dt = _as_of(attempted_at)
    attempted_iso = attempted_dt.isoformat()
    requested_from_date = _date_bound(requested_from)
    requested_to_date = _date_bound(requested_to)
    market = str(market or "").strip().upper()
    if not market or market == "UNKNOWN":
        raise ValueError("HISTORY_MARKET_UNVERIFIED")

    result = policy.validate_provider_history(
        payload,
        as_of=attempted_dt,
        date_from=requested_from_date,
        date_to=requested_to_date,
    )

    hstore = history_store or default_history_store()
    stored = history_v2.append_many(
        hstore,
        _v2_candles(
            symbol=symbol,
            instrument_type=instrument_type,
            market=market,
            settlement=settlement,
            rows=result.valid_rows,
            observed_at=attempted_iso,
        ),
    ) if result.valid_rows else {
        "versions_appended":0,"canonical_updates":0,"protected_by_precedence":0
    }

    close_rows = close_series.extract_rejected_close_evidence(
        payload,
        result.rejected_rows,
        symbol=symbol,
        instrument_type=instrument_type,
        market=market,
        settlement=settlement,
        observed_at=attempted_iso,
        as_of=attempted_dt,
        date_from=requested_from_date,
        date_to=requested_to_date,
    )
    close_stored = close_series.append_many(hstore, close_rows) if close_rows else {
        "versions_appended":0,"canonical_updates":0
    }
    close_first = min((row.date for row in close_rows), default=None)
    close_last = max((row.date for row in close_rows), default=None)

    ledger.init_schema(store)
    _init_rejections(store)
    attempt = ledger.HistoryAttempt(
        symbol=str(symbol).upper(),
        instrument_type=str(instrument_type).upper(),
        settlement=str(settlement).upper(),
        source="PPI_PRODUCTION_HISTORY",
        requested_from=requested_from_date.isoformat() if requested_from_date else None,
        requested_to=requested_to_date.isoformat() if requested_to_date else None,
        attempted_at=attempted_iso,
        completed_at=datetime.now(timezone.utc).isoformat(),
        provider_rows=result.provider_rows,
        valid_rows=result.valid_count,
        rejected_rows=result.rejected_count,
        state=result.storage_quality,
        detail=(f"market={market}; context={result.context_state}; "
                f"ratio={result.valid_ratio:.6f}; close_only={len(close_rows)}; "
                "sin interpolacion ni velas sinteticas"),
        raw_body_hash=ledger.body_hash(payload),
        metadata={
            "market": market,
            "context_state": result.context_state,
            "valid_ratio": result.valid_ratio,
            "first_date": result.first_date,
            "last_date": result.last_date,
            "full_ohlc_rows": result.valid_count,
            "close_only_rows": len(close_rows),
            "close_only_first_date": close_first,
            "close_only_last_date": close_last,
            "close_only_quality": close_series.QUALITY,
            "close_only_store": "history_close_canonical_v1",
            "ready_paper_implication": "NONE",
            "execution_price_implication": "NONE",
            "history_store": "history_canonical_v2",
        },
    )
    attempt_id = ledger.append_attempt(store, attempt)

    if result.rejected_rows:
        with store.connect() as c:
            c.executemany(
                """INSERT INTO history_row_rejections_v2(
                  attempt_id,symbol,instrument_type,market,settlement,row_index,reason,recorded_at)
                  VALUES(?,?,?,?,?,?,?,?)""",
                [
                    (attempt_id, str(symbol).upper(), str(instrument_type).upper(), market,
                     str(settlement).upper(), int(item.index), str(item.reason), attempted_iso)
                    for item in result.rejected_rows
                ],
            )

    return {
        "attempt_id": attempt_id,
        "provider_rows": result.provider_rows,
        "valid_rows": result.valid_count,
        "full_ohlc_rows": result.valid_count,
        "rejected_rows": result.rejected_count,
        "close_only_rows": len(close_rows),
        "close_only_versions_appended": close_stored["versions_appended"],
        "close_only_canonical_updates": close_stored["canonical_updates"],
        "versions_appended": stored["versions_appended"],
        "canonical_updates": stored["canonical_updates"],
        "protected_by_precedence": stored["protected_by_precedence"],
        "valid_ratio": result.valid_ratio,
        "storage_quality": result.storage_quality,
        "context_state": result.context_state,
        "first_date": result.first_date,
        "last_date": result.last_date,
        "close_only_first_date": close_first,
        "close_only_last_date": close_last,
        "ready_paper_implication": "NONE",
        "execution_price_implication": "NONE",
    }
