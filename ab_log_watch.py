"""
ab_log_watch.py — NUEVO EN v14.0 (Instrucción 9 del pedido de v14).

EL PEDIDO
=========
"Sección de qué tengo que ver manualmente en los logs. Busca una forma
automática de mandarme la información vía Telegram ya que no puedo acceder
de forma manual."

Hasta v13.0, el Documento Maestro tenía una sección que explicaba qué mirar
en los logs y cada cuánto (sección M.3: "cada cuánto revisar los logs").
Esa sección describía trabajo humano: entrar por SSH o al dashboard,
descargar el archivo, buscar líneas con ERROR, cruzarlas contra el estado
del Circuit Breaker, mirar si hubo backups... Si no podés hacer eso, la
sección no servía de nada: era una lista de tareas para alguien que no
existe.

Este módulo la reemplaza entera. En vez de decirte qué buscar, lo busca
solo y te manda a Telegram únicamente lo que amerita atención humana. La
sección M.3 del Documento Maestro v14 pasa a ser "qué te llega solo y qué
significa cada aviso", no "qué tenés que ir a mirar".

DOS MECANISMOS, DISTINTOS A PROPÓSITO
=====================================
1) VIGILANCIA CONTINUA (scan_and_alert, cada LOG_WATCH_INTERVAL_MINUTES):
   lee SOLO lo nuevo del archivo de log desde la última vez (guarda el
   offset en disco), agrupa los errores por "firma" (tipo de error, no
   línea exacta) y manda UN mensaje con el resumen. Si no hay nada nuevo,
   no manda nada — el silencio es información: significa que todo va bien.

   Por qué agrupar por firma: un mismo problema (ej. PPI devolviendo 503)
   puede escribir 200 líneas en 10 minutos. Mandar 200 mensajes de Telegram
   sería peor que no mandar ninguno, porque garantiza que dejes de leerlos.
   Se manda "×47 veces" y una línea de ejemplo.

2) PARTE DIARIO DE SALUD (send_daily_health_digest, al cierre de rueda):
   el reemplazo directo de la revisión manual. Junta en un solo mensaje
   todo lo que un operador humano hubiera ido a buscar a mano: errores del
   día, aperturas del Circuit Breaker, anomalías de AIOps, kill switch,
   notificaciones de Telegram que fallaron, edad del último backup, uso de
   disco, ejecuciones parciales y estado del stream de tiempo real.

ANTI-SPAM (decisión de diseño deliberada)
=========================================
Una misma firma de error no se repite en Telegram antes de
LOG_WATCH_REPEAT_SUPPRESS_MINUTES (default 60). Si el problema sigue, se
vuelve a mencionar en el parte diario igual, así que no se pierde: se deja
de repetir, no de reportar. Un aviso que llega 40 veces por hora se vuelve
ruido, y el ruido se ignora — que es exactamente el modo de fallar que este
módulo tiene que evitar.
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from collections import OrderedDict
from typing import Optional
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

logger = logging.getLogger("log_watch")

LOG_DIR = os.getenv("LOG_DIR", "data/logs")
LOG_FILE_PATH = os.path.join(LOG_DIR, "trading_bot.log")
STATE_PATH = os.getenv("LOG_WATCH_STATE_PATH", "data/log_watch_state.json")
DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")

LOG_WATCH_ENABLED = os.getenv("LOG_WATCH_ENABLED", "true").lower() == "true"
LOG_WATCH_INTERVAL_MINUTES = int(os.getenv("LOG_WATCH_INTERVAL_MINUTES", "30"))
LOG_WATCH_REPEAT_SUPPRESS_MINUTES = int(os.getenv("LOG_WATCH_REPEAT_SUPPRESS_MINUTES", "60"))
LOG_WATCH_MAX_SIGNATURES_PER_MESSAGE = int(os.getenv("LOG_WATCH_MAX_SIGNATURES_PER_MESSAGE", "6"))

# Patrones que elevan una línea a "crítico" aunque su nivel de log sea WARNING:
# son los que históricamente precedieron a una caída real de este sistema.
_CRITICAL_PATTERNS = [
    (re.compile(r"Circuit Breaker.*OPEN", re.I), "Circuit Breaker abierto (PPI degradado o AIOps preventivo)"),
    (re.compile(r"kill switch|trigger_halt|HALT", re.I), "Kill switch / corte de riesgo"),
    (re.compile(r"login|autenticando|Unauthorized|401|403", re.I), "Problema de autenticación con PPI"),
    (re.compile(r"PartiallyFilled|ejecución parcial", re.I), "Ejecución parcial de una orden"),
    (re.compile(r"stale|dato viejo|desactualizad", re.I), "Datos de mercado desactualizados"),
]

# Ruido conocido que NO amerita despertar a nadie (se sigue guardando en el
# archivo de log, simplemente no se notifica).
_IGNORE_PATTERNS = [
    re.compile(r"formato inesperado para", re.I),      # ticker ilíquido en Sandbox: esperable
    re.compile(r"no cotiza|sin liquidez", re.I),
]


# ---------------------------------------------------------------------- #
# Estado persistente (offset del archivo + última vez que se avisó de cada firma)
# ---------------------------------------------------------------------- #
def _load_state() -> dict:
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"offset": 0, "inode": None, "last_sent": {}}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)  # escritura atómica: nunca deja un JSON a medias


def _read_new_lines(state: dict) -> list:
    """Lee lo escrito desde la última pasada. Maneja la rotación del archivo
    (RotatingFileHandler lo renombra y arranca uno nuevo): si el inode cambió
    o el archivo se achicó, se vuelve a leer desde el principio en vez de
    quedarse esperando en un offset que ya no existe — ese es el bug clásico
    de los "tail" caseros, y sería especialmente malo acá porque el archivo
    rota justo cuando más se está escribiendo, que es cuando algo va mal."""
    if not os.path.exists(LOG_FILE_PATH):
        return []
    st = os.stat(LOG_FILE_PATH)
    offset = state.get("offset", 0)
    if state.get("inode") != st.st_ino or st.st_size < offset:
        offset = 0
    with open(LOG_FILE_PATH, encoding="utf-8", errors="replace") as f:
        f.seek(offset)
        lines = f.readlines()
        state["offset"] = f.tell()
    state["inode"] = st.st_ino
    return lines


def _signature(line: str) -> str:
    """Firma estable de un error: se le sacan números, fechas, tickers entre
    comillas y IDs, para que 20 variantes del mismo problema colapsen en una
    sola entrada del resumen."""
    normalized = re.sub(r"\d+", "#", line)
    normalized = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27,}", "<id>", normalized)
    return hashlib.md5(normalized[:300].encode("utf-8")).hexdigest()[:10]


def _classify(line: str) -> Optional[str]:
    for pattern in _IGNORE_PATTERNS:
        if pattern.search(line):
            return None
    for pattern, etiqueta in _CRITICAL_PATTERNS:
        if pattern.search(line):
            return etiqueta
    if "[ERROR]" in line or "[CRITICAL]" in line:
        return "Error de aplicación"
    return None


# ---------------------------------------------------------------------- #
# 1) Vigilancia continua
# ---------------------------------------------------------------------- #
def scan_and_alert(notifier) -> dict:
    """Corre cada LOG_WATCH_INTERVAL_MINUTES desde el scheduler de j_main.
    Devuelve un dict con lo encontrado (útil para tests y para el dashboard);
    manda Telegram solo si hay algo que amerite."""
    if not LOG_WATCH_ENABLED:
        return {"enabled": False}

    state = _load_state()
    lines = _read_new_lines(state)
    hallazgos = OrderedDict()

    for line in lines:
        etiqueta = _classify(line)
        if not etiqueta:
            continue
        sig = _signature(line)
        if sig not in hallazgos:
            hallazgos[sig] = {"etiqueta": etiqueta, "count": 0, "ejemplo": line.strip()[:220]}
        hallazgos[sig]["count"] += 1

    ahora = time.time()
    last_sent = state.get("last_sent", {})
    a_notificar = []
    for sig, data in hallazgos.items():
        ultima = last_sent.get(sig, 0)
        if ahora - ultima >= LOG_WATCH_REPEAT_SUPPRESS_MINUTES * 60:
            a_notificar.append((sig, data))
            last_sent[sig] = ahora
    # Poda del diccionario de supresión para que no crezca sin límite con el
    # tiempo (firmas que no se ven hace más de un día ya no importan).
    state["last_sent"] = {k: v for k, v in last_sent.items() if ahora - v < 86400}
    _save_state(state)

    if not a_notificar:
        return {"enabled": True, "lineas_nuevas": len(lines), "hallazgos": len(hallazgos), "enviados": 0}

    a_notificar.sort(key=lambda x: x[1]["count"], reverse=True)
    partes = ["🔎 *VIGILANCIA DE LOGS* — novedades desde el último chequeo\n"]
    for _, data in a_notificar[:LOG_WATCH_MAX_SIGNATURES_PER_MESSAGE]:
        partes.append(f"• *{data['etiqueta']}* (×{data['count']})\n  `{data['ejemplo']}`")
    if len(a_notificar) > LOG_WATCH_MAX_SIGNATURES_PER_MESSAGE:
        partes.append(f"\n…y {len(a_notificar) - LOG_WATCH_MAX_SIGNATURES_PER_MESSAGE} tipo(s) más de aviso.")
    partes.append("\nEl detalle completo queda en el log descargable del panel (/logs).")
    notifier.send_telegram("\n".join(partes))

    return {"enabled": True, "lineas_nuevas": len(lines), "hallazgos": len(hallazgos),
            "enviados": len(a_notificar)}


# ---------------------------------------------------------------------- #
# 2) Parte diario de salud
# ---------------------------------------------------------------------- #
def _query(sql, params=()) -> list:
    try:
        conn = ac_db.connect_raw()
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except sqlite3.Error:
        return []


def build_daily_health_digest() -> str:
    """Arma el texto del parte diario. Separado del envío para poder
    reutilizarlo desde el dashboard y desde los tests sin mandar Telegram."""
    import y_infra_monitor as infra

    lineas = ["🩺 *PARTE DIARIO DE SALUD DEL SISTEMA*\n"]

    # --- Errores del día, agrupados por componente ---
    eventos = _query(
        "SELECT component, state, COUNT(*) AS n FROM system_events "
        "WHERE timestamp >= datetime('now', '-1 day') GROUP BY component, state ORDER BY n DESC"
    )
    if eventos:
        lineas.append("*Eventos de sistema (24h):*")
        for e in eventos[:8]:
            lineas.append(f"• {e['component']} → {e['state']}: {e['n']}")
    else:
        lineas.append("*Eventos de sistema (24h):* ninguno. Circuit Breaker y AIOps sin novedades.")

    # --- Kill switch ---
    halts = _query("SELECT reason, timestamp FROM risk_halts WHERE timestamp >= datetime('now', '-1 day')")
    if halts:
        lineas.append(f"\n🛑 *Kill switch activado {len(halts)} vez/veces hoy.* Último motivo: {halts[-1]['reason']}")
    else:
        lineas.append("\n✅ Kill switch: sin activaciones hoy.")

    # --- Notificaciones que no llegaron ---
    fallidas = _query(
        "SELECT COUNT(*) AS n FROM failed_notifications WHERE timestamp >= datetime('now', '-1 day')"
    )
    n_fallidas = fallidas[0]["n"] if fallidas else 0
    if n_fallidas:
        lineas.append(f"⚠️ {n_fallidas} notificación(es) de Telegram fallaron y quedaron guardadas (ver /api/failed-notifications).")

    # --- Operatoria del día ---
    trades = _query(
        "SELECT COUNT(*) AS n, COALESCE(SUM(realized_pnl_ars), 0) AS pnl FROM closed_trades "
        "WHERE closed_at >= datetime('now', '-1 day')"
    )
    if trades and trades[0]["n"]:
        lineas.append(f"\n*Operatoria:* {trades[0]['n']} operación(es) cerrada(s), PnL neto ${trades[0]['pnl']:.2f}.")
    else:
        lineas.append("\n*Operatoria:* sin operaciones cerradas hoy.")

    señales = _query(
        "SELECT status, COUNT(*) AS n FROM signals WHERE timestamp >= datetime('now', '-1 day') "
        "GROUP BY status ORDER BY n DESC LIMIT 5"
    )
    if señales:
        resumen = ", ".join(f"{s['status']}: {s['n']}" for s in señales)
        lineas.append(f"*Señales evaluadas:* {resumen}")

    # --- Infraestructura ---
    try:
        backup = infra.get_backup_status()
        disco = infra.get_disk_usage()
        # y_infra_monitor.get_backup_status() devuelve status en
        # {"ok", "atrasado", "sin_backups"} — no severidades de color.
        icono_estado = {"ok": "✅", "atrasado": "⚠️", "sin_backups": "🔴"}
        estado_backup = icono_estado.get(backup.get("status"), "•")
        edad = f" (hace {backup['age_hours']}hs)" if backup.get("age_hours") is not None else ""
        lineas.append(
            f"\n*Infraestructura:* {estado_backup} último backup: "
            f"{backup.get('last_backup_at') or 'nunca'}{edad} "
            f"| disco: {disco.get('used_pct', '?')}% usado | base: {infra.get_db_size_mb()} MB"
        )
        # get_infra_suggestions() devuelve dicts {severity, text}: sólo se
        # muestran las que ameritan acción (no las verdes de "todo normal",
        # que en un parte diario son ruido).
        for sugerencia in infra.get_infra_suggestions():
            if sugerencia.get("severity") in ("red", "yellow"):
                icono = "🔴" if sugerencia["severity"] == "red" else "⚠️"
                lineas.append(f"{icono} {sugerencia['text']}")
    except Exception as e:
        lineas.append(f"\n*Infraestructura:* no se pudo leer el estado ({e}).")

    # --- Tiempo real (v14) ---
    try:
        import x_ppi_websocket as ws
        estado_ws = ws.get_stream_status()
        if estado_ws.get("enabled"):
            simbolo = "✅" if estado_ws.get("healthy") else "⚠️"
            edad_tick = estado_ws.get("last_tick_age_seconds")
            detalle_tick = f"último tick hace {edad_tick}s" if edad_tick is not None else "sin ticks recibidos aún"
            lineas.append(
                f"*Tiempo real (SignalR):* {simbolo} {estado_ws.get('subscribed', 0)} instrumentos "
                f"suscritos, {detalle_tick}, {estado_ws.get('ticks_received', 0)} ticks en total."
            )
        else:
            lineas.append("*Tiempo real (SignalR):* apagado — se opera con polling HTTP normal.")
    except Exception:
        pass

    lineas.append("\n_Este parte reemplaza la revisión manual de logs: si algo hubiera requerido tu atención, está acá arriba._")
    return "\n".join(lineas)


def send_daily_health_digest(notifier):
    try:
        notifier.send_telegram(build_daily_health_digest())
    except Exception as e:
        logger.error("No se pudo enviar el parte diario de salud: %s", e)
