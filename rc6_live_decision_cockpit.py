#!/usr/bin/env python3
"""RC6 live Decision Cockpit.

Read-only collector for the private operator site. It never calls broker APIs
directly and never mutates the trading engine. Observer state is read through
the already-running dashboard container; IOL is consumed cache-only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, time as clock_time, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
MARKET_OPEN = clock_time(10, 30)
MARKET_CLOSE = clock_time(17, 0)

ROOT = Path(os.getenv("POROTA_ROOT", "/opt/porota-trading"))
DATA = ROOT / "data"
OUTPUT = Path(os.getenv(
    "POROTA_LIVE_COCKPIT_OUTPUT",
    str(DATA / "paper_v17" / "snapshots" / "live_latest.json"),
))
PREOPEN = DATA / "paper_v17" / "snapshots" / "preopen_latest.json"
POSTCLOSE = DATA / "paper_v17" / "snapshots" / "postclose_latest.json"
IOL_CACHE = DATA / "market" / "iol_shadow_latest.json"
VALIDATION = DATA / "validation" / "validation_milestones_rc6.json"

MAX_ROWS = 10
IOL_FRESH_SECONDS = int(os.getenv("POROTA_LIVE_IOL_FRESH_SECONDS", "180"))


def _number(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _parse_dt(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=TZ)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".live-cockpit.", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def is_operational_market_window(now: datetime) -> bool:
    local = now.astimezone(TZ)
    try:
        import ak_byma_calendar as byma
        business_day = bool(byma.es_dia_habil_operativo(local.date()))
    except Exception:
        # Fail closed: a weekday alone is not enough to certify BYMA open.
        return False
    return business_day and MARKET_OPEN <= local.time() < MARKET_CLOSE


def _observer_state_via_container() -> dict:
    """Read the existing dashboard API from inside its own container.

    This avoids loading dashboard credentials on the host and does not create
    a second broker/API reader.
    """
    code = r'''
import json, os, sqlite3, urllib.request
token=os.getenv("DASHBOARD_ACCESS_TOKEN","")
if not token:
    raise SystemExit("DASHBOARD_ACCESS_TOKEN_NOT_AVAILABLE")
req=urllib.request.Request(
    "http://127.0.0.1:8000/api/observer/state",
    headers={"Authorization":"Bearer "+token,"Cache-Control":"no-cache"},
)
with urllib.request.urlopen(req,timeout=20) as r:
    obj=json.loads(r.read().decode("utf-8"))
if not isinstance(obj,dict):
    raise SystemExit("OBSERVER_STATE_INVALID")

db_path=os.getenv("PAPER_V17_DB_PATH","/app/data/paper_v17/observer_v17.db")
extra={"gates":[],"fills":[],"family_coverage":[],"catalog_status":[],"api_health":[],"source_sync":[]}
try:
    conn=sqlite3.connect("file:"+db_path+"?mode=ro",uri=True,timeout=5)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    def exists(name):
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone() is not None
    def rows(sql):
        return [dict(r) for r in conn.execute(sql).fetchall()]
    if exists("trade_gate_evaluations"):
        extra["gates"]=rows("""SELECT evaluated_at,symbol,technical_gate,ai_gate,patrimonial_gate,
            final_result,reason,paper_id FROM trade_gate_evaluations ORDER BY id DESC LIMIT 100""")
    if exists("paper_fills") and exists("paper_positions"):
        extra["fills"]=rows("""SELECT f.filled_at,f.paper_id,f.side,f.quantity,f.price,f.costs,f.slippage,
            p.symbol,p.currency,p.asset_class FROM paper_fills f
            LEFT JOIN paper_positions p ON p.paper_id=f.paper_id ORDER BY f.id DESC LIMIT 100""")
    if exists("catalog_family_coverage"):
        extra["family_coverage"]=rows("""SELECT instrument_type,declared,queries,observed_count,ready_paper_count,
            discovery_status,checked_at FROM catalog_family_coverage ORDER BY instrument_type""")
    if exists("financial_instrument_catalog"):
        extra["catalog_status"]=rows("""SELECT instrument_type,status,capability,COUNT(*) total,MAX(last_seen_at) last_seen_at
            FROM financial_instrument_catalog GROUP BY instrument_type,status,capability
            ORDER BY instrument_type,status,capability""")
    if exists("api_health"):
        extra["api_health"]=rows("""SELECT component,state,detail,checked_at,last_success_at,source
            FROM api_health ORDER BY component""")
    if exists("source_sync"):
        extra["source_sync"]=rows("""SELECT source,status,last_attempt_at,last_success_at,items,detail
            FROM source_sync ORDER BY source""")
    conn.close()
except Exception as exc:
    extra={"status":"READ_ERROR","error":type(exc).__name__,"gates":[],"fills":[],"family_coverage":[],"catalog_status":[],"api_health":[],"source_sync":[]}
obj["_cockpit_db"]=extra
print(json.dumps(obj,ensure_ascii=False,separators=(",",":")))
'''
    proc = subprocess.run(
        ["/usr/bin/docker", "exec", "porota_production_dashboard", "python", "-c", code],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("OBSERVER_READ_FAILED:" + (proc.stderr or proc.stdout)[-240:])
    value = json.loads(proc.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("OBSERVER_STATE_INVALID")
    return value


def _compact_decision(row: dict) -> dict:
    return {
        "decided_at": row.get("decided_at"),
        "symbol": row.get("symbol"),
        "action": row.get("action"),
        "score": _number(row.get("score")),
        "reason": row.get("reason") or "NO_REASON",
        "strategy_version": row.get("strategy_version"),
    }


def _decision_views(decisions: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    compact = [_compact_decision(row) for row in decisions if isinstance(row, dict)]
    latest_by_symbol: dict[str, dict] = {}
    for row in compact:
        symbol = str(row.get("symbol") or "").upper().strip()
        if symbol and symbol not in latest_by_symbol:
            latest_by_symbol[symbol] = row

    opportunities = list(latest_by_symbol.values())
    opportunities.sort(
        key=lambda row: (
            "BUY" in str(row.get("action") or "").upper(),
            row.get("score") is not None,
            row.get("score") if row.get("score") is not None else -1e18,
        ),
        reverse=True,
    )
    why_not = [
        row for row in compact
        if "BUY" not in str(row.get("action") or "").upper()
    ]
    return opportunities[:MAX_ROWS], why_not[:MAX_ROWS], compact[:MAX_ROWS]


def _change_from_preopen(preopen: dict, opportunities: list[dict]) -> dict:
    baseline = preopen.get("decision_sample") if isinstance(preopen.get("decision_sample"), list) else []
    if not baseline:
        return {
            "status": "INSUFFICIENT_EVIDENCE",
            "label": "Sin muestra de decisiones en el snapshot pre-rueda",
            "changes": [],
        }
    before = {
        str(row.get("symbol") or "").upper(): str(row.get("action") or "")
        for row in baseline if isinstance(row, dict) and row.get("symbol")
    }
    changes = []
    for row in opportunities:
        symbol = str(row.get("symbol") or "").upper()
        old, new = before.get(symbol), str(row.get("action") or "")
        if old is not None and old != new:
            changes.append({"symbol": symbol, "from": old, "to": new})
    return {
        "status": "VERIFIED",
        "label": f"{len(changes)} cambios de acción respecto de pre-rueda",
        "changes": changes[:MAX_ROWS],
    }


def _iol_summary(iol: dict, now: datetime) -> dict:
    rows = [row for row in (iol.get("symbols") or []) if isinstance(row, dict)]
    fresh_ready = 0
    stale = 0
    divergence = 0
    ready = 0
    for row in rows:
        if row.get("state") == "READY":
            ready += 1
        captured = _parse_dt(row.get("captured_at"))
        age = (now.astimezone(timezone.utc) - captured.astimezone(timezone.utc)).total_seconds() if captured else None
        if row.get("state") == "READY" and age is not None and 0 <= age <= IOL_FRESH_SECONDS:
            fresh_ready += 1
        elif age is None or age > IOL_FRESH_SECONDS:
            stale += 1
        comparison = row.get("primary_comparison") if isinstance(row.get("primary_comparison"), dict) else {}
        if comparison.get("state") == "PRICE_DIVERGENCE":
            divergence += 1
    return {
        "source": iol.get("source") or "IOL_MCP",
        "mode": iol.get("mode") or "SHADOW",
        "decision_effect": iol.get("decision_effect") or "OBSERVE_ONLY",
        "refreshed_at": iol.get("refreshed_at"),
        "universe_observed": len(rows),
        "ready": ready,
        "fresh_ready": fresh_ready,
        "stale_or_unknown": stale,
        "price_divergence": divergence,
        "influence_on_live_decision": "NONE_OBSERVE_ONLY",
    }


def _risk_summary(opened: list[dict]) -> dict:
    notionals = []
    unrealized = []
    by_symbol = Counter()
    for row in opened:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "N/D")
        by_symbol[symbol] += 1
        q = _number(row.get("quantity"))
        px = _number(row.get("current_price"))
        if px is None:
            px = _number(row.get("entry_price"))
        if q is not None and px is not None:
            notionals.append(abs(q * px))
        upnl = _number(row.get("unrealized_pnl"))
        if upnl is not None:
            unrealized.append(upnl)
    return {
        "open_positions": len(opened),
        "estimated_notional_ars": round(sum(notionals), 2) if notionals else None,
        "unrealized_pnl_ars": round(sum(unrealized), 2) if unrealized else None,
        "symbols": dict(by_symbol),
        "risk_authority": "DISPLAY_ONLY",
    }



def _first_number(row: dict, *names):
    for name in names:
        value = _number(row.get(name))
        if value is not None:
            return value
    return None


def _compact_position(row: dict) -> dict:
    return {
        "paper_id": row.get("paper_id"),
        "symbol": row.get("symbol"),
        "asset_class": row.get("asset_class") or row.get("instrument_type"),
        "currency": row.get("currency") or "N/D",
        "market": row.get("market") or "N/D",
        "settlement": row.get("settlement") or "N/D",
        "status": row.get("status"),
        "quantity": _first_number(row, "quantity", "open_quantity"),
        "entry_price": _first_number(row, "entry_price", "average_price"),
        "current_price": _first_number(row, "current_price", "mark_price", "last"),
        "stop_price": _first_number(row, "stop_price", "stop"),
        "target_price": _first_number(row, "target_price", "target"),
        "unrealized_pnl": _first_number(row, "unrealized_pnl", "unrealized_pnl_ars"),
        "net_pnl": _first_number(row, "net_pnl", "net_pnl_ars"),
        "opened_at": row.get("opened_at"),
        "closed_at": row.get("closed_at"),
        "close_reason": row.get("close_reason"),
        "max_favorable": _first_number(row, "max_favorable"),
        "max_adverse": _first_number(row, "max_adverse"),
    }


def _performance_summary(closed: list[dict]) -> dict:
    by_currency: dict[str, dict] = {}
    reasons = Counter()
    for raw in closed:
        if not isinstance(raw, dict):
            continue
        row = _compact_position(raw)
        currency = str(row.get("currency") or "N/D")
        bucket = by_currency.setdefault(currency, {
            "trades": 0, "wins": 0, "losses": 0, "flats": 0,
            "net_pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0,
            "largest_win": None, "largest_loss": None,
        })
        pnl = row.get("net_pnl")
        if pnl is None:
            continue
        bucket["trades"] += 1
        bucket["net_pnl"] += pnl
        if pnl > 0:
            bucket["wins"] += 1
            bucket["gross_profit"] += pnl
            bucket["largest_win"] = pnl if bucket["largest_win"] is None else max(bucket["largest_win"], pnl)
        elif pnl < 0:
            bucket["losses"] += 1
            bucket["gross_loss"] += pnl
            bucket["largest_loss"] = pnl if bucket["largest_loss"] is None else min(bucket["largest_loss"], pnl)
        else:
            bucket["flats"] += 1
        if row.get("close_reason"):
            reasons[str(row["close_reason"])] += 1
    for bucket in by_currency.values():
        trades = bucket["trades"]
        decided = bucket["wins"] + bucket["losses"]
        bucket["net_pnl"] = round(bucket["net_pnl"], 2)
        bucket["gross_profit"] = round(bucket["gross_profit"], 2)
        bucket["gross_loss"] = round(bucket["gross_loss"], 2)
        bucket["win_rate_pct"] = round(bucket["wins"] * 100.0 / decided, 2) if decided else None
        bucket["profit_factor"] = round(bucket["gross_profit"] / abs(bucket["gross_loss"]), 3) if bucket["gross_loss"] < 0 else None
        bucket["expectancy"] = round(bucket["net_pnl"] / trades, 2) if trades else None
    return {
        "by_currency": by_currency,
        "close_reasons": dict(reasons.most_common(10)),
        "mixed_currency_total_suppressed": len(by_currency) > 1,
    }


def _market_summary(quotes: list[dict], now: datetime) -> dict:
    spreads = []
    ages = []
    crossed = 0
    missing_book = 0
    stale = 0
    by_asset = Counter()
    for row in quotes:
        if not isinstance(row, dict):
            continue
        asset = str(row.get("asset_class") or row.get("instrument_type") or "N/D")
        by_asset[asset] += 1
        bid, ask = _number(row.get("bid")), _number(row.get("ask"))
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            if ask < bid:
                crossed += 1
            if mid > 0:
                spreads.append((ask - bid) / mid * 10000.0)
        else:
            missing_book += 1
        stamp = _parse_dt(row.get("book_at") or row.get("trade_at") or row.get("observed_at"))
        if stamp:
            age = (now.astimezone(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
            if age >= 0:
                ages.append(age)
                if age > IOL_FRESH_SECONDS:
                    stale += 1
        else:
            stale += 1
    return {
        "quotes": len([q for q in quotes if isinstance(q, dict)]),
        "valid_books": len(spreads),
        "median_spread_bps": round(median(spreads), 2) if spreads else None,
        "crossed_books": crossed,
        "missing_book": missing_book,
        "stale_or_unknown": stale,
        "freshest_age_seconds": round(min(ages), 1) if ages else None,
        "stalest_age_seconds": round(max(ages), 1) if ages else None,
        "by_asset_class": dict(by_asset),
    }


def _decision_funnel(decisions: list[dict]) -> dict:
    actions = Counter()
    reasons = Counter()
    strategies = Counter()
    for raw in decisions:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "N/D").upper()
        actions[action] += 1
        reasons[str(raw.get("reason") or "NO_REASON")] += 1
        if raw.get("strategy_version"):
            strategies[str(raw.get("strategy_version"))] += 1
    return {
        "actions": dict(actions),
        "top_reasons": dict(reasons.most_common(10)),
        "strategies": dict(strategies.most_common(10)),
        "sample_size": sum(actions.values()),
    }


def _family_summary(quotes: list[dict], decisions: list[dict], opened: list[dict], closed: list[dict]) -> list[dict]:
    symbol_family = {}
    for row in quotes:
        if isinstance(row, dict) and row.get("symbol"):
            symbol_family[str(row["symbol"]).upper()] = str(row.get("asset_class") or row.get("instrument_type") or "N/D")
    families: dict[str, dict] = {}
    def bucket(name):
        return families.setdefault(name or "N/D", {"family": name or "N/D", "quotes": 0, "decisions": 0, "open_positions": 0, "closed_positions": 0})
    for row in quotes:
        if isinstance(row, dict):
            bucket(str(row.get("asset_class") or row.get("instrument_type") or "N/D"))["quotes"] += 1
    for row in decisions:
        if isinstance(row, dict):
            family = str(row.get("asset_class") or symbol_family.get(str(row.get("symbol") or "").upper()) or "N/D")
            bucket(family)["decisions"] += 1
    for row in opened:
        if isinstance(row, dict):
            bucket(str(row.get("asset_class") or row.get("instrument_type") or "N/D"))["open_positions"] += 1
    for row in closed:
        if isinstance(row, dict):
            bucket(str(row.get("asset_class") or row.get("instrument_type") or "N/D"))["closed_positions"] += 1
    return sorted(families.values(), key=lambda row: (row["quotes"], row["decisions"], row["open_positions"]), reverse=True)[:20]


def _copy_rows(value, limit=20) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [dict(row) for row in value if isinstance(row, dict)][:limit]



def _execution_summary(rows: list[dict]) -> dict:
    by_currency: dict[str, dict] = {}
    recent = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        currency = str(raw.get("currency") or "N/D")
        bucket = by_currency.setdefault(currency, {"fills": 0, "slippage_sum": 0.0, "slippage_count": 0, "costs_sum": 0.0, "costs_count": 0})
        bucket["fills"] += 1
        slip = _number(raw.get("slippage"))
        if slip is not None:
            bucket["slippage_sum"] += slip
            bucket["slippage_count"] += 1
        costs = _number(raw.get("costs"))
        if costs is not None:
            bucket["costs_sum"] += costs
            bucket["costs_count"] += 1
        if len(recent) < 20:
            recent.append({
                "filled_at": raw.get("filled_at"), "paper_id": raw.get("paper_id"),
                "symbol": raw.get("symbol"), "asset_class": raw.get("asset_class"),
                "currency": currency, "side": raw.get("side"),
                "quantity": _number(raw.get("quantity")), "price": _number(raw.get("price")),
                "costs": costs, "slippage": slip,
            })
    for bucket in by_currency.values():
        bucket["avg_slippage"] = round(bucket["slippage_sum"] / bucket["slippage_count"], 6) if bucket["slippage_count"] else None
        bucket["total_costs"] = round(bucket["costs_sum"], 6) if bucket["costs_count"] else None
        bucket.pop("slippage_sum", None)
        bucket.pop("slippage_count", None)
        bucket.pop("costs_sum", None)
        bucket.pop("costs_count", None)
    return {"by_currency": by_currency, "recent": recent, "sample_size": sum(x["fills"] for x in by_currency.values())}


def _gate_summary(rows: list[dict]) -> dict:
    final = Counter()
    reasons = Counter()
    technical = Counter()
    ai = Counter()
    patrimonial = Counter()
    recent = []
    for raw in rows if isinstance(rows, list) else []:
        if not isinstance(raw, dict):
            continue
        final[str(raw.get("final_result") or "N/D")] += 1
        reasons[str(raw.get("reason") or "NO_REASON")] += 1
        technical[str(raw.get("technical_gate") or "N/D")] += 1
        ai[str(raw.get("ai_gate") or "N/D")] += 1
        patrimonial[str(raw.get("patrimonial_gate") or "N/D")] += 1
        if len(recent) < 20:
            recent.append(dict(raw))
    return {
        "final_results": dict(final),
        "top_reasons": dict(reasons.most_common(10)),
        "technical": dict(technical),
        "ai": dict(ai),
        "patrimonial": dict(patrimonial),
        "recent": recent,
        "sample_size": sum(final.values()),
    }


def _deep_cockpit_summary(observer: dict) -> dict:
    raw = observer.get("_cockpit_db") if isinstance(observer.get("_cockpit_db"), dict) else {}
    return {
        "status": raw.get("status") or "VERIFIED",
        "error": raw.get("error"),
        "execution": _execution_summary(raw.get("fills") if isinstance(raw.get("fills"), list) else []),
        "gates": _gate_summary(raw.get("gates") if isinstance(raw.get("gates"), list) else []),
        "family_coverage": _copy_rows(raw.get("family_coverage"), 40),
        "catalog_status": _copy_rows(raw.get("catalog_status"), 80),
        "api_health": _copy_rows(raw.get("api_health"), 40),
        "source_sync": _copy_rows(raw.get("source_sync"), 40),
    }


def capture_preopen_baseline(observer: dict, now: datetime, path: Path = PREOPEN) -> dict:
    """Attach a bounded read-only decision sample to the preserved preopen file."""
    preopen = _read_json(path)
    if preopen.get("phase") != "preopen":
        return {"status": "NOT_APPLICABLE", "reason": "PREOPEN_SNAPSHOT_NOT_AVAILABLE"}
    decisions = observer.get("decisions") if isinstance(observer.get("decisions"), list) else []
    sample = [_compact_decision(row) for row in decisions if isinstance(row, dict)][:MAX_ROWS]
    preopen["decision_sample"] = sample
    preopen["decision_sample_captured_at"] = now.astimezone(TZ).isoformat(timespec="seconds")
    preopen["decision_sample_read_only"] = True
    _atomic_json(path, preopen)
    return {"status": "VERIFIED", "decision_sample": len(sample)}


def build_payload(observer: dict, iol: dict, validation: dict, preopen: dict, postclose: dict, now: datetime) -> dict:
    state = observer.get("state") if isinstance(observer.get("state"), dict) else {}
    decisions = observer.get("decisions") if isinstance(observer.get("decisions"), list) else []
    opened = observer.get("open") if isinstance(observer.get("open"), list) else []
    closed = observer.get("closed") if isinstance(observer.get("closed"), list) else []

    opportunities, why_not, trace = _decision_views(decisions)
    quotes = observer.get("quotes") if isinstance(observer.get("quotes"), list) else []
    compact_open = [_compact_position(row) for row in opened if isinstance(row, dict)][:MAX_ROWS]
    compact_closed = [_compact_position(row) for row in closed if isinstance(row, dict)][:MAX_ROWS]
    market_summary = _market_summary(quotes, now)
    performance_summary = _performance_summary(closed)
    decision_funnel = _decision_funnel(decisions)
    family_summary = _family_summary(quotes, decisions, opened, closed)
    deep = _deep_cockpit_summary(observer)

    try:
        real_orders_sent = int(state.get("real_orders_sent"))
    except (TypeError, ValueError):
        real_orders_sent = None
    mode = state.get("mode")

    alerts = []
    if mode != "PRODUCTION_PAPER":
        alerts.append("MODE_NOT_PRODUCTION_PAPER")
    if real_orders_sent != 0:
        alerts.append("REAL_ORDERS_NOT_ZERO_OR_UNVERIFIED")

    iol_state = _iol_summary(iol, now)
    if iol_state["universe_observed"] and not iol_state["fresh_ready"]:
        alerts.append("IOL_SHADOW_NO_FRESH_READY_ROWS")

    regime = state.get("market_regime") or state.get("regime") or "N/D"
    validation_summary = validation.get("summary") if isinstance(validation.get("summary"), dict) else {}

    return {
        "schema_version": 1,
        "phase": "live",
        "status": "VERIFIED" if not [a for a in alerts if a.startswith(("MODE_", "REAL_"))] else "INSUFFICIENT_EVIDENCE",
        "generated_at": now.astimezone(TZ).isoformat(timespec="seconds"),
        "read_only": True,
        "decision_authority": "OBSERVE_ONLY",
        "automatic_strategy_change": False,
        "real_orders_authorized": False,
        "mode": mode,
        "real_orders_sent": real_orders_sent,
        "market_regime": regime,
        "top_opportunities": opportunities,
        "why_not_traded": why_not,
        "traceability": trace,
        "changes_from_preopen": _change_from_preopen(preopen, opportunities),
        "iol": iol_state,
        "risk": _risk_summary(opened),
        "positions": compact_open,
        "recent_closed_positions": compact_closed,
        "market": market_summary,
        "performance": performance_summary,
        "decision_funnel": decision_funnel,
        "families": family_summary,
        "execution": deep["execution"],
        "gate_matrix": deep["gates"],
        "family_readiness": deep["family_coverage"],
        "catalog_status": deep["catalog_status"],
        "source_health": {
            "status": deep["status"],
            "error": deep["error"],
            "api_health": deep["api_health"],
            "source_sync": deep["source_sync"],
        },
        "portfolio": {
            "equity": observer.get("equity") if isinstance(observer.get("equity"), dict) else {},
            "balances_by_currency": _copy_rows(observer.get("balances_by_currency"), 16),
            "daily_risk": _copy_rows(observer.get("daily_risk"), 16),
            "valuation_quality": _copy_rows(observer.get("valuation_quality"), 16),
            "exit_supervisor": observer.get("exit_supervisor") if isinstance(observer.get("exit_supervisor"), dict) else {},
            "exit_intents": _copy_rows(observer.get("exit_intents"), 20),
            "notification_counts": _copy_rows(observer.get("notification_counts"), 20),
        },
        "runtime": {
            "decisions_visible": len(decisions),
            "open_positions": len(opened),
            "closed_positions_visible": len(closed),
        },
        "validation": {
            "generated_at": validation.get("generated_at"),
            "ledger_status": validation.get("ledger_status"),
            "summary": validation_summary,
            "real_money_state": validation.get("real_money_state"),
        },
        "preopen_generated_at": preopen.get("generated_at"),
        "postclose_generated_at": postclose.get("generated_at"),
        "urgent_alerts": alerts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-preopen-baseline", action="store_true")
    args = parser.parse_args()
    now = datetime.now(TZ)

    if args.capture_preopen_baseline:
        try:
            result = capture_preopen_baseline(_observer_state_via_container(), now)
            print(json.dumps({**result, "read_only": True, "phase": "preopen-baseline"}, sort_keys=True))
            return 0 if result.get("status") in {"VERIFIED", "NOT_APPLICABLE"} else 2
        except Exception as exc:
            print(json.dumps({"status": "ERROR", "phase": "preopen-baseline", "read_only": True, "error": type(exc).__name__}, sort_keys=True))
            return 2

    if not is_operational_market_window(now):
        print(json.dumps({"status": "NOT_DUE", "phase": "live", "read_only": True}, sort_keys=True))
        return 0
    try:
        payload = build_payload(
            _observer_state_via_container(),
            _read_json(IOL_CACHE),
            _read_json(VALIDATION),
            _read_json(PREOPEN),
            _read_json(POSTCLOSE),
            now,
        )
        _atomic_json(OUTPUT, payload)
        print(json.dumps({
            "status": payload["status"],
            "phase": "live",
            "read_only": True,
            "real_orders_sent": payload["real_orders_sent"],
            "opportunities": len(payload["top_opportunities"]),
        }, sort_keys=True))
        return 0 if payload["status"] == "VERIFIED" else 2
    except Exception as exc:
        print(json.dumps({
            "status": "ERROR", "phase": "live", "read_only": True,
            "error": type(exc).__name__,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
