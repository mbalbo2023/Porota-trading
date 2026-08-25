"""
j_main.py — Bucle principal y Scheduler (v8.0)

Cambios de esta revisión:
  - Ya no evalúa solo 3 tickers hardcodeados: recorre el universo de
    n_instrument_watchlist.json (CEDEARs + acciones + bonos), filtrado por
    liquidez real (m_instrument_universe.py) antes de analizar cada uno.
  - Los titulares del día quedan guardados (no solo usados al vuelo), para
    que o_dashboard.py pueda mostrar "qué noticias subieron hoy".
  - ORDER_AUTO_EXECUTE ahora viene "true" por default (ver
    l_order_confirmation.py) — un toque en Telegram ejecuta la orden real.
  - Kill switch (p_risk_guardian.py): se chequea en cada vuelta del loop,
    antes de evaluar instrumentos nuevos. Si se activa, el bot deja de
    alertar hasta revisión manual (hallazgo de las auditorías 7.1/7.4).
  - g_news_feed.py ahora puede devolver "sin noticias confiables" en vez
    de un fallback inventado — se propaga tal cual a Gemini, que vetea.
"""

import time
import threading
import logging
import sqlite3
import uuid
import json
import os
import signal
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo
from apscheduler.schedulers.background import BackgroundScheduler

# ---------------------------------------------------------------------- #
# NUEVO EN v14.0 — CARGA DE CONFIGURACIÓN ANTES QUE CUALQUIER OTRA COSA
# ---------------------------------------------------------------------- #
# Esto tiene que ejecutarse ANTES de importar cualquier módulo del proyecto.
# Motivo (bug latente encontrado al corregir docker-compose.yml en v14.0):
# varios módulos leen os.getenv() a nivel de módulo, es decir en el momento
# en que se los importa. Hasta v13.0 eso funcionaba de casualidad, porque
# docker-compose inyectaba todo el .env en el entorno del contenedor con
# `env_file:`, así que las variables ya estaban ahí antes de que arrancara
# Python. Al sacar `env_file` (ver el comentario largo en docker-compose.yml
# sobre por qué había que sacarlo), esa red desaparece: si no se carga el
# .env acá arriba, los módulos importados leerían sus valores por defecto y
# el bot arrancaría, silenciosamente, con la configuración equivocada.
from dotenv import load_dotenv
load_dotenv()

import aa_env_guard as env_guard
import ab_log_watch as log_watch
from b_notifiers import MultiChannelNotifier
from c_ppi_client import ResilientPPIClient
from f_gemini_decision_engine import GeminiDecisionEngine
from g_news_feed import fetch_latest_headlines
from i_auto_tuner import AutoTuner
import h_daily_report as daily_report
import e_technical_engine as technical_engine
import d_economics as economics
import k_position_manager as position_manager
import l_order_confirmation as order_confirmation
import m_instrument_universe as universe
import p_risk_guardian as risk_guardian
import r_news_engine_247 as news_247
from s_learning_engine import LearningEngine
import t_model_guardian as model_guardian
# NUEVO EN v15.0
import ad_macro_history as macro_history
import ae_ppi_api_watch as ppi_api_watch
import af_model_registry as model_registry
import ag_kill_switch_supervisor as ks_supervisor
import ah_market_tools as market_tools
import ak_byma_calendar as byma_calendar
import al_market_startup as market_startup
# NUEVO EN v16.2 — piezas que en v16.1 existían pero nadie llamaba.
import aj_trade_gate as gate          # el portón de 29 casos, ahora cableado
import ai_derivatives_engine as deriv  # dimensionamiento de opciones y futuros
import ax_equity                       # patrimonio real (efectivo + tenencia)
import au_fee_schedule                 # aranceles por clase de activo
# NUEVO EN v13.0 — Cost-Validator Halt de scalping (ver
# u_aiops_watcher.validate_scalping_viability). Se importa acá, no dentro
# de main(), porque evaluate_instrument() la necesita en cada vuelta del
# loop de escaneo, no solo una vez al arrancar.
from u_aiops_watcher import validate_scalping_viability as aiops_scalping_validator
# CORRECCIÓN DE AUTOAUDITORÍA v12.0: w_agent_graph.py es una feature de
# SOLO OBSERVACIÓN (modo shadow, ver pendiente #1). Importarlo como
# dependencia dura acá arriba haría que una falla en la instalación de
# `langgraph` (una librería nueva en esta versión) impida arrancar el bot
# REAL — desproporcionado para algo que ni siquiera participa de la
# ejecución. Se degrada con gracia, mismo criterio que ChromaDB/
# scikit-learn en m_introspection_engine.py y u_aiops_watcher.py.
try:
    import w_agent_graph as agent_graph
    AGENT_GRAPH_AVAILABLE = True
except ImportError as _e:
    agent_graph = None
    AGENT_GRAPH_AVAILABLE = False
    logging.getLogger("main").warning(
        "w_agent_graph no disponible (%s) — modo shadow del grafo multi-agente desactivado; "
        "el bot sigue operando normalmente con la lógica secuencial.", _e,
    )

# SIMPLIFICADO EN v11.0 y ACTUALIZADO EN v14.0: hasta v10.5 convivían dos
# mecanismos de rotación de logs (RotatingFileHandler de Python + logrotate
# del sistema operativo, configurado por el instalador systemd). Los dos
# apuntando al mismo archivo es una receta para perder líneas: cada uno cree
# ser el dueño del archivo. Desde v14.0 el instalador systemd ya no existe
# (Docker es el único camino de despliegue soportado), así que queda UN solo
# mecanismo, dentro del proceso, sin ambigüedad posible.
#
# El bot escribe a DOS destinos, cada uno con su razón de ser:
#   - stdout: lo captura Docker (`docker compose logs -f`).
#   - LOG_DIR/trading_bot.log: archivo rotado (5MB × 5 = 25MB tope) que es
#     el que se descarga desde el dashboard y el que lee la vigilancia
#     automática de logs (ab_log_watch.py, nueva en v14.0).
LOG_DIR = os.getenv("LOG_DIR", "data/logs")
os.makedirs(LOG_DIR, exist_ok=True)
from logging.handlers import RotatingFileHandler
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)
_file_handler = RotatingFileHandler(
    os.path.join(LOG_DIR, "trading_bot.log"), maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(), _file_handler],
)
logger = logging.getLogger("main")

SERVER_TIMEZONE = os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires")
DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")
MARKET_OPEN_HOUR = int(os.getenv("MARKET_OPEN_HOUR", "11"))
MARKET_CLOSE_HOUR = int(os.getenv("MARKET_CLOSE_HOUR", "17"))
RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", "1.0"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
STOP_LOSS_ATR_MULT = float(os.getenv("STOP_LOSS_ATR_MULT", "1.0"))

# NUEVO EN v10.5 — modo scalping (pedido explícito, para probar el motor
# en SANDBOX): ciclos de escaneo mucho más cortos, análisis técnico en
# 1M/5M (e_technical_engine.evaluate_technical_scalping) en vez de
# 1H/15M/5M, y un horizonte de mantenimiento de posición en MINUTOS, no
# días, para prorratear el hurdle en consecuencia. Se activa solo para
# CEDEARs (los únicos con datos intradía de 1M vía yfinance) — el resto de
# las clases (ACCIONES/BONOS/ETF locales) sigue con el análisis normal,
# porque PPI no ofrece velas de 1 minuto.
SCALPING_MODE = os.getenv("SCALPING_MODE", "false").lower() == "true"
SCALPING_TARGET_HOLD_MINUTES = int(os.getenv("SCALPING_TARGET_HOLD_MINUTES", "30"))
SCALPING_SCAN_INTERVAL_SECONDS = int(os.getenv("SCALPING_SCAN_INTERVAL_SECONDS", "10"))
WIN_RATE_LOOKBACK_DAYS = int(os.getenv("WIN_RATE_LOOKBACK_DAYS", "30"))

runtime_config = {
    "MIN_SCORE_TECH": float(os.getenv("DEFAULT_MIN_SCORE_TECH", "0.70")),
    "MIN_SCORE_MACRO": float(os.getenv("DEFAULT_MIN_SCORE_MACRO", "0.70")),
    "TAKE_PROFIT_ATR_MULT": float(os.getenv("TAKE_PROFIT_ATR_MULT", "2.0")),
}
TARGET_HOLD_DAYS = int(os.getenv("TARGET_HOLD_DAYS", "5"))

# Universo de instrumentos, filtrado por liquidez una vez al día (a la apertura).
active_universe = []

# NUEVO EN v14.0 — referencia global al cliente de streams de tiempo real,
# para que refresh_universe_job() pueda re-suscribirlo cuando cambia el
# universo del día sin tener que pasarlo por parámetro por todo el archivo.
ws_client = None


def load_tuned_thresholds():
    if not os.path.exists("auto_tune_config.json"):
        return
    try:
        with open("auto_tune_config.json") as f:
            tuned = json.load(f)
        if "min_score_tech" in tuned:
            runtime_config["MIN_SCORE_TECH"] = float(tuned["min_score_tech"])
        if "min_score_macro" in tuned:
            runtime_config["MIN_SCORE_MACRO"] = float(tuned["min_score_macro"])
        if "take_profit_atr_mult" in tuned:
            runtime_config["TAKE_PROFIT_ATR_MULT"] = float(tuned["take_profit_atr_mult"])
        logger.info("Umbrales actualizados desde auto_tune_config.json: %s (%s)",
                    runtime_config, tuned.get("notas_del_ajuste", ""))
    except Exception as e:
        logger.error("Error leyendo auto_tune_config.json, se mantienen los umbrales vigentes: %s", e)


def init_db():
    conn = ac_db.connect_raw()
    # CORRECCIÓN DE LA AUDITORÍA 8.1 (rev. 2), aceptada: con el bot, el
    # auto-tuner y el dashboard leyendo/escribiendo la misma base de datos
    # SQLite al mismo tiempo (dos procesos distintos: j_main.py y
    # o_dashboard.py), el modo por default de SQLite puede tirar
    # "database is locked". WAL (Write-Ahead Logging) permite lecturas y
    # escrituras simultáneas de forma segura. Se activa una sola vez acá
    # porque queda grabado en el archivo de la base — no hace falta
    # repetirlo en cada módulo que abre su propia conexión.
    conn.execute("PRAGMA journal_mode=WAL")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id TEXT PRIMARY KEY,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            ticker TEXT,
            status TEXT,
            reason TEXT,
            score_tech REAL,
            macro_score REAL,
            net_return_pct_est REAL,
            hurdle_pct REAL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_headlines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            headline TEXT,
            UNIQUE(date, headline)
        )
    """)
    # NUEVO EN v13.0 — Monitor de Decisiones de IA (pedido explícito):
    # un registro liviano de CADA evaluación macro/geopolítica que hace
    # Gemini (o Claude de respaldo), independiente de en qué termina la
    # oportunidad después (técnico/hurdle/grafo). o_dashboard.py lo usa
    # para mostrar tasa de veto, score promedio y evolución en el tiempo
    # con enfoque gerencial (ver sección de dashboard del Documento
    # Maestro v13).
    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            ticker TEXT,
            macro_score REAL,
            veto_risk INTEGER,
            reason TEXT
        )
    """)
    # NUEVO EN v13.0 — evidencia acumulada del modo shadow/gating del
    # grafo LangGraph (ver w_agent_graph.py): antes solo quedaba en el
    # log de texto, ahora se persiste para poder calcular una tasa de
    # acuerdo real entre el grafo y la lógica secuencial — el dato que
    # hace falta para decidir con criterio un futuro cutover a gating
    # real (ver Documento Maestro v13, sección de roadmap).
    c.execute("""
        CREATE TABLE IF NOT EXISTS shadow_evaluations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            ticker TEXT,
            final_decision TEXT,
            agreed INTEGER,
            gating INTEGER
        )
    """)
    conn.commit()
    conn.close()


def log_ai_decision(ticker, macro_score=None, veto_risk=False, reason=""):
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute(
        "INSERT INTO ai_decisions (ticker, macro_score, veto_risk, reason) VALUES (?, ?, ?, ?)",
        (ticker, macro_score, int(bool(veto_risk)), reason),
    )
    conn.commit()
    conn.close()


def log_shadow_evaluation(ticker, final_decision, agreed=True, gating=False):
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute(
        "INSERT INTO shadow_evaluations (ticker, final_decision, agreed, gating) VALUES (?, ?, ?, ?)",
        (ticker, final_decision, int(bool(agreed)), int(bool(gating))),
    )
    conn.commit()
    conn.close()


# ===========================================================================
# AUXILIARES DEL PORTÓN OPERATIVO (v16.2)
# ===========================================================================
# Estas funciones existen porque el portón necesita datos que hasta ahora
# nadie calculaba. No son lógica de negocio nueva: son los sensores que el
# catálogo de 29 casos daba por supuestos y que no estaban conectados.

MARKET_OPEN_HOUR = int(os.getenv("MARKET_OPEN_HOUR", "11"))
MARKET_CLOSE_HOUR = int(os.getenv("MARKET_CLOSE_HOUR", "17"))


def _mercado_abierto(ahora=None) -> bool:
    """¿Hay rueda de contado en BYMA ahora mismo?

    Fines de semana, feriados y jornadas especiales quedan afuera mediante
    el calendario oficial auditado de BYMA. Si el año todavía no fue auditado,
    el portón queda cerrado: nunca se infiere que un día desconocido está
    habilitado para operar.

    El reloj del contenedor puede estar en UTC. Cuando no se inyecta una hora
    de prueba, se convierte explícitamente a SERVER_TIMEZONE antes de comparar
    las horas de rueda; el timezone del scheduler por sí solo no cambia
    datetime.now().
    """
    if ahora is None:
        ahora = datetime.now(ZoneInfo(SERVER_TIMEZONE))
    elif getattr(ahora, "tzinfo", None) is not None:
        ahora = ahora.astimezone(ZoneInfo(SERVER_TIMEZONE))
    if ahora.weekday() >= 5:
        return False
    if not byma_calendar.es_dia_habil_operativo(ahora.date()):
        return False
    return MARKET_OPEN_HOUR <= ahora.hour < MARKET_CLOSE_HOUR


def _desfasaje_de_reloj(ppi) -> float:
    """Segundos de diferencia entre el reloj del servidor y el del bróker.

    Se toma del timestamp de la última respuesta de PPI, que es la referencia
    que importa: el control de "cotización vieja" compara el tick contra la
    hora local, así que lo relevante es estar sincronizado con quien manda los
    ticks, no con un servidor NTP cualquiera. Si no hay referencia, devuelve
    0.0 en vez de inventar un número.
    """
    try:
        referencia = getattr(ppi, "last_server_epoch", None)
        if not referencia:
            return 0.0
        return gate.clock_drift_seconds(referencia)
    except Exception:
        return 0.0


def _minutos_de_apagon(news_result: dict) -> float:
    """Cuánto hace que los feeds de noticias no traen nada utilizable."""
    if not news_result.get("feeds_down", news_result.get("news_unavailable", False)):
        _minutos_de_apagon._desde = None
        return 0.0
    ahora = time.time()
    if getattr(_minutos_de_apagon, "_desde", None) is None:
        _minutos_de_apagon._desde = ahora
    return (ahora - _minutos_de_apagon._desde) / 60.0


_minutos_de_apagon._desde = None


def _evento_de_calendario_activo() -> bool:
    """¿Hay un dato macro programado dentro de la ventana?

    El precio de los minutos previos a un dato conocido no contiene la
    información que está por salir: abrir ahí es apostar a un número que
    todavía no se publicó.
    """
    try:
        return bool(macro_history.hay_evento_programado(
            ventana_minutos=int(os.getenv("CALENDAR_EVENT_WINDOW_MINUTES", "60"))))
    except Exception:
        return False


def _freno_de_costos_scalping() -> bool:
    """¿Las últimas operaciones de scalping cerraron con margen neto negativo?"""
    if not SCALPING_MODE:
        return False
    try:
        veredicto = aiops_scalping_validator()
        return not veredicto.get("viable", True)
    except Exception as e:
        logger.debug("No se pudo evaluar el freno de costos de scalping: %s", e)
        return False


def _exposicion_en_derivados_ars(ppi) -> float:
    total = 0.0
    for pos in position_manager.get_open_positions():
        clase = (pos.get("asset_class") or pos.get("instrument_type") or "").upper()
        if clase in ("OPCIONES", "FUTUROS"):
            total += float(pos.get("entry_price", 0)) * float(pos.get("quantity", 0))
    return total


def _tope_derivados_alcanzado(ppi) -> bool:
    """¿La exposición en derivados llegó al tope de concentración?

    Aunque cada posición arriesgue el 1%, el conjunto puede concentrar
    demasiado en productos con fecha de caducidad: llegado el vencimiento, la
    decisión se la queda el ejercicio automático.
    """
    try:
        capital = ax_equity.base_de_riesgo(ppi) or 0.0
        if capital <= 0:
            return False
        return deriv.portfolio_room_for_derivatives(
            capital, _exposicion_en_derivados_ars(ppi)) <= 0
    except Exception as e:
        logger.debug("No se pudo evaluar el tope de derivados: %s", e)
        return False


def registrar_abstencion_global(codigo: str, motivo: str) -> None:
    """Deja registrada una abstención de nivel 0 o 1 con su CÓDIGO del
    catálogo.

    Sin esto, el panel no puede mostrar lo que la documentación promete: los
    29 códigos con su nivel. Y sin el registro, la pregunta "por qué no operó
    hoy" solo se puede responder leyendo logs e interpretándolos, que es
    exactamente lo que este sistema decidió no hacer en ninguna otra parte.
    """
    try:
        log_signal("__SISTEMA__", codigo, motivo)
    except Exception as e:
        logger.debug("No se pudo registrar la abstención %s: %s", codigo, e)


def log_signal(ticker, status, reason, score_tech=None, macro_score=None, net_return=None, hurdle=None):
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute(
        "INSERT INTO signals (id, ticker, status, reason, score_tech, macro_score, "
        "net_return_pct_est, hurdle_pct) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), ticker, status, reason, score_tech, macro_score, net_return, hurdle),
    )
    conn.commit()
    conn.close()


def save_headlines(news_result):
    """Guarda los titulares del día para que el dashboard los pueda mostrar
    (antes se usaban al vuelo y se perdían). news_result es el dict que
    devuelve g_news_feed.fetch_latest_headlines()."""
    conn = ac_db.connect_raw()
    c = conn.cursor()
    for h in news_result.get("headlines", []):
        c.execute(
            "INSERT OR IGNORE INTO daily_headlines (date, headline) VALUES (?, ?)",
            (date.today().isoformat(), h),
        )
    conn.commit()
    conn.close()


def refresh_universe_job(ppi):
    """Se corre a la apertura: descubre instrumentos automáticamente
    (nuevo en v10.3 — ver m_instrument_universe.py) y descarta los que
    no tienen liquidez suficiente.

    NUEVO EN v14.0: apenas queda resuelto el universo del día, se le pasa la
    lista al stream de tiempo real para que se suscriba a esos instrumentos.
    En v12/v13 el cliente de WebSocket se instanciaba con una lista VACÍA y
    nunca se actualizaba — el propio archivo lo dejaba anotado como límite
    conocido. Ahora la suscripción sigue al universo real."""
    global active_universe, _permisos_de_cuenta
    if not getattr(ppi, "is_authenticated", lambda: False)():
        active_universe = []
        _permisos_de_cuenta = {}
        logger.error("Universo no actualizado: PPI no está autenticado. No se abren posiciones.")
        try:
            import bb_runtime_status
            bb_runtime_status.record_event(
                "TRADING_GATE", "BLOCKED_PPI_AUTH",
                "Universo vacío hasta recuperar autenticación PPI.",
            )
        except Exception:
            pass
        return
    # v16.2 — los permisos de la cuenta se sondean una vez por día, acá. El
    # método es no invasivo: se pide un presupuesto de orden, que simula y
    # devuelve el costo sin colocar nada. Se preguntan, no se asumen.
    try:
        _permisos_de_cuenta = universe.detect_account_permissions(ppi) or {}
    except Exception as e:
        logger.warning("No se pudieron detectar los permisos de la cuenta: %s", e)
        _permisos_de_cuenta = {}

    all_instruments = universe.build_universe(ppi)
    active_universe = universe.filter_by_liquidity(all_instruments, ppi)
    observacion = getattr(universe, "discover_observation_only", None)
    if callable(observacion):
        observacion(ppi)
    else:
        logger.info("Descubrimiento de observación no disponible; se omite sin afectar el universo operable.")
    logger.info("Universo activo tras filtro de liquidez: %s de %s instrumentos.",
                len(active_universe), len(all_instruments))
    _resubscribe_stream()


def _resubscribe_stream():
    """Le pasa el universo activo al stream de PPI. Silencioso y sin efecto
    si el stream está apagado o todavía no arrancó."""
    global ws_client
    if not ws_client:
        return
    try:
        ws_client.set_instruments([
            (inst.ticker, inst.instrument_type, inst.settlement) for inst in active_universe
        ])
        logger.info("Stream de tiempo real: %s instrumentos en la lista de suscripción.",
                    len(active_universe))
    except Exception as e:
        logger.warning("No se pudo actualizar la suscripción del stream: %s", e)


def send_weekly_report_job(notifier):
    report = AutoTuner().generate_weekly_report()
    notifier.send_telegram(report)


def send_monthly_report_job(notifier):
    """NUEVO EN v14.0 (Instrucción 7, mejora de reportes) — resumen mensual
    con enfoque gerencial por Telegram. El informe completo con gráficos
    queda descargable desde el dashboard; por Telegram va la síntesis, que
    es lo que se puede leer en un celular."""
    import z_reports_engine as reports_engine
    notifier.send_telegram(reports_engine.build_monthly_summary_text())


def scalping_preflight(notifier) -> dict:
    """NUEVO EN v14.0 (Instrucción 11: "dejá listo y funcionando el sistema de
    scalping").

    El scalping no falla por una sola pieza rota: falla porque una de seis
    condiciones no se cumple y nadie se entera hasta que la plata ya se fue en
    comisiones. Esta verificación corre al arrancar, cuando SCALPING_MODE está
    activo, y confirma UNA POR UNA las condiciones que el modo necesita. Si
    algo falta, lo dice por Telegram con nombre y apellido en vez de arrancar
    igual y fallar en silencio.

    Deliberadamente NO apaga el modo por su cuenta: apagar una configuración
    que el usuario puso a propósito, sin avisar, es peor que operar con una
    advertencia clara. Lo que sí hace el sistema solo, y con datos reales, es
    frenar el scalping si pierde plata — eso lo decide el Cost-Validator con
    operaciones cerradas (u_aiops_watcher.validate_scalping_viability), no una
    verificación de arranque.
    """
    chequeos = []

    # 1) El bot vivo no usa Yahoo. Hasta que PPI entregue intradía suficiente,
    #    scalping se considera no habilitado y no abre entradas.
    chequeos.append(("Velas intradiarias contractuales de PPI", False,
                     "Yahoo quedó aislado para investigación; falta 1m/5m confiable de PPI"))

    # 2) Stream de tiempo real: con polling HTTP y 2 req/s, una vuelta de
    #    escaneo de scalping no cierra a tiempo.
    try:
        import x_ppi_websocket as ppi_stream
        estado = ppi_stream.get_stream_status()
        chequeos.append(("Precios en tiempo real (stream de PPI)", bool(estado.get("enabled")),
                         "con polling HTTP la ventana de scalping se cierra antes de poder operar"))
    except Exception as e:
        chequeos.append(("Precios en tiempo real (stream de PPI)", False, str(e)[:80]))

    # 3) Cierre de fin de día (si no, riesgo overnight sobre posiciones de minutos).
    chequeos.append(("Cierre forzado de fin de día", position_manager.EOD_CLOSE_ENABLED,
                     "sin esto una posición de 30 minutos puede quedar abierta toda la noche"))

    # 4) Validador de costos (que frene si el scalping no da neto positivo).
    import u_aiops_watcher as aiops
    chequeos.append(("Validador de rentabilidad neta (Cost-Validator)", aiops.SCALPING_COST_VALIDATOR_ENABLED,
                     "sin esto el bot puede operar meses a pérdida sin frenarse solo"))

    # 5) Universo con CEDEARs (el scalping sólo aplica a CEDEARs).
    hay_cedears = any(i.asset_class == "CEDEARS" for i in active_universe)
    chequeos.append(("CEDEARs en el universo activo", hay_cedears,
                     "el scalping sólo opera CEDEARs; sin ninguno en la lista no va a hacer nada"))

    # 6) Intervalo de escaneo coherente con el horizonte de la operación.
    coherente = SCALPING_SCAN_INTERVAL_SECONDS <= SCALPING_TARGET_HOLD_MINUTES * 60 / 4
    chequeos.append(("Intervalo de escaneo vs. horizonte de posición", coherente,
                     f"escaneás cada {SCALPING_SCAN_INTERVAL_SECONDS}s para posiciones de "
                     f"{SCALPING_TARGET_HOLD_MINUTES} min: revisá que la salida se detecte a tiempo"))

    fallidos = [(n, m) for n, ok, m in chequeos if not ok]
    lineas = ["⚡ *VERIFICACIÓN DEL MODO SCALPING*\n"]
    for nombre, ok, _ in chequeos:
        lineas.append(f"{'✅' if ok else '❌'} {nombre}")
    if fallidos:
        lineas.append("\n*Qué revisar:*")
        for nombre, motivo in fallidos:
            lineas.append(f"• {nombre}: {motivo}")
        lineas.append("\nEl proceso arranca para diagnóstico, pero las entradas de scalping "
                      "quedan bloqueadas mientras falte intradía contractual.")
    else:
        lineas.append("\nTodas las condiciones del modo scalping están dadas.")

    notifier.send_telegram("\n".join(lineas))
    logger.info("Preflight de scalping: %s de %s condiciones OK.",
                len(chequeos) - len(fallidos), len(chequeos))
    return {"total": len(chequeos), "fallidos": len(fallidos)}


def _eod_close_time():
    """Hora y minuto del cierre forzado de intradía: EOD_CLOSE_MINUTES_BEFORE
    minutos antes de MARKET_CLOSE_HOUR. Se calcula acá (y no como dos
    variables sueltas) para que mover el horario de la rueda arrastre
    automáticamente el cierre forzado — si fueran independientes, la próxima
    vez que cambie el horario del mercado alguien se olvidaría de mover
    una de las dos."""
    minutos_antes = int(os.getenv("EOD_CLOSE_MINUTES_BEFORE", "5"))
    total = MARKET_CLOSE_HOUR * 60 - minutos_antes
    return max(total // 60, 0), max(total % 60, 0)


def run_monthly_autotune_job():
    AutoTuner().run_monthly_autotune()
    load_tuned_thresholds()


def run_daily_backup_job():
    """NUEVO EN v13.0 — ver comentario en main() (scheduler.add_job).
    Delegado a y_infra_monitor.py, que además expone el estado de estos
    backups al dashboard (sección de infraestructura)."""
    import y_infra_monitor as infra_monitor
    infra_monitor.run_daily_backup()


# ---------------------------------------------------------------------- #
# NUEVO EN v11.0 — Poda mensual de datos de bajo valor histórico.
#
# Por qué existe: la tabla `signals` graba CADA evaluación de CADA
# instrumento (pase o no los filtros) — es la fuente que alimenta el
# diagnóstico semanal (s_learning_engine.py) y, a futuro, cualquier
# reentrenamiento. En modo scalping, con escaneos cada
# SCALPING_SCAN_INTERVAL_SECONDS, esta tabla puede acumular miles de filas
# por día. `global_news_247` y `daily_headlines` son el mismo caso: texto
# de noticias en bruto, útil para el día en que se generaron, pero sin
# valor a largo plazo una vez que ya se usaron.
#
# QUÉ NO SE TOCA NUNCA acá (a propósito): closed_trades,
# learning_diagnostics, daily_balance_snapshot, auto_tune_history,
# recommendations_log y risk_halts — son las tablas chicas (pocas filas
# por día) que sí tienen valor permanente para aprendizaje/backtest y para
# auditar decisiones pasadas. Nunca se borran automáticamente.
#
# SIGNALS_RETENTION_DAYS (default 365): filas de `signals` más viejas que
# esto se eliminan. daily_headlines/global_news_247 usan un límite fijo más
# corto (90 días) porque son solo insumo de la evaluación del día, no un
# registro de decisiones. Se corre una vez al mes, junto al auto-tuning,
# nunca durante el horario de rueda.
SIGNALS_RETENTION_DAYS = int(os.getenv("SIGNALS_RETENTION_DAYS", "365"))
NEWS_ARCHIVE_RETENTION_DAYS = int(os.getenv("NEWS_ARCHIVE_RETENTION_DAYS", "90"))


def run_monthly_data_retention_job():
    """Poda mensual de `signals` y del archivo crudo de noticias. Nunca
    toca closed_trades/learning_diagnostics/auto_tune_history/etc. Corre
    en VACUUM al final para que el espacio liberado en disco se refleje de
    verdad en el tamaño del archivo .db (SQLite no lo libera solo)."""
    conn = ac_db.connect_raw()
    c = conn.cursor()
    deleted = {}
    try:
        c.execute(
            "DELETE FROM signals WHERE timestamp < datetime('now', ?)",
            (f"-{SIGNALS_RETENTION_DAYS} days",),
        )
        deleted["signals"] = c.rowcount
        c.execute(
            "DELETE FROM daily_headlines WHERE date < date('now', ?)",
            (f"-{NEWS_ARCHIVE_RETENTION_DAYS} days",),
        )
        deleted["daily_headlines"] = c.rowcount
        c.execute(
            "DELETE FROM global_news_247 WHERE fetched_at < datetime('now', ?)",
            (f"-{NEWS_ARCHIVE_RETENTION_DAYS} days",),
        )
        deleted["global_news_247"] = c.rowcount
        conn.commit()
    finally:
        conn.close()

    # VACUUM necesita su propia conexión, fuera de cualquier transacción abierta.
    conn2 = ac_db.connect_raw()
    conn2.execute("VACUUM")
    conn2.close()

    logger.info(
        "Poda mensual de datos: %s filas de signals (>%sd), %s headlines/noticias (>%sd). "
        "closed_trades y learning_diagnostics no se tocan.",
        deleted.get("signals", 0), SIGNALS_RETENTION_DAYS,
        deleted.get("daily_headlines", 0) + deleted.get("global_news_247", 0),
        NEWS_ARCHIVE_RETENTION_DAYS,
    )


def market_open_job(ppi, notifier):
    load_tuned_thresholds()
    if not getattr(ppi, "is_authenticated", lambda: False)():
        logger.error("Apertura sin sesión PPI: se informa y el motor queda sin entradas nuevas.")
        notifier.send_telegram(
            "🔴 *PPI NO AUTENTICADO*\nEl bot sigue encendido para diagnóstico, pero no "
            "evaluará ni abrirá posiciones. El reintento de login está limitado a una vez por hora."
        )
        return
    refresh_universe_job(ppi)
    daily_report.send_market_open_email(ppi, notifier)


def market_close_job(ppi, notifier):
    daily_report.send_market_close_summary(ppi, notifier)
    # NUEVO EN v14.0 (Instrucción 9): parte diario de salud. Reemplaza la
    # revisión manual de logs que el Documento Maestro pedía hasta v13.
    log_watch.send_daily_health_digest(notifier)


def eod_close_job(ppi, notifier):
    """NUEVO EN v14.0 — cierre forzado de posiciones intradía unos minutos
    antes de la campana (hallazgo CRÍTICO de la auditoría v13: riesgo
    overnight sobre posiciones dimensionadas para minutos). Ver
    k_position_manager.close_all_intraday_positions()."""
    position_manager.close_all_intraday_positions(ppi, notifier, economics.calculate_trade_costs_fraction)


def log_watch_job(notifier):
    """NUEVO EN v14.0 (Instrucción 9) — vigilancia continua del log: manda a
    Telegram sólo lo que amerita atención, agrupado y sin repetir."""
    log_watch.scan_and_alert(notifier)


def process_restart_requests_job(notifier):
    """NUEVO EN v14.0 (Instrucción 1) — el único lugar del sistema que puede
    decidir un reinicio.

    El panel web NO reinicia nada: sólo graba una solicitud. Este job la
    encuentra ya confirmada por Telegram y, si el sistema está desocupado
    (sin órdenes en vuelo, sin cierres en curso y —si el cambio toca
    ENVIRONMENT— sin posiciones abiertas), dispara el apagado ordenado que
    ya existía desde v13.0. Docker levanta el proceso de nuevo y el .env
    actualizado entra en vigencia.

    Si el sistema NO está desocupado, no fuerza nada: vuelve a intentar en
    el próximo ciclo. Una operación en curso siempre gana sobre un
    reinicio — es exactamente lo pedido ("que el sistema esté totalmente
    desocupado para impedir que esté cancelando, cometiendo un error o
    dejando de lado una operación").
    """
    try:
        solicitud = env_guard.get_confirmed_pending_restart()
        if not solicitud:
            return
        idle, motivo = env_guard.is_system_idle(
            require_no_open_positions=bool(solicitud.get("env_switch"))
        )
        if not idle:
            logger.info("Reinicio confirmado en espera: %s", motivo)
            return
        env_guard.mark_applied(solicitud["id"])
        notifier.send_telegram(
            "♻️ *REINICIANDO EL BOT*\n"
            f"Aplicando los cambios de configuración: `{solicitud['changed_vars']}`\n"
            "El sistema estaba desocupado. Te aviso cuando vuelva a estar operativo."
        )
        logger.warning("Reinicio solicitado y confirmado (%s). Iniciando apagado ordenado.",
                       solicitud["changed_vars"])
        _shutdown_requested.set()
    except Exception as e:
        logger.error("Error procesando solicitudes de reinicio: %s", e)


def check_exits_job(ppi, notifier):
    position_manager.check_exits(ppi, notifier, economics.calculate_trade_costs_fraction, scalping=SCALPING_MODE)


def poll_confirmations(ppi, notifier):
    order_confirmation.process_button_taps(ppi, notifier, position_manager)


def confirmations_thread_loop(ppi, notifier):
    """
    CORRECCIÓN — auditoría 9.1 (rev. 1), hallazgo válido: antes, el
    chequeo de botones de Telegram vivía adentro del mismo loop que
    escanea instrumentos, así que una vuelta larga de escaneo (varios
    instrumentos, cada uno con varias llamadas a la API) podía demorar
    hasta un minuto en darse cuenta de que tocaste "Confirmar". Ahora
    corre en su propio hilo, totalmente aparte del escaneo de mercado,
    revisando cada POLL_CONFIRMATIONS_INTERVAL_SECONDS (default 4s) —
    mucho más responsivo, y no compite por tiempo con el análisis de
    instrumentos.
    """
    interval = int(os.getenv("POLL_CONFIRMATIONS_INTERVAL_SECONDS", "4"))
    while True:
        try:
            poll_confirmations(ppi, notifier)
        except Exception as e:
            logger.error("Error en hilo de confirmaciones: %s", e)
        time.sleep(interval)


# Permisos de la cuenta, resueltos una vez al construir el universo. Se
# guardan a nivel de módulo porque evaluate_instrument los necesita en cada
# vuelta y volver a sondearlos por instrumento sería otra tormenta de
# llamadas como la que se corrigió con el CCL y con las noticias.
_permisos_de_cuenta: dict = {}


def evaluate_instrument(inst, ppi, gemini, notifier, ccl_cached,
                        news_cached=None, macro_cached=None, contexto=None):
    ticker = inst.ticker

    if not getattr(ppi, "is_authenticated", lambda: False)():
        log_signal(ticker, "REJECTED_PPI_AUTH", "PPI no autenticado; operación bloqueada.")
        return

    if risk_guardian.is_halted():
        return  # kill switch activo: no se evalúan instrumentos nuevos hasta revisión manual

    # Mitigación parcial de correlación (hallazgo de auditorías anteriores:
    # AAPL/SPY/NVDA se mueven casi juntos). No es un análisis de correlación
    # real —eso requeriría calcular correlación de retornos entre todo el
    # universo, que se dejó afuera por alcance— pero limitar cuántas
    # posiciones puede tener abiertas el bot A LA VEZ evita que "tres
    # alertas el mismo día" se conviertan en una sola apuesta grande
    # disfrazada de tres independientes.
    if len(position_manager.get_open_positions()) >= MAX_OPEN_POSITIONS:
        log_signal(ticker, "REJECTED_MAX_POSITIONS",
                   f"Ya hay {MAX_OPEN_POSITIONS} posiciones abiertas (límite configurado).")
        return

    # NUEVO EN v10.5 — modo scalping (solo CEDEARs, únicos con datos 1M vía
    # yfinance; ver docstring de SCALPING_MODE más arriba).
    scalping_active = SCALPING_MODE and inst.asset_class == "CEDEARS"
    if scalping_active:
        tech = technical_engine.evaluate_technical_scalping(ticker)
    elif inst.asset_class == "CEDEARS":
        tech = technical_engine.evaluate_technical(ticker, ppi_client=ppi)
    else:
        # DECISIÓN DE EXPERTO v12.0 (pendiente #2, resuelta): se activa
        # deflact_ccl=True por default para TODO lo que pasa por esta rama
        # (ACCIONES/BONOS/ETF locales) — no es una activación arbitraria:
        # esta rama existe precisamente porque son instrumentos con
        # precio en pesos, así que la corrección de la auditoría v11
        # (evitar falsos breakouts por devaluación, no por movimiento
        # técnico real) aplica a los tres tipos por igual, sin necesidad
        # de una lista de excepciones. Probado en Fase D: sin esto, un
        # escenario de devaluación fuerte daba score técnico 1.0 (falso
        # positivo); deflactado, 0.0 (tendencia real).
        tech = technical_engine.evaluate_technical_local(
            ticker, ppi, inst.instrument_type, inst.settlement, deflact_ccl=True,
        )

    if not tech.data_ok or tech.score_tech < runtime_config["MIN_SCORE_TECH"]:
        log_signal(ticker, "REJECTED_TECH", tech.reason, score_tech=tech.score_tech)
        return

    mkt = ppi.get_market_data(ticker, inst.instrument_type, inst.settlement)
    if not mkt or (time.time() - mkt.get("epoch_recv", 0)) > 30:
        log_signal(ticker, "REJECTED_STALE_DATA", "Cotización PPI ausente o vieja (>30s).",
                   score_tech=tech.score_tech)
        return
    current_price = mkt.get("price", 0)
    if current_price <= 0:
        return

    # NUEVO EN v13.0 — hallazgo MEDIO de Simulación_y_pasos_a_tener_en_cuenta:
    # antes se pedía el book una sola vez con el settlement configurado por
    # instrumento; si ese plazo puntual venía vacío en Sandbox (común en
    # BONOS), se perdía la oportunidad aunque hubiera liquidez real en el
    # otro plazo. get_book_with_fallback() reintenta automáticamente contra
    # A-48HS antes de asumir que no hay liquidez.
    book = ppi.get_book_with_fallback(ticker, inst.instrument_type, inst.settlement)
    spread_pct = 0.0
    if book and book.get("offers") and book.get("bids"):
        best_bid = book["bids"][0]["price"]
        if best_bid > 0:
            spread_pct = round((book["offers"][0]["price"] - best_bid) / best_bid * 100, 4)

    # Para instrumentos locales, atr_1h ya viene expresado en unidades de precio diario (ver e_technical_engine).
    ref_price = tech.signals.get("1H", tech.signals.get("1D", tech.signals.get("5M"))).ema_fast if tech.signals else current_price
    atr_pct = (tech.atr_1h / ref_price) * 100 if tech.atr_1h and ref_price else 0
    expected_gross = atr_pct * runtime_config["TAKE_PROFIT_ATR_MULT"]

    # CORRECCIÓN v10.5 — auditoría 3, hallazgo "Redundancia Severa en el
    # Cálculo del Dólar CCL": antes se llamaba ppi.get_ccl_rate() (2
    # peticiones HTTP) EN CADA instrumento evaluado — con 40 instrumentos,
    # 80 llamadas por vuelta del loop solo para recalcular el mismo valor.
    # Ahora se calcula UNA vez por ciclo de escaneo en main() y se pasa
    # como parámetro (ccl_cached) a esta función.
    ccl = ccl_cached
    # v16.2 — los titulares y el contexto macro del día llegan cacheados desde
    # el bucle principal: no cambian entre un instrumento y el siguiente, y
    # pedirlos por instrumento agotaba la cuota del modelo en una hora.
    news_result = news_cached if news_cached is not None else fetch_latest_headlines()
    dec_ia = gemini.evaluate_macro_and_geopolitics(
        ticker, news_result, ccl or 0, contexto_macro=macro_cached)

    # NUEVO EN v13.0 — Monitor de Decisiones de IA del dashboard (pedido
    # explícito: "mostrar cómo funcionó el motor de decisiones de la IA en
    # cada una de las operaciones, con enfoque gerencial"). No hace falta
    # una tabla nueva: log_signal() ya graba CADA evaluación con su
    # macro_score y su motivo — acá se agrega únicamente log_ai_decision(),
    # que además guarda si hubo veto explícito, para que o_dashboard.py
    # pueda calcular tasa de aprobación/veto y score promedio sin tener
    # que inferirlo del texto de "reason".
    log_ai_decision(ticker, macro_score=dec_ia.get("macro_score"), veto_risk=bool(dec_ia.get("veto_risk")),
                     reason=dec_ia.get("reason_for_voice", ""))

    if dec_ia.get("veto_risk") or dec_ia.get("macro_score", 0) < runtime_config["MIN_SCORE_MACRO"]:
        log_signal(ticker, "REJECTED_MACRO", dec_ia.get("reason_for_voice", ""),
                   score_tech=tech.score_tech, macro_score=dec_ia.get("macro_score"))
        return

    # NUEVO EN v13.0 — Debilidad->código de la Autoauditoría v12.0
    # (Cost-Validator Halt, ver u_aiops_watcher.validate_scalping_viability
    # y Auditoria_version_12.pdf 4.2). Se chequea ANTES de gastar el resto
    # del pipeline en una oportunidad de scalping si las últimas
    # operaciones de scalping cerradas vienen dejando margen neto negativo
    # después de costos reales — frena temporalmente en vez de seguir
    # acumulando operaciones perdedoras por fricción.
    if scalping_active:
        recent_scalps = position_manager.get_recent_scalping_trades()
        if not aiops_scalping_validator(recent_scalps):
            log_signal(ticker, "REJECTED_SCALPING_COST_HALT",
                       "Margen neto reciente de scalping por debajo del mínimo — freno preventivo.",
                       score_tech=tech.score_tech, macro_score=dec_ia.get("macro_score"))
            return

    # En scalping, el horizonte de mantenimiento se mide en minutos, no
    # días — se prorratea el hurdle mensual contra esa fracción del mes.
    target_hold_days = (SCALPING_TARGET_HOLD_MINUTES / (60 * 24)) if scalping_active else TARGET_HOLD_DAYS

    hurdle = economics.get_dynamic_hurdle_rate_monthly(ppi, dec_ia.get("estimated_monthly_inflation_pct"))
    net_return = economics.calculate_net_return_pct(expected_gross, spread_pct)
    check = economics.passes_hurdle(net_return, max(int(round(target_hold_days)), 1) if not scalping_active else 1,
                                     hurdle["hurdle_monthly_pct"])
    if scalping_active:
        # passes_hurdle() prorratea por días enteros como mínimo (max(days,1));
        # para scalping se recalcula el piso directamente en la fracción de
        # mes real que representan los minutos configurados, en vez de forzarlo
        # a redondear a 1 día completo (que sería un piso artificialmente alto).
        hurdle_prorated = hurdle["hurdle_monthly_pct"] * (target_hold_days / 30)
        check = {"approved": net_return > hurdle_prorated, "net_return_pct": net_return,
                 "hurdle_prorated_pct": round(hurdle_prorated, 4), "margin_pct": round(net_return - hurdle_prorated, 4)}

    if not check["approved"]:
        log_signal(ticker, "REJECTED_LOW_NET", f"Neto {net_return}% no supera {check['hurdle_prorated_pct']}%",
                   score_tech=tech.score_tech, macro_score=dec_ia.get("macro_score"),
                   net_return=net_return, hurdle=hurdle["hurdle_monthly_pct"])
        return

    # NUEVO EN v12.0 — MODO SHADOW del grafo multi-agente (decisión de
    # experto, pendiente #1 resuelto). La operación ya fue aprobada por la
    # lógica secuencial real (la que manda). Acá, EN PARALELO y sin poder
    # afectar la ejecución (salvo que LANGGRAPH_MODE=gating, ver más abajo),
    # se le pide al grafo LangGraph (w_agent_graph.py) su propio veredicto
    # sobre la misma oportunidad — reutilizando el macro_result ya
    # calculado, sin llamar a Gemini una segunda vez.
    try:
        if AGENT_GRAPH_AVAILABLE:
            gating_mode = getattr(agent_graph, "LANGGRAPH_MODE", "shadow") == "gating"
            if gating_mode:
                # NUEVO EN v13.0 — opt-in, ver w_agent_graph.real_gating_evaluate.
                # No es el default: solo se activa si LANGGRAPH_MODE=gating
                # en el .env, una decisión explícita y documentada.
                graph_result = agent_graph.real_gating_evaluate(
                    ppi, notifier, gemini, risk_guardian, model_guardian,
                    ticker=ticker, instrument_type=inst.instrument_type,
                    technical_score=tech.score_tech, macro_result=dec_ia, ccl_rate=ccl or 0.0,
                )
                log_shadow_evaluation(ticker, graph_result.get("final_decision", ""), agreed=True, gating=True)
                if graph_result.get("final_decision") == "REJECT":
                    log_signal(ticker, "REJECTED_GRAPH_GATING", graph_result.get("risk_reason", ""),
                               score_tech=tech.score_tech, macro_score=dec_ia.get("macro_score"))
                    return
            else:
                shadow_result = agent_graph.shadow_evaluate(
                    ppi, notifier, gemini, risk_guardian, model_guardian,
                    ticker=ticker, instrument_type=inst.instrument_type,
                    technical_score=tech.score_tech, macro_result=dec_ia, ccl_rate=ccl or 0.0,
                )
                agreed = shadow_result.get("final_decision") != "REJECT"
                # NUEVO EN v13.0 — antes esta discrepancia solo quedaba en el
                # log de texto; ahora se persiste para que el dashboard
                # (sección SRE/monitoreo) pueda mostrar la tasa de acuerdo
                # real entre el grafo y la lógica secuencial, la evidencia
                # que hace falta acumular antes de un futuro cutover a
                # gating real (ver Documento Maestro, sección de roadmap).
                log_shadow_evaluation(ticker, shadow_result.get("final_decision", ""), agreed=agreed, gating=False)
                if not agreed:
                    logger.warning(
                        "MODO SHADOW: el grafo multi-agente HABRÍA RECHAZADO %s (motivo: %s) mientras "
                        "la lógica secuencial real lo aprobó. No se bloquea la operación real por esto "
                        "— se registra para revisar la discrepancia antes de conectar el grafo en serio.",
                        ticker, shadow_result.get("risk_reason"),
                    )
    except Exception as e:
        logger.error("Evaluación del grafo multi-agente falló para %s (no afecta la operación real "
                     "salvo que LANGGRAPH_MODE=gating haya fallado antes de decidir, en cuyo caso ya "
                     "se hizo fail-open — ver real_gating_evaluate): %s", ticker, e)

    stop_loss_price = round(current_price - current_price * (atr_pct / 100) * STOP_LOSS_ATR_MULT, 2)
    take_profit_price = round(current_price + current_price * (atr_pct / 100) * runtime_config["TAKE_PROFIT_ATR_MULT"], 2)

    balances = ppi.get_available_balance() or []
    available_ars = 0.0
    for b in balances:
        label = f"{b.get('name', '')} {b.get('simbol', '')}".upper()
        if "PESO" in label or "ARS" in label:
            available_ars = float(b.get("amount", 0))
            break
    # Se descuenta lo ya comprometido en otras propuestas pendientes de
    # confirmar, para no dimensionar dos alertas contra el mismo dinero.
    available_ars = max(available_ars - order_confirmation.get_reserved_capital_ars(), 0)
    # ======================================================================
    # DIMENSIONAMIENTO RAMIFICADO POR CLASE DE ACTIVO (v16.2)
    # ======================================================================
    # Hasta esta versión TODO se dimensionaba con la fórmula de renta
    # variable: cantidad = riesgo / (entrada − stop). Aplicada a una opción,
    # esa cuenta ignora el lote y no usa la prima como pérdida máxima. Con una
    # prima de $100, ATR 5% y lote 100, el sistema creía arriesgar el 1% de la
    # cuenta y comprometía del orden de dos mil veces esa cifra.
    #
    # La base del riesgo es ahora el PATRIMONIO (ver ax_equity), no el
    # efectivo disponible: con el efectivo como base, el riesgo por operación
    # se achicaba solo a medida que el capital se invertía, sin que ninguna
    # regla lo dijera. El efectivo sigue siendo el tope de lo que se puede
    # comprar, que es una restricción distinta.
    equity_base = ax_equity.base_de_riesgo(ppi, fallback_efectivo=available_ars) or available_ars

    if inst.asset_class in ("OPCIONES", "FUTUROS"):
        cap = universe.assess_derivative_eligibility(inst, ppi, _permisos_de_cuenta)
        if not cap.get("operable"):
            log_signal(ticker, cap.get("codigo") or "INSTRUMENTO_SIN_MODELO_DE_RIESGO",
                       cap.get("motivo", ""), score_tech=tech.score_tech)
            return
        spec = cap["spec"]

        # Tope de concentración en derivados. Aunque cada posición arriesgue
        # el 1%, el conjunto puede concentrar demasiado en productos con fecha
        # de caducidad.
        room = deriv.portfolio_room_for_derivatives(
            equity_base, _exposicion_en_derivados_ars(ppi))
        if room <= 0:
            log_signal(ticker, "TOPE_DERIVADOS_ALCANZADO",
                       "La exposición en derivados llegó al tope de concentración.",
                       score_tech=tech.score_tech)
            return

        if inst.asset_class == "OPCIONES":
            quantity = deriv.size_long_option(
                equity_base, spec, current_price, RISK_PCT_PER_TRADE)
        else:
            quantity = deriv.size_future(
                equity_base, spec, current_price, stop_loss_price,
                RISK_PCT_PER_TRADE, free_margin_ars=available_ars,
                initial_margin_per_contract=getattr(spec, "initial_margin", None))

        unidad = current_price * (spec.lot_size or 1)
        if unidad > 0:
            quantity = min(quantity, int(room // unidad))
        if quantity <= 0:
            log_signal(ticker, "CAPITAL_INSUFICIENTE",
                       "El riesgo permitido no alcanza para un contrato entero.",
                       score_tech=tech.score_tech)
            return
    else:
        quantity = economics.calculate_position_size(
            equity_base, current_price, stop_loss_price,
            RISK_PCT_PER_TRADE, spread_pct=spread_pct,
            asset_class=inst.asset_class, max_affordable_ars=available_ars)
        quantity = economics.cap_by_book_liquidity(quantity, book)

    # PORTÓN — NIVEL 3: si el contexto está degradado, se achica la posición.
    # El multiplicador del piso de rentabilidad ya se aplicó más arriba, sobre
    # el hurdle. Con la política por defecto de v16.2 esto casi nunca se
    # activa: los feeds caídos con macro fresca ya no degradan.
    if contexto is not None and contexto.size_factor < 1.0:
        quantity = int(quantity * contexto.size_factor)
        if quantity <= 0:
            log_signal(ticker, "CONTEXTO_DEGRADADO",
                       "Con el factor de degradación aplicado no queda una cantidad operable.",
                       score_tech=tech.score_tech)
            return

    # NUEVO EN v10.5 (segunda revisión) — límite de exposición por ticker:
    # si ya hay posiciones abiertas de este mismo instrumento, no se
    # permite que la nueva compra lleve el total invertido en ese ticker
    # por encima de MAX_EXPOSURE_PER_TICKER_PCT del capital total
    # disponible. No reemplaza un análisis de correlación completo (eso
    # sigue siendo una limitación documentada, ver FODA), pero evita el
    # caso más directo: "iban tres alertas seguidas del mismo papel y las
    # tres se aprobaron como si fueran independientes".
    total_capital_ars = available_ars + order_confirmation.get_reserved_capital_ars()
    if total_capital_ars > 0:
        current_exposure = risk_guardian.get_exposure_by_ticker(ticker)
        max_exposure_ars = total_capital_ars * (risk_guardian.MAX_EXPOSURE_PER_TICKER_PCT / 100)
        room_left_ars = max(max_exposure_ars - current_exposure, 0)
        max_qty_by_exposure = int(room_left_ars // current_price) if current_price > 0 else 0
        if max_qty_by_exposure < quantity:
            quantity = max_qty_by_exposure

    if quantity <= 0:
        log_signal(ticker, "REJECTED_NO_SIZE", "Cantidad calculada por riesgo es 0 (saldo insuficiente o dato no disponible).",
                   score_tech=tech.score_tech, macro_score=dec_ia.get("macro_score"))
        return

    reason_txt = dec_ia.get("reason_for_voice", "") or tech.reason

    if order_confirmation.ORDER_EXECUTION_MODE == "auto":
        # NUEVO EN v10.5 — pedido explícito del usuario: sin botón de
        # confirmación, ejecución directa (por default, en SANDBOX).
        win_rate_info = position_manager.get_win_rate(days_back=WIN_RATE_LOOKBACK_DAYS)
        ppi_order_id = order_confirmation.execute_directly(
            ppi, notifier, position_manager, ticker, quantity, current_price,
            stop_loss_price, take_profit_price, inst.instrument_type, inst.settlement,
            inst.asset_class, net_return, reason_txt, win_rate_info, scalping=scalping_active,
        )
        if ppi_order_id:
            log_signal(ticker, "EXECUTED_AUTO", "Aprobado Técnico + Macro + Hurdle, ejecutado automáticamente (SANDBOX)",
                       score_tech=tech.score_tech, macro_score=dec_ia.get("macro_score"),
                       net_return=net_return, hurdle=hurdle["hurdle_monthly_pct"])
        else:
            log_signal(ticker, "EXECUTION_FAILED", "Aprobado pero falló la ejecución automática en PPI",
                       score_tech=tech.score_tech, macro_score=dec_ia.get("macro_score"))
        return

    # Modo "confirm" (PRODUCTION por default): flujo histórico con botón.
    msg = (
        f"🔔 *RECOMENDACIÓN LÍMITE* ({inst.asset_class})\n"
        f"{ticker} @ ${current_price}\n"
        f"Cantidad sugerida (riesgo {RISK_PCT_PER_TRADE}% del capital): {quantity}\n"
        f"Stop-loss: ${stop_loss_price}  |  Take-profit: ${take_profit_price}\n"
        f"Neto Est.: +{net_return}% (Piso: {hurdle['hurdle_monthly_pct']}%)\n"
        f"Motivo: {reason_txt}\n"
        f"Tocá *Confirmar* para colocarla (vence en {order_confirmation.CONFIRMATION_TIMEOUT_MIN} min)."
    )
    order_id = order_confirmation.create_pending_order(
        ticker, quantity, current_price, stop_loss_price, take_profit_price,
        instrument_type=inst.instrument_type, settlement=inst.settlement,
    )
    notifier.send_telegram_confirmation(msg, order_id)
    log_signal(ticker, "EXECUTED_ALERT", "Aprobado Técnico + Macro + Hurdle, esperando confirmación",
               score_tech=tech.score_tech, macro_score=dec_ia.get("macro_score"),
               net_return=net_return, hurdle=hurdle["hurdle_monthly_pct"])


# NUEVO EN v13.0 — Apagado seguro (Graceful Shutdown), hallazgo de
# Testing_y_sugerencias_mockups.pdf ("El Problema: un reinicio abrupto
# puede matar el proceso justo entre la colocación de una orden y su
# registro en la base de datos local, dejando posiciones huérfanas").
#
# ADAPTADO al diseño real de j_main.py (no se adoptó la clase
# TradingOrchestrator completa sugerida en el mockup, que asume un loop
# de una sola operación atómica por vuelta — acá cada vuelta recorre
# TODO el universo de instrumentos, así que el chequeo de la señal se
# hace ENTRE instrumentos, no solo entre vueltas completas, para que un
# SIGTERM no tenga que esperar a terminar de escanear 40 tickers antes
# de cortar):
#   - SIGTERM/SIGINT solo levantan la bandera _shutdown_requested — no
#     terminan el proceso de golpe (evita cortar una orden a mitad de
#     colocarse).
#   - El loop principal revisa la bandera después de cada instrumento
#     evaluado y al final de cada vuelta; al detectarla, para el AIOps
#     watcher y el cliente WebSocket, loguea el cierre ordenado y recién
#     ahí sale con sys.exit(0).
#   - Deliberadamente NO se agregó un endpoint web que dispare este
#     shutdown (a diferencia del mockup "/api/restart" sugerido): eso
#     contradice una decisión de seguridad ya documentada en
#     o_dashboard.py desde v8.0 ("darle a un proceso web la capacidad de
#     reiniciar servicios del sistema es un salto de privilegios que no
#     se justifica"). El shutdown graceful protege igual contra
#     reinicios de Docker/systemd/`docker stop`, que sí mandan SIGTERM
#     de por sí — sin necesidad de exponer un botón nuevo.
_shutdown_requested = threading.Event()
_runtime_heartbeat = bb_runtime_status.BotHeartbeat()


def _handle_shutdown_signal(signum, frame):
    logger.warning("Señal %s recibida — se completa la vuelta de escaneo en curso y se corta ordenadamente.",
                    signal.Signals(signum).name)
    _runtime_heartbeat.set_state("STOPPING", detail=f"Senal {signum} recibida.")
    _shutdown_requested.set()


def main():
    _runtime_heartbeat.start("STARTING", detail="Inicializando motor de trading.")
    bb_runtime_status.write_config_presence()
    # NUEVO EN v13.0 — se registra antes que nada más, junto al hook de
    # introspección, para que un SIGTERM llegue en cualquier punto del
    # arranque o del loop y siempre encuentre el handler instalado.
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    # NUEVO EN v12.0 (Instrucción 5) — se instala PRIMERO, antes de
    # cualquier otra inicialización, para capturar también un crash
    # durante el arranque (ej. falla de init_db) y no solo durante el
    # loop en régimen. El notifier se resuelve dentro del hook en
    # tiempo de ejecución vía la clausura de install(), así que se
    # re-instala una vez que el notifier real existe más abajo.
    import m_introspection_engine as introspection_engine
    introspection_engine.install(notifier=None)

    init_db()
    load_tuned_thresholds()
    notifier = MultiChannelNotifier()

    # Arranque autónomo: el dashboard queda disponible, pero el motor pesado
    # (PPI, Gemini, streams y universo) no se inicializa en fines de semana,
    # feriados ni antes de la ventana previa a la apertura.
    import ao_startup_gate as startup_gate
    modo = None
    if market_startup.AUTO_START_ENABLED:
        ultimo_motivo = None
        while not _shutdown_requested.is_set():
            listo, motivo = market_startup.evaluar_ventana()
            if listo:
                break
            if motivo != ultimo_motivo:
                _runtime_heartbeat.set_state("WAITING_OPEN", detail=motivo)
                startup_gate.marcar_espera_calendario(motivo)
                logger.info("Arranque automático en espera: %s", motivo)
                ultimo_motivo = motivo
            _shutdown_requested.wait(60)
        if _shutdown_requested.is_set():
            return
        modo = market_startup.modo_automatico()
        startup_gate.autorizar(modo, origen="calendario_BYMA")
        logger.info("Calendario BYMA habilitó el arranque automático en modo %s.", modo)

    _runtime_heartbeat.set_state("INITIALIZING", mode=str(modo or ""),
                                 detail="Inicializando servicios operativos.")
    ppi = ResilientPPIClient(notifier)
    gemini = GeminiDecisionEngine()

    # Re-instala el hook ahora que existe un notifier real, para que los
    # crashes críticos posteriores al arranque manden aviso por Telegram
    # además de activar el kill switch.
    introspection_engine.install(notifier=notifier)

    # ------------------------------------------------------------------ #
    # NUEVO EN v16.0 — PORTÓN DE ARRANQUE
    # ------------------------------------------------------------------ #
    # El bot ya no empieza a operar por el solo hecho de que el contenedor
    # levantó. Acá se detiene y espera una autorización explícita por
    # Telegram (o desde el panel, si Telegram no responde).
    #
    # La ubicación de estas líneas es deliberada: van DESPUÉS de tener
    # notificador —hace falta para poder preguntar— pero ANTES de suscribirse
    # al stream de precios, de construir el grafo de agentes y de arrancar el
    # planificador. Si se preguntara más abajo, el bot ya estaría consumiendo
    # cuota del bróker y manteniendo un socket abierto sin haber sido
    # autorizado, que es justo lo que se quiere evitar en un entorno de
    # pruebas que se reconstruye a cada rato.
    # Escucha de comandos de Telegram. Arranca ANTES del portón: si el bot va
    # a quedar esperando autorización, la parada de emergencia y el comando de
    # estado tienen que responder igual durante esa espera.
    # v16.2 — el sondeo durante el arranque ya no abre un getUpdates propio:
    # usa el consumidor único de l_order_confirmation y se apaga en cuanto el
    # bucle principal toma el control. Dos lectores simultáneos se comen los
    # mensajes del otro, y ese era el defecto que rompía las confirmaciones.
    import threading as _th
    _fin_bombeo = _th.Event()
    _hilo_bombeo = order_confirmation.arrancar_bombeo(notifier, ppi, _fin_bombeo)

    # La misma escucha continúa durante TODA la inicialización posterior.
    # Antes se apagaba inmediatamente después de autorizar y se reanudaba
    # varios minutos más tarde, después de llamadas al bróker. En esa ventana
    # PARADA y ESTADO no respondían, justamente cuando más se los necesita.
    if modo is None:
        modo = startup_gate.esperar_autorizacion(notifier)
    if modo == startup_gate.MODO_DETENIDO:
        logger.info("Arranque no autorizado. El bot queda levantado sin operar; "
                    "el panel sigue accesible en la solapa de Testing.")
        while not _shutdown_requested.is_set():
            time.sleep(5)
            if startup_gate.puede_operar():
                logger.info("Llegó la autorización: se continúa con el arranque.")
                break
        else:
            return
    if startup_gate.esta_en_simulacion():
        logger.warning("MODO SIMULACIÓN: el recorrido es completo pero ninguna orden "
                       "sale al mercado real. Cada paso queda registrado en /testing.")

    # NUEVO EN v13.0 — Solución de Notificación Crítica sugerida en
    # Sugerencias_mockup_versión_2.pdf: cualquier trigger_halt() (kill
    # switch, sea manual, por riesgo o por el motor SRE) manda AHORA un
    # aviso inmediato y de alta prioridad por Telegram, además de quedar
    # grabado en risk_halts. p_risk_guardian.py sigue siendo un módulo
    # "sin dependencias" (ver su ficha técnica) — el notifier se registra
    # acá como un callback opcional, nunca como un import duro, así que
    # el módulo sigue funcionando solo (y sus propios tests) sin notifier.
    risk_guardian.set_critical_notifier(notifier.notify_critical_event)

    # ======================================================================
    # NUEVO EN v15.0 — cableado de los cuatro subsistemas nuevos
    # ======================================================================
    # (a) Herramientas de mercado para Function Calling: el motor de IA ya no
    #     razona sobre precios recordados, los pide y los recibe de PPI.
    market_tools.bind_ppi_client(ppi)
    gemini.set_notifier(notifier)
    model_registry.resolve_model(notifier=notifier, forzar=True)
    model_registry.ModelHealthWatcher(notifier=notifier).start()

    # (b) Drenaje seguro del kill switch. Se registra ANTES de cualquier cosa
    #     que pueda dispararlo, para que ningún corte quede sin reconciliar.
    #     El lambda ata las dependencias que p_risk_guardian no conoce (y no
    #     debe conocer: es un módulo sin dependencias por diseño).
    risk_guardian.set_drain_callback(
        lambda motivo: ks_supervisor.drain_and_reconcile(
            ppi, notifier, position_manager, motivo)
    )

    # (c) Vigilancia de cambios en la API de PPI (PyPI + docs + esquema real).
    ppi_api_watch.PPIAPIWatcher(notifier=notifier).start()

    # (d) Series macro históricas: primer refresco al arrancar, en un hilo
    #     aparte para no demorar el arranque si el BCRA está lento.
    threading.Thread(target=lambda: macro_history.refresh(),
                     name="macro_refresh_inicial", daemon=True).start()

    # NUEVO EN v12.0 (Instrucción 6) — AIOps: Isolation Forest en un hilo
    # aparte, midiendo latencia real hacia PPI (ping liviano a AL30),
    # RAM del proceso (vía resource.getrusage, sin agregar psutil como
    # dependencia nueva) y slippage de las últimas operaciones cerradas.
    import u_aiops_watcher as aiops_watcher

    def _current_metrics():
        # Fuera de rueda no se quema cuota ni se fuerza una autenticación PPI
        # sólo para medir latencia. El watcher volverá a medir al abrir.
        if not _mercado_abierto():
            return None
        try:
            import resource
            estado_ppi = getattr(ppi, "auth_status", lambda: {})()
            if not estado_ppi.get("authenticated"):
                return None
            latency_ms = estado_ppi.get("last_call_latency_ms")
            if latency_ms is None:
                return None
            ram_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            recent_slippage = position_manager.get_recent_avg_slippage_pct() \
                if hasattr(position_manager, "get_recent_avg_slippage_pct") else 0.0
            return {"latency_ms": latency_ms, "ram_mb": ram_mb, "slippage_pct": recent_slippage}
        except Exception as e:
            logger.warning("AIOps: no se pudieron obtener métricas en este ciclo: %s", e)
            return None

    def _on_anomaly(reason: str):
        # CORREGIDO EN v13.0 — BUG REAL de la autoauditoría de v12.0: la
        # Bitácora v12.0 (entrada 15, "Bug 2") documentaba haber agregado
        # force_circuit_open() en c_ppi_client.py justamente para eliminar
        # el acceso directo a los atributos "privados" _circuit_state/
        # _circuit_opened_at desde afuera de la clase — pero este callback
        # (_on_anomaly, el único punto real donde ese acceso ocurría)
        # nunca se actualizó para usar el método nuevo. El bug que la
        # autoauditoría decía haber cerrado seguía presente en el único
        # lugar que importaba.
        # Una anomalía estadística aislada no demuestra una caída del broker.
        logger.warning("AIOps registró una anomalía sin modificar el circuito: %s", reason)
        # NUEVO EN v13.0 — se persiste también como anomalía de AIOps (no
        # solo como evento de Circuit Breaker) para que la sección
        # SRE/Monitoreo del dashboard pueda mostrar ambos por separado.
        import c_ppi_client
        c_ppi_client._persist_system_event("AIOPS", "ANOMALY_DETECTED", reason)

    watcher = aiops_watcher.AIOpsWatcher(on_anomaly=_on_anomaly, metrics_fn=_current_metrics)
    watcher.start()

    # NUEVO EN v12.0 (decisión de experto, pendiente #5 resuelto) — cliente
    # WebSocket opcional (apagado por default vía PPI_WEBSOCKET_ENABLED,
    # ver x_ppi_websocket.py). Se arranca temprano para que la caché tenga
    # ticks apenas empiece el escaneo; refresh_universe_job() más abajo ya
    # resuelve la lista de tickers a suscribir.
    import x_ppi_websocket as ppi_ws
    global ws_client
    ws_client = ppi_ws.PPIRealTimeClient(ppi_client=ppi, notifier=notifier)
    # RESUELTO EN v14.0: el límite honesto que estaba anotado acá en v12/v13
    # ("la lista de tickers queda vacía en este punto") ya no existe. El
    # cliente se arranca DESPUÉS de resolver el universo del día
    # (refresh_universe_job, más abajo), y refresh_universe_job vuelve a
    # suscribirlo cada mañana cuando el universo cambia.

    # CORRECCIÓN DE LA AUDITORÍA 8.1 (rev. 2), aceptada: si el proceso se
    # había caído justo después de colocar una orden real en PPI pero
    # antes de terminar de registrarla, esto retoma la vigilancia de esa
    # posición en vez de dejarla perdida.
    order_confirmation.recover_orphaned_orders(ppi, notifier, position_manager)
    risk_guardian.check_persisted_halt_on_startup()
    # NUEVO EN v15.0 — supervisor con criterio propio: recupera solo los
    # cortes TÉCNICOS (con verificaciones reales y techo diario) y deja los
    # financieros esperando un toque de botón en Telegram. Ver el docstring
    # de ag_kill_switch_supervisor.py para por qué esa línea divisoria.
    ks_supervisor.KillSwitchSupervisor(ppi, notifier, position_manager).start()
    if risk_guardian.is_halted():
        notifier.send_telegram(
            f"🛑 El bot arrancó pero el kill switch seguía activo de antes del reinicio "
            f"({risk_guardian.halt_reason()}). No va a evaluar instrumentos nuevos hasta "
            "que lo liberes a mano con r_clear_kill_switch.py."
        )

    if _mercado_abierto():
        refresh_universe_job(ppi)  # carga inicial si hay rueda; luego, cada apertura
    else:
        logger.info("Mercado cerrado: la carga del universo PPI se difiere hasta la próxima apertura.")

    # NUEVO EN v14.0 — recién ahora, con el universo ya resuelto, se abre el
    # stream de tiempo real (market data + notificaciones de cuenta). Ver
    # x_ppi_websocket.py: si falla o si la librería no lo soporta, el bot
    # sigue funcionando con polling HTTP sin enterarse.
    ws_client.start()

    if SCALPING_MODE:
        # NUEVO EN v14.0 (Instrucción 11) — ver scalping_preflight().
        try:
            scalping_preflight(notifier)
        except Exception as e:
            logger.warning("No se pudo completar la verificación de scalping: %s", e)

    # Transferencia sin solapamiento entre la escucha de arranque y la
    # escucha permanente. Se espera a que el primer hilo termine antes de
    # iniciar el segundo: sigue existiendo un único consumidor de getUpdates.
    _fin_bombeo.set()
    _hilo_bombeo.join(timeout=5)
    threading.Thread(target=confirmations_thread_loop, args=(ppi, notifier), daemon=True).start()

    scheduler = BackgroundScheduler(timezone=SERVER_TIMEZONE)
    # Backups, macro, noticias e informes corren en az_maintenance_scheduler,
    # propiedad del supervisor siempre activo. Aquí quedan únicamente tareas
    # que necesitan al motor bursátil y sus clientes ya inicializados.
    scheduler.add_job(market_open_job, "cron", day_of_week="mon-fri", hour=MARKET_OPEN_HOUR, minute=0,
                       timezone=SERVER_TIMEZONE, args=[ppi, notifier])
    scheduler.add_job(market_close_job, "cron", day_of_week="mon-fri", hour=MARKET_CLOSE_HOUR, minute=5,
                       timezone=SERVER_TIMEZONE, args=[ppi, notifier])
    # ------------------------------------------------------------------ #
    # NUEVO EN v14.0
    # ------------------------------------------------------------------ #
    # 1) Cierre forzado de posiciones intradía antes de la campana
    #    (hallazgo CRÍTICO de la auditoría v13 — riesgo overnight).
    eod_hour, eod_minute = _eod_close_time()
    scheduler.add_job(eod_close_job, "cron", day_of_week="mon-fri", hour=eod_hour, minute=eod_minute,
                       timezone=SERVER_TIMEZONE, args=[ppi, notifier])
    # 2) Vigilancia automática de logs -> Telegram (Instrucción 9): reemplaza
    #    la revisión manual que el usuario no puede hacer.
    scheduler.add_job(log_watch_job, "interval", minutes=log_watch.LOG_WATCH_INTERVAL_MINUTES,
                       args=[notifier])
    # 3) Guardián de reinicios pedidos desde el panel web y confirmados por
    #    Telegram (Instrucción 1). Corre seguido porque, una vez confirmado,
    #    el reinicio tiene que salir apenas el sistema quede libre.
    scheduler.add_job(process_restart_requests_job, "interval",
                       seconds=int(os.getenv("RESTART_CHECK_INTERVAL_SECONDS", "30")),
                       args=[notifier])
    scheduler.start()

    logger.info(
        "Bot Autónomo iniciado (huso horario scheduler: %s, umbrales: %s, "
        "ORDER_EXECUTION_MODE=%s, SCALPING_MODE=%s)...",
        SERVER_TIMEZONE, runtime_config, order_confirmation.ORDER_EXECUTION_MODE, SCALPING_MODE,
    )
    while not _shutdown_requested.is_set():
        try:
            # Fuera de rueda se corta ANTES de cualquier consulta operativa al
            # bróker. check_exits_job() y el guardián de riesgo consultan PPI;
            # ejecutarlos primero convertía cada domingo en una tormenta de
            # reintentos y evitaba registrar MERCADO_CERRADO.
            market_is_open = _mercado_abierto()
            if market_is_open:
                _runtime_heartbeat.set_state("RUNNING", mode=str(modo or ""),
                                             detail="Motor activo dentro de rueda.")
            else:
                _runtime_heartbeat.set_state("WAITING_OPEN", mode=str(modo or ""),
                                             detail="Mercado cerrado; motor en espera.")
            if not market_is_open:
                g0 = gate.check_session_health(
                    kill_switch_active=risk_guardian.is_halted(),
                    broker_session_ok=True,
                    db_ok=ac_db.healthcheck().get("ok", False),
                    market_open=False,
                    clock_drift_seconds=0.0)
                logger.warning("Rueda detenida por el portón: %s (%s)", g0.reason, g0.code)
                registrar_abstencion_global(g0.code, g0.reason)
                time.sleep(60)
                continue

            check_exits_job(ppi, notifier)
            risk_guardian.check_and_halt_if_needed(ppi, notifier)

            # ==============================================================
            # PORTÓN OPERATIVO — NIVEL 0 (cableado en v16.2)
            # ==============================================================
            # El catálogo de 29 casos existía, estaba bien escrito y bien
            # testeado, y no lo importaba ningún módulo del ciclo de trading.
            # En términos operativos eso significaba que este while corría
            # las 24 horas: el bot evaluaba fuera de rueda —quemando cuota de
            # PPI y del modelo, y pudiendo intentar cerrar posiciones con
            # precios de ayer—, no frenaba antes del cierre y no detectaba un
            # reloj corrido, que es justamente lo que vuelve inservible el
            # control de cotización vieja.
            g0 = gate.check_session_health(
                kill_switch_active=risk_guardian.is_halted(),
                broker_session_ok=bool(getattr(ppi, "logged_in", True)),
                db_ok=ac_db.healthcheck().get("ok", False),
                market_open=market_is_open,
                clock_drift_seconds=_desfasaje_de_reloj(ppi))
            if not g0.allow:
                logger.warning("Rueda detenida por el portón: %s (%s)", g0.reason, g0.code)
                registrar_abstencion_global(g0.code, g0.reason)
                time.sleep(60)
                continue

            # ==============================================================
            # PORTÓN OPERATIVO — NIVEL 1
            # ==============================================================
            # No se ABRE nada nuevo. Lo abierto se sigue cuidando: check_exits_job
            # ya corrió arriba, antes de este chequeo, y eso es deliberado.
            equity_base = ax_equity.base_de_riesgo(ppi)
            diario = ax_equity.perdida_diaria_pct(ppi)
            dd = ax_equity.drawdown_pct(ppi)
            g1 = gate.check_open_conditions(
                open_positions=len(position_manager.get_open_positions()),
                max_open_positions=MAX_OPEN_POSITIONS,
                daily_loss_pct=diario.get("pct"),
                max_daily_loss_pct=float(os.getenv("MAX_DAILY_LOSS_PCT", "1.0")),
                drawdown_pct=dd.get("pct"),
                max_drawdown_pct=float(os.getenv("MAX_DRAWDOWN_PCT", "5.0")),
                available_capital_ars=equity_base or 0.0,
                min_capital_ars=float(os.getenv("MIN_CAPITAL_ARS", "0")),
                minutes_to_close_value=gate.minutes_to_close(),
                no_open_minutes_before_close=float(
                    os.getenv("NO_OPEN_MINUTES_BEFORE_CLOSE", "20")),
                ai_engine_available=model_registry.get_status().get("modelo_activo") is not None,
                scalping_cost_brake=_freno_de_costos_scalping(),
                derivatives_cap_reached=_tope_derivados_alcanzado(ppi))
            if not g1.allow:
                logger.info("No se abren posiciones nuevas: %s (%s)", g1.reason, g1.code)
                registrar_abstencion_global(g1.code, g1.reason)
                time.sleep(60)
                continue
            # CORRECCIÓN v10.5 — auditoría 3, hallazgo "Redundancia Severa
            # en el Cálculo del Dólar CCL": se calcula UNA sola vez acá,
            # al principio de cada vuelta del loop, y se pasa a cada
            # evaluate_instrument() — antes eran 2 llamadas HTTP POR
            # INSTRUMENTO evaluado en la misma vuelta (hasta 80 llamadas
            # por ciclo con 40 instrumentos), todas para el mismo valor.
            ccl_cached = ppi.get_ccl_rate()

            # NUEVO EN v14.0 — prefetch concurrente de velas (ver
            # e_technical_engine.prefetch_bars). Se bajan en paralelo todas
            # las series que la vuelta va a necesitar ANTES de empezar a
            # evaluar; después, cada evaluate_technical() las encuentra en
            # caché y la evaluación pasa a ser cálculo local. Sin esto, con
            # 40 instrumentos la vuelta se iba minutos sólo esperando red,
            # que era el hallazgo de la auditoría sobre bucles bloqueantes.
            try:
                cedears = [i.ticker for i in active_universe if i.asset_class == "CEDEARS"]
                if (cedears and
                        technical_engine.TECHNICAL_DATA_SOURCE_CEDEARS != "ppi" and
                        not technical_engine.YFINANCE_SHADOW_ONLY):
                    technical_engine.prefetch_bars(cedears, scalping=SCALPING_MODE)
            except Exception as e:
                # El prefetch es una optimización: si falla, cada instrumento
                # baja sus datos como siempre. Nunca puede frenar la vuelta.
                logger.warning("Prefetch técnico falló (se sigue con descarga individual): %s", e)

            # ==============================================================
            # NOTICIAS Y CONTEXTO MACRO: UNA VEZ POR VUELTA (v16.2)
            # ==============================================================
            # fetch_latest_headlines() y la llamada de contexto al modelo
            # estaban DENTRO de evaluate_instrument, es decir una vez por
            # instrumento y por vuelta. Con 40 instrumentos y una vuelta cada
            # cuatro minutos eso son unos 600 barridos de RSS y hasta 600
            # llamadas al modelo por hora. Los proveedores de RSS cortan por
            # abuso, y ese volumen excede con holgura cualquier capa gratuita.
            # Cuando la cuota se agota caen el motor principal y el de
            # respaldo, se activa MOTOR_IA_SIN_RESPALDO y el bot deja de
            # abrir posiciones — sin que haya pasado absolutamente nada en el
            # mercado.
            #
            # Es exactamente el mismo patrón que este archivo ya había
            # corregido para el CCL en una versión anterior, cuando eran 80
            # llamadas por vuelta. Los titulares no cambian entre un
            # instrumento y el siguiente; el contexto macro del día, tampoco.
            news_cached = fetch_latest_headlines()
            save_headlines(news_cached)
            macro_cached = gemini.evaluar_contexto_macro_del_dia(news_cached, ccl_cached or 0)

            # PORTÓN — NIVEL 3: degradación por contexto. Se evalúa una vez
            # por vuelta, con el resultado ya cacheado.
            ctx = gate.evaluate_news_context(
                news_cached,
                macro_age_hours=macro_history.antiguedad_horas(),
                blackout_minutes=_minutos_de_apagon(news_cached),
                calendar_event_active=_evento_de_calendario_activo())
            if not ctx.allow:
                logger.info("No se abren posiciones nuevas: %s (%s)", ctx.reason, ctx.code)
                registrar_abstencion_global(ctx.code, ctx.reason)
                time.sleep(60)
                continue
            for nota in ctx.notes:
                logger.info("Contexto: %s", nota)

            scan_interval = SCALPING_SCAN_INTERVAL_SECONDS if SCALPING_MODE else 5
            for inst in active_universe:
                if _shutdown_requested.is_set():
                    break  # NUEVO EN v13.0 — corta ENTRE instrumentos, no solo entre vueltas completas
                evaluate_instrument(inst, ppi, gemini, notifier, ccl_cached,
                                    news_cached=news_cached, macro_cached=macro_cached,
                                    contexto=ctx)
                time.sleep(scan_interval)
            if _shutdown_requested.is_set():
                break
            time.sleep(SCALPING_SCAN_INTERVAL_SECONDS if SCALPING_MODE else 30)  # respiro entre vueltas de escaneo
        except Exception as e:
            logger.exception("Error main loop: %s", e)
            notifier.notify_error(f"Falla bucle: {e}")
            time.sleep(60)

    # NUEVO EN v13.0 — cierre ordenado: se completó la evaluación en curso
    # (o se cortó entre instrumentos, nunca a mitad de colocar una orden —
    # eso lo protege l_order_confirmation.py con su propio flujo atómico),
    # se para el scheduler y los hilos de fondo, y recién ahí se sale.
    logger.info("Apagado ordenado en curso: deteniendo scheduler, AIOps watcher y cliente WebSocket...")
    scheduler.shutdown(wait=False)
    watcher.stop()
    ws_client.stop()
    logger.info("Bot detenido de forma segura (shutdown graceful). Docker/systemd puede reiniciar el contenedor.")
    sys.exit(0)


if __name__ == "__main__":
    main()
