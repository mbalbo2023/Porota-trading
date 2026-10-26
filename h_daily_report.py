"""
daily_report.py — Apertura de rueda, resumen de cierre y P&L diario (v4.0)

CAMBIO DE ESTA REVISIÓN: los avisos de apertura/cierre ya NO van por
email — van por Telegram, igual que el resto de las notificaciones del
bot (decisión del usuario: un solo canal, sin SMTP, sin Instagram).

Qué hace:
  1. Al inicio de la rueda (BYMA, hora Argentina): manda un mail avisando
     que el bot arranca a escanear, y guarda una "foto" del saldo
     disponible en pesos como punto de partida del día.
  2. Al cierre: cruza las alertas que el bot emitió hoy contra las
     órdenes reales cargadas en PPI para clasificarlas en:
       - EJECUTADAS: hay una orden real con ese ticker hoy, sin error.
       - PERDIDAS: se alertó pero no aparece ninguna orden (no la tomaste,
         o el precio se escapó antes de que operaras).
       - CON ERROR: hay una orden pero el broker la rechazó/canceló.
     Calcula el P&L del día como la diferencia entre el saldo en pesos al
     cierre y la foto tomada a la apertura, y lo manda por mail.
  3. Guarda todo en la tabla trade_outcomes, que auto_tuner.py ahora lee
     en el ajuste mensual — así el "aprendizaje" automático se basa en lo
     que realmente pasó (ejecutado/perdido/rechazado/P&L), no solo en los
     scores que el bot calculó antes de alertar.

LIMITACIÓN HONESTA sobre el P&L: PPI no expone en su API pública un
"resultado realizado por operación" listo para consumir. Comparar el
saldo en pesos de apertura vs. cierre es la forma más confiable de
aproximarlo con los datos disponibles, pero ese número también se mueve
si vos depositás o retirás dinero de la cuenta el mismo día — no es un
error de cálculo, es un límite de qué datos expone la API.
"""

import logging
import os
import sqlite3
from datetime import datetime, date
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

logger = logging.getLogger("daily_report")
DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")
TZ_NOTE = "Hora de Argentina (America/Argentina/Buenos_Aires)"


def _init_tables():
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trade_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            alerts_sent INTEGER,
            orders_matched INTEGER,
            alerts_missed INTEGER,
            orders_error INTEGER,
            net_pnl_ars REAL
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_balance_snapshot (
            date TEXT,
            session TEXT,
            amount_ars REAL,
            PRIMARY KEY (date, session)
        )
    """)
    conn.commit()
    conn.close()


def _get_pesos_balance(ppi) -> float:
    """Busca en get_available_balance() el renglón en pesos. Verificar contra
    una respuesta real de Sandbox que el campo 'name'/'simbol' efectivamente
    diga algo que contenga 'PESO' — es el criterio documentado por PPI, pero
    nunca está de más confirmarlo contra la cuenta real antes de confiar en
    el número para decisiones."""
    balances = ppi.get_available_balance()
    if not balances:
        return None
    for b in balances:
        label = f"{b.get('name', '')} {b.get('simbol', '')}".upper()
        if "PESO" in label or "ARS" in label:
            return float(b.get("amount", 0))
    # Fallback: si no se identificó el renglón en pesos, no inventar un número.
    logger.warning("No se identificó el renglón en pesos dentro de get_available_balance().")
    return None


def _save_balance_snapshot(session: str, amount):
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO daily_balance_snapshot (date, session, amount_ars) VALUES (?, ?, ?)",
        (date.today().isoformat(), session, amount),
    )
    conn.commit()
    conn.close()


def _read_balance_snapshot(session: str):
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute(
        "SELECT amount_ars FROM daily_balance_snapshot WHERE date = ? AND session = ?",
        (date.today().isoformat(), session),
    )
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def send_market_open_email(ppi, notifier):
    _init_tables()
    balance = _get_pesos_balance(ppi)
    if balance is not None:
        _save_balance_snapshot("open", balance)

    subject = "🟢 Arranca la rueda — Bot de Trading"
    body = (
        f"El bot arrancó a escanear oportunidades.\n"
        f"Hora: {datetime.now().strftime('%Y-%m-%d %H:%M')} ({TZ_NOTE})\n"
        f"Saldo en pesos al arranque: {balance if balance is not None else 'no disponible'}\n"
    )
    notifier.send_telegram(f"{subject}\n\n{body}")


def _get_closed_trades_today():
    """Detalle de k_position_manager.closed_trades para el día de hoy — para
    el desglose operación por operación del resumen de cierre.

    La tabla legacy carece de familia, moneda, liquidación e ID de cargo
    facturado; entry_price/quantity no bastan para reconciliar tarifas.
    _reconcile_real_costs_today falla cerrado hasta disponer de ese vínculo."""
    conn = ac_db.connect_raw()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    try:
        c.execute(
            "SELECT ticker, exit_reason, realized_pnl_ars, entry_price, quantity FROM closed_trades "
            "WHERE date(closed_at) = date('now', 'localtime')"
        )
        rows = [dict(r) for r in c.fetchall()]
    except sqlite3.OperationalError:
        rows = []  # closed_trades todavía no existe si k_position_manager no cerró nada nunca
    conn.close()
    return rows


def _reconcile_real_costs_today(ppi, closed_today: list) -> dict:
    """Reject unverifiable cross-family/account totals before reading broker data.

    closed_trades has no family, trading currency, settlement or billed charge
    key. get_tax_report returns daily movements, not charges linked to these
    fills. Summing them and adjusting a global fee stress factor would mix
    currencies, unrelated charges and contract types. Re-enable only after an
    exact operation-to-invoice join and component/rounding proof exists.
    """
    runtime_mode = (os.getenv("DASHBOARD_OPERATION_MODE") or
                    os.getenv("ENVIRONMENT") or "").strip().upper()
    execution_mode = os.getenv("ORDER_EXECUTION_MODE", "").strip().upper()
    if runtime_mode != "PRODUCTION_REAL" or execution_mode not in {"REAL", "PRODUCTION_REAL"}:
        state = "NOT_OBSERVABLE_IN_PAPER"
    else:
        state = "NO_GO_UNLINKED_FAMILY_CURRENCY_AND_BROKER_CHARGES"
    return {"state": state, "estimated_cost_ars": None,
            "real_cost_ars": None, "diff_ars": None}


def _get_today_alert_tickers():
    conn = ac_db.connect_raw()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute(
        "SELECT ticker FROM signals WHERE status = 'EXECUTED_ALERT' "
        "AND date(timestamp) = date('now', 'localtime')"
    )
    tickers = [r["ticker"] for r in c.fetchall()]
    conn.close()
    return tickers


def _get_today_own_order_ids():
    """IDs reales de PPI que el propio bot generó hoy (vía l_order_confirmation),
    para reconciliar por ID en vez de solo por ticker (hallazgo de la
    auditoría 7.1: cruzar solo por ticker es frágil si hay más de una señal
    del mismo instrumento en el día)."""
    conn = ac_db.connect_raw()
    c = conn.cursor()
    ids = set()
    for table in ("open_positions", "closed_trades"):
        try:
            c.execute(
                f"SELECT ppi_order_id FROM {table} WHERE ppi_order_id IS NOT NULL "
                f"AND date(opened_at) = date('now', 'localtime')"
            )
            ids.update(r[0] for r in c.fetchall() if r[0])
        except sqlite3.OperationalError:
            continue
    conn.close()
    return ids


def send_market_close_summary(ppi, notifier):
    _init_tables()
    alerted_tickers = _get_today_alert_tickers()
    own_order_ids = _get_today_own_order_ids()

    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    orders_today = ppi.get_orders(today_start, datetime.now()) or []

    executed, errored = set(), set()
    for o in orders_today:
        ticker = o.get("ticker")
        order_id = o.get("id") or o.get("orderId")
        # Preferimos matchear por ID (más preciso); si esta orden no tiene
        # ID reconocible o no es nuestra, caemos al matching por ticker
        # como antes (menos preciso, pero sigue siendo mejor que nada para
        # órdenes cargadas a mano cuando ORDER_AUTO_EXECUTE está apagado).
        is_ours = (order_id in own_order_ids) if order_id else False
        if not is_ours and ticker not in alerted_tickers:
            continue
        status = (o.get("status") or "").upper()
        if any(kw in status for kw in ["RECHAZ", "ERROR", "CANCEL"]):
            errored.add(ticker)
        else:
            executed.add(ticker)

    missed = [t for t in alerted_tickers if t not in executed and t not in errored]

    # CORRECCIÓN v10.5 — auditoría 3, hallazgo "Medición Falsa de P&L
    # Diario": la diferencia de saldo (cierre - apertura) se mantiene
    # como referencia informativa, pero YA NO es el número principal —
    # se distorsiona con depósitos/retiros y cobro de aranceles/custodia,
    # tal como señaló la auditoría. El P&L de TRADING (lo que importa para
    # saber si el sistema funciona) ahora se calcula sumando
    # realized_pnl_ars de las operaciones efectivamente cerradas hoy en
    # closed_trades (k_position_manager) — ese número sale del precio real
    # de entrada/salida de cada operación, no de una foto de saldo general,
    # así que no lo ensucia un depósito o un retiro.
    close_balance = _get_pesos_balance(ppi)
    open_balance = _read_balance_snapshot("open")
    balance_diff_pnl = None
    if close_balance is not None and open_balance is not None:
        balance_diff_pnl = round(close_balance - open_balance, 2)
    if close_balance is not None:
        _save_balance_snapshot("close", close_balance)

    # --- NUEVO EN v7.0: detalle operación por operación + lección de cada una ---
    closed_today = _get_closed_trades_today()

    # NUEVO EN v12.0 — reconciliación de costos reales vs. estimados (ver
    # _reconcile_real_costs_today, decisión de experto documentada ahí).
    cost_reconciliation = _reconcile_real_costs_today(ppi, closed_today) if closed_today else None

    # P&L de trading (fuente principal, v10.5): suma de resultados reales
    # de las operaciones cerradas hoy — no se mueve por depósitos/retiros.
    trading_pnl = round(sum(t["realized_pnl_ars"] for t in closed_today), 2) if closed_today else None

    outcomes = {
        "alerts_sent": len(alerted_tickers),
        "orders_matched": len(executed),
        "alerts_missed": len(missed),
        "orders_error": len(errored),
        "net_pnl_ars": trading_pnl,  # se guarda el P&L de trading, no el de diferencia de saldo
    }

    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO trade_outcomes "
        "(date, alerts_sent, orders_matched, alerts_missed, orders_error, net_pnl_ars) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (date.today().isoformat(), outcomes["alerts_sent"], outcomes["orders_matched"],
         outcomes["alerts_missed"], outcomes["orders_error"], outcomes["net_pnl_ars"]),
    )
    conn.commit()
    conn.close()

    subject = f"📊 Cierre de rueda {date.today().isoformat()} — Bot de Trading"
    trading_pnl_txt = f"{trading_pnl:+.2f} ARS" if trading_pnl is not None else "sin operaciones cerradas hoy"
    balance_diff_txt = (f"{balance_diff_pnl:+.2f} ARS" if balance_diff_pnl is not None
                         else "no disponible (falta saldo de apertura o de cierre)")

    detalle_lines = []
    if closed_today:
        detalle_lines.append("\nOperaciones cerradas hoy (detalle y lección):")
        for t in closed_today:
            resultado = "ganó" if t["realized_pnl_ars"] > 0 else "perdió"
            if t["exit_reason"] == "TAKE_PROFIT":
                leccion = "el análisis técnico + macro acertó la dirección — el criterio que la aprobó queda reforzado."
            else:
                leccion = "el precio fue en contra a pesar de la aprobación — el auto-tuning mensual va a mirar este caso al recalibrar los umbrales."
            detalle_lines.append(
                f"- {t['ticker']}: {resultado} {t['realized_pnl_ars']:+.2f} ARS "
                f"({t['exit_reason']}). Lección: {leccion}"
            )
    else:
        detalle_lines.append("\nNinguna operación cerró hoy (ni stop-loss ni take-profit tocados).")

    reconciliation_txt = ""
    if cost_reconciliation:
        if cost_reconciliation.get("state") == "NOT_OBSERVABLE_IN_PAPER":
            reconciliation_txt = (
                "\nCostos realmente cobrados por PPI: NOT_OBSERVABLE_IN_PAPER. "
                "No se atribuyen cargos de cuenta a fills simulados ni se ajusta el tarifario.\n"
            )
        elif cost_reconciliation.get("state") == "NO_GO_UNLINKED_FAMILY_CURRENCY_AND_BROKER_CHARGES":
            reconciliation_txt = (
                "\nReconciliación de costos: NO_GO, falta vincular cada operación "
                "por familia, moneda y liquidación a su comprobante de cargos. "
                "No se calcula desvío ni se ajusta el tarifario.\n"
            )
        elif cost_reconciliation["real_cost_ars"] is not None:
            reconciliation_txt = (
                f"\nReconciliación de costos (estimado vs. real PPI): "
                f"estimado ${cost_reconciliation['estimated_cost_ars']:.2f} vs. "
                f"real ${cost_reconciliation['real_cost_ars']:.2f} "
                f"(diferencia {cost_reconciliation['diff_ars']:+.2f} ARS). "
                f"El PnL de trading de arriba usa el costo ESTIMADO (necesario en tiempo real); "
                f"esta diferencia es solo para contabilidad, no cambia ninguna decisión ya tomada.\n"
            )
        else:
            reconciliation_txt = (
                "\nReconciliación de costos: no se pudo obtener el reporte real de PPI hoy "
                "(get_tax_report) — se sigue usando el costo estimado para todo.\n"
            )

    body = (
        f"Resumen del día ({TZ_NOTE}):\n\n"
        f"Alertas emitidas: {outcomes['alerts_sent']}\n"
        f"Ejecutadas (orden confirmada en PPI): {outcomes['orders_matched']} — {sorted(executed)}\n"
        f"Perdidas (alerta enviada, sin orden encontrada): {outcomes['alerts_missed']} — {missed}\n"
        f"Con error/rechazadas por el broker: {outcomes['orders_error']} — {sorted(errored)}\n\n"
        f"P&L de trading (suma de operaciones cerradas hoy, fuente principal): {trading_pnl_txt}\n"
        f"Variación total de saldo en pesos (informativo — incluye depósitos/retiros/aranceles "
        f"si los hubo, NO usar para medir el sistema): {balance_diff_txt}\n"
        + "\n".join(detalle_lines) + "\n"
        + reconciliation_txt + "\n"
        "Nota: el P&L de trading de arriba sale del precio real de entrada/salida de cada "
        "operación cerrada (closed_trades), así que no lo distorsiona un depósito o retiro que "
        "hayas hecho en la cuenta — para eso está separado de la variación de saldo total.\n\n"
        "Estos resultados quedaron guardados y se van a usar en el ajuste automático "
        "mensual de umbrales (ver auto_tuner.py)."
    )
    notifier.send_telegram(f"{subject}\n\n{body}")
