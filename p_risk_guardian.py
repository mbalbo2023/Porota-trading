"""
p_risk_guardian.py — Kill switch automático (v8.0)

INCORPORA el hallazgo de las auditorías 7.1/7.4: un sistema que opera con
dinero real necesita un corte automático "hard-coded" ante pérdidas
anormales — no alcanza con que el sistema "sea conservador" en el papel.
La referencia regulatoria que citan las auditorías es real: la SEC exige
desde 2010 (Regla 15c3-5, "Market Access Rule") controles de riesgo
PRE-TRADE bajo control directo del bróker/operador, y el caso SEC vs.
Knight Capital (2013) — una pérdida de más de US$460 millones en 45
minutos por un algoritmo sin corte automático — es la referencia de por
qué "un humano mirando" no es un control válido.

Qué hace: antes de evaluar cualquier instrumento nuevo, chequea tres
condiciones. Si alguna se cumple, el bot deja de emitir alertas nuevas
(las posiciones abiertas se siguen monitoreando para poder cerrarlas) y
avisa por Telegram. Reactivar requiere revisar el .env a mano — a
propósito no se reactiva solo, porque el objetivo es forzar una revisión
humana después de un evento anormal.
"""

import os
import logging
import sqlite3
from datetime import date, datetime, timedelta
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)
import ax_equity  # NUEVO EN v16.2 — patrimonio real (efectivo + tenencia)

logger = logging.getLogger("risk_guardian")
DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")

MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "1.0"))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "5.0"))
MAX_CONSECUTIVE_STOP_LOSSES = int(os.getenv("MAX_CONSECUTIVE_STOP_LOSSES", "3"))
# NUEVO EN v10.5 (segunda revisión) — ver check_and_halt_if_needed(), punto 4.
CCL_INTRADAY_CIRCUIT_BREAKER_PCT = float(os.getenv("CCL_INTRADAY_CIRCUIT_BREAKER_PCT", "3.0"))
# NUEVO EN v10.5 (segunda revisión) — tope de capital por ticker, ver
# get_exposure_by_ticker() y su uso en j_main.py.
MAX_EXPOSURE_PER_TICKER_PCT = float(os.getenv("MAX_EXPOSURE_PER_TICKER_PCT", "40.0"))

_halted = False
_halt_reason = None
# NUEVO EN v13.0 — ver set_critical_notifier()/trigger_halt() más abajo.
_critical_notifier_callback = None
# NUEVO EN v15.0 — ver set_drain_callback()/_record_halt() más abajo.
_drain_callback = None


def set_drain_callback(callback):
    """NUEVO EN v15.0. Registra la función que se ejecuta en el instante del
    corte para dejar el sistema sin operaciones a mitad de camino (cancelar
    propuestas, reconciliar órdenes enviadas a PPI, comparar posiciones
    contra el bróker). j_main.py registra acá
    ag_kill_switch_supervisor.drain_and_reconcile con sus dependencias ya
    atadas. Mismo patrón que set_critical_notifier: callback opcional, no
    import duro, así este módulo sigue sin dependencias."""
    global _drain_callback
    _drain_callback = callback


def set_critical_notifier(callback):
    """
    NUEVO EN v13.0 — Sugerencias_mockup_versión_2.pdf, "Solución de
    Notificación Crítica": cualquier trigger_halt() manda ahora un aviso
    de alta prioridad de inmediato, no solo cuando check_and_halt_if_needed
    corre en la próxima vuelta del loop.

    Se implementa como callback OPCIONAL registrado en runtime (igual
    patrón que on_anomaly en u_aiops_watcher.AIOpsWatcher) en vez de un
    import directo de b_notifiers, para que este módulo siga cumpliendo
    su propio diseño documentado ("Depende de: (ninguno)" — ver ficha
    técnica en el Documento Maestro) y sus propios tests puedan seguir
    corriendo sin necesitar un notifier real. j_main.py lo registra una
    vez al arrancar, con notifier.notify_critical_event.
    """
    global _critical_notifier_callback
    _critical_notifier_callback = callback


def _init_table():
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS risk_halts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            triggered_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reason TEXT,
            cleared INTEGER DEFAULT 0,
            cleared_at DATETIME
        )
    """)
    # NUEVO EN v10.5 (segunda revisión) — guarda la primera lectura del
    # CCL de cada día, para poder medir cuánto se movió durante la rueda
    # (ver check_and_halt_if_needed(), punto 4).
    c.execute("""
        CREATE TABLE IF NOT EXISTS ccl_intraday_snapshot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            ccl REAL
        )
    """)
    conn.commit()
    conn.close()


def is_halted() -> bool:
    return _halted


def halt_reason() -> str:
    return _halt_reason or ""


def check_persisted_halt_on_startup():
    """
    CORRECCIÓN — auditoría 9.1 (rev. 1) señaló, con otro razonamiento, un
    problema real: antes, si systemd reiniciaba el proceso por cualquier
    motivo (caída del servidor, error no controlado, actualización de
    código) mientras el kill switch estaba activo, la marca "activado"
    vivía solo en una variable de Python en memoria — se perdía sola en
    cada reinicio, y el bot volvía a operar sin que nadie lo hubiera
    revisado. Ahora el corte queda grabado en la base (tabla risk_halts)
    y, al arrancar, si hay un corte sin resolver (cleared=0), el bot
    arranca IGUAL DE DETENIDO hasta que alguien lo libere a mano — un
    reinicio del proceso ya no es una forma accidental de "reactivar
    solo" el sistema.
    """
    global _halted, _halt_reason
    _init_table()
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("SELECT reason FROM risk_halts WHERE cleared = 0 ORDER BY triggered_at DESC LIMIT 1")
    row = c.fetchone()
    conn.close()
    if row:
        _halted = True
        _halt_reason = f"(persistido desde antes del reinicio) {row[0]}"
        logger.critical("Kill switch seguía activo de antes del reinicio: %s", _halt_reason)


def clear_halt():
    """Libera el corte a mano. Se llama explícitamente (por ejemplo, desde
    una consola de Python o un pequeño script aparte) después de haber
    revisado qué pasó — nunca se llama sola desde el propio bot."""
    global _halted, _halt_reason
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("UPDATE risk_halts SET cleared = 1, cleared_at = CURRENT_TIMESTAMP WHERE cleared = 0")
    conn.commit()
    conn.close()
    _halted = False
    _halt_reason = None
    logger.info("Kill switch liberado manualmente.")


def trigger_halt(reason: str):
    """NUEVO EN v12.0 (Instrucción 5) — punto de entrada PÚBLICO para que
    código interno del propio bot (el motor de introspección SRE,
    m_introspection_engine.py) active el kill switch de forma automática
    ante un crash crítico.

    CORRECCIÓN IMPORTANTE vs. el pedido original: la Instrucción 5 pide
    "invocar automáticamente r_clear_kill_switch.py" al detectar gravedad
    crítica. Eso es un error de nombres en el pedido, no algo que se pueda
    implementar tal cual: r_clear_kill_switch.py hace exactamente lo
    opuesto (LIBERA el corte, con confirmación humana explícita por
    diseño — ver su docstring, "nunca se llama sola desde el propio
    bot"). Automatizar una llamada a ESE script en un crash crítico, en el
    mejor caso no haría nada (si el bot no estaba halted todavía) y en el
    peor, si se lo modificara para no pedir confirmación, haría que el
    sistema se autorecupere de un error crítico sin revisión humana — el
    riesgo opuesto al que la Instrucción 5 pide mitigar.
    Lo que sí existía y es lo que corresponde usar es _record_halt(), ya
    presente en este archivo desde v8.0; se expone acá como función
    pública (trigger_halt) para que m_introspection_engine.py la llame sin
    tocar el nombre "privado" _record_halt directamente."""
    _record_halt(reason)


def _record_halt(reason: str):
    global _halted, _halt_reason
    _halted = True
    _halt_reason = reason
    _init_table()
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("INSERT INTO risk_halts (reason) VALUES (?)", (reason,))
    conn.commit()
    conn.close()
    logger.critical("KILL SWITCH ACTIVADO: %s", reason)
    # NUEVO EN v13.0 — ver set_critical_notifier(). Nunca debe poder
    # tumbar el propio halt si el notifier falla (ej. Telegram caído
    # justo en el peor momento) — por eso va en un try/except separado,
    # después de que el corte YA quedó grabado y aplicado.
    if _critical_notifier_callback:
        try:
            _critical_notifier_callback("KILL SWITCH ACTIVADO", reason)
        except Exception as e:
            logger.error("No se pudo mandar la notificación crítica del kill switch: %s", e)

    # NUEVO EN v15.0 — DRENAJE SEGURO (pedido: "que no deje operaciones
    # huérfanas"). Se dispara en el mismo instante del corte, no en el
    # próximo arranque: hasta v14.0, lo que estaba a mitad de camino
    # (propuestas pendientes de un botón que ya nadie iba a apretar,
    # órdenes enviadas a PPI sin confirmar) se quedaba ahí hasta el
    # siguiente reinicio del proceso, que podía ser días después.
    #
    # Va DESPUÉS de grabar y aplicar el halt, y en su propio try/except, por
    # el mismo criterio que el notifier: el drenaje es importante, pero el
    # corte lo es más. Si el drenaje falla, el bot igual queda detenido.
    # Se registra por callback (ver set_drain_callback) para que este módulo
    # siga sin dependencias duras — ag_kill_switch_supervisor.py importa
    # p_risk_guardian, así que un import al revés sería circular.
    if _drain_callback:
        try:
            _drain_callback(reason)
        except Exception as e:
            logger.error("El drenaje posterior al kill switch falló: %s", e)


def check_and_halt_if_needed(ppi_client, notifier) -> bool:
    """
    Se llama en CADA vuelta del loop principal, antes de evaluar
    instrumentos nuevos. Devuelve True si el bot está (o queda) detenido.
    Una vez detenido, se mantiene detenido hasta reinicio manual del
    proceso — no hay auto-reactivación.
    """
    global _halted
    if _halted:
        return True

    # ======================================================================
    # 1) PÉRDIDA DIARIA — SOBRE PATRIMONIO, NO SOBRE CAJA (corregido v16.2)
    # ======================================================================
    # Antes esta cuenta comparaba el saldo en pesos actual contra la foto del
    # saldo en pesos de la apertura. Eso producía dos fallas silenciosas y
    # opuestas:
    #
    #   (a) FALSO POSITIVO GARANTIZADO. Comprar reduce la caja sin reducir el
    #       patrimonio. Con MAX_DAILY_LOSS_PCT=1,0 bastaba una orden que
    #       consumiera más del 1% del efectivo para que el sistema declarara
    #       "pérdida diaria". Como es un corte FINANCIERO, nunca se
    #       auto-libera: exige intervención humana. En la práctica el bot se
    #       apagaba solo con la primera operación de cada día.
    #
    #   (b) FALSO NEGATIVO. Una posición abierta que cae 30% no mueve la
    #       caja, así que la protección que justifica todo el diseño
    #       conservador del sistema no se enteraba.
    #
    # Ahora la magnitud es el patrimonio (efectivo + tenencia valuada a
    # mercado) y se neutralizan los depósitos y retiros del día antes de
    # calcular el porcentaje. Toda esa lógica vive en ax_equity.py.
    diario = ax_equity.perdida_diaria_pct(ppi_client)
    if not diario["ok"]:
        # Un patrimonio que no se puede determinar NO es un patrimonio
        # estable: es un dato faltante. No se corta (cortar por no poder
        # medir apagaría el bot cada vez que el bróker demore una respuesta),
        # pero se registra, porque operar sin poder medir la pérdida es
        # exactamente la situación que el portón operativo tiene que ver.
        logger.warning("No se pudo evaluar la pérdida diaria: %s", diario["motivo"])
    else:
        daily_pct = diario["pct"]
        if daily_pct <= -MAX_DAILY_LOSS_PCT:
            eq = diario["equity"]
            _record_halt(
                f"Pérdida diaria {daily_pct:.2f}% ≥ límite {MAX_DAILY_LOSS_PCT}% "
                f"(patrimonio ${eq.total:,.0f} vs apertura ${diario['apertura']:,.0f}"
                + (f", flujos netos ${diario['flujos']:,.0f}" if diario["flujos"] else "")
                + ")")
            notifier.send_telegram(
                f"🛑 *KILL SWITCH ACTIVADO*\nPérdida diaria: {daily_pct:.2f}% "
                f"(límite: {MAX_DAILY_LOSS_PCT}%)\n\n"
                f"Patrimonio actual: ${eq.total:,.0f}\n"
                f"  · efectivo ${eq.efectivo_ars:,.0f}\n"
                f"  · tenencia ${eq.tenencia_ars:,.0f}\n"
                f"Apertura del día: ${diario['apertura']:,.0f}\n"
                + (f"Depósitos/retiros del día: ${diario['flujos']:,.0f} (descontados)\n"
                   if diario["flujos"] else "")
                + "\nEl bot dejó de abrir posiciones. Las abiertas se siguen "
                "vigilando y se pueden cerrar. Este es un corte FINANCIERO: "
                "no se auto-libera, la decisión de volver a operar es tuya."
            )
            return True

    # ======================================================================
    # 2) DRAWDOWN — pico de PATRIMONIO contra patrimonio ACTUAL (v16.2)
    # ======================================================================
    # Antes se comparaba MAX(saldo en pesos) histórico contra el saldo de
    # APERTURA del día. Tres cosas mal en una línea: la magnitud era caja, el
    # término de comparación era un número de la mañana en vez de uno de
    # ahora, y el pico salía de un MAX() sobre fotos heterogéneas.
    #
    # Encima, ni los depósitos ni los retiros se neutralizaban: sacar plata
    # de la cuenta aparecía como caída patrimonial y podía activar un corte
    # financiero sin que hubiera existido ninguna pérdida; un depósito
    # elevaba el pico de forma permanente y endurecía el umbral para siempre.
    dd = ax_equity.drawdown_pct(ppi_client)
    if dd["ok"] and dd["pct"] >= MAX_DRAWDOWN_PCT:
        _record_halt(f"Drawdown {dd['pct']:.2f}% ≥ límite {MAX_DRAWDOWN_PCT}% "
                     f"(pico ${dd['pico']:,.0f} → actual ${dd['equity'].total:,.0f})")
        notifier.send_telegram(
            f"🛑 *KILL SWITCH ACTIVADO*\nDrawdown desde el máximo: {dd['pct']:.2f}% "
            f"(límite: {MAX_DRAWDOWN_PCT}%)\n\n"
            f"Pico de patrimonio: ${dd['pico']:,.0f}\n"
            f"Patrimonio actual: ${dd['equity'].total:,.0f}\n\n"
            "Corte FINANCIERO: no se auto-libera."
        )
        return True

    # 3) Stop-losses consecutivos (operaciones cerradas seguidas en pérdida).
    conn = ac_db.connect_raw()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        c.execute("SELECT exit_reason FROM closed_trades ORDER BY closed_at DESC LIMIT ?",
                  (MAX_CONSECUTIVE_STOP_LOSSES,))
        recent = [dict(r)["exit_reason"] for r in c.fetchall()]
    except sqlite3.OperationalError:
        recent = []
    conn.close()

    if len(recent) == MAX_CONSECUTIVE_STOP_LOSSES and all(r == "STOP_LOSS" for r in recent):
        _record_halt(f"{MAX_CONSECUTIVE_STOP_LOSSES} stop-losses consecutivos")
        notifier.send_telegram(
            f"🛑 *KILL SWITCH ACTIVADO*\n{MAX_CONSECUTIVE_STOP_LOSSES} operaciones seguidas "
            "cerraron en stop-loss. El bot dejó de emitir alertas nuevas hasta revisión manual."
        )
        return True

    # 4) NUEVO EN v10.5 (segunda revisión) — circuit breaker de
    # volatilidad intradía del dólar CCL. Contexto de por qué esta forma
    # y no otra: una auditoría externa recomendó calcular la "brecha
    # CCL/MEP" (AL30/GD30 vs AL30D/GD30D) como indicador de riesgo. Se
    # evaluó implementarlo tal cual y se decidió NO hacerlo — la
    # combinación exacta de bonos/settlement que PPI usa internamente
    # para MEP no está confirmada en la documentación disponible, y
    # calcular mal una fórmula financiera es peor que no tenerla (ver
    # Bitácora v10.5, sección de hallazgos rechazados). En cambio, se
    # implementa acá una versión del mismo concepto de riesgo (movimiento
    # cambiario anormal) con un dato que el bot YA calcula de forma
    # confiable en cada ciclo: el CCL spot (AL30/AL30D, ver
    # c_ppi_client.get_ccl_rate()). Si el CCL se movió más de
    # CCL_INTRADAY_CIRCUIT_BREAKER_PCT respecto a la primera lectura del
    # día, es una señal de estrés cambiario fuerte — se corta, igual que
    # ante una pérdida o un drawdown.
    ccl_now = ppi_client.get_ccl_rate()
    if ccl_now:
        today = date.today().isoformat()
        conn = ac_db.connect_raw()
        c = conn.cursor()
        c.execute("SELECT ccl FROM ccl_intraday_snapshot WHERE date = ? ORDER BY id ASC LIMIT 1", (today,))
        first_row = c.fetchone()
        if first_row is None:
            c.execute("INSERT INTO ccl_intraday_snapshot (date, ccl) VALUES (?, ?)", (today, ccl_now))
            conn.commit()
            conn.close()
        else:
            conn.close()
            ccl_open = first_row[0]
            if ccl_open and ccl_open > 0:
                move_pct = abs(ccl_now - ccl_open) / ccl_open * 100
                if move_pct >= CCL_INTRADAY_CIRCUIT_BREAKER_PCT:
                    _record_halt(f"CCL se movió {move_pct:.2f}% intradía "
                                 f"(${ccl_open} → ${ccl_now}) ≥ límite {CCL_INTRADAY_CIRCUIT_BREAKER_PCT}%")
                    notifier.send_telegram(
                        f"🛑 *KILL SWITCH ACTIVADO — MOVIMIENTO CAMBIARIO ANORMAL*\n"
                        f"El dólar CCL se movió {move_pct:.2f}% desde la apertura "
                        f"(${ccl_open} → ${ccl_now}), por encima del límite configurado "
                        f"({CCL_INTRADAY_CIRCUIT_BREAKER_PCT}%). El bot dejó de emitir alertas "
                        "nuevas hasta revisión manual — un salto así suele ir de la mano de "
                        "eventos que conviene mirar antes de seguir operando."
                    )
                    return True

    return False


def get_exposure_by_ticker(ticker: str) -> float:
    """
    NUEVO EN v10.5 (segunda revisión) — respuesta acotada y honesta al
    pedido de "límite de exposición cruzada por emisor/activo" de la
    auditoría externa: implementar el caso general (exposición por
    EMISOR, ej. sumar todo lo que tenés en instrumentos del mismo
    emisor/grupo económico) requeriría un mapeo ticker→emisor que este
    proyecto no tiene y que se decidió no fabricar sin una fuente de
    datos confiable. Lo que SÍ se puede calcular con los datos que ya
    existen es la exposición por TICKER (cuánto capital ya está invertido
    en ese mismo instrumento entre las posiciones abiertas) — un caso más
    chico, pero real y verificable. Devuelve el capital en ARS ya
    invertido en ese ticker específico, sumando entry_price*quantity de
    las posiciones abiertas.
    """
    conn = ac_db.connect_raw()
    c = conn.cursor()
    try:
        c.execute("SELECT entry_price, quantity FROM open_positions WHERE ticker = ?", (ticker,))
        rows = c.fetchall()
    except sqlite3.OperationalError:
        rows = []
    conn.close()
    return sum(price * qty for price, qty in rows)
