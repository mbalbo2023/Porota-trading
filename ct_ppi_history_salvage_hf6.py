"""HF6-v2: salva filas PPI históricas válidas sin aceptar filas defectuosas.

Este módulo transforma un payload PPI histórico en evidencia canónica de
históricos. No repara, interpola, forward-fillea ni sintetiza velas.

La persistencia operativa/readiness sigue separada: almacenar contexto
histórico no habilita una familia para PAPER.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import al_historical_ingest as hist
import cp_history_ingest_policy_hf6 as policy
import cq_history_attempt_ledger_hf6 as ledger


def _to_candle(row: dict) -> tuple:
    """Convert one already-validated PPI row to the canonical daily tuple."""
    return (
        str(row["date"])[:10],
        float(row["openingPrice"]),
        float(row["max"]),
        float(row["min"]),
        float(row["price"]),
        float(row["volume"]),
    )


def _date_bound(value):
    """Normalize scheduler/date inputs to ``date`` for policy comparisons."""
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
          settlement TEXT NOT NULL,
          row_index INTEGER NOT NULL,
          reason TEXT NOT NULL,
          recorded_at TEXT NOT NULL
        )""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_history_rejections_v2_identity
          ON history_row_rejections_v2(symbol,instrument_type,settlement,attempt_id)""")


def ingest_ppi_payload(store, *, symbol: str, instrument_type: str,
                       settlement: str, payload, requested_from=None,
                       requested_to=None, attempted_at=None) -> dict:
    """Persist valid PPI rows and append immutable quality evidence.

    Returns storage/context information only. It intentionally has no
    READY_PAPER field and no execution side effect.
    """
    attempted_dt = _as_of(attempted_at)
    attempted_iso = attempted_dt.isoformat()
    requested_from_date = _date_bound(requested_from)
    requested_to_date = _date_bound(requested_to)
    result = policy.validate_provider_history(
        payload,
        as_of=attempted_dt,
        date_from=requested_from_date,
        date_to=requested_to_date,
    )

    candles = [_to_candle(row) for row in result.valid_rows]
    rows_written = hist.guardar_velas(
        str(symbol).upper(), str(instrument_type).upper(), candles,
        "PPI_PRODUCTION_HISTORY", adjusted=False,
    ) if candles else 0

    ledger.init_schema(store)
    _init_rejections(store)
    attempt = ledger.HistoryAttempt(
        symbol=str(symbol).upper(),
        instrument_type=str(instrument_type).upper(),
        settlement=str(settlement),
        source="PPI_PRODUCTION_HISTORY",
        requested_from=requested_from_date.isoformat() if requested_from_date else None,
        requested_to=requested_to_date.isoformat() if requested_to_date else None,
        attempted_at=attempted_iso,
        completed_at=datetime.now(timezone.utc).isoformat(),
        provider_rows=result.provider_rows,
        valid_rows=result.valid_count,
        rejected_rows=result.rejected_count,
        state=result.storage_quality,
        detail=(f"context={result.context_state}; ratio={result.valid_ratio:.6f}; "
                "sin interpolacion ni velas sinteticas"),
        raw_body_hash=ledger.body_hash(payload),
        metadata={
            "context_state": result.context_state,
            "valid_ratio": result.valid_ratio,
            "first_date": result.first_date,
            "last_date": result.last_date,
            "ready_paper_implication": "NONE",
        },
    )
    attempt_id = ledger.append_attempt(store, attempt)

    if result.rejected_rows:
        with store.connect() as c:
            c.executemany(
                """INSERT INTO history_row_rejections_v2(
                  attempt_id,symbol,instrument_type,settlement,row_index,reason,recorded_at)
                  VALUES(?,?,?,?,?,?,?)""",
                [
                    (attempt_id, str(symbol).upper(), str(instrument_type).upper(),
                     str(settlement), int(item.index), str(item.reason), attempted_iso)
                    for item in result.rejected_rows
                ],
            )

    return {
        "attempt_id": attempt_id,
        "provider_rows": result.provider_rows,
        "valid_rows": result.valid_count,
        "rejected_rows": result.rejected_count,
        "rows_written": rows_written,
        "valid_ratio": result.valid_ratio,
        "storage_quality": result.storage_quality,
        "context_state": result.context_state,
        "first_date": result.first_date,
        "last_date": result.last_date,
        "ready_paper_implication": "NONE",
    }
