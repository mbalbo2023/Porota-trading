"""RC4 post-close A3 CEM history ingestion, background/reference only.

The runner matches exact symbols from Porota's verified candidate universe to
A3 CEM's public symbol catalogue.  It never infers settlement from CEM's
numeric ``settlement`` field and never provides a live trading price.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from cv_history_store_adapter_hf6 import default_history_store
from cu_history_store_v2_hf6 import append_many
from cz_a3_cem_public_history_hf6 import A3CEMPublicReadOnlyClient
from db_a3_cem_normalizer_hf6 import normalize_symbols, closing_to_candle
from cy_market_source_arbitration_hf6 import assert_source_invariants

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
FAMILIES = {"FUTUROS", "OPCIONES"}


def _data(payload):
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    return []


def load_targets(connection):
    tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "candidate_universe" not in tables:
        return []
    rows=[]
    for row in connection.execute(
        """SELECT ticker,instrument_type,market,settlement FROM candidate_universe
           WHERE status='AVAILABLE' ORDER BY instrument_type,ticker,market,settlement"""):
        symbol,family,market,settlement=map(lambda x:str(x or '').upper(),row)
        if family in FAMILIES and market not in {"","UNKNOWN"} and settlement not in {"","UNKNOWN"}:
            rows.append((symbol,family,market,settlement))
    return rows


def run(store, *, client=None, history_store=None, now=None, batch_limit=40):
    assert_source_invariants()
    current=now or datetime.now(TZ)
    if current.tzinfo is None: current=current.replace(tzinfo=TZ)
    with store.connect() as c:
        state=c.execute("SELECT session_state,real_orders_sent FROM observer_state WHERE id=1").fetchone()
        if not state or str(state[0])!="MARKET_CLOSED" or int(state[1] or 0)!=0:
            return {"ran":False,"reason":"RUNTIME_NOT_CLOSED_OR_SAFE","execution_allowed":False}
        targets=load_targets(c)
    cem=client or A3CEMPublicReadOnlyClient()
    symbols={x.symbol:x for x in normalize_symbols(cem.symbols()) if x.family in FAMILIES}
    hstore=history_store or default_history_store()
    start=(current.date()-timedelta(days=365)).isoformat()
    end=current.date().isoformat()
    selected=targets[:max(1,int(batch_limit))]
    stats={"selected":len(selected),"matched":0,"unmatched":0,"failed":0,
           "versions_appended":0,"canonical_updates":0,"protected_by_precedence":0}
    for symbol,family,market,settlement in selected:
        meta=symbols.get(symbol)
        if meta is None or meta.family!=family:
            stats["unmatched"]+=1; continue
        try:
            payload=cem.closing_prices(symbol=symbol,date_from=start,date_to=end,page=1,page_size=500)
            candles=[]
            for row in _data(payload):
                try:
                    candles.append(closing_to_candle(row,instrument_type=family,
                                                     market=market,settlement_identity=settlement))
                except (TypeError,ValueError):
                    continue
            result=append_many(hstore,candles)
            stats["matched"]+=1
            for key in ("versions_appended","canonical_updates","protected_by_precedence"):
                stats[key]+=int(result.get(key) or 0)
        except Exception:
            stats["failed"]+=1
    return {"ran":True,"source":"A3_CEM_CLOSING","execution_allowed":False,**stats}
