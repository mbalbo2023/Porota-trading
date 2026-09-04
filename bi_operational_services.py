"""Servicios 24x7 del observador: SRE, backups, macro, noticias y reportes.

No importa el cliente PPI ni contiene capacidad de órdenes. Todas las lecturas
de red son GET públicas; los informes se construyen únicamente con la base
paper y quedan disponibles para el dashboard independiente.
"""

from __future__ import annotations

import calendar
import gzip
import hashlib
import html
import json
import os
import re
import resource
import shutil
import sqlite3
import statistics
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import cd_spot_ledger as spot_ledger
from bs_instrument_contracts import aware_datetime
from bt_caucion_paper import CaucionBook
from cg_paper_workspace import artifact_root


TZ = ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
ROOT_DATA = artifact_root()
REPORT_DIR = ROOT_DATA / "reports"
BACKUP_DIR = ROOT_DATA / "backups" / "paper"
OFFICIAL_BCRA = "https://api.bcra.gob.ar"
OFFICIAL_SERIES = "https://apis.datos.gob.ar/series/api/series"
NEWS_FEEDS = (
    ("Ámbito", "https://www.ambito.com/rss/home.xml"),
    ("El Cronista", "https://www.cronista.com/files/rss/news.xml"),
    ("WSJ Business", "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml"),
    ("Bloomberg Markets", "https://feeds.bloomberg.com/markets/news.rss"),
)
DATOS_AR_SERIES = {
    "IPC mensual INDEC": "145.3_INGNACUAL_DICI_M_38",
    "IPC nivel general": "101.1_I2NG_2016_M_22",
    "EMAE actividad": "143.3_NO_PR_2004_A_21",
    "Tipo de cambio mayorista": "168.1_T_CAMBIOR_D_0_0_26",
}
BCRA_VARIABLES = {
    "Reservas internacionales": 1,
    "Tasa TAMAR privados TNA": 44,
    "Base monetaria": 15,
}


def now_iso():
    return datetime.now(TZ).isoformat(timespec="seconds")


def _connect(path):
    c = sqlite3.connect(str(path), timeout=20)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _remove_sqlite_bundle(path):
    """Elimina el archivo temporal SQLite y sus auxiliares WAL/SHM."""
    path = Path(path)
    for item in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        item.unlink(missing_ok=True)


def init_schema(store):
    with store.connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS sre_snapshots(
          id INTEGER PRIMARY KEY AUTOINCREMENT, measured_at TEXT NOT NULL,
          state TEXT NOT NULL, db_bytes INTEGER NOT NULL, wal_bytes INTEGER NOT NULL,
          disk_total_bytes INTEGER NOT NULL, disk_free_bytes INTEGER NOT NULL,
          memory_rss_bytes INTEGER NOT NULL, db_query_ms REAL NOT NULL,
          db_integrity TEXT NOT NULL, payload_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS backup_runs(
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
          kind TEXT NOT NULL, path TEXT NOT NULL UNIQUE, size_bytes INTEGER NOT NULL,
          sha256 TEXT NOT NULL, restore_test TEXT NOT NULL, state TEXT NOT NULL,
          detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS financial_series(
          source TEXT NOT NULL, indicator TEXT NOT NULL, observed_date TEXT NOT NULL,
          value REAL, unit TEXT NOT NULL, fetched_at TEXT NOT NULL,
          PRIMARY KEY(source,indicator,observed_date));
        CREATE TABLE IF NOT EXISTS financial_news(
          id INTEGER PRIMARY KEY AUTOINCREMENT, fetched_at TEXT NOT NULL,
          published_at TEXT, source TEXT NOT NULL, category TEXT NOT NULL,
          title TEXT NOT NULL, url TEXT NOT NULL, UNIQUE(source,title));
        CREATE TABLE IF NOT EXISTS report_registry(
          id INTEGER PRIMARY KEY AUTOINCREMENT, period_type TEXT NOT NULL,
          period_key TEXT NOT NULL, created_at TEXT NOT NULL, pdf_path TEXT,
          ai_path TEXT, state TEXT NOT NULL, detail TEXT NOT NULL,
          UNIQUE(period_type,period_key));
        CREATE TABLE IF NOT EXISTS operational_jobs(
          job_key TEXT PRIMARY KEY, last_run_at TEXT, last_success_at TEXT,
          state TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS universe_cycle_metrics(
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL, eligible_total INTEGER NOT NULL,
          selected_count INTEGER NOT NULL, successful_count INTEGER NOT NULL,
          failed_count INTEGER NOT NULL, duration_seconds REAL NOT NULL,
          cursor_before INTEGER NOT NULL, cursor_after INTEGER NOT NULL,
          recommended_limit INTEGER NOT NULL, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS symbol_cycle_metrics(
          id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_started_at TEXT NOT NULL,
          symbol TEXT NOT NULL, latency_ms REAL NOT NULL, state TEXT NOT NULL,
          detail TEXT NOT NULL);
        """)


def _job_due(store, key, seconds):
    with store.connect() as c:
        row = c.execute("SELECT last_run_at FROM operational_jobs WHERE job_key=?", (key,)).fetchone()
    if not row or not row[0]:
        return True
    try:
        return (datetime.now(TZ) - datetime.fromisoformat(row[0])).total_seconds() >= seconds
    except Exception:
        return True


def _job(store, key, state, detail, success=False):
    stamp = now_iso()
    with store.connect() as c:
        previous = c.execute("SELECT last_success_at FROM operational_jobs WHERE job_key=?", (key,)).fetchone()
        last_success = stamp if success else (previous[0] if previous else None)
        c.execute("""INSERT INTO operational_jobs VALUES(?,?,?,?,?)
          ON CONFLICT(job_key) DO UPDATE SET last_run_at=excluded.last_run_at,
          last_success_at=excluded.last_success_at,state=excluded.state,detail=excluded.detail""",
          (key, stamp, last_success, state, str(detail)[:1000]))


def collect_sre(store):
    """Mide la persistencia y el contenedor; no necesita Docker socket."""
    init_schema(store)
    started = time.perf_counter()
    db_path = Path(store.path)
    try:
        with store.connect() as c:
            integrity = str(c.execute("PRAGMA quick_check").fetchone()[0])
            counts = {}
            for table in ("market_snapshots", "paper_decisions", "paper_positions",
                          "paper_learning_samples", "financial_news"):
                try:
                    counts[table] = int(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except sqlite3.Error:
                    counts[table] = 0
        query_ms = round((time.perf_counter() - started) * 1000, 3)
        usage = shutil.disk_usage(db_path.parent)
        db_bytes = db_path.stat().st_size if db_path.exists() else 0
        wal_path = Path(str(db_path) + "-wal")
        wal_bytes = wal_path.stat().st_size if wal_path.exists() else 0
        rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)
        free_pct = usage.free / usage.total * 100 if usage.total else 0
        state = "VERDE" if integrity == "ok" and free_pct >= 20 and query_ms < 250 else "AMARILLO"
        payload = {"tables": counts, "disk_free_pct": round(free_pct, 2),
                   "python": os.sys.version.split()[0], "pid": os.getpid()}
        with store.connect() as c:
            c.execute("""INSERT INTO sre_snapshots VALUES(NULL,?,?,?,?,?,?,?,?,?,?)""",
                      (now_iso(), state, db_bytes, wal_bytes, usage.total, usage.free,
                       rss, query_ms, integrity, json.dumps(payload, ensure_ascii=False)))
            c.execute("DELETE FROM sre_snapshots WHERE id NOT IN (SELECT id FROM sre_snapshots ORDER BY id DESC LIMIT 2016)")
        _job(store, "SRE_SNAPSHOT", state, f"quick_check={integrity}; libre={free_pct:.1f}%", success=state == "VERDE")
        return payload | {"state": state, "db_bytes": db_bytes, "wal_bytes": wal_bytes,
                          "query_ms": query_ms, "integrity": integrity}
    except Exception as exc:
        _job(store, "SRE_SNAPSHOT", "ROJO", f"{type(exc).__name__}: {exc}")
        return {"state": "ROJO", "detail": str(exc)}


def create_backup(store, force=False):
    """Backup consistente, comprimido y con restore test real."""
    init_schema(store)
    key = datetime.now(TZ).strftime("%Y-%m-%d")
    if not force and not _job_due(store, "DAILY_BACKUP", 20 * 3600):
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    raw = BACKUP_DIR / f"observer_{key}.db"
    final = Path(str(raw) + ".gz")
    test = BACKUP_DIR / f".restore_test_{os.getpid()}.db"
    try:
        source = _connect(store.path)
        target = sqlite3.connect(str(raw))
        source.backup(target)
        target.close(); source.close()
        with open(raw, "rb") as src, gzip.open(final, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        _remove_sqlite_bundle(raw)
        digest = hashlib.sha256(final.read_bytes()).hexdigest()
        with gzip.open(final, "rb") as src, open(test, "wb") as dst:
            shutil.copyfileobj(src, dst)
        with sqlite3.connect(str(test)) as c:
            restore = str(c.execute("PRAGMA quick_check").fetchone()[0])
        _remove_sqlite_bundle(test)
        if restore != "ok":
            raise RuntimeError("restore quick_check=" + restore)
        size = final.stat().st_size
        with store.connect() as c:
            c.execute("""INSERT OR REPLACE INTO backup_runs
              (created_at,kind,path,size_bytes,sha256,restore_test,state,detail)
              VALUES(?,?,?,?,?,?,?,?)""",
              (now_iso(), "DAILY", str(final), size, digest, "OK", "VERDE",
               "SQLite online backup + descompresión + PRAGMA quick_check"))
        cutoff = datetime.now(TZ).date() - timedelta(days=30)
        for old in BACKUP_DIR.glob("observer_????-??-??.db.gz"):
            try:
                if date.fromisoformat(old.name[9:19]) < cutoff:
                    old.unlink()
            except Exception:
                pass
        _job(store, "DAILY_BACKUP", "VERDE", f"{final.name}; {size} bytes; restore OK", success=True)
        return str(final)
    except Exception as exc:
        _remove_sqlite_bundle(raw); _remove_sqlite_bundle(test)
        _job(store, "DAILY_BACKUP", "ROJO", f"{type(exc).__name__}: {exc}")
        return None


def create_history_backup(store, force=False):
    """Backup online SQLite del History Store v2 con restore test real."""
    init_schema(store)
    if not force and not _job_due(store, "HISTORY_BACKUP", 20 * 3600):
        return None
    source_path = Path(os.getenv("HIST_DB_PATH", "data/market_history.db")).resolve()
    if not source_path.exists():
        _job(store, "HISTORY_BACKUP", "NO_APLICA", "market_history.db todavía no existe")
        return None
    key = datetime.now(TZ).strftime("%Y-%m-%d")
    target_dir = ROOT_DATA / "backups" / "history"
    target_dir.mkdir(parents=True, exist_ok=True)
    raw = target_dir / f"market_history_{key}.db"
    final = Path(str(raw) + ".gz")
    test = target_dir / f".restore_test_{os.getpid()}.db"
    try:
        source = _connect(source_path)
        target = sqlite3.connect(str(raw))
        source.backup(target)
        target.close(); source.close()
        with open(raw, "rb") as src, gzip.open(final, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        _remove_sqlite_bundle(raw)
        digest = hashlib.sha256(final.read_bytes()).hexdigest()
        with gzip.open(final, "rb") as src, open(test, "wb") as dst:
            shutil.copyfileobj(src, dst)
        with sqlite3.connect(str(test)) as c:
            restore = str(c.execute("PRAGMA quick_check").fetchone()[0])
        _remove_sqlite_bundle(test)
        if restore != "ok":
            raise RuntimeError("restore quick_check=" + restore)
        size = final.stat().st_size
        with store.connect() as c:
            c.execute("""INSERT OR REPLACE INTO backup_runs
              (created_at,kind,path,size_bytes,sha256,restore_test,state,detail)
              VALUES(?,?,?,?,?,?,?,?)""",
              (now_iso(), "HISTORY_DAILY", str(final), size, digest, "OK", "VERDE",
               "History Store SQLite online backup + descompresión + PRAGMA quick_check"))
        cutoff = datetime.now(TZ).date() - timedelta(days=30)
        for old in target_dir.glob("market_history_????-??-??.db.gz"):
            try:
                if date.fromisoformat(old.name[15:25]) < cutoff:
                    old.unlink()
            except Exception:
                pass
        _job(store, "HISTORY_BACKUP", "VERDE", f"{final.name}; {size} bytes; restore OK", success=True)
        return str(final)
    except Exception as exc:
        _remove_sqlite_bundle(raw); _remove_sqlite_bundle(test)
        _job(store, "HISTORY_BACKUP", "ROJO", f"{type(exc).__name__}: {exc}")
        return None


def _save_financial(store, source, indicator, points, unit):
    with store.connect() as c:
        c.executemany("INSERT OR REPLACE INTO financial_series VALUES(?,?,?,?,?,?)",
                      [(source, indicator, str(day)[:10], float(value), unit, now_iso())
                       for day, value in points if day and value is not None])
    return len(points)


def refresh_financial(store, timeout=10):
    """BCRA v4 + series oficiales INDEC/Economía; una vez cada 12 horas."""
    init_schema(store)
    if not _job_due(store, "FINANCIAL_REFRESH", 12 * 3600):
        return {"state": "CACHED"}
    successes, failures, rows = 0, [], 0
    since = (datetime.now(TZ).date() - timedelta(days=730)).isoformat()
    for name, variable_id in BCRA_VARIABLES.items():
        try:
            url = (f"{OFFICIAL_BCRA}/estadisticas/v4.0/monetarias/{variable_id}?" +
                   urlencode({"desde": since, "hasta": date.today().isoformat(), "limit": 3000}))
            req = Request(url, headers={"Accept": "application/json", "Accept-Language": "es-AR",
                                        "User-Agent": "PorotaObserver/16.3.5"})
            with urlopen(req, timeout=timeout) as response:
                payload = json.load(response)
            details = []
            for block in payload.get("results", []):
                details.extend(block.get("detalle", []) if isinstance(block, dict) else [])
            points = [(row.get("fecha"), row.get("valor")) for row in details if isinstance(row, dict)]
            rows += _save_financial(store, "BCRA", name, points, "según BCRA")
            successes += 1
        except Exception as exc:
            failures.append(f"BCRA {name}: {type(exc).__name__}")
    for name, series_id in DATOS_AR_SERIES.items():
        try:
            url = OFFICIAL_SERIES + "?" + urlencode({"ids": series_id, "start_date": since,
                                                       "format": "json", "limit": 1000})
            req = Request(url, headers={"Accept": "application/json", "User-Agent": "PorotaObserver/16.3.5"})
            with urlopen(req, timeout=timeout) as response:
                payload = json.load(response)
            points = [(row[0], row[1]) for row in payload.get("data", []) if len(row) >= 2]
            unit = "% mensual" if "mensual" in name.lower() else "índice"
            rows += _save_financial(store, "INDEC / datos.gob.ar", name, points, unit)
            successes += 1
        except Exception as exc:
            failures.append(f"INDEC {name}: {type(exc).__name__}")
    state = "VERDE" if successes and not failures else "AMARILLO" if successes else "ROJO"
    detail = f"{successes} series correctas; {rows} puntos; " + (", ".join(failures) if failures else "sin fallas")
    _job(store, "FINANCIAL_REFRESH", state, detail, success=bool(successes))
    return {"state": state, "rows": rows, "failures": failures}


def _clean(text):
    value = re.sub(r"<[^>]+>", "", str(text or ""))
    return " ".join(value.split())[:500]


def _news_category(title):
    text = title.lower()
    if any(word in text for word in ("guerra", "conflicto", "otan", "china", "eeuu", "elección", "sanción")):
        return "Geopolítica"
    if any(word in text for word in ("inflación", "tasa", "banco central", "fed", "dólar", "empleo", "pib")):
        return "Economía"
    return "Mercados"


def refresh_news(store, timeout=8):
    init_schema(store)
    if not _job_due(store, "NEWS_REFRESH", 45 * 60):
        return {"state": "CACHED"}
    try:
        import feedparser
    except Exception as exc:
        _job(store, "NEWS_REFRESH", "ROJO", f"feedparser: {exc}")
        return {"state": "ROJO", "rows": 0}
    good, failures, inserted = 0, [], 0
    for source, url in NEWS_FEEDS:
        try:
            req = Request(url, headers={"User-Agent": "PorotaObserver/16.3.5"})
            with urlopen(req, timeout=timeout) as response:
                parsed = feedparser.parse(response.read(2_000_000))
            if getattr(parsed, "bozo", False) and not parsed.entries:
                raise ValueError("RSS no parseable")
            good += 1
            with store.connect() as c:
                for entry in parsed.entries[:15]:
                    title = _clean(getattr(entry, "title", ""))
                    if not title:
                        continue
                    published = getattr(entry, "published_parsed", None)
                    published_at = datetime.fromtimestamp(calendar.timegm(published), TZ).isoformat() if published else None
                    before = c.total_changes
                    c.execute("""INSERT OR IGNORE INTO financial_news
                      (fetched_at,published_at,source,category,title,url) VALUES(?,?,?,?,?,?)""",
                      (now_iso(), published_at, source, _news_category(title), title,
                       str(getattr(entry, "link", ""))[:1000]))
                    inserted += c.total_changes - before
        except Exception as exc:
            failures.append(f"{source}: {type(exc).__name__}")
    with store.connect() as c:
        cutoff = (datetime.now(TZ) - timedelta(days=90)).isoformat()
        c.execute("DELETE FROM financial_news WHERE fetched_at < ?", (cutoff,))
    state = "VERDE" if good == len(NEWS_FEEDS) else "AMARILLO" if good else "ROJO"
    _job(store, "NEWS_REFRESH", state,
         f"{good}/{len(NEWS_FEEDS)} feeds; {inserted} titulares nuevos; " + (", ".join(failures) or "sin fallas"),
         success=bool(good))
    return {"state": state, "rows": inserted, "failures": failures}


def _period_data(store, start, end):
    with store.connect() as c:
        c.execute("BEGIN")
        positions = [dict(r) for r in c.execute("""SELECT * FROM paper_positions
          WHERE (julianday(opened_at)>=julianday(?) AND julianday(opened_at)<julianday(?))
          OR (status='CLOSED' AND julianday(closed_at)>=julianday(?) AND julianday(closed_at)<julianday(?))
          ORDER BY opened_at""", (start, end, start, end))]
        closed = [dict(r) for r in c.execute("""SELECT * FROM paper_positions WHERE status='CLOSED'
          AND julianday(closed_at)>=julianday(?) AND julianday(closed_at)<julianday(?)""", (start, end))]
        cutoff = aware_datetime(end)-timedelta(microseconds=1)
        remaining, all_realized = spot_ledger.positions_at(c,cutoff)
        realizations = [r for r in all_realized if aware_datetime(start)<=aware_datetime(r['closed_at'])<aware_datetime(end)]
        selected_ids = {p['paper_id'] for p in positions} | {r['paper_id'] for r in realizations}
        by_id = {p['paper_id']:p for p in remaining}
        for p in c.execute("SELECT * FROM paper_positions WHERE status='CLOSED' AND julianday(closed_at)<julianday(?)",(end,)):
            by_id[p['paper_id']] = dict(p)
        positions = [by_id[k] for k in selected_ids if k in by_id]
        positions.sort(key=lambda p:aware_datetime(p['opened_at']))
        cauciones = []
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_cauciones'").fetchone():
            cauciones = [p for p in CaucionBook(store).positions(status='MATURED',connection=c)
                         if aware_datetime(start)<=aware_datetime(p['settled_at'])<aware_datetime(end)]
        decisions = [dict(r) for r in c.execute("""SELECT * FROM paper_decisions
          WHERE julianday(decided_at)>=julianday(?) AND julianday(decided_at)<julianday(?) ORDER BY decided_at""", (start, end))]
        news = [dict(r) for r in c.execute("""SELECT * FROM financial_news
          WHERE julianday(fetched_at)>=julianday(?) AND julianday(fetched_at)<julianday(?) ORDER BY COALESCE(published_at,fetched_at) DESC LIMIT 25""",
          (start, end))]
        try:
            gates = [dict(r) for r in c.execute("""SELECT * FROM trade_gate_evaluations
              WHERE julianday(evaluated_at)>=julianday(?) AND julianday(evaluated_at)<julianday(?) ORDER BY evaluated_at""", (start, end))]
        except sqlite3.Error:
            gates = []
    pnl_by_currency = {}
    for p in realizations:
        currency = p.get("currency", "ARS")
        pnl_by_currency[currency] = pnl_by_currency.get(currency, Decimal(0)) + Decimal(p.get("net_pnl") or "0")
    for p in cauciones:
        currency = p["currency"]
        pnl_by_currency[currency] = pnl_by_currency.get(currency, Decimal(0)) + Decimal(p["gross_interest"]) - Decimal(p["total_fees"])
    # Un informe histórico no convierte en resultado del día un cierre futuro.
    for p in positions:
        if p.get("closed_at") and datetime.fromisoformat(p["closed_at"].replace("Z", "+00:00")) >= datetime.fromisoformat(end.replace("Z", "+00:00")):
            p["status"] = "OPEN"
            for key in ("closed_at", "exit_price", "exit_cost", "gross_pnl", "net_pnl", "close_reason"):
                p[key] = None
    pnl = float(pnl_by_currency.get("ARS", 0))
    wins = sum(1 for p in closed if float(p.get("net_pnl") or 0) > 0)
    return {"positions": positions, "closed": closed, "realizations": realizations, "decisions": decisions,
            "news": news, "gates": gates, "pnl": pnl, "wins": wins,
            "pnl_by_currency": {key: str(value) for key, value in sorted(pnl_by_currency.items())},
            "cauciones_matured": cauciones,
            "win_rate": wins / len(closed) * 100 if closed else None}


def _lesson(position):
    pnl = float(position.get("net_pnl") or 0)
    reason = str(position.get("close_reason") or "posición abierta")
    if pnl > 0:
        return "Ganó porque el movimiento favorable superó costos y slippage; conservar las variables que justificaron la entrada."
    if pnl < 0:
        return (f"Perdió y cerró por {reason}; revisar spread, momentum, profundidad, duración y contexto IA antes de ampliar exposición.")
    return "Sin resultado neto concluyente; no usar esta muestra para relajar umbrales."


def _report_story(title, period, data):
    operations = []
    for p in data["positions"]:
        operations.append({
            "instrumento": p.get("symbol"), "estado": p.get("status"),
            "moneda_plaza": p.get("currency", "ARS"), "mercado": p.get("market", "BYMA"),
            "apertura": p.get("opened_at"), "cierre": p.get("closed_at"),
            "entrada": p.get("entry_price"), "salida": p.get("exit_price"),
            "pnl_neto": p.get("net_pnl"), "motivo_cierre": p.get("close_reason"),
            "cantidad_remanente": p.get("quantity") if p.get("status")=="OPEN" else "0",
            "pnl_parcial_realizado": p.get("realized_net_pnl"),
            "leccion": _lesson(p), "variables": json.loads(p.get("features_json") or "{}"),
        })
    return {
        "titulo": title, "periodo": period, "generado_en": now_iso(),
        "modo": "SIMULACIÓN PRODUCTIVA", "ordenes_reales": 0,
        "resumen": {"operaciones": len(data["positions"]), "cerradas": len(data["closed"]),
                    "ganadoras": data["wins"], "win_rate_pct": data["win_rate"],
                    "pnl_neto_ars": round(data["pnl"], 2), "decisiones": len(data["decisions"]),
                    "pnl_por_moneda": data.get("pnl_by_currency", {}),
                    "cauciones_vencidas": len(data.get("cauciones_matured", [])),
                    "bloqueos_finales": sum(1 for g in data["gates"] if g.get("final_result") == "BLOCKED")},
        "operaciones": operations, "secuencia_de_portones": data["gates"],
        "realizaciones_spot_del_periodo": data.get("realizations", []),
        "cauciones_vencidas": [{"contrato": p["instrument_id"], "moneda_plaza": p["currency"],
                                "capital": p["principal"], "interes_bruto": p["gross_interest"],
                                "costos": p["total_fees"], "acreditacion": p["settled_at"],
                                "interes_neto": str(Decimal(p["gross_interest"]) - Decimal(p["total_fees"]))}
                               for p in data.get("cauciones_matured", [])],
        "decisiones": data["decisions"], "noticias_relevantes": data["news"],
        "nota": "Archivo preparado para discusión de lecciones aprendidas con una IA; no contiene secretos ni datos de cuenta.",
    }


def _pdf(story, path):
    import reportlab
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    styles = getSampleStyleSheet()
    # Fuentes incluidas con ReportLab: el PDF conserva su tipografía al exportar.
    font_dir = Path(reportlab.__file__).parent / "fonts"
    for name, filename in (("Porota", "Vera.ttf"), ("Porota-Bold", "VeraBd.ttf"),
                           ("Porota-Italic", "VeraIt.ttf"), ("Porota-BoldItalic", "VeraBI.ttf")):
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(font_dir / filename)))
    pdfmetrics.registerFontFamily("Porota", normal="Porota", bold="Porota-Bold",
                                 italic="Porota-Italic", boldItalic="Porota-BoldItalic")
    for style in styles.byName.values():
        style.fontName = "Porota-Bold" if style.name.startswith("Heading") else "Porota"
    doc = SimpleDocTemplate(str(path), pagesize=A4, rightMargin=14*mm, leftMargin=14*mm,
                            topMargin=14*mm, bottomMargin=14*mm,
                            title=story["titulo"], author="Porota Trading")
    blocks = [Paragraph(html.escape(story["titulo"]), styles["Title"]),
              Paragraph(f"{html.escape(story['periodo'])} · Modo SIMULACIÓN PRODUCTIVA · órdenes reales: 0", styles["Normal"]),
              Spacer(1, 8)]
    summary = story["resumen"]
    table = Table([["Operaciones", "Cerradas", "Ganadoras", "Win rate", "PnL neto ARS"],
                   [summary["operaciones"], summary["cerradas"], summary["ganadoras"],
                    "s/d" if summary["win_rate_pct"] is None else f"{summary['win_rate_pct']:.1f}%",
                    f"$ {summary['pnl_neto_ars']:,.2f}"]], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#14213d")),
                               ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                               ("GRID", (0,0), (-1,-1), .3, colors.grey),
                               ("VALIGN", (0,0), (-1,-1), "TOP"),
                               ("FONTNAME", (0,0), (-1,-1), "Porota"),
                               ("FONTSIZE", (0,0), (-1,-1), 8)]))
    blocks += [table, Spacer(1, 8), Paragraph("Resultados realizados por moneda/plaza (sin conversión ni suma entre monedas)", styles["Heading3"])]
    for currency, pnl in summary.get("pnl_por_moneda", {}).items():
        blocks.append(Paragraph(html.escape(f"{currency}: {Decimal(pnl):,.2f}"), styles["Normal"]))
    if story.get("cauciones_vencidas"):
        blocks.append(Paragraph("Cauciones colocadoras acreditadas", styles["Heading2"]))
        for item in story["cauciones_vencidas"]:
            blocks.append(Paragraph(html.escape(
                f"{item['contrato']} · {item['moneda_plaza']} · capital {item['capital']} · "
                f"interés bruto {item['interes_bruto']} · costos {item['costos']} · "
                f"interés neto {item['interes_neto']} · acreditación {item['acreditacion']}"), styles["BodyText"]))
    blocks += [Spacer(1, 12), Paragraph("Operaciones y lecciones", styles["Heading2"])]
    if not story["operaciones"]:
        blocks.append(Paragraph("No hubo operaciones de compraventa simuladas en este período.", styles["Normal"]))
    for op in story["operaciones"]:
        color = "#16833b" if float(op.get("pnl_neto") or 0) > 0 else "#c62828" if float(op.get("pnl_neto") or 0) < 0 else "#667085"
        blocks += [Paragraph(f"<font color='{color}'><b>{html.escape(str(op['instrumento']))} · {html.escape(str(op['estado']))} · PnL {html.escape(op.get('moneda_plaza', 'ARS'))} {float(op.get('pnl_neto') or 0):,.2f}</b></font>", styles["Heading3"]),
                   Paragraph(html.escape(op["leccion"]), styles["BodyText"]), Spacer(1, 5)]
    blocks += [PageBreak()]
    if story["noticias_relevantes"]:
        blocks.append(Paragraph("Noticias financieras, económicas y geopolíticas", styles["Heading2"]))
        for item in story["noticias_relevantes"][:20]:
            blocks.append(Paragraph(f"<b>{html.escape(item['category'])} · {html.escape(item['source'])}</b>: {html.escape(item['title'])}", styles["BodyText"]))
            blocks.append(Spacer(1, 3))
    else:
        blocks.append(Paragraph("Noticias excluidas del aprendizaje por configuración HF5.", styles["BodyText"]))
    blocks += [Spacer(1, 10), Paragraph("Trazabilidad de portones", styles["Heading2"])]
    for gate in story["secuencia_de_portones"][-30:]:
        blocks.append(Paragraph(html.escape(
            f"{gate.get('symbol')}: técnico={gate.get('technical_gate')}; "
            f"patrimonial={gate.get('patrimonial_gate')}; final={gate.get('final_result')}. {gate.get('reason')}"), styles["BodyText"]))
    doc.build(blocks)


def generate_report(store, period_type, period_key, start, end):
    init_schema(store)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    safe_key = re.sub(r"[^0-9A-Za-z_-]", "-", period_key)
    pdf_path = REPORT_DIR / f"informe_{period_type.lower()}_{safe_key}.pdf"
    ai_path = REPORT_DIR / f"lecciones_ia_{period_type.lower()}_{safe_key}.json"
    data = _period_data(store, start, end)
    if os.getenv("PAPER_NEWS_INGEST_ENABLED", "false").lower() not in {"1", "true", "yes"}:
        # Conservar evidencia ya persistida, pero no mezclarla con nuevas
        # muestras de trading ni con el paquete de aprendizaje.
        data["news"] = []
    story = _report_story(f"Informe {period_type.lower()} — Porota Trading", f"{start} a {end}", data)
    try:
        _pdf(story, pdf_path)
        ai_path.write_text(json.dumps(story, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.chmod(pdf_path, 0o640); os.chmod(ai_path, 0o640)
        with store.connect() as c:
            c.execute("""INSERT OR REPLACE INTO report_registry
              (period_type,period_key,created_at,pdf_path,ai_path,state,detail)
              VALUES(?,?,?,?,?,?,?)""",
              (period_type, period_key, now_iso(), str(pdf_path), str(ai_path), "VERDE",
               f"{len(data['positions'])} operaciones; {len(data['news'])} noticias"))
        return str(pdf_path), str(ai_path)
    except Exception as exc:
        with store.connect() as c:
            c.execute("""INSERT OR REPLACE INTO report_registry
              (period_type,period_key,created_at,pdf_path,ai_path,state,detail)
              VALUES(?,?,?,?,?,?,?)""",
              (period_type, period_key, now_iso(), None, None, "ROJO", f"{type(exc).__name__}: {exc}"))
        return None, None


def ensure_reports(store, include_today=False):
    """PDF diario y consolidaciones; un único paquete IA móvil de siete días."""
    init_schema(store)
    today = datetime.now(TZ).date()
    target = today if include_today else today - timedelta(days=1)
    start = datetime.combine(target, datetime.min.time(), TZ).isoformat()
    end = datetime.combine(target + timedelta(days=1), datetime.min.time(), TZ).isoformat()
    generate_report(store, "DIARIO", target.isoformat(), start, end)
    rolling_start = target - timedelta(days=6)
    generate_report(store, "IA_SEMANAL", f"{rolling_start}_{target}",
                    datetime.combine(rolling_start, datetime.min.time(), TZ).isoformat(), end)
    # Backfill de fechas observadas: deja descarga día por día aun si el
    # servicio fue instalado después de esas operaciones.
    with store.connect() as c:
        observed = [r[0] for r in c.execute("""SELECT DISTINCT day FROM (
          SELECT date(opened_at,'-3 hours') day FROM paper_positions
          UNION SELECT date(closed_at,'-3 hours') day FROM paper_positions WHERE status='CLOSED'
          UNION SELECT date(filled_at,'-3 hours') day FROM paper_fills WHERE side='SELL_SIMULATED'
          UNION SELECT date(settled_at,'-3 hours') day FROM paper_cauciones WHERE status='MATURED'
          UNION SELECT date(decided_at,'-3 hours') day FROM paper_decisions)
          WHERE day IS NOT NULL AND day<>'' ORDER BY day""").fetchall()]
        existing = {r[0] for r in c.execute(
            "SELECT period_key FROM report_registry WHERE period_type='DIARIO'").fetchall()}
    for day_text in observed:
        if day_text in existing:
            continue
        try:
            day = date.fromisoformat(day_text)
        except ValueError:
            continue
        day_start = datetime.combine(day, datetime.min.time(), TZ).isoformat()
        day_end = datetime.combine(day + timedelta(days=1), datetime.min.time(), TZ).isoformat()
        generate_report(store, "DIARIO", day_text, day_start, day_end)
    if target.weekday() == 6:
        week_start = target - timedelta(days=6)
        generate_report(store, "SEMANAL", f"{week_start}_{target}",
                        datetime.combine(week_start, datetime.min.time(), TZ).isoformat(), end)
    if today.day <= 3:
        last_day = today.replace(day=1) - timedelta(days=1)
        first_day = last_day.replace(day=1)
        month_key = first_day.strftime("%Y-%m")
        month_start = datetime.combine(first_day, datetime.min.time(), TZ).isoformat()
        month_end = datetime.combine(last_day + timedelta(days=1), datetime.min.time(), TZ).isoformat()
        pdf_path, _ = generate_report(store, "MENSUAL", month_key, month_start, month_end)
        if pdf_path:
            with store.connect() as c:
                weekly = c.execute("""SELECT pdf_path,ai_path FROM report_registry
                  WHERE period_type='SEMANAL' AND period_key LIKE ?""", (month_key + "%",)).fetchall()
                for row in weekly:
                    for value in row:
                        if value:
                            Path(value).unlink(missing_ok=True)
                c.execute("DELETE FROM report_registry WHERE period_type='SEMANAL' AND period_key LIKE ?",
                          (month_key + "%",))
    _job(store, "REPORTS", "VERDE", "Informes actualizados", success=True)


def send_closing_summary(store):
    """Un resumen por fecha en outbox; generar no equivale a entregar."""
    from bn_telegram_bus import enqueue
    init_schema(store)
    today = datetime.now(TZ).date()
    key = "TELEGRAM_CLOSE_" + today.isoformat()
    with store.connect() as c:
        existing = c.execute('SELECT state FROM paper_notification_outbox WHERE event_key=?',(key,)).fetchone()
        if existing:
            return 'ENTREGADO' if existing[0]=='SENT' else existing[0]
        legacy = c.execute('SELECT state,last_success_at FROM operational_jobs WHERE job_key=?',(key,)).fetchone()
        if legacy and legacy['state']=='VERDE' and legacy['last_success_at']:
            return 'YA_ENVIADO'
    start = datetime.combine(today, datetime.min.time(), TZ).isoformat()
    end = datetime.combine(today + timedelta(days=1), datetime.min.time(), TZ).isoformat()
    data = _period_data(store, start, end)
    win = "s/d" if data["win_rate"] is None else f"{data['win_rate']:.1f}%"
    text = ("📊 POROTA — CIERRE SIMULACIÓN PRODUCTIVA\n"
            f"Fecha: {today.isoformat()}\nOperaciones cerradas: {len(data['closed'])}\n"
            f"Win rate: {win}\nPnL paper neto ARS: $ {data['pnl']:,.2f}\n"
            f"Por moneda/plaza: {json.dumps(data.get('pnl_by_currency', {}), ensure_ascii=False)}\n"
            f"Cauciones vencidas: {len(data.get('cauciones_matured', []))}\n"
            f"Decisiones evaluadas: {len(data['decisions'])}\nÓrdenes reales: 0\n"
            "Dashboard: disponible 24x7. Informe diario: menú Reportes.")
    at = now_iso()
    with store.connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if enqueue(c,key,'CLOSING_SUMMARY',text,at):
            c.execute('''INSERT INTO operational_jobs VALUES(?,?,NULL,'EN_COLA',?)
              ON CONFLICT(job_key) DO UPDATE SET last_run_at=excluded.last_run_at,
                state=excluded.state,detail=excluded.detail''',
                (key,at,'Resumen persistido; pendiente de ACK de Telegram'))
    return 'EN_COLA'


def service_tick(store, phase, force=False):
    """Trabajos no críticos; cada familia conserva su propio TTL."""
    init_schema(store)
    if force or _job_due(store, "SRE_SNAPSHOT", 300):
        collect_sre(store)
    if force or _job_due(store, "DAILY_BACKUP", 20 * 3600):
        create_backup(store, force=force)
    if force or _job_due(store, "HISTORY_BACKUP", 20 * 3600):
        create_history_backup(store, force=force)
    if force or _job_due(store, "FINANCIAL_REFRESH", 12 * 3600):
        refresh_financial(store)
    news_enabled = os.getenv("PAPER_NEWS_INGEST_ENABLED", "false").lower() in {"1", "true", "yes"}
    if news_enabled and (force or _job_due(store, "NEWS_REFRESH", 45 * 60)):
        refresh_news(store)
    elif not news_enabled and (force or _job_due(store, "NEWS_REFRESH", 12 * 3600)):
        _job(store, "NEWS_REFRESH", "NO_APLICA",
             "Ingesta deshabilitada por HF5; evidencia histórica preservada", success=False)
    hour = datetime.now(TZ).hour
    if phase == "CLOSED" and hour >= int(os.getenv("MARKET_CLOSE_HOUR", "17")):
        if force or _job_due(store, "REPORTS", 6 * 3600):
            ensure_reports(store, include_today=True)
        send_closing_summary(store)
