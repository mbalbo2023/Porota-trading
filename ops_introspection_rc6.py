#!/usr/bin/env python3
"""Introspección funcional horaria y bajo demanda de Porota RC6.

Lee el ledger y los estados del runtime; no cambia trading ni configuracion.
Puede encolar una alerta CRITICAL en el outbox PAPER mediante una opcion
explicita. Siempre conserva JSON/Markdown inmutables y un resumen ultimo.md.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ch_empirical_learning import empirical_expectancy
from ci_operational_context import breadth_observation, sector_observation
from _version import VERSION

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
DB = os.getenv("PAPER_V17_DB_PATH", "/app/data/paper_v17/observer_v17.db")
OUT = Path(os.getenv("INTROSPECTION_DIR", "/app/data/introspection"))
MAX_HOLD = int(os.getenv("PAPER_MAX_HOLD_MINUTES", "360"))
HOTFIX_TAG = VERSION.rsplit("-", 1)[-1].upper()
FILE_TAG = HOTFIX_TAG.lower()


def connect(readonly=True):
    target = f"file:{DB}?mode=ro" if readonly else DB
    c = sqlite3.connect(target, uri=readonly, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def table(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def one(c, sql, params=(), default=0):
    row = c.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else default


def rows(c, sql, params=()):
    return [dict(r) for r in c.execute(sql, params)]


def atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def collect():
    now = datetime.now(TZ)
    hour = (now-timedelta(hours=1)).isoformat()
    day = datetime.combine(now.date(), datetime.min.time(), TZ).isoformat()
    result = {"version": VERSION, "hotfix": HOTFIX_TAG,
              "timestamp": now.isoformat(timespec="seconds"), "warnings": [], "anomalies": []}
    with connect() as c:
        result["quick_check"] = one(c, "PRAGMA quick_check", default="missing")
        observer = dict(c.execute("SELECT * FROM observer_state WHERE id=1").fetchone() or {})
        result["observer"] = {k: observer.get(k) for k in (
            "mode", "process_state", "session_state", "ppi_auth", "heartbeat_at",
            "last_market_data_at", "real_orders_sent", "http_allowed", "http_blocked")}
        manifest_path=Path(DB).parents[1]/"operation_mode.json"
        try: result["manifest"]=json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError,ValueError,TypeError): result["manifest"]={}
        if result["manifest"].get("mode") != result["observer"].get("mode"):
            result["anomalies"].append("manifest_observer_mode_mismatch")
        result["trading"] = {
            "decisions_1h": one(c, "SELECT COUNT(*) FROM paper_decisions WHERE julianday(decided_at)>=julianday(?)", (hour,)),
            # _open registra PAPER_FILLED_BUY para toda apertura, incluida la
            # de scalping. SCALPING_PAPER_FILLED_BUY es una etiqueta adicional
            # del mismo fill y sumarla duplicaría la compra.
            "buys_1h": one(c, "SELECT COUNT(*) FROM paper_events WHERE event_type='PAPER_FILLED_BUY' AND julianday(event_at)>=julianday(?)", (hour,)),
            "sells_1h": one(c, "SELECT COUNT(*) FROM paper_events WHERE event_type='PAPER_FILLED_SELL' AND julianday(event_at)>=julianday(?)", (hour,)),
            "open": one(c, "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"),
            "closed_today": one(c, "SELECT COUNT(*) FROM paper_positions WHERE status='CLOSED' AND julianday(closed_at)>=julianday(?)", (day,)),
            "net_pnl_today": one(c, "SELECT COALESCE(SUM(CAST(net_pnl AS REAL)),0) FROM paper_positions WHERE status='CLOSED' AND julianday(closed_at)>=julianday(?)", (day,), 0.0),
        }
        result["reasons_1h"] = rows(c, """SELECT reason,COUNT(*) count FROM paper_decisions
          WHERE julianday(decided_at)>=julianday(?) GROUP BY reason ORDER BY count DESC LIMIT 8""", (hour,))
        coherence = {
            "orphan_fills": one(c, """SELECT COUNT(*) FROM paper_fills f LEFT JOIN
              paper_positions p USING(paper_id) WHERE p.paper_id IS NULL"""),
            "open_without_buy": one(c, """SELECT COUNT(*) FROM paper_positions p
              WHERE p.status='OPEN' AND NOT EXISTS(SELECT 1 FROM paper_fills f
              WHERE f.paper_id=p.paper_id AND f.side='BUY_SIMULATED')"""),
            "closed_without_sell": one(c, """SELECT COUNT(*) FROM paper_positions p
              WHERE p.status='CLOSED' AND NOT EXISTS(SELECT 1 FROM paper_fills f
              WHERE f.paper_id=p.paper_id AND f.side='SELL_SIMULATED')"""),
            "open_without_exit_intent": one(c, """SELECT COUNT(*) FROM paper_positions p
              LEFT JOIN paper_exit_intents x USING(paper_id)
              WHERE p.status='OPEN' AND x.paper_id IS NULL""") if table(c,"paper_exit_intents") else 0,
            "open_over_max_hold": one(c, """SELECT COUNT(*) FROM paper_positions
              WHERE status='OPEN' AND (julianday('now')-julianday(opened_at))*1440>?""", (MAX_HOLD,)),
        }
        result["coherence"] = coherence
        for key, value in coherence.items():
            if value:
                result["anomalies"].append(f"{key}={value}")
        result["workers"] = {}
        for name, source in (("supervisor","paper_supervisor_state"),
                             ("exit_reader","paper_exit_reader_state"),
                             ("telegram","paper_notification_worker"),
                             ("candles","candle_worker_state"),
                             ("scalping","intraday_scalping_worker_state")):
            if table(c, source):
                row = dict(c.execute(f"SELECT * FROM {source} WHERE id=1").fetchone() or {})
                selected={k: row.get(k) for k in ("state","heartbeat_at","detail")}
                try: selected["heartbeat_age_seconds"]=(now-datetime.fromisoformat(row["heartbeat_at"]).astimezone(TZ)).total_seconds()
                except (KeyError,ValueError,TypeError): selected["heartbeat_age_seconds"]=None
                result["workers"][name] = selected
            else:
                result["workers"][name] = {"state":"NOT_INSTALLED","heartbeat_at":None,"detail":""}
        result["ingestion"] = {
            "sources": rows(c, "SELECT source,status,last_attempt_at,last_success_at,items,detail FROM source_sync ORDER BY source") if table(c,"source_sync") else [],
            "last_market_date": one(c, "SELECT MAX(date_to) FROM production_history", default=None) if table(c,"production_history") else None,
            "last_download": one(c, "SELECT MAX(downloaded_at) FROM production_history", default=None) if table(c,"production_history") else None,
            "instruments": one(c, "SELECT COUNT(*) FROM production_history WHERE row_count>0") if table(c,"production_history") else 0,
            "rows": one(c, "SELECT COALESCE(SUM(row_count),0) FROM production_history") if table(c,"production_history") else 0,
            "raw": one(c, "SELECT COUNT(*) FROM historical_raw_archive") if table(c,"historical_raw_archive") else 0,
            "candle_versions": one(c, "SELECT COUNT(*) FROM candle_versions") if table(c,"candle_versions") else 0,
            "latest_candle_known_at": one(c, "SELECT MAX(known_at) FROM candle_versions", default=None) if table(c,"candle_versions") else None,
        }
        result["economics"] = {
            "evaluated_1h": one(c, "SELECT COUNT(*) FROM trade_gate_evaluations WHERE julianday(evaluated_at)>=julianday(?)", (hour,)),
            "blocked_1h": one(c, "SELECT COUNT(*) FROM trade_gate_evaluations WHERE julianday(evaluated_at)>=julianday(?) AND final_result='BLOCKED'", (hour,)),
            "opened_1h": one(c, "SELECT COUNT(*) FROM trade_gate_evaluations WHERE julianday(evaluated_at)>=julianday(?) AND final_result='OPENED_SIMULATED'", (hour,)),
            "health": rows(c, "SELECT component,state,checked_at,last_success_at,detail FROM api_health WHERE component LIKE 'PAPER_ECONOMIC%' ORDER BY checked_at DESC") if table(c,"api_health") else [],
        }
        result["gate_contradictions_1h"] = rows(c, """SELECT decision_key,symbol,final_result,
          technical_gate,patrimonial_gate,detail_json FROM trade_gate_evaluations
          WHERE julianday(evaluated_at)>=julianday(?) AND final_result='OPENED_SIMULATED'
          AND (technical_gate<>'APPROVE' OR patrimonial_gate<>'APPROVE')""",(hour,))
        if result["gate_contradictions_1h"]:
            result["anomalies"].append("gate_contradiction_opened_simulated")
        result["currencies_today"] = rows(c, """SELECT currency,status,COUNT(*) count,
          COALESCE(SUM(CAST(net_pnl AS REAL)),0) net_pnl FROM paper_positions
          WHERE julianday(opened_at)>=julianday(?) GROUP BY currency,status ORDER BY currency,status""",(day,))
        closed_sample = rows(c, """WITH ranked AS (
          SELECT status,currency,net_pnl,
            ROW_NUMBER() OVER (PARTITION BY currency ORDER BY julianday(closed_at) DESC,paper_id DESC) sample_rank
          FROM paper_positions WHERE status='CLOSED'
        ) SELECT status,currency,net_pnl FROM ranked WHERE sample_rank<=100
          ORDER BY currency,sample_rank""")
        result["learning_expectancy"] = empirical_expectancy(closed_sample, minimum_sample=30)
        result["scalping"] = {
            "evaluated_1h": one(c,"SELECT COUNT(*) FROM scalping_candidates WHERE julianday(evaluated_at)>=julianday(?)",(hour,)) if table(c,"scalping_candidates") else 0,
            "rejected_1h": one(c,"SELECT COUNT(*) FROM scalping_candidates WHERE julianday(evaluated_at)>=julianday(?) AND action<>'BUY_CANDIDATE'",(hour,)) if table(c,"scalping_candidates") else 0,
            "approved_1h": one(c,"SELECT COUNT(*) FROM scalping_candidates WHERE julianday(evaluated_at)>=julianday(?) AND action='BUY_CANDIDATE'",(hour,)) if table(c,"scalping_candidates") else 0,
            "paper_positions_today": one(c,"SELECT COUNT(*) FROM paper_positions WHERE julianday(opened_at)>=julianday(?) AND features_json LIKE '%SCALPING_PAPER%'",(day,)),
        }
        result["currency_funnel"] = rows(c, """SELECT currency,
          COUNT(DISTINCT symbol) observed_symbols,
          MAX(observed_at) last_observed_at
          FROM market_snapshots WHERE julianday(observed_at)>=julianday(?)
          GROUP BY currency ORDER BY currency""", (day,))
        regime_rows = rows(c, """WITH ranked AS (
          SELECT symbol,currency,last,
            ROW_NUMBER() OVER (PARTITION BY symbol,currency ORDER BY julianday(COALESCE(trade_at,observed_at)),id) first_rank,
            ROW_NUMBER() OVER (PARTITION BY symbol,currency ORDER BY julianday(COALESCE(trade_at,observed_at)) DESC,id DESC) last_rank
          FROM market_snapshots WHERE last_kind='TRADE' AND CAST(last AS REAL)>0
            AND julianday(COALESCE(trade_at,observed_at))>=julianday(?)
        ) SELECT symbol,currency,
          MAX(CASE WHEN first_rank=1 THEN last END) first_price,
          MAX(CASE WHEN last_rank=1 THEN last END) last_price
          FROM ranked GROUP BY symbol,currency ORDER BY symbol,currency""", (day,))
        result["market_regime_observation"] = breadth_observation(regime_rows, minimum_symbols=4)
        open_positions = rows(c, """SELECT paper_id,symbol,asset_class,market,currency,settlement
          FROM paper_positions WHERE status='OPEN' ORDER BY opened_at""")
        catalog = rows(c, """SELECT ticker,instrument_type,market,currency,settlement,metadata_json
          FROM financial_instrument_catalog WHERE status='AVAILABLE'""") if table(c, "financial_instrument_catalog") else []
        sector_by_identity = {}
        for item in catalog:
            try:
                raw = json.loads(item.get("metadata_json") or "{}")
            except (TypeError, ValueError):
                raw = {}
            values = {str(raw.get(key)).strip() for key in
                      ("sector", "industry", "sectorName", "industryName")
                      if raw.get(key) not in (None, "")}
            key = (item.get("ticker"), item.get("instrument_type"), item.get("market"),
                   item.get("currency"), item.get("settlement"))
            if len(values) == 1:
                sector_by_identity[key] = values.pop()
        sector_positions = []
        for position in open_positions:
            key = (position.get("symbol"), position.get("asset_class"), position.get("market"),
                   position.get("currency"), position.get("settlement"))
            sector_positions.append(dict(position, sector=sector_by_identity.get(key)))
        result["sector_concentration"] = sector_observation(sector_positions)
        result["ppi_errors_1h"] = rows(c, """SELECT event_type,detail,COUNT(*) count FROM paper_events
          WHERE julianday(event_at)>=julianday(?) AND event_type IN ('DATA_ERROR','EXIT_BOOK_ERROR',
          'EXIT_READER_LOGIN_ERROR','EXIT_READER_SESSION_INVALID','PPI_SESSION_INVALID')
          GROUP BY event_type,detail ORDER BY count DESC LIMIT 20""", (hour,))
        result["ppi_logins_1h"] = rows(c, """SELECT detail,COUNT(*) count,
          MIN(event_at) first_at,MAX(event_at) last_at FROM paper_events
          WHERE julianday(event_at)>=julianday(?) AND event_type='PPI_LOGIN'
          GROUP BY detail ORDER BY detail""", (hour,))
        coverage = (dict(c.execute("""SELECT declared,queries,observed_count,discovery_status
          FROM catalog_family_coverage WHERE instrument_type='CAUCIONES'""").fetchone() or {})
          if table(c, "catalog_family_coverage") else {})
        observed_cauciones = one(c, """SELECT COUNT(*) FROM financial_instrument_catalog
          WHERE instrument_type='CAUCIONES' AND status='AVAILABLE'""") if table(c, "financial_instrument_catalog") else 0
        placed_today = one(c, """SELECT COUNT(*) FROM paper_cauciones
          WHERE julianday(opened_at)>=julianday(?)""", (day,)) if table(c, "paper_cauciones") else 0
        result["caucion_readiness"] = {
            "declared": coverage.get("declared"),
            "queries": coverage.get("queries", 0),
            "observed_contracts": observed_cauciones,
            # SearchInstrument no contiene la oferta ejecutable completa. Hasta
            # incorporar y validar una fuente de libro/términos, no hay una
            # CaucionOffer apta que pueda entregarse al asignador.
            "complete_offers": 0,
            "placed_today": placed_today,
            "state": "MISSING_CURRENT_CONTRACT_TERMS" if (observed_cauciones or placed_today) else (
                str(coverage.get("discovery_status") or "NOT_OBSERVED")),
            "missing_offer_requirements": [
                "instrument_id", "currency", "annual_rate_fraction", "start_date",
                "maturity_at", "quoted_at", "available_principal", "minimum_principal",
                "principal_step", "day_count_basis", "fee_payment", "metadata_source",
            ],
            "missing_policy_requirements": [
                "frozen_at", "reserve_cash", "maximum_cash_fraction",
                "maximum_principal", "liquidity_deadline", "maximum_quote_age_seconds",
                "participation", "minimum_net_profit", "ranking",
                "session_open_at", "session_close_at", "session_source",
            ],
        }
        coverage_rows = rows(c, """SELECT instrument_type,declared,queries,observed_count,
          ready_paper_count,discovery_status,checked_at FROM catalog_family_coverage
          ORDER BY instrument_type""") if table(c, "catalog_family_coverage") else []
        capability_rows = rows(c, """SELECT instrument_type,capability,COUNT(*) count
          FROM financial_instrument_catalog WHERE status='AVAILABLE'
          GROUP BY instrument_type,capability ORDER BY instrument_type,capability""") if table(c, "financial_instrument_catalog") else []
        capabilities = {}
        for item in capability_rows:
            capabilities.setdefault(item["instrument_type"], []).append(
                {"capability": item["capability"], "count": item["count"]})
        result["family_readiness"] = [
            dict(item, capabilities=capabilities.get(item["instrument_type"], []),
                 excluded_by_default=False)
            for item in coverage_rows
        ]
        result["telegram"] = ({r["state"]: r["count"] for r in rows(c,
          "SELECT state,COUNT(*) count FROM paper_notification_outbox GROUP BY state")}
          if table(c,"paper_notification_outbox") else {})
        db_path = Path(DB)
        usage = shutil.disk_usage(db_path.parent)
        result["storage"] = {
            "filesystem_total": usage.total, "filesystem_used": usage.used,
            "filesystem_free": usage.free,
            "filesystem_used_pct": round(usage.used / usage.total * 100, 2) if usage.total else None,
            "database_bytes": db_path.stat().st_size if db_path.exists() else None,
            "wal_bytes": Path(str(db_path)+"-wal").stat().st_size if Path(str(db_path)+"-wal").exists() else 0,
        }
        publication_path = Path(DB).parents[1] / "introspection_publish/publication_status.json"
        try:
            publication = json.loads(publication_path.read_text(encoding="utf-8"))
            if not isinstance(publication, dict):
                raise ValueError("INVALID_PUBLICATION_STATUS")
            result["github_publication"] = {key: publication.get(key) for key in (
                "status", "recorded_at", "branch", "day", "detail", "retention_days")}
        except (OSError, ValueError, TypeError):
            result["github_publication"] = {
                "status": "NOT_RECORDED", "recorded_at": None,
                "branch": "runtime-observability", "day": None,
                "detail": None, "retention_days": 90,
            }
    if result["quick_check"] != "ok":
        result["anomalies"].append("sqlite_quick_check_not_ok")
    if int(result["observer"].get("real_orders_sent") or 0) != 0:
        result["anomalies"].append("real_orders_sent_nonzero")
    market_open = str(result["observer"].get("session_state") or "").upper() == "MARKET_OPEN"
    if market_open and result["trading"]["decisions_1h"] == 0:
        result["anomalies"].append("no_decisions_during_open_market")
    if result["ppi_errors_1h"]:
        result["warnings"].append("ppi_errors_last_hour")
    if result["market_regime_observation"]["state"] == "BEARISH_BREADTH":
        result["warnings"].append("market_regime_alert_only")
    if result["github_publication"].get("status") in {"FAILED", "NOT_RECORDED"}:
        result["warnings"].append("github_introspection_publication_" +
                                  str(result["github_publication"].get("status")).lower())
    if market_open:
        for name,item in result["workers"].items():
            age=item.get("heartbeat_age_seconds")
            if age is None or age > (300 if name=="scalping" else 60):
                result["anomalies"].append(f"worker_stale:{name}")
    if not any(row.get("currency") in {"USD","USD_MEP","USD_CCL"} for row in result["currencies_today"]):
        result["warnings"].append("no_usd_operations_today")
    return result


def previous():
    files = sorted(OUT.glob("porota_introspection_hf*_*.json")) if OUT.exists() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def compare(current, old):
    if not old:
        return [f"Primer snapshot {HOTFIX_TAG}; sin comparación anterior."]
    changes = []
    for key in ("open", "closed_today", "net_pnl_today"):
        before = old.get("trading", {}).get(key)
        after = current["trading"][key]
        if before != after:
            changes.append(f"trading.{key}: {before} -> {after}")
    if current["ingestion"]["rows"] != old.get("ingestion", {}).get("rows"):
        changes.append(f"ingestion.rows: {old.get('ingestion',{}).get('rows')} -> {current['ingestion']['rows']}")
    return changes or ["Sin cambios funcionales contra el snapshot anterior."]


def verdict(result):
    if result["anomalies"]:
        return "CRITICAL"
    if result["warnings"]:
        return "WARN"
    return "OK"


def enqueue_critical(result, stamp):
    if result["verdict"] != "CRITICAL":
        return False
    body = "🔴 POROTA — INTROSPECCIÓN CRÍTICA\n" + "\n".join(result["anomalies"][:8])
    with connect(readonly=False) as c:
        if not table(c, "paper_notification_outbox"):
            return False
        return bool(c.execute("""INSERT OR IGNORE INTO paper_notification_outbox
          (event_key,kind,body,priority,created_at,next_attempt_at)
          VALUES(?,?,?,?,?,?)""", (f"introspection-{FILE_TAG}:{stamp}", "INTROSPECTION",
          body[:3000], 0, result["timestamp"], result["timestamp"])).rowcount)


def render(result):
    t, i = result["trading"], result["ingestion"]
    publication = result.get("github_publication", {})
    lines = [f"# POROTA — INTROSPECCIÓN {HOTFIX_TAG} — {result['timestamp']}",
             f"## {result['verdict']}", "",
             f"- Motor: {result['observer'].get('process_state')} / {result['observer'].get('session_state')}",
             f"- Órdenes reales: {result['observer'].get('real_orders_sent')}",
             f"- SQLite: {result['quick_check']}",
             f"- Decisiones última hora: {t['decisions_1h']}",
             f"- Compras / ventas última hora: {t['buys_1h']} / {t['sells_1h']}",
             f"- Posiciones abiertas / cerradas hoy: {t['open']} / {t['closed_today']}",
             f"- PnL neto hoy: {t['net_pnl_today']}",
             f"- Histórico: {i['instruments']} instrumentos / {i['rows']} filas",
             f"- Último dato bursátil: {i['last_market_date']}",
             f"- Última descarga: {i['last_download']}", "",
             f"- Última versión de vela conocida: {i['latest_candle_known_at']}",
             ("- Scalping última hora: "
              f"evaluados {result['scalping']['evaluated_1h']} / "
              f"rechazados {result['scalping']['rejected_1h']} / "
              f"aprobados {result['scalping']['approved_1h']} / "
              f"fills PAPER hoy {result['scalping']['paper_positions_today']}"),
             f"- Disco usado: {result['storage']['filesystem_used_pct']}%", "",
             ("- GitHub observabilidad: "
              f"{publication.get('status')} / {publication.get('recorded_at')} / "
              f"retención {publication.get('retention_days',90)} días"), "",
             "## Cambios"] + [f"- {x}" for x in result["changes"]]
    if result["warnings"]:
        lines += ["", "## Advertencias"] + [f"- {x}" for x in result["warnings"]]
    if result["anomalies"]:
        lines += ["", "## Anomalías"] + [f"- {x}" for x in result["anomalies"]]
    return "\n".join(lines)+"\n"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--enqueue-critical", action="store_true")
    args = parser.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    result = collect()
    result["changes"] = compare(result, previous())
    result["verdict"] = verdict(result)
    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S_%f")
    if args.enqueue_critical:
        result["telegram_enqueued"] = enqueue_critical(result, stamp)
    json_path = OUT / f"porota_introspection_{FILE_TAG}_{stamp}.json"
    md_path = OUT / f"porota_introspection_{FILE_TAG}_{stamp}.md"
    atomic_text(json_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    text = render(result)
    atomic_text(md_path, text)
    atomic_text(OUT / f"ultimo_{FILE_TAG}.md", text)
    print(text, end="")
    return 2 if result["verdict"] == "CRITICAL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
