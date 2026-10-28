"""Estado operativo persistido compartido por el bot y el dashboard.

No realiza llamadas de red.  Esa regla es deliberada: abrir una pantalla de
salud nunca debe consumir cuota del broker ni provocar una autenticación.
"""

import json
import os
import sqlite3
import time
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import ac_db


SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires")
CONFIG_PRESENCE_PATH = os.getenv("CONFIG_PRESENCE_PATH", "data/config_presence.json")
BOT_RUNTIME_STATE_PATH = os.getenv("BOT_RUNTIME_STATE_PATH", "data/bot_runtime_state.json")
BOT_HEARTBEAT_SECONDS = int(os.getenv("BOT_HEARTBEAT_SECONDS", "30"))
BOT_HEARTBEAT_STALE_SECONDS = int(os.getenv("BOT_HEARTBEAT_STALE_SECONDS", "120"))


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



def read_bot_state() -> dict:
    """Estado local del motor; nunca consulta PPI ni otra API externa."""
    try:
        with open(BOT_RUNTIME_STATE_PATH, encoding="utf-8") as stream:
            state = json.load(stream)
        if not isinstance(state, dict):
            raise ValueError("estado invalido")
    except (OSError, ValueError, TypeError):
        return {"state": "STOPPED", "alive": False, "detail": "Sin heartbeat activo."}
    age = age_seconds(state.get("updated_at"))
    explicit_stopped = state.get("state") in ("STOPPED", "STOPPING", "ERROR")
    state["age_seconds"] = age
    state["alive"] = bool(not explicit_stopped and age is not None
                          and age <= BOT_HEARTBEAT_STALE_SECONDS)
    if not state["alive"] and not explicit_stopped:
        state["state"] = "STALE"
        state["detail"] = "El proceso no renueva su heartbeat."
    return state


class BotHeartbeat:
    """Un solo archivo atomico, actualizado por un hilo liviano."""

    def __init__(self):
        self._state = "STOPPED"
        self._mode = ""
        self._detail = ""
        self._started_at = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _write(self) -> None:
        with self._lock:
            payload = {
                "state": self._state,
                "mode": self._mode,
                "detail": self._detail,
                "started_at": self._started_at,
                "updated_at": now_iso(),
                "pid": os.getpid(),
            }
        directory = os.path.dirname(BOT_RUNTIME_STATE_PATH)
        if directory:
            os.makedirs(directory, exist_ok=True)
        temporary = f"{BOT_RUNTIME_STATE_PATH}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, BOT_RUNTIME_STATE_PATH)

    def start(self, state: str = "STARTING", mode: str = "", detail: str = "") -> None:
        with self._lock:
            self._state = state
            self._mode = mode
            self._detail = detail
            self._started_at = now_iso()
        self._stop.clear()
        self._write()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="bot_heartbeat", daemon=True)
            self._thread.start()

    def set_state(self, state: str, mode: str = "", detail: str = "") -> None:
        with self._lock:
            self._state = state
            if mode:
                self._mode = mode
            self._detail = str(detail)[:500]
        self._write()

    def _run(self) -> None:
        while not self._stop.wait(BOT_HEARTBEAT_SECONDS):
            try:
                self._write()
            except Exception:
                pass

    def stop(self, detail: str = "Detenido ordenadamente.") -> None:
        self.set_state("STOPPED", detail=detail)
        self._stop.set()



def write_config_presence() -> None:
    """Persist only booleans; never values, lengths, hashes or credentials."""
    from v_config_metadata import CONFIG_METADATA

    configured = {
        var: bool(str(os.getenv(var, "")).strip())
        for var, _section, _description, _default, _sensitive in CONFIG_METADATA
    }
    payload = {
        "schema": 1,
        "generated_at": now_iso(),
        "source": "trading-engine",
        "configured": configured,
    }
    directory = os.path.dirname(CONFIG_PRESENCE_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temporary = f"{CONFIG_PRESENCE_PATH}.{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, CONFIG_PRESENCE_PATH)
    finally:
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass


def read_config_presence() -> dict:
    """Read the safe manifest. Missing/corrupt means unknown, never absent."""
    try:
        with open(CONFIG_PRESENCE_PATH, encoding="utf-8") as stream:
            payload = json.load(stream)
        configured = payload.get("configured")
        if payload.get("schema") != 1 or not isinstance(configured, dict):
            return {}
        if any(not isinstance(value, bool) for value in configured.values()):
            return {}
        return payload
    except (OSError, ValueError, TypeError):
        return {}

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
