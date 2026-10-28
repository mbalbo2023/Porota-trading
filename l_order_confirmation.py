"""
l_order_confirmation.py — Ejecución de órdenes: automática (SANDBOX) o con
confirmación por Telegram (PRODUCTION) (v10.5)

REESCRITO EN v10.5 A PEDIDO EXPLÍCITO DEL USUARIO: "como voy a trabajar en
el sandbox, no en producción, quiero que las órdenes se ejecuten de manera
automática. No quiero el botón de confirmación en Telegram".

CÓMO QUEDÓ RESUELTO — dos modos de ejecución, elegidos por
ORDER_EXECUTION_MODE:
  - "auto": apenas una oportunidad pasa los tres filtros (técnico, macro,
    hurdle), el bot llama directo a budget_order()+confirm_order() en PPI
    — SIN botón, sin esperar nada — y manda un único mensaje de Telegram
    avisando qué se ejecutó, con el win rate estimado real del sistema
    (de k_position_manager.get_win_rate(), nunca un número inventado), la
    oportunidad detectada, cómo se ejecutó y qué monto se puso en juego.
    Cuando la posición cierra (stop-loss o take-profit), se manda otro
    mensaje con el resultado final — ver notify_order_result() en
    b_notifiers.py.
  - "confirm": el flujo anterior (botón Confirmar/Cancelar en Telegram)
    sigue existiendo tal cual, sin tocarse — se conserva para PRODUCTION.

DEFAULT POR AMBIENTE (no es una decisión arbitraria — es la misma lógica
de seguridad en capas que ya tenía v8.0/v10.4, solo que ahora el default
depende del ambiente en vez de ser fijo):
  - En SANDBOX, ORDER_EXECUTION_MODE por default es "auto" — es
    justamente el pedido, y en Sandbox no hay dinero real en juego, así
    que no hace falta ningún interruptor extra.
  - En PRODUCTION, ORDER_EXECUTION_MODE por default sigue siendo
    "confirm" — auto-ejecución en PRODUCTION sin ningún toque humano es
    una decisión demasiado grande para que quede activada por default.
    Si en algún momento QUERÉS auto-ejecución también en PRODUCTION, hace
    falta poner explícitamente ORDER_EXECUTION_MODE=auto Y
    PRODUCTION_AUTO_EXECUTE_CONFIRMED=true en el .env — las dos cosas a
    la vez, a propósito.

SALVAGUARDA NUEVA v10.5 (auditoría 3, Doc D.8): antes de ejecutar
CUALQUIER orden real, en cualquiera de los dos modos, se verifica que
instrument_type esté en TRADEABLE_INSTRUMENT_TYPES. Si por cualquier
motivo un instrumento no operable (OPCIONES/FUTUROS/CAUCIONES/FCI, que
siguen en modo "solo visible" — ver m_instrument_universe.py) llegara
hasta acá, la orden se rechaza y se avisa, en vez de ejecutarse con una
lógica de riesgo que no le corresponde.

MEDIDAS DE SEGURIDAD QUE SE MANTIENEN IGUAL QUE EN v8.0/v10.4 (aplican a
los dos modos):
  1. Antes de ejecutar, se vuelve a pedir la cotización actual y se
     compara contra el precio analizado. Si se movió más de
     PRICE_REVALIDATION_TOLERANCE_PCT, se cancela en vez de ejecutar a un
     precio que ya no fue el analizado.
  2. En modo "confirm", cada propuesta vence a los
     ORDER_CONFIRMATION_TIMEOUT_MINUTES (default 10).
  3. Transacciones atómicas: apenas PPI confirma la orden, se graba el
     estado ANTES de avisar o abrir la posición — así un reinicio en el
     medio no pierde el rastro (ver recover_orphaned_orders()).
"""

import os
import time
import datetime
import logging
import sqlite3
import uuid

import aa_env_guard as env_guard
import p_risk_guardian as risk_guardian
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

logger = logging.getLogger("order_confirmation")
DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")

CONFIRMATION_TIMEOUT_MIN = int(os.getenv("ORDER_CONFIRMATION_TIMEOUT_MINUTES", "10"))
PRICE_TOLERANCE_PCT = float(os.getenv("PRICE_REVALIDATION_TOLERANCE_PCT", "1.0"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "SANDBOX").upper()
PRODUCTION_AUTO_EXECUTE_CONFIRMED = os.getenv("PRODUCTION_AUTO_EXECUTE_CONFIRMED", "false").lower() == "true"

# NUEVO EN v10.5 — reemplaza a ORDER_AUTO_EXECUTE (que solo decidía si el
# botón de Telegram, una vez tocado, ejecutaba solo o no). Ahora decide
# directamente el modo de ejecución. Default por ambiente, ver docstring.
_execution_mode_raw = os.getenv("ORDER_EXECUTION_MODE", "").strip().lower()
if _execution_mode_raw in ("auto", "confirm"):
    ORDER_EXECUTION_MODE = _execution_mode_raw
else:
    ORDER_EXECUTION_MODE = "auto" if ENVIRONMENT == "SANDBOX" else "confirm"

if ORDER_EXECUTION_MODE == "auto" and ENVIRONMENT == "PRODUCTION" and not PRODUCTION_AUTO_EXECUTE_CONFIRMED:
    logger.warning(
        "ORDER_EXECUTION_MODE=auto + ENVIRONMENT=PRODUCTION sin "
        "PRODUCTION_AUTO_EXECUTE_CONFIRMED=true: por seguridad, se fuerza "
        "el modo 'confirm' (botón de Telegram) hasta que agregues esa "
        "variable al .env de forma deliberada."
    )
    ORDER_EXECUTION_MODE = "confirm"

# Compatibilidad hacia atrás: algunos módulos/documentación viejos leen
# ORDER_AUTO_EXECUTE. Se mantiene como alias derivado, no como fuente de verdad.
ORDER_AUTO_EXECUTE = ORDER_EXECUTION_MODE == "auto"

# NUEVO EN v10.5 — auditoría 3, Doc D.8: única lista de tipos de
# instrumento que este archivo tiene permitido ejecutar de verdad. Debe
# coincidir con m_instrument_universe.DISCOVERABLE_TYPES (las 4 clases que
# el motor de riesgo sabe operar). Es una salvaguarda defensiva — no
# depende de que el resto del código nunca tenga un bug que deje pasar un
# instrumento equivocado hasta acá.
TRADEABLE_INSTRUMENT_TYPES = {"CEDEARS", "ACCIONES", "BONOS", "ETF"}

# NUEVO EN v10.5 — modo scalping (pedido explícito, para probar el motor
# en SANDBOX): cuando está activo, j_main.py pasa target_hold_days más
# corto y el ATR de 5M en vez de 1H/diario — este archivo no necesita
# saber de scalping para nada más que etiquetar el mensaje de Telegram.
SCALPING_MODE = os.getenv("SCALPING_MODE", "false").lower() == "true"

# NUEVO EN v13.0 — hallazgo ALTO de Simulación_y_pasos_a_tener_en_cuenta_
# para_corregir.pdf: cuánto tiempo (en segundos) se sigue "pollando" una
# orden que quedó PartiallyFilled antes de aceptar el remanente pendiente
# como definitivo y abrir la posición con lo efectivamente ejecutado.
PARTIAL_FILL_POLL_SECONDS = int(os.getenv("PARTIAL_FILL_POLL_SECONDS", "20"))
PARTIAL_FILL_POLL_INTERVAL_SECONDS = int(os.getenv("PARTIAL_FILL_POLL_INTERVAL_SECONDS", "2"))


def _resolve_filled_quantity(ppi, notifier, ppi_order_id, requested_qty: int, ticker: str):
    """
    NUEVO EN v13.0 — cierra el HALLAZGO CRÍTICO documentado en
    Simulación_y_pasos_a_tener_en_cuenta_para_corregir.pdf: antes, si PPI
    devolvía una orden en estado 'PartiallyFilled', este código no lo
    contemplaba — asumía 'Filled' o 'Rejected/Cancelled' únicamente, así
    que una ejecución parcial (algo frecuente en el mercado argentino,
    donde la liquidez llega fragmentada) terminaba abriendo la posición
    con la cantidad SOLICITADA en vez de la REALMENTE COMPRADA,
    desincronizando k_position_manager del portfolio real en PPI —
    arruinando el cálculo de PnL y de los stop-loss/take-profit.

    Devuelve (quantity_to_use, status_final):
      - Si 'Filled' (o la librería no informa 'status', comportamiento
        de siempre): (requested_qty, 'Filled').
      - Si 'PartiallyFilled': se reconsulta el estado de la orden cada
        PARTIAL_FILL_POLL_INTERVAL_SECONDS durante hasta
        PARTIAL_FILL_POLL_SECONDS, por si termina de llenarse. Si sigue
        parcial al cabo de ese tiempo, se acepta la cantidad
        efectivamente llenada (puede ser 0 si get_order_status no está
        disponible — ver c_ppi_client.get_order_status) y se avisa por
        Telegram, en vez de asumir en silencio que se compró todo.
      - Si 'Rejected'/'Cancelled': (0, status).
    """
    # NUEVO EN v14.0 — get_order_status() ahora consulta primero el stream de
    # cuenta de PPI (push en tiempo real con QuantityExecuted real) y recién
    # después el REST. Ver el bug corregido en c_ppi_client.get_order_status:
    # en v13.0 este llamado devolvía SIEMPRE None por un nombre de método
    # equivocado, así que toda esta lógica de ejecución parcial nunca llegaba
    # a ejecutarse y se asumía "Filled" en todos los casos.
    status_data = ppi.get_order_status(ppi_order_id) if ppi_order_id else None
    status = (status_data or {}).get("status") if status_data else None

    if status is None:
        # No se pudo confirmar el estado (ni por push ni por REST). Se
        # mantiene el comportamiento histórico —asumir ejecución total— pero
        # AHORA SE AVISA, en vez de hacerlo en silencio: si esto se repite,
        # es una señal de que hay que mirar el stream de cuenta.
        logger.warning("No se pudo confirmar el estado de la orden %s (%s). Se asume ejecución "
                       "total; verificar en la app de PPI.", ppi_order_id, ticker)
        return requested_qty, "Filled"

    if status == "Filled":
        return requested_qty, "Filled"

    if status == "PartiallyFilled":
        filled_qty = status_data.get("filledQuantity", 0)
        elapsed = 0
        while elapsed < PARTIAL_FILL_POLL_SECONDS:
            time.sleep(PARTIAL_FILL_POLL_INTERVAL_SECONDS)
            elapsed += PARTIAL_FILL_POLL_INTERVAL_SECONDS
            status_data = ppi.get_order_status(ppi_order_id)
            if not status_data:
                break
            status = status_data.get("status")
            filled_qty = status_data.get("filledQuantity", filled_qty)
            if status == "Filled":
                return requested_qty, "Filled"
            if status in ("Rejected", "Cancelled"):
                break
        logger.warning(
            "Orden %s de %s quedó PartiallyFilled: %s/%s. Se abre la posición con la cantidad "
            "efectivamente ejecutada, no con la solicitada.", ppi_order_id, ticker, filled_qty, requested_qty,
        )
        notifier.send_telegram(
            f"⚠️ *EJECUCIÓN PARCIAL* — {ticker}\n"
            f"Se pidieron {requested_qty} y se ejecutaron {filled_qty}. El remanente no se "
            f"completó a tiempo — la posición se abre con la cantidad realmente comprada."
        )
        return filled_qty, "PartiallyFilled"

    if status in ("Rejected", "Cancelled"):
        return 0, status

    # Estado no reconocido — se avisa pero no se asume nada silenciosamente.
    logger.warning("Orden %s de %s devolvió un estado no reconocido (%s) — se asume ejecución "
                    "total por compatibilidad, revisar manualmente en PPI.", ppi_order_id, ticker, status)
    return requested_qty, status or "Filled"


def _init_table():
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS pending_orders (
            id TEXT PRIMARY KEY,
            ticker TEXT,
            quantity INTEGER,
            entry_price REAL,
            stop_loss_price REAL,
            take_profit_price REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'PENDING',
            ppi_order_id TEXT,
            instrument_type TEXT DEFAULT 'CEDEARS',
            settlement TEXT DEFAULT 'A-24HS'
        )
    """)
    # AMPLIACIÓN v10.5 — columnas nuevas para bases ya existentes (creadas
    # con versiones anteriores del bot). ALTER TABLE falla si la columna ya
    # existe, así que se ignora ese error puntual — es la forma estándar de
    # migrar SQLite sin una librería de migraciones aparte.
    for ddl in ("ALTER TABLE pending_orders ADD COLUMN instrument_type TEXT DEFAULT 'CEDEARS'",
                "ALTER TABLE pending_orders ADD COLUMN settlement TEXT DEFAULT 'A-24HS'"):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def create_pending_order(ticker, quantity, entry_price, stop_loss_price, take_profit_price,
                          instrument_type="CEDEARS", settlement="A-24HS") -> str:
    _init_table()
    order_id = str(uuid.uuid4())[:8]
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute(
        "INSERT INTO pending_orders (id, ticker, quantity, entry_price, stop_loss_price, "
        "take_profit_price, instrument_type, settlement) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (order_id, ticker, quantity, entry_price, stop_loss_price, take_profit_price,
         instrument_type, settlement),
    )
    conn.commit()
    conn.close()
    return order_id


def _get_pending(order_id):
    conn = ac_db.connect_raw()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM pending_orders WHERE id = ? AND status = 'PENDING'", (order_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_reserved_capital_ars() -> float:
    """
    CORRECCIÓN — auditoría 9.1 (rev. 1), hallazgo válido: cada propuesta
    calculaba cuánto comprar contra el saldo TOTAL disponible, sin
    descontar lo que ya estaba comprometido en otras propuestas
    pendientes de confirmar. Si dos alertas quedaban esperando
    confirmación al mismo tiempo, las dos se dimensionaban contra el
    mismo dinero — al confirmar ambas, la segunda podía fallar en PPI por
    fondos insuficientes, o terminar sobre-apalancando la cuenta.

    Esta función suma el valor (precio x cantidad) de todas las
    propuestas todavía PENDING, para que j_main.py lo reste del saldo
    disponible antes de calcular el tamaño de la próxima propuesta.
    """
    _init_table()
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("SELECT entry_price, quantity FROM pending_orders WHERE status = 'PENDING'")
    reserved = sum(price * qty for price, qty in c.fetchall())
    conn.close()
    return reserved


def _mark(order_id, status):
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("UPDATE pending_orders SET status = ? WHERE id = ?", (status, order_id))
    conn.commit()
    conn.close()


def _age_minutes(pending):
    dt = datetime.datetime.strptime(pending["created_at"], "%Y-%m-%d %H:%M:%S")
    return (time.time() - dt.timestamp()) / 60


def process_button_taps(ppi, notifier, position_manager=None):
    """CONSUMIDOR ÚNICO de las actualizaciones de Telegram.

    Se llama seguido desde el loop principal. Lee una sola vez y despacha por
    prefijo: comandos escritos, botones de la parada de emergencia, taps del
    guardián de reinicios, taps del kill switch y confirmaciones de orden.

    v16.2 — `position_manager` pasa a ser opcional. Durante la espera de
    autorización de arranque todavía no existe, y la parada de emergencia
    tiene que responder igual en esa ventana: si el bot quedó levantado
    esperando un OK que nadie va a dar, ESTADO y PARADA siguen siendo los
    controles que uno quiere que funcionen.
    """
    if not hasattr(process_button_taps, "_offset"):
        process_button_taps._offset = 0

    taps, new_offset = notifier.get_telegram_button_taps(process_button_taps._offset)
    process_button_taps._offset = new_offset

    for tap in taps:
        try:
            import bb_runtime_status
            tipo = "COMANDO" if "text" in tap else "BOTON"
            resumen = tap.get("text") or tap.get("data") or ""
            bb_runtime_status.record_telegram("ENTRANTE", tipo, "RECIBIDO", resumen)
        except Exception:
            pass
        # ------------------------------------------------------------------
        # NUEVO EN v16.2 — despacho de MENSAJES ESCRITOS
        # ------------------------------------------------------------------
        # El lector unificado ahora entrega también los comandos de texto. Se
        # despachan acá, en el único consumidor de getUpdates, en vez de en un
        # segundo bucle propio: ese segundo bucle era el que competía por las
        # mismas actualizaciones y hacía perder confirmaciones de órdenes.
        #
        # El remitente ya viene validado por el lector, pero se vuelve a pasar
        # a procesar_mensaje() para que valide por su cuenta. Es defensa en
        # profundidad deliberada: el lector puede cambiar, y una validación
        # que vive en un solo lugar deja de existir el día que alguien
        # refactoriza ese lugar.
        if tap.get("text"):
            try:
                import ao_startup_gate as startup_gate
                resultado_arranque = startup_gate.procesar_respuesta_telegram(
                    tap["text"], origen="telegram"
                )
                if resultado_arranque is not None:
                    if resultado_arranque.get("ok"):
                        notifier.send_telegram(
                            f"✅ Arranque autorizado en modo {resultado_arranque['modo']}."
                        )
                    else:
                        notifier.send_telegram(
                            "⚠️ No se pudo autorizar el arranque: "
                            + resultado_arranque.get("motivo", "respuesta inválida")
                        )
                    continue
                import ar_telegram_commands as tg_cmd
                tg_cmd.procesar_mensaje(tap["text"], notifier, ppi,
                                        remitente_id=tap.get("sender_id"))
            except Exception as e:
                logger.error("Error procesando el comando de Telegram %r: %s",
                             tap["text"][:40], e)
            continue

        action, _, order_id = tap["data"].partition(":")
        notifier.answer_telegram_callback(tap["callback_id"], "Procesando...")

        if action == "arranque":
            import ao_startup_gate as startup_gate
            resultado_arranque = startup_gate.procesar_boton_telegram(tap["data"])
            if resultado_arranque and resultado_arranque.get("ok"):
                notifier.send_telegram(
                    f"✅ Arranque autorizado en modo {resultado_arranque['modo']}."
                )
            else:
                notifier.send_telegram(
                    "⚠️ No se pudo autorizar el arranque: "
                    + ((resultado_arranque or {}).get("motivo", "botón inválido"))
                )
            continue

        # NUEVO EN v16.2 — botones de la parada de emergencia y del motor SRE.
        # Mismo criterio de siempre: un solo lector, despacho por prefijo.
        if action in ("parada", "ordenada", "liquidar", "corte", "corte_final",
                      "rearrancar", "sre_aplicar"):
            try:
                import ar_telegram_commands as tg_cmd
                tg_cmd.procesar_boton(tap["data"], notifier, ppi,
                                      remitente_id=tap.get("sender_id"))
            except Exception as e:
                logger.error("Error procesando el botón %r de Telegram: %s", action, e)
            continue

        # NUEVO EN v14.0 — despacho por prefijo. Telegram entrega las
        # actualizaciones con un offset incremental, así que tiene que haber
        # UN SOLO lector de getUpdates en todo el sistema: si dos módulos
        # leyeran por su cuenta, cada uno se comería los mensajes del otro.
        # Por eso los taps del guardián de reinicios (Instrucción 1) también
        # entran por acá y se reparten por el prefijo del callback_data.
        if env_guard.handle_telegram_tap(action, order_id, notifier):
            continue

        # NUEVO EN v15.0 — taps del kill switch (prefijos KSOK / KSNO). Mismo
        # criterio que los del guardián de reinicios: UN solo lector de
        # getUpdates en todo el sistema, y despacho por prefijo. Esto es lo
        # que permite liberar un corte estructural desde la tablet, con un
        # toque, sin entrar por SSH a correr r_clear_kill_switch.py.
        if action in ("KSOK", "KSNO"):
            import ag_kill_switch_supervisor as ks_supervisor
            if action == "KSOK":
                if ks_supervisor.aprobar_desde_telegram(notifier):
                    logger.warning("Kill switch liberado por confirmación desde Telegram.")
                else:
                    notifier.send_telegram("El kill switch ya no estaba activo — no había nada que liberar.")
            else:
                notifier.send_telegram(
                    "🛑 Entendido: el kill switch sigue activo. El bot no va a evaluar "
                    "instrumentos nuevos hasta que lo liberes."
                )
            continue

        pending = _get_pending(order_id)
        if not pending:
            notifier.send_telegram("Esa propuesta ya venció o ya fue procesada.")
            continue

        if action == "CANCEL":
            _mark(order_id, "CANCELLED")
            notifier.send_telegram(f"❌ Cancelado: {pending['ticker']} — no se colocó ninguna orden.")
            continue

        if action == "CONFIRM":
            if position_manager is None:
                logger.warning("Confirmación de orden recibida antes de que el "
                               "sistema esté operativo: se ignora.")
                continue
            _handle_confirm(ppi, notifier, position_manager, pending)


def _handle_confirm(ppi, notifier, position_manager, pending):
    order_id = pending["id"]
    age_min = _age_minutes(pending)
    if age_min > CONFIRMATION_TIMEOUT_MIN:
        _mark(order_id, "EXPIRED")
        notifier.send_telegram(
            f"⏱️ La propuesta de {pending['ticker']} venció ({int(age_min)} min desde la alerta) — "
            "el precio ya no es confiable. Esperá la próxima alerta."
        )
        return

    mkt = ppi.get_market_data(pending["ticker"], pending.get("instrument_type", "CEDEARS"),
                               pending.get("settlement", "A-24HS"))
    current_price = mkt.get("price", 0) if mkt else 0
    if current_price <= 0:
        notifier.send_telegram(f"⚠️ No pude obtener la cotización actual de {pending['ticker']}, no se ejecutó.")
        return

    drift_pct = abs(current_price - pending["entry_price"]) / pending["entry_price"] * 100
    if drift_pct > PRICE_TOLERANCE_PCT:
        _mark(order_id, "PRICE_DRIFT")
        notifier.send_telegram(
            f"⚠️ El precio de {pending['ticker']} se movió {drift_pct:.2f}% desde la alerta "
            f"(${pending['entry_price']} → ${current_price}) — no se ejecutó por seguridad. "
            "Esperá la próxima alerta si sigue conviniendo."
        )
        return

    # NUEVO EN v10.5 — auditoría externa recibida sobre el paquete v10.5,
    # hallazgo real confirmado: en modo "confirm", entre el momento en que
    # j_main.py chequeó risk_guardian.is_halted() (al detectar la
    # oportunidad) y el momento en que TOCÁS el botón "Confirmar" pueden
    # pasar hasta ORDER_CONFIRMATION_TIMEOUT_MINUTES (10 min por default)
    # — si el kill switch se activa justo en el medio (por ejemplo, por
    # una racha de stop-losses mientras la propuesta seguía esperando tu
    # confirmación), la versión anterior del código NO volvía a chequear
    # el freno antes de ejecutar. Ahora sí: se revalida acá, justo antes
    # de tocar la API real de PPI, con el mismo criterio "si está
    # cortado, no se ejecuta nada nuevo" que ya aplica al resto del bot.
    if risk_guardian.is_halted():
        _mark(order_id, "REJECTED_KILL_SWITCH")
        notifier.send_telegram(
            f"🛑 No se ejecutó la orden de {pending['ticker']}: el kill switch se activó "
            f"mientras esperaba tu confirmación ({risk_guardian.halt_reason()}). "
            "Revisá el motivo antes de liberar el freno."
        )
        return

    _execute_real_order(ppi, notifier, position_manager, pending, current_price)


def _execute_real_order(ppi, notifier, position_manager, pending, current_price):
    """
    Coloca la orden REAL en PPI (modo "confirm", tras el botón de
    Telegram). "Transacciones atómicas" (auditoría 8.1 rev. 2): apenas PPI
    confirma la orden, se graba el ppi_order_id y el estado 'SENT_TO_PPI'
    ANTES de hacer cualquier otra cosa (avisar por Telegram, abrir la
    posición) — así, si el proceso se reinicia en el medio,
    recover_orphaned_orders() (llamada al arrancar main()) puede terminar
    el trabajo solo.

    SALVAGUARDA v10.5 (auditoría 3, Doc D.8): rechaza cualquier
    instrument_type fuera de TRADEABLE_INSTRUMENT_TYPES antes de tocar la
    API de PPI, sin importar cómo haya llegado hasta acá.
    """
    instrument_type = pending.get("instrument_type", "CEDEARS")
    settlement = pending.get("settlement", "A-24HS")
    if instrument_type not in TRADEABLE_INSTRUMENT_TYPES:
        notifier.send_telegram(
            f"🛑 Orden de {pending['ticker']} RECHAZADA por seguridad: tipo de instrumento "
            f"'{instrument_type}' no está en la lista de tipos operables ({', '.join(sorted(TRADEABLE_INSTRUMENT_TYPES))})."
        )
        _mark(pending["id"], "REJECTED_INSTRUMENT_TYPE")
        return

    account_number = ppi.account_number
    budget = ppi.budget_order(account_number, pending["quantity"], current_price, pending["ticker"],
                               instrument_type=instrument_type, settlement=settlement)
    if not budget:
        notifier.send_telegram(f"⚠️ PPI no devolvió presupuesto para {pending['ticker']}, no se ejecutó. "
                                "Cargala manualmente si todavía te conviene.")
        _mark(pending["id"], "BUDGET_FAILED")
        return

    disclaimers = budget.get("disclaimers", [])
    # CORRECCIÓN v11.0 — hallazgo propio (no reportado por ninguna auditoría
    # externa): confirm_order() se manda dentro de _call_with_retry() en
    # c_ppi_client.py, que reintenta hasta 4 veces con backoff si la llamada
    # tira una excepción (timeout de red, por ejemplo). Sin un external_id
    # estable, un timeout DESPUÉS de que PPI ya procesó la orden en su lado
    # (pero antes de que la respuesta llegue) provoca que el reintento mande
    # una SEGUNDA orden real idéntica — plata duplicada por un problema de
    # red, no de lógica. external_id (documentado en la API oficial de PPI
    # como campo para "idempotencia y seguimiento") se genera acá, UNA sola
    # vez por propuesta, y se reutiliza en todos los reintentos de la misma
    # llamada — así PPI puede reconocer que es el mismo pedido.
    external_id = f"pending-{pending['id']}"
    # NUEVO EN v14.0 — lock de actividad (Instrucción 1): mientras hay una
    # orden viajando hacia PPI, el sistema NO está desocupado y cualquier
    # reinicio pedido desde el panel web queda en espera hasta que termine.
    with env_guard.activity("ORDER_EXECUTION", pending["ticker"]):
        confirmation = ppi.confirm_order(account_number, pending["quantity"], current_price,
                                          pending["ticker"], disclaimers,
                                          instrument_type=instrument_type, settlement=settlement,
                                          external_id=external_id)
    if not confirmation:
        notifier.send_telegram(f"🔴 Error al confirmar la orden de {pending['ticker']} en PPI — "
                                "revisala manualmente en la app antes de asumir que no se ejecutó.")
        _mark(pending["id"], "CONFIRM_FAILED")
        return

    # Punto crítico: la orden YA es real en PPI a partir de acá. Se graba
    # de inmediato, antes de mandar el Telegram o abrir la posición.
    ppi_order_id = confirmation.get("id")
    _save_ppi_order_id_and_mark(pending["id"], ppi_order_id, "SENT_TO_PPI")

    # NUEVO EN v13.0 — ver _resolve_filled_quantity(): puede devolver menos
    # de pending["quantity"] si la orden quedó parcialmente ejecutada.
    filled_qty, fill_status = _resolve_filled_quantity(
        ppi, notifier, ppi_order_id, pending["quantity"], pending["ticker"],
    )
    if filled_qty <= 0:
        notifier.send_telegram(
            f"🔴 La orden de {pending['ticker']} (ID {ppi_order_id}) terminó en estado "
            f"'{fill_status}' sin ejecutar nada — no se abre posición."
        )
        _mark(pending["id"], f"EXECUTED_ZERO_FILL_{fill_status}")
        return

    notifier.send_telegram(
        f"✅ *ORDEN COLOCADA EN PPI*\n"
        f"{instrument_type} {pending['ticker']} x{filled_qty} @ ${current_price}\n"
        f"ID de orden en PPI: {ppi_order_id}"
    )
    # CORREGIDO EN v14.0 — bug real de v13.0: acá no se pasaba `scalping`,
    # así que TODA posición abierta por el camino "confirm" (el default en
    # PRODUCTION) quedaba grabada como no-scalping, sin importar el modo con
    # el que se hubiera generado la señal. Consecuencias concretas: (a) el
    # Cost-Validator de scalping (u_aiops_watcher.validate_scalping_viability)
    # se alimentaba solo de las operaciones del camino "auto", así que en
    # PRODUCTION nunca veía nada y no podía frenar un scalping que perdía
    # plata; (b) el cierre de fin de día nuevo de v14 no las habría
    # reconocido como intradía.
    position_manager.open_position(pending["ticker"], current_price, pending["stop_loss_price"],
                                    pending["take_profit_price"], filled_qty,
                                    ppi_order_id=ppi_order_id, instrument_type=instrument_type,
                                    settlement=settlement,
                                    planned_entry_price=pending["entry_price"],
                                    scalping=SCALPING_MODE)
    _mark(pending["id"], "EXECUTED")


def _save_ppi_order_id_and_mark(order_id, ppi_order_id, status):
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("UPDATE pending_orders SET status = ?, ppi_order_id = ? WHERE id = ?",
              (status, ppi_order_id, order_id))
    conn.commit()
    conn.close()


def recover_orphaned_orders(ppi, notifier, position_manager):
    """
    Se llama UNA vez al arrancar main(). Busca propuestas que quedaron en
    'SENT_TO_PPI' (la orden se colocó en PPI pero el proceso se cayó antes
    de terminar de registrarla acá) y termina el trabajo: abre la
    posición para que el stop-loss/take-profit se sigan vigilando, en vez
    de dejarla "perdida".
    """
    _init_table()
    conn = ac_db.connect_raw()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM pending_orders WHERE status = 'SENT_TO_PPI'")
    orphans = [dict(r) for r in c.fetchall()]
    conn.close()

    for o in orphans:
        logger.warning("Recuperando orden huérfana tras reinicio: %s (ppi_order_id=%s)",
                        o["ticker"], o.get("ppi_order_id"))
        position_manager.open_position(o["ticker"], o["entry_price"], o["stop_loss_price"],
                                        o["take_profit_price"], o["quantity"],
                                        ppi_order_id=o.get("ppi_order_id"),
                                        instrument_type=o.get("instrument_type", "CEDEARS"),
                                        settlement=o.get("settlement", "A-24HS"))
        _mark(o["id"], "EXECUTED")
        notifier.send_telegram(
            f"🔄 *RECUPERACIÓN TRAS REINICIO*\nSe encontró una orden de {o['ticker']} que se había "
            f"colocado en PPI antes de un reinicio del bot. Se retomó la vigilancia de "
            f"stop-loss/take-profit — revisá en PPI que los datos coincidan."
        )


def execute_directly(ppi, notifier, position_manager, ticker, quantity, current_price,
                      stop_loss_price, take_profit_price, instrument_type, settlement,
                      asset_class, net_return_pct, reason, win_rate_info, scalping=False):
    """
    NUEVO EN v10.5 — modo "auto" (SANDBOX por default, pedido explícito).
    Hace exactamente lo mismo que _execute_real_order() en cuanto a
    seguridad (whitelist de instrument_type, budget antes de confirm,
    grabado atómico antes de avisar) pero SIN pasar por pending_orders ni
    por el botón de Telegram — la oportunidad ya pasó los tres filtros
    (técnico, macro, hurdle) en j_main.py, así que acá se ejecuta directo.

    Devuelve el ppi_order_id si se ejecutó, o None si se rechazó/falló (en
    todos los casos ya avisó por Telegram el motivo).
    """
    # NUEVO EN v10.5 — defensa en profundidad: j_main.py ya chequea
    # risk_guardian.is_halted() antes de llegar hasta acá, pero se
    # revalida de nuevo acá mismo (mismo criterio que en el modo
    # "confirm", ver _handle_confirm más arriba) para que esta función no
    # dependa de que quien la llame se acuerde de chequearlo — es la
    # última línea antes de tocar la API real de PPI.
    if risk_guardian.is_halted():
        notifier.send_telegram(
            f"🛑 No se ejecutó la orden de {ticker}: el kill switch está activo "
            f"({risk_guardian.halt_reason()})."
        )
        return None

    if instrument_type not in TRADEABLE_INSTRUMENT_TYPES:
        notifier.send_telegram(
            f"🛑 Orden de {ticker} RECHAZADA por seguridad: tipo de instrumento "
            f"'{instrument_type}' no está en la lista de tipos operables "
            f"({', '.join(sorted(TRADEABLE_INSTRUMENT_TYPES))})."
        )
        return None

    account_number = ppi.account_number
    budget = ppi.budget_order(account_number, quantity, current_price, ticker,
                               instrument_type=instrument_type, settlement=settlement)
    if not budget:
        notifier.send_telegram(f"⚠️ PPI no devolvió presupuesto para {ticker} — oportunidad descartada, no se ejecutó.")
        return None

    disclaimers = budget.get("disclaimers", [])
    # CORRECCIÓN v11.0 — mismo hallazgo que en _execute_real_order(): external_id
    # estable por oportunidad, para que un reintento por timeout de red no
    # duplique la orden real en modo auto (Sandbox).
    external_id = f"auto-{ticker}-{int(time.time())}"
    with env_guard.activity("ORDER_EXECUTION", ticker):
        confirmation = ppi.confirm_order(account_number, quantity, current_price, ticker, disclaimers,
                                          instrument_type=instrument_type, settlement=settlement,
                                          external_id=external_id)
    if not confirmation:
        notifier.send_telegram(f"🔴 Error al colocar la orden de {ticker} en PPI — revisá el estado en la app.")
        return None

    ppi_order_id = confirmation.get("id")
    # NUEVO EN v13.0 — mismo tratamiento de PartiallyFilled que en el
    # modo "confirm" (ver _resolve_filled_quantity).
    filled_qty, fill_status = _resolve_filled_quantity(ppi, notifier, ppi_order_id, quantity, ticker)
    if filled_qty <= 0:
        notifier.send_telegram(
            f"🔴 La orden de {ticker} (ID {ppi_order_id}) terminó en estado '{fill_status}' "
            f"sin ejecutar nada — no se abre posición."
        )
        return None

    amount_ars = current_price * filled_qty
    # Igual que en el flujo de confirmación: se abre la posición ANTES de
    # avisar, así el stop-loss/take-profit quedan vigilados sin importar
    # qué pase con el envío del Telegram.
    position_manager.open_position(ticker, current_price, stop_loss_price, take_profit_price,
                                    filled_qty, ppi_order_id=ppi_order_id,
                                    instrument_type=instrument_type, settlement=settlement,
                                    scalping=scalping)
    notifier.notify_order_auto_executed(
        ticker=ticker, asset_class=asset_class, quantity=filled_qty, price=current_price,
        amount_ars=amount_ars, stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
        net_return_pct=net_return_pct, win_rate_info=win_rate_info, reason=reason,
        ppi_order_id=ppi_order_id, scalping=scalping,
    )
    return ppi_order_id


# ---------------------------------------------------------------------------
# Bombeo durante la espera de autorización (v16.2)
# ---------------------------------------------------------------------------

def arrancar_bombeo(notifier, ppi, stop_event, intervalo: float = 4.0):
    """Hilo que sondea Telegram MIENTRAS el bucle principal todavía no corre.

    Existe por una razón concreta: el portón de arranque puede dejar el bot
    esperando una autorización hasta 60 minutos, y antes de esta versión la
    escucha de comandos se arrancaba en un hilo propio justamente para cubrir
    esa ventana. Ese hilo era el segundo lector de getUpdates y hubo que
    eliminarlo. Pero la necesidad que lo justificaba era legítima: sin esto,
    durante la espera no responderían ni ESTADO ni PARADA.

    La diferencia con el hilo anterior es que este NO lee por su cuenta:
    llama al mismo consumidor único. Y se apaga en cuanto el bucle principal
    toma el control, así que nunca hay dos cosas leyendo a la vez — que era
    el defecto de fondo, no el hilo en sí.
    """
    import threading

    def bucle():
        fallos = 0
        while not stop_event.is_set():
            try:
                process_button_taps(ppi, notifier, None)
                fallos = 0
            except Exception as e:
                fallos += 1
                logger.warning("Fallo al sondear Telegram durante el arranque "
                               "(%d seguidos): %s", fallos, e)
                if fallos == 10:
                    logger.error("La escucha de Telegram lleva 10 fallos seguidos. "
                                 "La parada de emergencia puede no estar respondiendo.")
            stop_event.wait(intervalo)

    hilo = threading.Thread(target=bucle, name="telegram-arranque", daemon=True)
    hilo.start()
    logger.info("Escucha de Telegram activa durante el arranque. Mandá AYUDA para ver la lista.")
    return hilo
