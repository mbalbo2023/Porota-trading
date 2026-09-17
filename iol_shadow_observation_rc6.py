"""IOL MCP market-data observations for RC6.

Asynchronous, cache-backed SHADOW source. It is never part of the live PPI
decision path: it has no order capability and cannot change READY or HOLD.
"""
from __future__ import annotations
from datetime import datetime, timezone
import json, os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, Iterable
from cy_market_source_arbitration_hf6 import compare_background_numeric

SCHEMA_VERSION=1
MODE="SHADOW"
DECISION_EFFECT="OBSERVE_ONLY"
SOURCE="IOL_MCP"
LIVE_DECISION_AUTHORITY=False
DEFAULT_MARKET="BCBA"

def cache_path(root: Path | str | None=None) -> Path:
    if root is not None:
        return Path(root) / "iol_shadow_latest.json"
    configured=os.getenv("POROTA_IOL_SHADOW_CACHE_PATH","").strip()
    return Path(configured) if configured else Path("/app/data/market/iol_shadow_latest.json")

def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile("w",encoding="utf-8",dir=path.parent,prefix=".iol-shadow-",suffix=".tmp",delete=False) as h:
        json.dump(payload,h,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        h.write("\n"); h.flush(); os.fsync(h.fileno()); temp=Path(h.name)
    os.replace(temp,path)

def _number(value):
    try: return float(value) if value not in (None,"") else None
    except (TypeError,ValueError): return None

def _quote_summary(payload: dict) -> dict:
    if not isinstance(payload,dict): return {}
    bid=_number(payload.get("bid") or payload.get("best_bid"))
    ask=_number(payload.get("ask") or payload.get("best_ask"))
    last=_number(payload.get("last") or payload.get("last_price") or payload.get("price"))
    spread=(ask-bid)/bid*100.0 if bid is not None and ask is not None and bid>0 and ask>=bid else None
    return {"last":last,"bid":bid,"ask":ask,"spread_pct":spread}

def refresh(symbols: Iterable[str], call_tool: Callable[[str,dict],dict], *, root: Path | str | None=None, market: str=DEFAULT_MARKET, primary_last_by_symbol: dict[str,float] | None=None, tolerance_pct: float=2.0, now: datetime | None=None) -> dict:
    """Collect bounded MCP observations through an injected credential-owning client."""
    observed_at=(now or datetime.now(timezone.utc)).isoformat()
    primary=primary_last_by_symbol or {}
    rows=[]
    for raw in symbols:
        symbol=str(raw or "").upper().strip()
        if not symbol: continue
        try:
            quote=_quote_summary(call_tool("get_asset_quote",{"symbol":symbol,"market":market}))
            info=call_tool("get_asset_info",{"symbol":symbol,"market":market})
            primary_last=_number(primary.get(symbol))
            rows.append({"symbol":symbol,"market":market,"state":"READY" if quote else "UNAVAILABLE","quote":quote,"asset_type":str((info or {}).get("type") or ""),"currency":str((info or {}).get("currency") or ""),"units_per_lot":(info or {}).get("units_per_lot"),"primary_comparison":compare_background_numeric(primary_last,quote.get("last"),tolerance_pct=tolerance_pct),"decision_effect":DECISION_EFFECT})
        except Exception as exc:
            rows.append({"symbol":symbol,"market":market,"state":"UNAVAILABLE","reason":f"{type(exc).__name__}:{str(exc)[:160]}","decision_effect":DECISION_EFFECT})
    payload={"schema_version":SCHEMA_VERSION,"refreshed_at":observed_at,"source":SOURCE,"mode":MODE,"decision_effect":DECISION_EFFECT,"live_decision_authority":False,"real_money_authorized":False,"symbols":rows}
    _write_atomic(cache_path(root),payload)
    return payload

def collect(root: Path | str | None=None) -> dict:
    """Read the last observation only; never access IOL."""
    try: payload=json.loads(cache_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError,OSError,json.JSONDecodeError): payload={}
    if payload.get("schema_version") != SCHEMA_VERSION:
        return {"source":SOURCE,"mode":MODE,"state":"UNAVAILABLE","decision_effect":DECISION_EFFECT,"live_decision_authority":False,"real_money_authorized":False,"symbols":[],"reason":"CACHE_MISSING_OR_INVALID"}
    return {"source":SOURCE,"mode":MODE,"state":"READY" if payload.get("symbols") else "INSUFFICIENT_DATA","decision_effect":DECISION_EFFECT,"live_decision_authority":False,"real_money_authorized":False,"refreshed_at":payload.get("refreshed_at"),"symbols":payload.get("symbols") or []}
