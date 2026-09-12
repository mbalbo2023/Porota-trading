"""
aa_env_guard.py — NUEVO EN v14.0 (Instrucción 1 del pedido de v14).

QUÉ RESUELVE
============
Hasta la v13.0, cambiar una variable del .env desde el dashboard dejaba el
archivo actualizado pero el bot seguía corriendo con los valores viejos en
memoria, y la única forma de aplicarlos era entrar por SSH al servidor y
reiniciar el proceso a mano. El pedido de v14 es explícito:

  1. Que el sistema avise que forzar un cambio de ENVIRONMENT es un tema de
     seguridad y que el reinicio se dispare desde la página web.
  2. Que esa opción SOLO esté disponible en el momento en que efectivamente
     se modificó una variable — no antes, no como un botón permanente.
  3. Que además pida confirmación por Telegram antes de reiniciar.
  4. Que el reinicio NUNCA ocurra si hay transacciones en curso: el sistema
     tiene que estar totalmente desocupado, para no cancelar ni dejar a
     medias una operación.

DECISIÓN DE DISEÑO (y por qué no se expone un endpoint que reinicie procesos)
============================================================================
Desde v8.0 este proyecto tiene documentada una regla de seguridad: el
dashboard web NO ejecuta comandos del sistema operativo (nada de
`systemctl restart`, nada de `os.system`, nada de matar procesos). Darle a
un proceso web la capacidad de reiniciar servicios es una escalada de
privilegios que no se justifica, y sigue siendo cierto en v14.

Entonces el reinicio se hace al revés, sin que el proceso web tenga ningún
poder extra: el dashboard solo GRABA una solicitud en la base. El proceso
del bot (j_main.py), que sí es dueño de su propio ciclo de vida, revisa esa
tabla cada RESTART_CHECK_INTERVAL_SECONDS y, cuando encuentra una solicitud
CONFIRMADA por Telegram y el sistema está desocupado, se apaga solo con el
apagado ordenado que ya existía desde v13.0. Docker (restart:
unless-stopped) lo vuelve a levantar y el proceso nuevo lee el .env
actualizado. El dashboard nunca ejecuta nada: pide, no ordena.

CADENA DE SEGURIDAD COMPLETA (4 llaves, todas obligatorias)
===========================================================
  Llave 1 — Token del dashboard (ya existía) + CSRF del formulario.
  Llave 2 — La solicitud de reinicio solo se puede crear dentro de los
            ENV_CHANGE_REQUEST_WINDOW_MINUTES posteriores a un guardado que
            REALMENTE cambió algo (se compara valor por valor, no se
            confía en que el formulario se haya enviado).
  Llave 3 — Confirmación humana por Telegram, con botones, validando que
            el remitente sea el TELEGRAM_CHAT_ID autorizado (esa validación
            ya la hace b_notifiers.get_telegram_button_taps desde v10.5).
  Llave 4 — Sistema desocupado: sin órdenes en vuelo, sin ejecuciones en
            curso y —si el cambio incluye pasar de SANDBOX a PRODUCTION o
            al revés— sin ninguna posición abierta.

Sobre la llave 4 y el cambio de entorno: cambiar ENVIRONMENT con posiciones
abiertas es peligroso de una forma que no es obvia. Las posiciones abiertas
de la tabla local fueron abiertas contra UN ambiente (Sandbox, por ejemplo);
si el bot reinicia apuntando a PRODUCTION, el vigilante de stop-loss/
take-profit va a seguir intentando cerrarlas... contra una cuenta real donde
esos papeles no existen. Por eso, para un cambio de ENVIRONMENT, la
exigencia es más dura: cero posiciones abiertas, sin excepción.
"""

import contextlib
import logging
import os
import sqlite3
import time
import uuid
from datetime import datetime
from typing import Optional, Tuple
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

logger = logging.getLogger("env_guard")

DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")

# Ventana durante la cual, tras guardar un cambio real de configuración, la
# opción de reinicio queda habilitada en el dashboard. Pasada la ventana, el
# botón desaparece solo y hay que volver a guardar un cambio para verlo.
ENV_CHANGE_REQUEST_WINDOW_MINUTES = int(os.getenv("ENV_CHANGE_REQUEST_WINDOW_MINUTES", "15"))

# Cuánto tiempo se espera la confirmación por Telegram antes de dar la
# solicitud por vencida (y no reiniciar nada).
ENV_RESTART_CONFIRM_TIMEOUT_MINUTES = int(os.getenv("ENV_RESTART_CONFIRM_TIMEOUT_MINUTES", "10"))

# Un lock de actividad más viejo que esto se considera basura de un proceso
# que murió sin liberarlo (no puede bloquear el reinicio para siempre).
ACTIVITY_LOCK_MAX_AGE_SECONDS = int(os.getenv("ACTIVITY_LOCK_MAX_AGE_SECONDS", "300"))

# Variables cuyo cambio se considera CRÍTICO: siempre exigen confirmación
# explícita por Telegram, y en el dashboard se muestran con la advertencia
# de seguridad correspondiente.
CRITICAL_VARS = {
    "ENVIRONMENT",
    "PPI_API_KEY",
    "PPI_API_SECRET",
    "PPI_ACCOUNT_NUMBER",
    "ORDER_EXECUTION_MODE",
    "PRODUCTION_AUTO_EXECUTE_CONFIRMED",
    "SCALPING_MODE",
    "LANGGRAPH_MODE",
    "MAX_DAILY_LOSS_PCT",
    "MAX_DRAWDOWN_PCT",
    "RISK_PCT_PER_TRADE",
    "IOL_COST_ESTIMATE_EXPLICIT_OPT_IN",
}

# NUEVO EN v16.0 — hallazgo aceptado de la auditoría. La validación anterior
# comprobaba que la variable EXISTIERA, no que tuviera contenido usable. Una
# credencial vacía o truncada pasaba el control y el sistema arrancaba para
# fallar más adelante, en el primer login, con un mensaje mucho menos claro
# que "falta la credencial". Estos son los largos mínimos plausibles de cada
# tipo de secreto: no validan que la llave sea correcta —eso solo lo sabe el
# servidor— pero sí atajan el caso frecuente de un copiado incompleto.
LONGITUD_MINIMA_SECRETOS = {
    "PPI_API_KEY": 10,
    "PPI_API_SECRET": 10,
    "GEMINI_API_KEY": 20,
    "TELEGRAM_BOT_TOKEN": 20,
    "TELEGRAM_CHAT_ID": 5,
    "DASHBOARD_ACCESS_TOKEN": 12,
    "IOL_USERNAME": 3,
    "IOL_PASSWORD": 6,
}


def validar_secretos_criticos(exigir_iol: bool = False) -> list:
    """Devuelve la lista de problemas encontrados. Vacía significa que se puede
    arrancar. Se reportan TODOS los problemas juntos y no el primero: si faltan
    tres credenciales, enterarse de a una obliga a arrancar tres veces."""
    problemas = []
    obligatorias = ["PPI_API_KEY", "PPI_API_SECRET", "PPI_ACCOUNT_NUMBER",
                    "GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    if exigir_iol or os.getenv("IOL_ENABLED", "false").lower() == "true":
        obligatorias += ["IOL_USERNAME", "IOL_PASSWORD"]

    for var in obligatorias:
        valor = (os.getenv(var) or "").strip()
        if not valor:
            problemas.append(f"{var}: falta o está vacía.")
            continue
        minimo = LONGITUD_MINIMA_SECRETOS.get(var)
        if minimo and len(valor) < minimo:
            problemas.append(
                f"{var}: tiene {len(valor)} caracteres y se esperan al menos {minimo}. "
                f"Suele ser un copiado incompleto.")
        if valor.lower() in ("tu_clave", "cambiar", "changeme", "xxx", "none", "null"):
            problemas.append(f"{var}: quedó con un valor de plantilla sin reemplazar.")

    entorno = (os.getenv("ENVIRONMENT") or "").upper()
    if entorno not in ("SANDBOX", "PRODUCTION"):
        problemas.append(f"ENVIRONMENT vale '{entorno}' y solo acepta SANDBOX o PRODUCTION.")

    return problemas


STATUS_PENDING_CONFIRM = "PENDING_TELEGRAM"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_REJECTED = "REJECTED"
STATUS_APPLIED = "APPLIED"
STATUS_EXPIRED = "EXPIRED"


# ---------------------------------------------------------------------- #
# Base de datos
# ---------------------------------------------------------------------- #
def _connect():
    return ac_db.connect_raw()


def init_tables():
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS env_change_requests (
            id TEXT PRIMARY KEY,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            changed_vars TEXT,
            env_switch INTEGER DEFAULT 0,
            status TEXT DEFAULT 'SAVED',
            confirmed_at DATETIME,
            applied_at DATETIME,
            detail TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS activity_locks (
            component TEXT PRIMARY KEY,
            acquired_at REAL,
            detail TEXT
        )
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------- #
# Locks de actividad — "¿el sistema está haciendo algo ahora mismo?"
# ---------------------------------------------------------------------- #
# Se usan como context manager alrededor de los tramos donde el bot está
# tocando plata de verdad: colocar una orden en PPI, resolver una ejecución
# parcial, cerrar una posición. Son locks INFORMATIVOS (no serializan nada,
# no reemplazan a ningún otro mecanismo de concurrencia): existen solo para
# que is_system_idle() pueda responder con la verdad en vez de con una
# suposición.
def acquire_lock(component: str, detail: str = ""):
    try:
        init_tables()
        conn = _connect()
        conn.execute(
            "INSERT OR REPLACE INTO activity_locks (component, acquired_at, detail) VALUES (?, ?, ?)",
            (component, time.time(), detail),
        )
        conn.commit()
        conn.close()
    except Exception as e:  # nunca puede romper la operatoria real
        logger.debug("No se pudo tomar el lock de actividad %s: %s", component, e)


def release_lock(component: str):
    try:
        conn = _connect()
        conn.execute("DELETE FROM activity_locks WHERE component = ?", (component,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug("No se pudo liberar el lock de actividad %s: %s", component, e)


@contextlib.contextmanager
def activity(component: str, detail: str = ""):
    """Uso: `with aa_env_guard.activity("ORDER_EXECUTION", ticker): ...`

    Si el proceso muere adentro del bloque, el lock queda huérfano en la
    base — por eso los locks vencen solos a los ACTIVITY_LOCK_MAX_AGE_SECONDS
    (ver _fresh_locks). Un lock huérfano puede demorar un reinicio unos
    minutos; nunca puede bloquearlo para siempre.
    """
    acquire_lock(component, detail)
    try:
        yield
    finally:
        release_lock(component)


def _fresh_locks() -> list:
    try:
        conn = _connect()
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM activity_locks").fetchall()
        conn.close()
    except Exception:
        return []
    cutoff = time.time() - ACTIVITY_LOCK_MAX_AGE_SECONDS
    return [dict(r) for r in rows if (r["acquired_at"] or 0) >= cutoff]


# ---------------------------------------------------------------------- #
# ¿Está el sistema desocupado?
# ---------------------------------------------------------------------- #
def is_system_idle(require_no_open_positions: bool = False) -> Tuple[bool, str]:
    """Devuelve (idle, motivo_si_no). El motivo se muestra tal cual por
    Telegram y en el dashboard: si el reinicio se posterga, se dice
    exactamente por qué, en vez de dejar al usuario esperando sin saber."""
    init_tables()

    locks = _fresh_locks()
    if locks:
        detalle = ", ".join(f"{l['component']}({l.get('detail') or 's/d'})" for l in locks)
        return False, f"hay operaciones en curso: {detalle}"

    try:
        conn = _connect()
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM pending_orders WHERE status IN ('PENDING', 'SENT_TO_PPI')"
        )
        pendientes = c.fetchone()[0]
        conn.close()
    except sqlite3.OperationalError:
        pendientes = 0  # la tabla todavía no existe: no hay órdenes, entonces
    if pendientes:
        return False, f"hay {pendientes} orden(es) esperando confirmación o en vuelo hacia PPI"

    if require_no_open_positions:
        try:
            conn = _connect()
            abiertas = conn.execute("SELECT COUNT(*) FROM open_positions").fetchone()[0]
            conn.close()
        except sqlite3.OperationalError:
            abiertas = 0
        if abiertas:
            return False, (
                f"hay {abiertas} posición(es) abierta(s) y el cambio incluye ENVIRONMENT — "
                "cerralas (o esperá a que cierren solas por stop/take-profit) antes de "
                "cambiar de ambiente"
            )

    return True, ""


# ---------------------------------------------------------------------- #
# Registro del cambio de configuración (lo llama o_dashboard al guardar)
# ---------------------------------------------------------------------- #
def register_change(changed_vars: list, env_switch: bool, detail: str = "") -> Optional[str]:
    """Se llama DESPUÉS de escribir el .env, y solo si realmente cambió al
    menos una variable (o_dashboard compara valor por valor). Devuelve el id
    de la solicitud, que es lo que habilita el botón de reinicio durante los
    próximos ENV_CHANGE_REQUEST_WINDOW_MINUTES."""
    if not changed_vars:
        return None
    init_tables()
    request_id = str(uuid.uuid4())
    conn = _connect()
    conn.execute(
        "INSERT INTO env_change_requests (id, changed_vars, env_switch, status, detail) "
        "VALUES (?, ?, ?, 'SAVED', ?)",
        (request_id, ",".join(changed_vars), int(env_switch), detail),
    )
    conn.commit()
    conn.close()
    logger.info("Cambio de configuración registrado (%s): %s", request_id, ", ".join(changed_vars))
    return request_id


def get_recent_saved_change() -> Optional[dict]:
    """La última solicitud en estado SAVED dentro de la ventana de tiempo.
    Si devuelve None, el dashboard NO muestra el botón de reinicio: es
    exactamente el comportamiento pedido ("solo al momento en que se
    realiza una modificación, no antes")."""
    init_tables()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM env_change_requests WHERE status = 'SAVED' "
        "AND created_at >= datetime('now', ?) ORDER BY created_at DESC LIMIT 1",
        (f"-{ENV_CHANGE_REQUEST_WINDOW_MINUTES} minutes",),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_request(request_id: str) -> Optional[dict]:
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM env_change_requests WHERE id = ?", (request_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _set_status(request_id: str, status: str, column: Optional[str] = None):
    conn = _connect()
    if column:
        conn.execute(
            f"UPDATE env_change_requests SET status = ?, {column} = CURRENT_TIMESTAMP WHERE id = ?",
            (status, request_id),
        )
    else:
        conn.execute("UPDATE env_change_requests SET status = ? WHERE id = ?", (status, request_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------- #
# Paso 2: pedir confirmación por Telegram
# ---------------------------------------------------------------------- #
def request_restart_confirmation(request_id: str, notifier) -> Tuple[bool, str]:
    """Lo llama el dashboard cuando el usuario aprieta "Solicitar reinicio".
    Manda el mensaje con botones y deja la solicitud en PENDING_TELEGRAM.
    NO reinicia nada acá: el reinicio real lo decide j_main.py después de la
    confirmación y del chequeo de sistema desocupado."""
    req = get_request(request_id)
    if not req:
        return False, "La solicitud no existe o ya fue procesada."
    if req["status"] != "SAVED":
        return False, f"La solicitud ya está en estado {req['status']}."

    env_switch = bool(req["env_switch"])
    idle, motivo = is_system_idle(require_no_open_positions=env_switch)

    aviso_seguridad = ""
    if env_switch:
        aviso_seguridad = (
            "\n\n🔐 *ATENCIÓN — CAMBIO DE AMBIENTE*\n"
            "Este cambio toca `ENVIRONMENT` (SANDBOX ↔ PRODUCTION). Es la variable "
            "más sensible del sistema: define si las órdenes se colocan con dinero "
            "real. Confirmá solo si estás 100% seguro."
        )

    estado_txt = "✅ El sistema está desocupado ahora mismo." if idle else (
        f"⏳ Ahora mismo NO se puede reiniciar ({motivo}). Si confirmás, el reinicio "
        "queda agendado y se ejecuta apenas el sistema se desocupe."
    )

    mensaje = (
        "♻️ *SOLICITUD DE REINICIO DEL BOT*\n\n"
        f"Se modificó la configuración desde el panel web:\n`{req['changed_vars']}`\n\n"
        "Los valores nuevos recién van a tomar efecto cuando el bot se reinicie.\n\n"
        f"{estado_txt}"
        f"{aviso_seguridad}\n\n"
        f"Tenés {ENV_RESTART_CONFIRM_TIMEOUT_MINUTES} minutos para responder."
    )

    notifier.send_telegram_generic_confirmation(
        mensaje,
        confirm_data=f"ENVOK:{request_id}",
        cancel_data=f"ENVNO:{request_id}",
        confirm_text="♻️ Confirmar reinicio",
        cancel_text="❌ No reiniciar",
    )
    _set_status(request_id, STATUS_PENDING_CONFIRM)
    return True, "Se envió la confirmación por Telegram. Revisá tu celular."


# ---------------------------------------------------------------------- #
# Paso 3: procesar el tap del botón de Telegram
# ---------------------------------------------------------------------- #
def handle_telegram_tap(action: str, request_id: str, notifier) -> bool:
    """Lo llama l_order_confirmation.process_button_taps() cuando el
    callback_data no es de una orden sino de un reinicio (prefijos ENVOK /
    ENVNO). Devuelve True si el tap era de este módulo.

    Por qué el despacho vive en process_button_taps y no en un hilo propio:
    Telegram entrega las actualizaciones con un offset incremental y
    consumirlas desde dos lugares distintos hace que un consumidor se coma
    los mensajes del otro. Hay UN solo lector de getUpdates en todo el
    sistema, y reparte por prefijo.
    """
    if action not in ("ENVOK", "ENVNO"):
        return False

    req = get_request(request_id)
    if not req:
        notifier.send_telegram("Esa solicitud de reinicio ya no existe.")
        return True
    if req["status"] != STATUS_PENDING_CONFIRM:
        notifier.send_telegram(f"Esa solicitud de reinicio ya estaba en estado {req['status']}.")
        return True

    if action == "ENVNO":
        _set_status(request_id, STATUS_REJECTED)
        notifier.send_telegram(
            "❌ Reinicio cancelado. Los cambios quedaron guardados en el .env pero el bot "
            "sigue corriendo con los valores anteriores hasta el próximo reinicio."
        )
        return True

    # Vencimiento: si tardó más de la ventana, no se acepta.
    try:
        creado = datetime.strptime(req["created_at"], "%Y-%m-%d %H:%M:%S")
        if (time.time() - creado.timestamp()) / 60 > ENV_RESTART_CONFIRM_TIMEOUT_MINUTES + ENV_CHANGE_REQUEST_WINDOW_MINUTES:
            _set_status(request_id, STATUS_EXPIRED)
            notifier.send_telegram("⏰ Esa solicitud de reinicio venció. Volvé a guardar los cambios en el panel.")
            return True
    except (ValueError, TypeError):
        pass

    _set_status(request_id, STATUS_CONFIRMED, column="confirmed_at")
    idle, motivo = is_system_idle(require_no_open_positions=bool(req["env_switch"]))
    if idle:
        notifier.send_telegram("✅ Confirmado. El bot se va a reiniciar en los próximos segundos.")
    else:
        notifier.send_telegram(
            f"✅ Confirmado, pero el reinicio queda EN ESPERA porque {motivo}.\n"
            "Se va a ejecutar solo apenas el sistema quede desocupado. Te aviso cuando pase."
        )
    return True


# ---------------------------------------------------------------------- #
# Paso 4: lo que ejecuta j_main.py
# ---------------------------------------------------------------------- #
def get_confirmed_pending_restart() -> Optional[dict]:
    init_tables()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM env_change_requests WHERE status = ? ORDER BY confirmed_at ASC LIMIT 1",
        (STATUS_CONFIRMED,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def mark_applied(request_id: str):
    _set_status(request_id, STATUS_APPLIED, column="applied_at")


def get_history(limit: int = 20) -> list:
    init_tables()
    conn = _connect()
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM env_change_requests ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
