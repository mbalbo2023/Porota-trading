"""
k_position_manager.py — Stop-loss, take-profit y P&L real por operación (v6.0)

CORRIGE UN HUECO CRÍTICO detectado en esta revisión: hasta la v5.0, el bot
mandaba una alerta de COMPRA con un precio sugerido, pero nunca decía
cuándo vender (ni stop-loss ni take-profit) ni cuánto comprar, y nunca
volvía a mirar la posición después de alertarla. El "P&L del día" que
existía (h_daily_report.py) era una foto de saldo general, no el resultado
de cada operación puntual — no alcanzaba para calcular un win rate real.

Qué hace ahora:
  1. Cuando j_main.py aprueba una alerta, calcula un STOP-LOSS y un
     TAKE-PROFIT a partir del ATR real del activo (no un número fijo — se
     adapta a la volatilidad de cada CEDEAR en cada momento) y registra la
     posición como "abierta".
  2. En cada vuelta del loop principal, chequea el precio actual contra el
     stop y el target de cada posición abierta. Si se tocó alguno de los
     dos, manda un aviso de SALIDA por Telegram con el resultado real de
     ESA operación (no una foto general de saldo) y la marca como cerrada.
  3. De la tabla closed_trades sale el win rate real del sistema — cuántas
     operaciones cerraron en take-profit vs. en stop-loss — que es lo que
     i_auto_tuner.py usa ahora para el aprendizaje mensual.

LÍMITE HONESTO: esto es un seguimiento por PRECIO, no una confirmación de
que vos ejecutaste la operación en PPI. Si el bot alertó pero no compraste,
igual va a "seguir" la posición como si la hubieras tomado. Para evitar que
eso ensucie el win rate, h_daily_report.py sigue cruzando contra las
órdenes reales de PPI para separar "alertado" de "efectivamente operado".
"""

import datetime
import logging
import os
import sqlite3
import time
import uuid
from typing import Optional

import aa_env_guard as env_guard
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

logger = logging.getLogger("position_manager")
DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")


def _init_tables():
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS open_positions (
            id TEXT PRIMARY KEY,
            ticker TEXT,
            entry_price REAL,
            stop_loss_price REAL,
            take_profit_price REAL,
            quantity INTEGER,
            ppi_order_id TEXT,
            opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            instrument_type TEXT DEFAULT 'CEDEARS',
            settlement TEXT DEFAULT 'A-24HS'
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS closed_trades (
            id TEXT PRIMARY KEY,
            ticker TEXT,
            entry_price REAL,
            exit_price REAL,
            quantity INTEGER,
            exit_reason TEXT,
            realized_pnl_ars REAL,
            realized_pnl_pct REAL,
            ppi_order_id TEXT,
            opened_at DATETIME,
            closed_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # AMPLIACIÓN v10.5 — migración suave para bases ya existentes (ver
    # mismo patrón en l_order_confirmation.py).
    for ddl in ("ALTER TABLE open_positions ADD COLUMN instrument_type TEXT DEFAULT 'CEDEARS'",
                "ALTER TABLE open_positions ADD COLUMN settlement TEXT DEFAULT 'A-24HS'",
                # NUEVO EN v12.0 (Instrucción 6, cierre del pendiente dejado en
                # BITACORA_v12_entradas.md entrada 11): guarda el precio que se
                # había ANALIZADO al generar la señal, distinto del precio real
                # de fill (entry_price), para poder medir slippage real de
                # ejecución — antes no existía ninguna columna que permitiera
                # esta comparación.
                "ALTER TABLE open_positions ADD COLUMN planned_entry_price REAL",
                "ALTER TABLE closed_trades ADD COLUMN planned_entry_price REAL",
                # NUEVO EN v13.0 — antes el flag scalping viajaba a
                # check_exits()/notify_order_result() pero nunca se
                # guardaba, así que no había forma de aislar SOLO las
                # operaciones de scalping para el Cost-Validator Halt
                # (u_aiops_watcher.validate_scalping_viability). Ver
                # get_recent_scalping_trades() más abajo.
                "ALTER TABLE open_positions ADD COLUMN scalping INTEGER DEFAULT 0",
                "ALTER TABLE closed_trades ADD COLUMN scalping INTEGER DEFAULT 0"):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


def open_position(ticker: str, entry_price: float, stop_loss_price: float,
                   take_profit_price: float, quantity: int, ppi_order_id: str = None,
                   instrument_type: str = "CEDEARS", settlement: str = "A-24HS",
                   planned_entry_price: Optional[float] = None, scalping: bool = False):
    """
    CORRECCIÓN DE LA AUDITORÍA 7.1 (acepto el hallazgo): antes solo se
    guardaba el ticker, y h_daily_report.py cruzaba alertas contra
    órdenes SOLO por ticker — frágil si hay más de una señal del mismo
    ticker en el día. Ahora se guarda también el ppi_order_id real (el
    que devuelve confirm_order() de PPI) para poder reconciliar por ID,
    no solo por nombre de instrumento.

    AMPLIADO v10.5: también se guarda instrument_type/settlement, para que
    check_exits() consulte la cotización con el tipo correcto en vez de
    asumir siempre CEDEARS/A-24HS (relevante ahora que ACCIONES/BONOS/ETF
    también pueden llegar a operarse de verdad).

    AMPLIADO v12.0: planned_entry_price es el precio que tenía la señal
    ANTES de la revalidación de precio en l_order_confirmation.py
    (distinto de entry_price, que es el precio real de fill). Si no se
    pasa, se asume igual a entry_price (slippage 0 registrado) en vez de
    quedar NULL — así get_recent_avg_slippage_pct() no tiene que manejar
    huecos de datos de llamadas viejas al abrir la posición.

    AMPLIADO v13.0: se guarda también si la posición se abrió en modo
    scalping — antes el flag viajaba hasta notify_order_result() y se
    perdía ahí, sin quedar registrado en ningún lado. Hace falta
    persistirlo para que u_aiops_watcher.validate_scalping_viability()
    pueda alimentarse de operaciones de scalping reales, no de todas.
    """
    _init_tables()
    if planned_entry_price is None:
        planned_entry_price = entry_price
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute(
        "INSERT INTO open_positions (id, ticker, entry_price, stop_loss_price, "
        "take_profit_price, quantity, ppi_order_id, instrument_type, settlement, "
        "planned_entry_price, scalping) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), ticker, entry_price, stop_loss_price, take_profit_price,
         quantity, ppi_order_id, instrument_type, settlement, planned_entry_price, int(scalping)),
    )
    conn.commit()
    conn.close()


def get_open_positions():
    _init_tables()
    conn = ac_db.connect_raw()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM open_positions")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _close_position(position, exit_price, reason, costs_pct):
    conn = ac_db.connect_raw()
    c = conn.cursor()

    gross_pnl = (exit_price - position["entry_price"]) * position["quantity"]
    cost_ars = position["entry_price"] * position["quantity"] * costs_pct
    realized_pnl = round(gross_pnl - cost_ars, 2)
    realized_pnl_pct = round(
        ((exit_price - position["entry_price"]) / position["entry_price"]) * 100 - (costs_pct * 100), 4
    )

    c.execute(
        "INSERT INTO closed_trades (id, ticker, entry_price, exit_price, quantity, exit_reason, "
        "realized_pnl_ars, realized_pnl_pct, ppi_order_id, opened_at, planned_entry_price, scalping) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), position["ticker"], position["entry_price"], exit_price,
         position["quantity"], reason, realized_pnl, realized_pnl_pct,
         position.get("ppi_order_id"), position["opened_at"],
         position.get("planned_entry_price"), position.get("scalping", 0)),
    )
    c.execute("DELETE FROM open_positions WHERE id = ?", (position["id"],))
    conn.commit()
    conn.close()
    return realized_pnl, realized_pnl_pct


def get_recent_avg_slippage_pct(days: int = 7) -> float:
    """NUEVO EN v12.0 (Instrucción 6) — slippage promedio REAL de ejecución
    (no estimado): |entry_price real - planned_entry_price| / planned_entry_price,
    de las operaciones cerradas en los últimos `days` días. Usada por
    u_aiops_watcher.py como una de las tres métricas del Isolation Forest.

    Cierra el pendiente explícito dejado en BITACORA_v12_entradas.md
    (entrada 11): antes no existía ninguna columna que permitiera
    calcular esto sin inventar un número.

    Devuelve 0.0 si no hay operaciones cerradas recientes con
    planned_entry_price registrado (en vez de None, para que el watcher
    de AIOps no tenga que manejar un tercer estado — "sin dato" se trata
    igual que "sin slippage observado todavía")."""
    _init_tables()
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute(
        "SELECT entry_price, planned_entry_price FROM closed_trades "
        "WHERE closed_at >= datetime('now', ?) AND planned_entry_price IS NOT NULL "
        "AND planned_entry_price > 0",
        (f"-{days} days",),
    )
    rows = c.fetchall()
    conn.close()
    if not rows:
        return 0.0
    deviations = [abs(entry - planned) / planned * 100 for entry, planned in rows if planned]
    return round(sum(deviations) / len(deviations), 4) if deviations else 0.0


def check_exits(ppi, notifier, costs_pct_fn, scalping=False):
    """Recorre las posiciones abiertas y cierra las que tocaron stop-loss o
    take-profit. costs_pct_fn es economics.calculate_trade_costs_pct, para no
    duplicar la fórmula de comisiones acá.

    CORRECCIÓN v10.5: antes se consultaba siempre con instrument_type=
    "CEDEARS", settlement="A-24HS" fijos, sin importar qué tipo de
    instrumento era la posición realmente — funcionaba de casualidad
    porque hasta ahora todo lo que se operaba de verdad eran CEDEARs. Con
    ACCIONES/BONOS/ETF ahora también ejecutables, se usa el
    instrument_type/settlement guardados al abrir la posición."""
    for pos in get_open_positions():
        inst_type = pos.get("instrument_type") or "CEDEARS"
        settlement = pos.get("settlement") or "A-24HS"
        mkt = ppi.get_market_data(pos["ticker"], inst_type, settlement)
        if not mkt or (time.time() - mkt.get("epoch_recv", 0)) > 30:
            continue
        price = mkt.get("price", 0)
        if price <= 0:
            continue

        reason = None
        if price <= pos["stop_loss_price"]:
            reason = "STOP_LOSS"
        elif price >= pos["take_profit_price"]:
            reason = "TAKE_PROFIT"

        if reason:
            costs_pct = costs_pct_fn()
            # NUEVO EN v14.0 — lock de actividad: mientras se está cerrando
            # una posición, aa_env_guard.is_system_idle() devuelve False y
            # cualquier reinicio pedido desde el panel web queda en espera.
            # Es una de las cuatro llaves de la Instrucción 1 ("que el
            # sistema esté totalmente desocupado").
            with env_guard.activity("POSITION_EXIT", pos["ticker"]):
                pnl_ars, pnl_pct = _close_position(pos, price, reason, costs_pct)
            # CORREGIDO EN v14.0 — bug real de v13.0: acá se pasaba
            # `scalping=scalping`, o sea el MODO GLOBAL con el que corre el
            # bot en este momento, no si ESA posición se había abierto como
            # scalping (columna `scalping`, que v13.0 agregó justamente para
            # eso). Si el modo se apagaba con posiciones de scalping
            # abiertas, el mensaje de resultado las reportaba como
            # operaciones normales.
            es_scalping = bool(pos.get("scalping", 0)) or scalping
            # NUEVO v10.5 — mensaje de resultado final enriquecido (entrada,
            # salida, cantidad), pedido explícito del usuario ("cuando
            # finalice, que me digan cómo finalizó la transacción").
            notifier.notify_order_result(
                ticker=pos["ticker"], reason=reason, pnl_ars=pnl_ars, pnl_pct=pnl_pct,
                entry_price=pos["entry_price"], exit_price=price, quantity=pos["quantity"],
                scalping=es_scalping,
            )
            logger.info("Posición cerrada: %s %s pnl=%.2f (%.2f%%)", pos["ticker"], reason, pnl_ars, pnl_pct)


def get_recent_scalping_trades(limit: int = 10) -> list:
    """NUEVO EN v13.0 — alimenta u_aiops_watcher.validate_scalping_viability()
    con operaciones de scalping YA CERRADAS y reales (no proyectadas),
    más recientes primero."""
    _init_tables()
    conn = ac_db.connect_raw()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT entry_price, exit_price FROM closed_trades WHERE scalping = 1 "
        "ORDER BY closed_at DESC LIMIT ?", (limit,),
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_win_rate(days_back: int = 30) -> dict:
    """Win rate real de las operaciones CERRADAS (no de las alertadas) en los
    últimos N días — esto es lo que hay que mirar para saber si el sistema
    funciona, no la cantidad de alertas enviadas."""
    _init_tables()
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute(
        "SELECT realized_pnl_ars FROM closed_trades WHERE closed_at >= datetime('now', ?)",
        (f"-{days_back} days",),
    )
    pnls = [r[0] for r in c.fetchall()]
    conn.close()

    total = len(pnls)
    wins = len([p for p in pnls if p > 0])
    return {
        "total_closed": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate_pct": round(wins / total * 100, 1) if total else None,
        "net_pnl_ars": round(sum(pnls), 2) if total else None,
        "lookback_days": days_back,
    }


# ---------------------------------------------------------------------- #
# NUEVO EN v14.0 — Cierre forzado de fin de día ("EOD Close All")
# ---------------------------------------------------------------------- #
# HALLAZGO CRÍTICO DE LA AUDITORÍA v13 (aceptado, era real): en modo
# scalping las posiciones se abren con un horizonte de MINUTOS
# (SCALPING_TARGET_HOLD_MINUTES, default 30) y su stop/take-profit están
# calculados para ese horizonte. Pero nada garantizaba que se cerraran ese
# mismo día: si el precio quedaba entre el stop y el target al sonar la
# campana, la posición seguía abierta hasta la rueda siguiente. Una
# operación dimensionada para 30 minutos pasaba a soportar el gap de
# apertura del día siguiente — riesgo overnight que el scalping,
# justamente, dice no tomar. Con un CEDEAR eso incluye lo que haya hecho
# el subyacente en Nueva York después del cierre local.
#
# QUÉ HACE: unos minutos antes del cierre (EOD_CLOSE_MINUTES_BEFORE, default
# 5), cierra a PRECIO DE MERCADO todas las posiciones intradía abiertas.
# Se usa mercado y no límite a propósito: acá el objetivo es salir sí o sí;
# un límite que no se ejecuta deja exactamente el problema que se quería
# evitar.
#
# QUÉ NO HACE (decisión explícita): NO cierra posiciones que no sean
# intradía. Una posición swing abierta con TARGET_HOLD_DAYS=5 tiene que
# sobrevivir a la noche — cerrarla al cierre sería romper la estrategia,
# no protegerla. El criterio para decidir es la columna `scalping` de la
# posición (o EOD_CLOSE_ALL_POSITIONS=true si se quiere forzar todo).
EOD_CLOSE_ENABLED = os.getenv("EOD_CLOSE_ENABLED", "true").lower() == "true"
EOD_CLOSE_ALL_POSITIONS = os.getenv("EOD_CLOSE_ALL_POSITIONS", "false").lower() == "true"


def _is_intraday(position: dict) -> bool:
    if EOD_CLOSE_ALL_POSITIONS:
        return True
    return bool(position.get("scalping", 0))


def close_all_intraday_positions(ppi, notifier, costs_pct_fn) -> dict:
    """Lo dispara el scheduler de j_main.py todos los días de rueda, unos
    minutos antes del cierre. Devuelve un resumen para el log y el informe.

    Cada cierre se hace bajo el lock de actividad, para que un reinicio
    pedido desde el panel web no pueda caer justo en el medio (Instrucción
    1 del pedido de v14).
    """
    if not EOD_CLOSE_ENABLED:
        logger.info("EOD_CLOSE_ENABLED=false — no se fuerza el cierre de posiciones intradía.")
        return {"enabled": False}

    posiciones = [p for p in get_open_positions() if _is_intraday(p)]
    if not posiciones:
        logger.info("Cierre de fin de día: no hay posiciones intradía abiertas.")
        return {"enabled": True, "cerradas": 0, "fallidas": 0}

    cerradas, fallidas = 0, []
    for pos in posiciones:
        ticker = pos["ticker"]
        inst_type = pos.get("instrument_type") or "CEDEARS"
        settlement = pos.get("settlement") or "A-24HS"
        try:
            with env_guard.activity("EOD_CLOSE", ticker):
                orden = ppi.sell_at_market(
                    ticker, pos["quantity"], instrument_type=inst_type, settlement=settlement,
                    external_id=f"eod-{pos['id']}",
                )
                if not orden:
                    fallidas.append(ticker)
                    continue

                # Precio de referencia para registrar el resultado: el último
                # dato de mercado disponible. Si no hay ninguno, se usa el
                # precio de entrada (PnL 0 registrado) y se avisa — inventar
                # un precio de salida ensuciaría el win rate, que es el
                # insumo del aprendizaje mensual.
                mkt = ppi.get_market_data(ticker, inst_type, settlement)
                precio_salida = float((mkt or {}).get("price") or 0) or pos["entry_price"]
                pnl_ars, pnl_pct = _close_position(pos, precio_salida, "EOD_CLOSE", costs_pct_fn())
                cerradas += 1
                notifier.notify_order_result(
                    ticker=ticker, reason="EOD_CLOSE", pnl_ars=pnl_ars, pnl_pct=pnl_pct,
                    entry_price=pos["entry_price"], exit_price=precio_salida,
                    quantity=pos["quantity"], scalping=bool(pos.get("scalping", 0)),
                )
        except Exception as e:
            logger.exception("Falló el cierre de fin de día de %s: %s", ticker, e)
            fallidas.append(ticker)

    resumen = (
        f"🔔 *CIERRE DE FIN DE DÍA*\n"
        f"Posiciones intradía cerradas a mercado: {cerradas}."
    )
    if fallidas:
        resumen += (
            f"\n\n🔴 *NO se pudieron cerrar:* {', '.join(fallidas)}.\n"
            "Revisalas a mano en la app de PPI antes del cierre: quedan expuestas "
            "al riesgo overnight."
        )
    notifier.send_telegram(resumen)
    logger.info("Cierre de fin de día: %s cerradas, %s fallidas.", cerradas, len(fallidas))
    return {"enabled": True, "cerradas": cerradas, "fallidas": len(fallidas), "tickers_fallidos": fallidas}
