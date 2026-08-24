"""Estado operativo persistido compartido por el bot y el dashboard.

No realiza llamadas de red.  Esa regla es deliberada: abrir una pantalla de
salud nunca debe consumir cuota del broker ni provocar una autenticación.
"""

import json
import os
import sqlite3
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import ac_db


SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires")


def configure_process_timezone() -> None:
    """Hace que logging y datetime.now() legado usen la zona del mercado."""
    os.environ["TZ"] = SERVER_TIMEZONE
    if hasattr(time, "tzset"):
        time.tzset()


def now_local() -> datetime:
    return datetime.now(ZoneInfo(SERVER_TIMEZONE))


def now_iso() -> str:
    return now_local().isoformat(timespec="seconds")


def epoch_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, ZoneInfo(SERVER_TIMEZONE)).isoformat(timespec="seconds")


def age_seconds(value) -> float | None:
    """Antigüedad de un epoch o ISO persistido; ``None`` si no es legible."""
    try:
        if isinstance(value, (int, float)):
            epoch = float(value)
        else:
            text = str(value or "").strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo(SERVER_TIMEZONE))
            epoch = parsed.timestamp()
        return max(0.0, time.time() - epoch)
    except Exception:
        return None


def record_event(component: str, state: str, detail: str = "") -> None:
    """Guarda una transición pequeña. Nunca propaga una falla de monitoreo."""
    try:
        with ac_db.connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS system_events (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       timestamp TEXT,
                       component TEXT,
                       state TEXT,
                       detail TEXT
                   )"""
            )
            conn.execute(
                "INSERT INTO system_events(timestamp, component, state, detail) VALUES (?, ?, ?, ?)",
                (now_iso(), component, state, str(detail)[:1000]),
            )
    except Exception:
        pass


def latest_event(component: str) -> dict:
    try:
        conn = ac_db.connect_raw()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM system_events WHERE component=? ORDER BY id DESC LIMIT 1",
            (component,),
        ).fetchone()
        conn.close()
        return dict(row) if row else {}
    except Exception:
        return {}


def record_telegram(direction: str, event_type: str, status: str, summary: str = "") -> None:
    """Registra metadatos de Telegram; limita texto y ofusca secretos."""
    try:
        from c_ppi_client import obfuscate_secret
        safe = obfuscate_secret(str(summary)).replace("\n", " ")[:300]
    except Exception:
        safe = "[contenido no disponible]"
    try:
        with ac_db.connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS telegram_activity (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       timestamp TEXT,
                       direction TEXT,
                       event_type TEXT,
                       status TEXT,
                       summary TEXT
                   )"""
            )
            conn.execute(
                """INSERT INTO telegram_activity
                   (timestamp, direction, event_type, status, summary)
                   VALUES (?, ?, ?, ?, ?)""",
                (now_iso(), direction, event_type, status, safe),
            )
    except Exception:
        pass


def telegram_activity(limit: int = 100) -> list:
    try:
        conn = ac_db.connect_raw()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM telegram_activity ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 500)),),
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]
    except Exception:
        return []


def read_verifier_report(family: str) -> dict:
    """Lee el último informe del verificador sin ejecutar una prueba nueva."""
    candidates = [
        f"data/informe_api_{family}.json",
        f"data/informe_apis_{family}.json",
        "data/informe_apis.json",
    ]
    for path in candidates:
        try:
            if not os.path.exists(path):
                continue
            data = json.loads(open(path, encoding="utf-8").read())
            return {"path": path, "mtime": os.path.getmtime(path), "data": data}
        except Exception:
            continue
    return {}
