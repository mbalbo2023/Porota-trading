"""HF6-v2: reconciliación batch post-cierre de históricos con Data912.

Este módulo NO pertenece al hot path de trading. Selecciona, desde el estado
histórico de Porota, sólo identidades que necesitan reparación y delega la
descarga histórica a ba_data912_history.refresh_symbols().

Invariantes:
- PPI sigue siendo fuente primaria.
- Data912 nunca provee precio live/book/saldo/sizing/decisión/ejecución.
- Sólo ACCIONES, CEDEARS y BONOS pueden usar este fallback.
- Tener histórico suficiente no cambia READY_PAPER.
- El job es idempotente respecto del archivo histórico: guardar_velas hace
  UPSERT por (symbol,date).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable
from zoneinfo import ZoneInfo

import ba_data912_history as data912
import cp_history_ingest_policy_hf6 as policy

TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
RECONCILE_HOUR = int(os.getenv("DATA912_RECONCILE_HOUR", "19"))
RECONCILE_MINUTE = int(os.getenv("DATA912_RECONCILE_MINUTE", "45"))
RECONCILE_BATCH_LIMIT = max(1, min(int(os.getenv("DATA912_RECONCILE_BATCH_LIMIT", "80")), 243))


@dataclass(frozen=True)
class HistoricalIdentity:
    symbol: str
    instrument_type: str
    settlement: str
    valid_rows: int
    latest_state: str


def due_now(now: datetime | None = None) -> bool:
    """True once per local day window after configured post-close time.

    Day/business-session eligibility is checked by the caller/scheduler; this
    pure helper only owns the local clock threshold.
    """
    current = now or datetime.now(TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ)
    else:
        current = current.astimezone(TZ)
    return current.time().replace(tzinfo=None) >= time(RECONCILE_HOUR, RECONCILE_MINUTE)


def load_targets(connection) -> list[HistoricalIdentity]:
    """Load candidates from Porota's catalog/history state, not Data912's list."""
    tables = {r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if "candidate_universe" not in tables:
        return []

    attempts = {}
    if "production_history_attempts" in tables:
        for row in connection.execute(
            """SELECT symbol,instrument_type,settlement,state,valid_rows
               FROM production_history_attempts"""
        ):
            attempts[(row[0], row[1], row[2])] = (str(row[3] or ""), int(row[4] or 0))

    result = []
    for row in connection.execute(
        """SELECT ticker,instrument_type,settlement,status
           FROM candidate_universe
           WHERE status='AVAILABLE'
           ORDER BY instrument_type,ticker,settlement"""
    ):
        symbol, family, settlement, _status = row
        family = str(family or "").upper()
        if not policy.data912_fallback_allowed(family):
            continue
        state, valid_rows = attempts.get((symbol, family, settlement), ("NO_ATTEMPT", 0))
        if policy.needs_data912_reconciliation(family, valid_rows, state):
            result.append(HistoricalIdentity(
                str(symbol).upper(), family, str(settlement), valid_rows, state
            ))
    return result


def prioritize(targets: Iterable[HistoricalIdentity]) -> list[HistoricalIdentity]:
    """Worst history first; deterministic tie-breakers avoid random hammering."""
    state_rank = {
        "EMPTY_OR_INVALID": 0,
        "ERROR": 1,
        "NO_ATTEMPT": 2,
        "PARTIAL": 3,
        "VALID_ROWS_WITH_REJECTIONS": 4,
        "VALID_PAYLOAD": 5,
    }
    return sorted(
        targets,
        key=lambda x: (
            state_rank.get(str(x.latest_state).upper(), 3),
            int(x.valid_rows),
            x.instrument_type,
            x.symbol,
            x.settlement,
        ),
    )


def run(store, *, batch_limit: int | None = None) -> dict:
    """Execute one historical-only Data912 reconciliation batch."""
    if data912.DATA912_EXECUTION_ALLOWED:
        raise RuntimeError("DATA912_EXECUTION_INVARIANT_BROKEN")

    with store.connect() as c:
        targets = prioritize(load_targets(c))

    limit = RECONCILE_BATCH_LIMIT if batch_limit is None else max(1, int(batch_limit))
    selected = targets[:limit]
    pairs = [(x.symbol, x.instrument_type) for x in selected]

    result = data912.refresh_symbols(pairs, batch_limit=limit)
    return {
        "ok": bool(result.get("ok")),
        "selected": len(selected),
        "remaining_after_selection": max(0, len(targets) - len(selected)),
        "rows_written": int(result.get("rows_written") or 0),
        "successful": int(result.get("successful") or 0),
        "without_history": int(result.get("without_history") or 0),
        "failed": int(result.get("failed") or 0),
        "execution_allowed": False,
        "source": "DATA912_HISTORICAL_BATCH",
        "selection": [
            {
                "symbol": x.symbol,
                "instrument_type": x.instrument_type,
                "settlement": x.settlement,
                "valid_rows_before": x.valid_rows,
                "state_before": x.latest_state,
            }
            for x in selected
        ],
    }
