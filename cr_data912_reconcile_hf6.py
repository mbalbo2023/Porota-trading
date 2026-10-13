"""HF6-v2: reconciliación batch post-cierre de históricos con Data912.

No pertenece al hot path. Selecciona identidades desde Porota y delega la
descarga al sink History Store v2. Data912 nunca provee precio live/book/
saldo/sizing/decisión/ejecución ni habilita READY_PAPER.
"""
from __future__ import annotations

import os
import json
from dataclasses import dataclass
from datetime import datetime, time
from typing import Iterable
from zoneinfo import ZoneInfo

import ba_data912_history as data912
import cp_history_ingest_policy_hf6 as policy
import cw_data912_history_v2_sink as data912_v2

TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
RECONCILE_HOUR = int(os.getenv("DATA912_RECONCILE_HOUR", "19"))
RECONCILE_MINUTE = int(os.getenv("DATA912_RECONCILE_MINUTE", "45"))
RECONCILE_BATCH_LIMIT = max(1, min(int(os.getenv("DATA912_RECONCILE_BATCH_LIMIT", "80")), 500))


@dataclass(frozen=True)
class HistoricalIdentity:
    symbol: str
    instrument_type: str
    market: str
    settlement: str
    valid_rows: int
    latest_state: str
    currency: str = ""


def due_now(now: datetime | None = None) -> bool:
    current = now or datetime.now(TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=TZ)
    else:
        current = current.astimezone(TZ)
    return current.time().replace(tzinfo=None) >= time(RECONCILE_HOUR, RECONCILE_MINUTE)


def load_targets(connection) -> list[HistoricalIdentity]:
    """Load candidates from Porota's universe, never from Data912 discovery."""
    tables = {r[0] for r in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if not {'candidate_universe','candidate_identity_v2','financial_instrument_catalog'} & tables:
        return []

    attempts = {}
    if "production_history_attempts" in tables:
        columns={r[1] for r in connection.execute('PRAGMA table_info(production_history_attempts)')}
        if {'market','currency'} <= columns:
            for row in connection.execute('''SELECT symbol,instrument_type,market,currency,
                settlement,state,valid_rows FROM production_history_attempts'''):
                attempts[tuple(str(x or '').upper() for x in row[:5])]=(str(row[5] or ''),int(row[6] or 0))
    if 'history_attempt_ledger_v2' in tables:
        for row in connection.execute('''SELECT symbol,instrument_type,settlement,state,
                valid_rows,metadata_json FROM history_attempt_ledger_v2 ORDER BY attempted_at,id'''):
            try:
                meta=json.loads(row[5] or '{}')
                if not meta.get('market') or not meta.get('currency'):continue
                key=tuple(str(x).upper() for x in (row[0],row[1],meta['market'],meta['currency'],row[2]))
                attempts[key]=(str(row[3] or ''),int(row[4] or 0))
            except (TypeError,ValueError):continue

    result = []
    # The legacy universe's three-field projection is not monetary authority.
    # Resolve from the exact catalog; do not synthesize ARS for old fixtures.
    if 'candidate_identity_v2' in tables:
        candidates = connection.execute("""SELECT ticker,instrument_type,market,settlement,
           status,currency FROM candidate_identity_v2 WHERE can_simulate=1
           ORDER BY instrument_type,ticker,market,currency,settlement""").fetchall()
    elif 'financial_instrument_catalog' in tables:
        candidates = connection.execute("""SELECT ticker,instrument_type,market,settlement,
           status,currency FROM financial_instrument_catalog WHERE status='AVAILABLE'
           ORDER BY instrument_type,ticker,market,currency,settlement""").fetchall()
    else:
        cols={r[1] for r in connection.execute('PRAGMA table_info(candidate_universe)')}
        if 'currency' not in cols: return []
        candidates=connection.execute("""SELECT ticker,instrument_type,market,settlement,
           status,currency FROM candidate_universe WHERE status='AVAILABLE'
           ORDER BY instrument_type,ticker,market,currency,settlement""").fetchall()
    for row in candidates:
        symbol, family, market, settlement, _status,currency = row
        symbol = str(symbol or "").upper()
        family = str(family or "").upper()
        market = str(market or "").upper()
        settlement = str(settlement or "").upper()
        currency = str(currency or "").upper()
        if currency not in {'ARS','USD','USD_MEP','USD_CCL'}: continue
        if family not in policy.OPERATIONAL_HISTORY_FAMILIES:
            continue
        if not policy.data912_fallback_allowed(family):
            continue
        if not market or market == "UNKNOWN" or not settlement or settlement == "UNKNOWN":
            continue
        state, valid_rows = attempts.get(
            (symbol,family,market,currency,settlement), ("NO_ATTEMPT",0)
        )
        if policy.needs_data912_reconciliation(family,valid_rows,state):
            result.append(HistoricalIdentity(
                symbol,family,market,settlement,valid_rows,state,currency
            ))
    return result


def prioritize(targets: Iterable[HistoricalIdentity]) -> list[HistoricalIdentity]:
    state_rank = {
        "EMPTY_OR_INVALID":0,
        "ERROR":1,
        "NO_ATTEMPT":2,
        "PARTIAL":3,
        "VALID_ROWS_WITH_REJECTIONS":4,
        "VALID_PAYLOAD":5,
    }
    return sorted(
        targets,
        key=lambda x:(
            state_rank.get(str(x.latest_state).upper(),3),
            int(x.valid_rows),x.instrument_type,x.symbol,x.market,x.settlement,
        ),
    )


def run(store, *, batch_limit: int | None = None) -> dict:
    """Execute one historical-only Data912 reconciliation batch into History v2."""
    if data912.DATA912_EXECUTION_ALLOWED:
        raise RuntimeError("DATA912_EXECUTION_INVARIANT_BROKEN")

    with store.connect() as c:
        targets = prioritize(load_targets(c))

    limit = RECONCILE_BATCH_LIMIT if batch_limit is None else max(1,int(batch_limit))
    selected = targets[:limit]
    identities = [
        (x.symbol,x.instrument_type,x.market,x.currency,x.settlement) for x in selected
    ]
    result = data912_v2.refresh_identities(identities,batch_limit=limit)
    return {
        "ok":bool(result.get("ok")),
        "selected":len(selected),
        "remaining_after_selection":max(0,len(targets)-len(selected)),
        "versions_appended":int(result.get("versions_appended") or 0),
        "canonical_updates":int(result.get("canonical_updates") or 0),
        "protected_rows":int(result.get("protected_rows") or 0),
        "successful":int(result.get("successful") or 0),
        "without_history":int(result.get("without_history") or 0),
        "failed":int(result.get("failed") or 0),
        "execution_allowed":False,
        "source":"DATA912_HISTORICAL_BATCH_V2",
        "selection":[
            {
                "symbol":x.symbol,
                "instrument_type":x.instrument_type,
                "market":x.market,
                "currency":x.currency,
                "settlement":x.settlement,
                "valid_rows_before":x.valid_rows,
                "state_before":x.latest_state,
            }
            for x in selected
        ],
    }
