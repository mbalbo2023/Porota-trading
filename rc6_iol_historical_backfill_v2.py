#!/usr/bin/env python3
"""RC6 IOL historical backfill V2.

Read-only against IOL and the PAPER observer DB. Writes only to a dedicated
historical SQLite database. No order methods are imported or called.

Design:
- one-year daily baseline by default;
- exact candidate_universe identity only (no fuzzy symbol inference);
- resumable per (market,family,symbol);
- derivatives use their actual available life inside the requested window;
- empty/unsupported symbols are recorded, not fabricated;
- provenance is part of every primary key.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ak_iol_client import IOLClient

SOURCE = "IOL"
SUPPORTED = {"ACCIONES", "CEDEARS", "BONOS", "ON", "LETRAS", "OPCIONES", "ETF", "FUTUROS"}
# IOL's current API returns CEDEAR history on the unadjusted series. This is
# evidence from the live read-only AAPL diagnostic: adjusted=0 rows while
# sinAjustar returned history. Keep CEDEARS out of ADJUSTED_FAMILIES rather
# than silently treating an empty adjusted response as missing history.
ADJUSTED_FAMILIES = {"ACCIONES", "ETF"}


def norm_family(value: str) -> str:
    s = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "ACCION": "ACCIONES", "ACCIONES": "ACCIONES",
        "CEDEAR": "CEDEARS", "CEDEARS": "CEDEARS",
        "BONO": "BONOS", "BONOS": "BONOS",
        "OBLIGACIONES_NEGOCIABLES": "ON", "ONS": "ON", "ON": "ON",
        "LETRA": "LETRAS", "LETRAS": "LETRAS",
        "OPCION": "OPCIONES", "OPCIONES": "OPCIONES",
        "ETFS": "ETF", "ETF": "ETF",
        "FUTURO": "FUTUROS", "FUTUROS": "FUTUROS",
    }
    return aliases.get(s, s)


def iol_market(family: str, candidate_market: str) -> str:
    if family == "FUTUROS":
        return "rofex"
    cm = str(candidate_market or "").strip().lower()
    if cm in {"nyse", "nasdaq", "amex", "bcs", "bcba"}:
        return cm
    return "bcba"


def init_history(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    PRAGMA journal_mode=WAL;
    PRAGMA synchronous=NORMAL;
    CREATE TABLE IF NOT EXISTS market_historical_ohlcv_v2(
        source TEXT NOT NULL,
        market TEXT NOT NULL,
        family TEXT NOT NULL,
        symbol TEXT NOT NULL,
        date TEXT NOT NULL,
        open REAL, high REAL, low REAL, close REAL, volume REAL,
        adjusted INTEGER NOT NULL DEFAULT 0,
        ingested_at TEXT NOT NULL,
        PRIMARY KEY(source,market,family,symbol,date)
    );
    CREATE INDEX IF NOT EXISTS idx_hist_v2_symbol_date
      ON market_historical_ohlcv_v2(symbol,date);
    CREATE TABLE IF NOT EXISTS historical_ingest_progress_v2(
        source TEXT NOT NULL,
        market TEXT NOT NULL,
        family TEXT NOT NULL,
        symbol TEXT NOT NULL,
        requested_days INTEGER NOT NULL,
        status TEXT NOT NULL,
        rows_written INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT,
        first_date TEXT,
        last_date TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(source,market,family,symbol)
    );
    CREATE TABLE IF NOT EXISTS historical_ingest_runs_v2(
        run_id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        requested_days INTEGER NOT NULL,
        candidates INTEGER NOT NULL DEFAULT 0,
        ok INTEGER NOT NULL DEFAULT 0,
        empty INTEGER NOT NULL DEFAULT 0,
        failed INTEGER NOT NULL DEFAULT 0,
        rows_written INTEGER NOT NULL DEFAULT 0,
        quick_check TEXT,
        notes TEXT
    );
    """)
    conn.commit()


def load_candidates(observer_db: str, selected_families: set[str]) -> list[dict]:
    uri = Path(observer_db).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=15) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("OBSERVER_DB_QUICK_CHECK_FAILED")
        cols = {r[1] for r in c.execute("PRAGMA table_info(candidate_universe)")}
        need = {"ticker", "instrument_type"}
        if not need.issubset(cols):
            raise RuntimeError("CANDIDATE_UNIVERSE_SCHEMA_INCOMPLETE")
        fields = [x for x in ("ticker", "instrument_type", "market", "settlement", "status") if x in cols]
        sql = "SELECT " + ",".join(fields) + " FROM candidate_universe"
        if "status" in cols:
            sql += " WHERE status='AVAILABLE'"
        out = []
        seen = set()
        for row in c.execute(sql):
            d = dict(row)
            family = norm_family(d.get("instrument_type"))
            symbol = str(d.get("ticker") or "").strip().upper()
            if family not in SUPPORTED or family not in selected_families or not symbol:
                continue
            market = iol_market(family, d.get("market"))
            key = (market, family, symbol)
            if key in seen:
                continue
            seen.add(key)
            out.append({"market": market, "family": family, "symbol": symbol})
        return sorted(out, key=lambda x: (x["family"], x["market"], x["symbol"]))


def first(d: dict, *keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def normalize_bar(row: dict):
    if not isinstance(row, dict):
        return None
    date = str(first(row, "fecha", "date", "Fecha", "Date") or "")[:10]
    close = first(row, "ultimoPrecio", "cierre", "close", "ultimo", "último")
    if not date or close in (None, "", 0, 0.0):
        return None
    try:
        return (
            date,
            first(row, "apertura", "open"),
            first(row, "maximo", "máximo", "high"),
            first(row, "minimo", "mínimo", "low"),
            close,
            first(row, "montoOperado", "volumen", "volume") or 0,
        )
    except Exception:
        return None


def upsert_bars(conn, task, bars, adjusted: bool) -> int:
    now = datetime.now(timezone.utc).isoformat()
    rows = [
        (SOURCE, task["market"], task["family"], task["symbol"], b[0], b[1], b[2], b[3], b[4], b[5], int(adjusted), now)
        for b in bars
    ]
    if not rows:
        return 0
    conn.executemany("""
      INSERT INTO market_historical_ohlcv_v2
       (source,market,family,symbol,date,open,high,low,close,volume,adjusted,ingested_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(source,market,family,symbol,date) DO UPDATE SET
       open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
       volume=excluded.volume,adjusted=excluded.adjusted,ingested_at=excluded.ingested_at
    """, rows)
    conn.commit()
    return len(rows)


def progress(conn, task, days, status, written, error=None, first_date=None, last_date=None):
    conn.execute("""
      INSERT INTO historical_ingest_progress_v2
       (source,market,family,symbol,requested_days,status,rows_written,attempts,last_error,first_date,last_date,updated_at)
      VALUES(?,?,?,?,?,?,?,1,?,?,?,?)
      ON CONFLICT(source,market,family,symbol) DO UPDATE SET
       requested_days=excluded.requested_days,status=excluded.status,
       rows_written=excluded.rows_written,
       attempts=historical_ingest_progress_v2.attempts+1,
       last_error=excluded.last_error,first_date=excluded.first_date,
       last_date=excluded.last_date,updated_at=excluded.updated_at
    """, (
        SOURCE, task["market"], task["family"], task["symbol"], days, status,
        written, str(error or "")[:300] or None, first_date, last_date,
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--observer-db", required=True)
    ap.add_argument("--history-db", required=True)
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--families", default=",".join(sorted(SUPPORTED)))
    ap.add_argument("--max-symbols", type=int, default=0)
    args = ap.parse_args()
    if args.days < 30 or args.days > 730:
        raise SystemExit("DAYS_OUT_OF_POLICY_RANGE")
    selected = {norm_family(x) for x in args.families.split(",") if x.strip()}
    selected &= SUPPORTED
    client = IOLClient()
    if not client.enabled:
        raise SystemExit("IOL_CLIENT_DISABLED_OR_CREDENTIALS_MISSING")
    tasks = load_candidates(args.observer_db, selected)
    if args.max_symbols > 0:
        tasks = tasks[: args.max_symbols]
    hpath = Path(args.history_db)
    hpath.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(hpath, timeout=60)
    init_history(conn)
    started = datetime.now(timezone.utc).isoformat()
    run_id = conn.execute(
        "INSERT INTO historical_ingest_runs_v2(started_at,requested_days,candidates) VALUES(?,?,?)",
        (started, args.days, len(tasks)),
    ).lastrowid
    conn.commit()
    ok = empty = failed = total_written = 0
    by_family = {}
    for index, task in enumerate(tasks, 1):
        adjusted = task["family"] in ADJUSTED_FAMILIES
        try:
            raw = client.get_serie_historica(task["symbol"], mercado=task["market"], dias=args.days, ajustada=adjusted)
            bars = [b for b in (normalize_bar(x) for x in (raw or [])) if b]
            bars.sort(key=lambda x: x[0])
            written = upsert_bars(conn, task, bars, adjusted)
            total_written += written
            if bars:
                status = "OK"
                ok += 1
                progress(conn, task, args.days, status, written, first_date=bars[0][0], last_date=bars[-1][0])
            else:
                status = "EMPTY"
                empty += 1
                progress(conn, task, args.days, status, 0, error="NO_SERIES_FOR_EXACT_IDENTITY")
            fam = by_family.setdefault(task["family"], {"ok": 0, "empty": 0, "failed": 0, "rows": 0})
            fam["ok" if bars else "empty"] += 1
            fam["rows"] += written
        except Exception as exc:
            failed += 1
            progress(conn, task, args.days, "FAILED", 0, error=type(exc).__name__ + ":" + str(exc)[:220])
            fam = by_family.setdefault(task["family"], {"ok": 0, "empty": 0, "failed": 0, "rows": 0})
            fam["failed"] += 1
        if index % 25 == 0:
            print(json.dumps({"progress": index, "total": len(tasks), "ok": ok, "empty": empty, "failed": failed, "rows": total_written}, sort_keys=True), flush=True)
    quick = conn.execute("PRAGMA quick_check").fetchone()[0]
    conn.execute("""
      UPDATE historical_ingest_runs_v2
      SET finished_at=?,ok=?,empty=?,failed=?,rows_written=?,quick_check=?,notes=?
      WHERE run_id=?
    """, (
        datetime.now(timezone.utc).isoformat(), ok, empty, failed, total_written, quick,
        json.dumps({"families": by_family, "identity_policy": "EXACT_ONLY_NO_FUZZY_INFERENCE"}, sort_keys=True), run_id,
    ))
    conn.commit()
    conn.close()
    result = {
        "run_id": run_id, "days": args.days, "candidates": len(tasks), "ok": ok,
        "empty": empty, "failed": failed, "rows_written": total_written,
        "quick_check": quick, "families": by_family,
        "identity_policy": "EXACT_ONLY_NO_FUZZY_INFERENCE",
    }
    print("IOL_BACKFILL_V2=" + json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    if quick != "ok" or not tasks:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())