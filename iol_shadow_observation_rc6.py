"""IOL MCP SHADOW observations for RC6: cache-only, never a decision source."""
from __future__ import annotations
from datetime import datetime, timezone
import json, os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Callable, Iterable
from cy_market_source_arbitration_hf6 import compare_background_numeric

SCHEMA_VERSION=1
MODE="SHADOW"
DECISION_EFFECT="OBSERVE_ONLY"
SOURCE="IOL_MCP"
LIVE_DECISION_AUTHORITY=False
DEFAULT_MARKET="BCBA"
READ_ONLY_TOOLS=frozenset({"get_asset_quote","get_asset_info"})
MAX_SYMBOLS_PER_REFRESH=50

def cache_path(root: Path | str | None=None) -> Path:
    if root is not None: return Path(root) / "iol_shadow_latest.json"
    configured=os.getenv("POROTA_IOL_SHADOW_CACHE_PATH","").strip()
    return Path(configured) if configured else Path("/app/data/market/iol_shadow_latest.json")

def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile("w",encoding="utf-8",dir=path.parent,prefix=".iol-shadow-",suffix=".tmp",delete=False) as h:
        json.dump(payload,h,ensure_ascii=False,sort_keys=True,separators=(",",":"))
        h.write("\n"); h.flush(); os.fsync(h.fileno()); temp=Path(h.name)
    # The cache is sanitized market observation, not OAuth material. The dashboard runs as a non-root user.
    os.chmod(temp,0o644)
    os.replace(temp,path)

def _number(value: Any) -> float | None:
    try: return float(value) if value not in (None,"") else None
    except (TypeError,ValueError): return None

def _first_number(*values: Any) -> float | None:
    for value in values:
        if isinstance(value, dict):
            value = value.get("value") or value.get("amount") or value.get("price")
        number=_number(value)
        if number is not None: return number
    return None

def _quote_summary(payload: dict) -> dict:
    """Normalize IOL market data and preserve its provider trade timestamp."""
    if not isinstance(payload,dict): return {}
    trade=payload.get("trade") if isinstance(payload.get("trade"),dict) else {}
    bid=_first_number(payload.get("bid"),payload.get("best_bid"),payload.get("buy_price"),trade.get("bid"))
    ask=_first_number(payload.get("ask"),payload.get("best_ask"),payload.get("sell_price"),trade.get("ask"))
    bid_size=_first_number(payload.get("bid_size"),payload.get("best_bid_size"),payload.get("buy_quantity"),trade.get("bid_size"))
    ask_size=_first_number(payload.get("ask_size"),payload.get("best_ask_size"),payload.get("sell_quantity"),trade.get("ask_size"))
    last=_first_number(payload.get("last"),payload.get("last_price"),payload.get("price"),
                       payload.get("unit_price"),trade.get("last"),trade.get("price"),
                       trade.get("unit_price"),trade.get("lot_price"))
    provider_observed_at=None
    for raw_time in (payload.get("timestamp"),payload.get("observed_at"),payload.get("date"),
                     trade.get("timestamp"),trade.get("observed_at"),trade.get("date")):
        try:
            parsed=datetime.fromisoformat(str(raw_time).replace("Z","+00:00"))
            if parsed.tzinfo is not None:
                provider_observed_at=parsed.isoformat()
                break
        except (TypeError,ValueError):
            continue
    spread=(ask-bid)/bid*100.0 if bid is not None and ask is not None and bid>0 and ask>=bid else None
    return {"last":last,"bid":bid,"ask":ask,"bid_size":bid_size,"ask_size":ask_size,"spread_pct":spread,
            "variation_pct":_first_number(payload.get("variation"),payload.get("variation_pct"),trade.get("variation")),
            "cash_volume":_first_number(payload.get("cash_volume"),payload.get("volume_amount"),trade.get("cash_volume")),
            "provider_observed_at":provider_observed_at}

def refresh(symbols: Iterable[str], call_tool: Callable[[str,dict],dict], *, root: Path | str | None=None,
            market: str=DEFAULT_MARKET, primary_last_by_symbol: dict[str,float] | None=None,
            tolerance_pct: float=2.0, now: datetime | None=None) -> dict:
    """Legacy small refresh; production scheduling is implemented by iol_shadow_collector_rc6."""
    observed_at=(now or datetime.now(timezone.utc)).isoformat()
    primary=primary_last_by_symbol or {}; rows=[]
    for raw in list(symbols)[:MAX_SYMBOLS_PER_REFRESH]:
        symbol=str(raw or "").upper().strip()
        if not symbol: continue
        try:
            if READ_ONLY_TOOLS != frozenset({"get_asset_quote","get_asset_info"}): raise PermissionError("IOL_SHADOW_TOOL_ALLOWLIST_VIOLATION")
            quote=_quote_summary(call_tool("get_asset_quote",{"symbol":symbol,"market":market,"term":"t1"}))
            info=call_tool("get_asset_info",{"symbol":symbol,"market":market}) or {}
            rows.append({"symbol":symbol,"market":market,"term":"t1","state":"READY" if quote.get("last") is not None else "UNAVAILABLE",
                         "quote":quote,"asset_type":str(info.get("type") or ""), "currency":str(info.get("currency") or ""),
                         "units_per_lot":info.get("units_per_lot"),"primary_comparison":compare_background_numeric(_number(primary.get(symbol)),quote.get("last"),tolerance_pct=tolerance_pct),"decision_effect":DECISION_EFFECT})
        except Exception as exc:
            rows.append({"symbol":symbol,"market":market,"term":"t1","state":"UNAVAILABLE","reason":f"{type(exc).__name__}:{str(exc)[:160]}","decision_effect":DECISION_EFFECT})
    payload={"schema_version":SCHEMA_VERSION,"refreshed_at":observed_at,"source":SOURCE,"mode":MODE,"decision_effect":DECISION_EFFECT,
             "live_decision_authority":False,"real_money_authorized":False,"symbols":rows}
    _write_atomic(cache_path(root),payload); return payload

def collect(root: Path | str | None=None) -> dict:
    try: payload=json.loads(cache_path(root).read_text(encoding="utf-8"))
    except (FileNotFoundError,OSError,json.JSONDecodeError): payload={}
    if payload.get("schema_version") not in (SCHEMA_VERSION,2,3):
        return {"source":SOURCE,"mode":MODE,"state":"UNAVAILABLE","decision_effect":DECISION_EFFECT,"live_decision_authority":False,"real_money_authorized":False,"symbols":[],"reason":"CACHE_MISSING_OR_INVALID"}
    return {"source":SOURCE,"mode":MODE,"state":"READY" if payload.get("symbols") else "INSUFFICIENT_DATA","decision_effect":DECISION_EFFECT,
            "live_decision_authority":False,"real_money_authorized":False,"refreshed_at":payload.get("refreshed_at"),"progress":payload.get("progress") if isinstance(payload.get("progress"),dict) else {},"telemetry":payload.get("telemetry") if isinstance(payload.get("telemetry"),dict) else {},"primary_comparison_contract":payload.get("primary_comparison_contract") if isinstance(payload.get("primary_comparison_contract"),dict) else {},"symbols":payload.get("symbols") or []}
