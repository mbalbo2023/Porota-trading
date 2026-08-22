"""
ag_kill_switch_supervisor.py — Kill switch con criterio propio (NUEVO EN v15.0)

EL PEDIDO
=========
"El kill switch tiene que tener inteligencia propia para poder reiniciarse y
no dejar operaciones huérfanas; no puedo hacerlo a mano."

Hasta v14.0 el kill switch era deliberadamente tonto y de una sola vía:
cualquier corte —desde "se cayó la red 30 segundos" hasta "perdimos el 8%
del capital en el día"— dejaba el bot detenido hasta que una persona entrara
por SSH al servidor y corriera r_clear_kill_switch.py. Para alguien que
opera desde una tablet por voz, eso es un bloqueo real: un corte técnico
trivial de un martes a la noche deja el bot apagado hasta el jueves.

LO QUE SE HACE EN v15.0, Y LO QUE DELIBERADAMENTE NO
====================================================
Se automatiza la recuperación de los cortes TÉCNICOS. NO se automatiza la
recuperación de los cortes FINANCIEROS.

No es una limitación técnica ni una salvedad burocrática: es la única forma
en que el pedido tiene sentido. Un kill switch que se libera solo después de
que el bot perdió plata no es un kill switch, es un botón de "seguí
perdiendo". La distinción es la siguiente:

  TRANSITORIO (se auto-recupera):
    - Caída de red / timeouts contra PPI
    - Circuit breaker abierto por latencia o errores HTTP
    - Stream de tiempo real caído
    - Rate limiting del bróker
    - Fallo del motor de IA (modelo caído, cuota)
    - Reinicio del contenedor sin causa financiera
    Causa: infraestructura. La condición se puede VERIFICAR resuelta desde
    el código, así que el bot puede decidir con evidencia.

  ESTRUCTURAL (nunca se auto-recupera):
    - Límite de pérdida diaria / drawdown máximo
    - Exposición por encima de lo permitido
    - Crash con diagnóstico CRÍTICO del motor SRE
    - Desvío de costos reales vs. estimados (Cost-Validator)
    - Discrepancia de posiciones entre el bot y PPI
    Causa: la lógica de trading o el mercado. No hay nada que el bot pueda
    "verificar" para saber que ya se puede volver a arriesgar plata.

PERO EL PEDIDO SE CUMPLE IGUAL, SIN SSH
=======================================
Para los cortes estructurales no hace falta la terminal: llega un mensaje de
Telegram con el motivo, el estado de las posiciones y un botón de
confirmación. Un toque desde la tablet libera el corte, con el mismo doble
chequeo que antes hacía el script. O sea: cero intervención manual para lo
técnico, y un toque —no una sesión SSH— para lo que sí requiere una decisión
humana. r_clear_kill_switch.py sigue existiendo como camino de emergencia.

"NO DEJAR OPERACIONES HUÉRFANAS" — EL DRENAJE SEGURO
====================================================
Esta es la parte más importante y la que no existía en ninguna versión
anterior. Antes, al dispararse el kill switch, el bot simplemente dejaba de
evaluar instrumentos nuevos. Lo que quedaba a mitad de camino se quedaba a
mitad de camino: órdenes enviadas a PPI sin confirmar, propuestas pendientes
de un botón de Telegram que ya nadie iba a apretar, posiciones abiertas con
stop-loss vigilado por un bot que estaba detenido.

drain_and_reconcile() se ejecuta AHORA en el momento del corte, antes de
que el bot se quede quieto, y hace cuatro cosas en orden:

  1) Cancela las propuestas pendientes que todavía no se enviaron a PPI
     (esas son inocuas: nunca existieron para el bróker).
  2) Reconcilia contra PPI todas las órdenes en estado SENT_TO_PPI:
     pregunta el estado real de cada una y la cierra en la base con lo que
     PPI diga que pasó. Acá es donde se resolvían mal las huérfanas.
  3) Compara las posiciones abiertas en la base contra el portfolio real de
     PPI y reporta cualquier diferencia (una posición que el bot cree tener
     y PPI no, o al revés).
  4) Deja constancia de las posiciones que quedan abiertas y SIN vigilancia
     automática de stop-loss, con su precio de corte, para que la decisión
     de sostenerlas o cerrarlas a mano sea informada.

El resultado de las cuatro va al mensaje de Telegram del corte. La idea es
que cuando suene el teléfono, el mensaje ya diga qué quedó pasando, en vez
de "kill switch activado" a secas.
"""

import os
import json
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from dotenv import load_dotenv

import ac_db
import p_risk_guardian as risk_guardian

load_dotenv()
logger = logging.getLogger("kill_switch_supervisor")

AUTO_RECOVERY_ENABLED = os.getenv("KILL_SWITCH_AUTO_RECOVERY", "true").lower() == "true"
# Cuánto esperar antes del primer intento de recuperación (que la causa
# transitoria tenga tiempo de resolverse sola).
COOLDOWN_MINUTES = float(os.getenv("KILL_SWITCH_COOLDOWN_MINUTES", "15"))
# Techo diario de recuperaciones automáticas. Si el bot se cortó y recuperó
# cuatro veces en un día, el problema no es transitorio: se queda detenido.
MAX_AUTO_RECOVERIES_PER_DAY = int(os.getenv("KILL_SWITCH_MAX_AUTO_RECOVERIES", "3"))
CHECK_INTERVAL_SECONDS = float(os.getenv("KILL_SWITCH_CHECK_INTERVAL", "300"))

# ---------------------------------------------------------------------------
# Clasificación de causas. Se hace por coincidencia de texto sobre el motivo
# del halt porque los motivos son strings libres desde v8.0 y cambiar eso
# ahora obligaría a tocar los ~12 call-sites de trigger_halt(). El default es
# el conservador: lo que no reconozco, es ESTRUCTURAL.
# ---------------------------------------------------------------------------
PATRONES_TRANSITORIOS = (
    "ws_timeout", "websocket", "stream", "circuit breaker", "circuitbreaker",
    "timeout", "connectionerror", "connection error", "rate limit", "ratelimit",
    "429", "502", "503", "504", "red", "network", "dns",
    "aiops: latencia", "aiops: latency", "motor ia", "gemini", "modelo de ia",
    "api de ppi no responde", "login fallido",
)
PATRONES_ESTRUCTURALES = (
    "pérdida", "perdida", "drawdown", "límite diario", "limite diario",
    "exposición", "exposicion", "capital", "cost-validator", "costos",
    "slippage", "discrepancia", "auto-sanación sre", "auto-sanacion sre",
    "gravedad crítica", "gravedad critica", "manual", "scalping",
)


def clasificar_causa(motivo: str) -> str:
    """Devuelve 'TRANSITORIA' o 'ESTRUCTURAL'. Ante la duda, ESTRUCTURAL."""
    if not motivo:
        return "ESTRUCTURAL"
    m = motivo.lower()
    # Lo estructural gana siempre: si un motivo menciona pérdida Y timeout,
    # es un corte por plata que además tuvo un timeout, no al revés.
    for p in PATRONES_ESTRUCTURALES:
        if p in m:
            return "ESTRUCTURAL"
    for p in PATRONES_TRANSITORIOS:
        if p in m:
            return "TRANSITORIA"
    return "ESTRUCTURAL"


def _init_table():
    with ac_db.connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kill_switch_recoveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ocurrido_en TEXT DEFAULT CURRENT_TIMESTAMP,
                motivo_original TEXT,
                clasificacion TEXT,
                resultado TEXT,
                verificaciones TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kill_switch_drains (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ocurrido_en TEXT DEFAULT CURRENT_TIMESTAMP,
                motivo TEXT,
                informe TEXT
            )
        """)


# ============================================================================
# 1. DRENAJE SEGURO — que no queden operaciones huérfanas
# ============================================================================
def drain_and_reconcile(ppi, notifier, position_manager, motivo: str = "") -> dict:
    """Se ejecuta en el instante del corte. Devuelve un informe con todo lo
    que quedó pasando. Está escrito para no tirar nunca: si una parte falla,
    las otras tres se ejecutan igual y el fallo queda en el informe. Un
    drenaje a medias es infinitamente mejor que un drenaje que crashea."""
    informe = {
        "momento": datetime.now().isoformat(timespec="seconds"),
        "motivo": motivo,
        "propuestas_canceladas": [],
        "ordenes_reconciliadas": [],
        "posiciones_abiertas": [],
        "discrepancias": [],
        "errores": [],
    }

    # --- Paso 1: propuestas pendientes que nunca llegaron a PPI ---
    try:
        with ac_db.connect() as conn:
            filas = conn.execute(
                "SELECT id, ticker, quantity FROM pending_orders "
                "WHERE status IN ('PENDING', 'AWAITING_CONFIRMATION')"
            ).fetchall()
            for pid, ticker, qty in filas:
                conn.execute(
                    "UPDATE pending_orders SET status = 'CANCELLED_BY_KILL_SWITCH' WHERE id = ?",
                    (pid,))
                informe["propuestas_canceladas"].append({"id": pid, "ticker": ticker, "cantidad": qty})
    except Exception as e:
        informe["errores"].append(f"Cancelación de propuestas: {e}")

    # --- Paso 2: órdenes ya enviadas a PPI — reconciliar contra el bróker ---
    try:
        with ac_db.connect() as conn:
            enviadas = conn.execute(
                "SELECT id, ticker, ppi_order_id, quantity, entry_price, stop_loss_price, "
                "take_profit_price, instrument_type, settlement FROM pending_orders "
                "WHERE status = 'SENT_TO_PPI'"
            ).fetchall()
        for (pid, ticker, ppi_order_id, qty, entry, sl, tp, itype, settle) in enviadas:
            registro = {"id": pid, "ticker": ticker, "ppi_order_id": ppi_order_id}
            try:
                # get_order_status() es el wrapper propio del proyecto sobre
                # orders.get_order_detail() de la librería oficial (ver la
                # ficha de c_ppi_client.py: el nombre corto es el que existe
                # acá, y ya resuelve account_number y reintentos por dentro).
                detalle = ppi.get_order_status(ppi_order_id) if ppi_order_id else None
                estado = (detalle or {}).get("status") or (detalle or {}).get("Status") or "DESCONOCIDO"
                ejecutado = (detalle or {}).get("quantityExecuted") or \
                            (detalle or {}).get("QuantityExecuted") or 0
                registro.update({"estado_en_ppi": estado, "ejecutado": ejecutado})
                if float(ejecutado or 0) > 0:
                    # Se ejecutó (total o parcialmente): la posición TIENE que
                    # existir en la base, o el bot pierde de vista plata real.
                    position_manager.open_position(
                        ticker, entry, sl, tp, float(ejecutado),
                        ppi_order_id=ppi_order_id,
                        instrument_type=itype or "CEDEARS",
                        settlement=settle or "A-24HS")
                    nuevo_estado = "EXECUTED_RECONCILED"
                else:
                    nuevo_estado = f"CLOSED_BY_DRAIN_{estado}"
                with ac_db.connect() as conn:
                    conn.execute("UPDATE pending_orders SET status = ? WHERE id = ?",
                                 (nuevo_estado, pid))
                registro["resolucion"] = nuevo_estado
            except Exception as e:
                registro["resolucion"] = f"NO SE PUDO CONSULTAR: {e}"
                informe["errores"].append(f"Reconciliación de orden {ppi_order_id}: {e}")
            informe["ordenes_reconciliadas"].append(registro)
    except Exception as e:
        informe["errores"].append(f"Lectura de órdenes enviadas: {e}")

    # --- Paso 3: posiciones abiertas y comparación contra PPI ---
    abiertas = []
    try:
        abiertas = position_manager.get_open_positions() or []
        for pos in abiertas:
            informe["posiciones_abiertas"].append({
                "ticker": pos.get("ticker"),
                "cantidad": pos.get("quantity"),
                "precio_entrada": pos.get("entry_price"),
                "stop_loss": pos.get("stop_loss_price"),
                "take_profit": pos.get("take_profit_price"),
                "vigilancia_automatica": False,  # el bot queda detenido
            })
    except Exception as e:
        informe["errores"].append(f"Lectura de posiciones abiertas: {e}")

    try:
        # get_available_balance() devuelve la tenencia de la cuenta: es el
        # equivalente a "portfolio" en la librería oficial de PPI. No existe
        # un get_portfolio() en este wrapper — usar uno inventado habría
        # hecho fallar el drenaje justo cuando más se lo necesita.
        portfolio = ppi.get_available_balance() or []
        tickers_ppi = set()
        for item in portfolio:
            if not isinstance(item, dict):
                continue
            t = (item.get("ticker") or item.get("Ticker") or item.get("symbol")
                 or item.get("Symbol") or item.get("instrument"))
            if t:
                tickers_ppi.add(str(t).upper())
        tickers_bot = {str(p.get("ticker", "")).upper() for p in abiertas}
        for t in sorted(tickers_bot - tickers_ppi):
            informe["discrepancias"].append(
                f"{t}: el bot la tiene como posición abierta y NO aparece en el portfolio de PPI")
        for t in sorted(tickers_ppi - tickers_bot):
            informe["discrepancias"].append(
                f"{t}: está en el portfolio de PPI y el bot no la tiene registrada")
    except Exception as e:
        informe["errores"].append(f"Comparación contra el portfolio de PPI: {e}")

    # --- Persistir el informe ---
    try:
        _init_table()
        with ac_db.connect() as conn:
            conn.execute("INSERT INTO kill_switch_drains (motivo, informe) VALUES (?, ?)",
                         (motivo, json.dumps(informe, ensure_ascii=False)))
    except Exception as e:
        logger.error("No se pudo grabar el informe de drenaje: %s", e)

    # --- Avisar, con el detalle adentro del mensaje ---
    if notifier:
        try:
            mensaje = _formatear_informe(informe)
            if clasificar_causa(motivo) == "ESTRUCTURAL":
                # Corte estructural: hace falta una decisión humana, pero NO
                # una sesión SSH. Van los botones en el mismo mensaje.
                notifier.send_telegram_generic_confirmation(
                    mensaje + "\n\n👉 Este corte NO se libera solo (causa financiera o de "
                              "lógica de trading). Revisá lo de arriba y decidí.",
                    confirm_data="KSOK:manual",
                    cancel_data="KSNO:manual",
                    confirm_text="✅ Revisado, reanudar",
                    cancel_text="🛑 Mantener detenido",
                )
            else:
                notifier.send_telegram(
                    mensaje + "\n\n🔄 El corte parece técnico: el supervisor va a intentar "
                              f"recuperarlo solo en los próximos {int(COOLDOWN_MINUTES)} minutos "
                              "si las condiciones se normalizan.")
        except Exception:
            pass
    return informe


def _formatear_informe(inf: dict) -> str:
    partes = ["🛑 *KILL SWITCH — DRENAJE COMPLETADO*", f"Motivo: {inf.get('motivo')}", ""]
    can = inf["propuestas_canceladas"]
    partes.append(f"• Propuestas canceladas antes de llegar a PPI: {len(can)}"
                  + (f" ({', '.join(c['ticker'] for c in can)})" if can else ""))
    rec = inf["ordenes_reconciliadas"]
    partes.append(f"• Órdenes ya enviadas a PPI, reconciliadas: {len(rec)}")
    for r in rec:
        partes.append(f"   – {r['ticker']}: {r.get('resolucion')}")
    pos = inf["posiciones_abiertas"]
    if pos:
        partes.append(f"• ⚠️ Posiciones que quedan ABIERTAS y SIN vigilancia automática "
                      f"de stop-loss ({len(pos)}):")
        for p in pos:
            partes.append(f"   – {p['ticker']} x{p['cantidad']} | entrada ${p['precio_entrada']} "
                          f"| SL ${p['stop_loss']} | TP ${p['take_profit']}")
        partes.append("   Decidí vos si las sostenés o las cerrás desde la app de PPI.")
    else:
        partes.append("• No quedan posiciones abiertas.")
    if inf["discrepancias"]:
        partes.append("• 🔴 DISCREPANCIAS con el portfolio de PPI:")
        partes.extend(f"   – {d}" for d in inf["discrepancias"])
    if inf["errores"]:
        partes.append("• Errores durante el drenaje (revisar logs):")
        partes.extend(f"   – {e}" for e in inf["errores"][:5])
    return "\n".join(partes)


# ============================================================================
# 2. VERIFICACIONES antes de auto-recuperar
# ============================================================================
def _verificar_condiciones(ppi, position_manager) -> Dict[str, Any]:
    """Cada verificación devuelve True/False y su motivo. TODAS tienen que dar
    True para que el bot se libere solo. Es una lista corta a propósito: cada
    chequeo tiene que ser algo que se pueda comprobar de verdad, no una
    heurística optimista."""
    checks = {}

    # a) PPI responde y la sesión está viva.
    try:
        ppi.get_market_data("AL30", "BONOS", "INMEDIATA")
        checks["ppi_responde"] = (True, "PPI respondió a una consulta de mercado")
    except Exception as e:
        checks["ppi_responde"] = (False, f"PPI sigue sin responder: {e}")

    # b) El circuit breaker está cerrado.
    try:
        estado = getattr(ppi, "_circuit_state", None)
        nombre = getattr(estado, "name", str(estado))
        checks["circuit_cerrado"] = (nombre == "CLOSED", f"Circuit breaker: {nombre}")
    except Exception as e:
        checks["circuit_cerrado"] = (False, f"No se pudo leer el circuit breaker: {e}")

    # c) No quedan órdenes sin reconciliar.
    try:
        with ac_db.connect() as conn:
            pendientes = conn.execute(
                "SELECT COUNT(*) FROM pending_orders WHERE status = 'SENT_TO_PPI'"
            ).fetchone()[0]
        checks["sin_ordenes_huerfanas"] = (pendientes == 0,
                                           f"Órdenes sin reconciliar: {pendientes}")
    except Exception as e:
        checks["sin_ordenes_huerfanas"] = (False, f"No se pudo verificar: {e}")

    # d) El motor de IA responde (si no, el bot vetearía todo igual).
    try:
        import af_model_registry
        estado_modelo = af_model_registry.get_status()
        ok = estado_modelo.get("modelo_activo") is not None
        checks["motor_ia_disponible"] = (ok, f"Modelo activo: {estado_modelo.get('modelo_activo')}")
    except Exception as e:
        checks["motor_ia_disponible"] = (False, f"No se pudo verificar el motor de IA: {e}")

    return checks


def _recuperaciones_hoy() -> int:
    try:
        _init_table()
        with ac_db.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM kill_switch_recoveries "
                "WHERE resultado = 'RECUPERADO' AND date(ocurrido_en) = date('now', 'localtime')"
            ).fetchone()[0]
    except Exception:
        return 0


def _momento_del_halt() -> Optional[datetime]:
    try:
        with ac_db.connect() as conn:
            row = conn.execute(
                "SELECT triggered_at FROM risk_halts WHERE cleared = 0 "
                "ORDER BY triggered_at DESC LIMIT 1").fetchone()
        if row and row[0]:
            return datetime.fromisoformat(str(row[0]).replace("T", " ").split(".")[0])
    except Exception:
        pass
    return None


def intentar_recuperacion(ppi, notifier, position_manager) -> dict:
    """El corazón del supervisor. Devuelve un dict con lo que se decidió y por
    qué — se muestra tal cual en el panel, para que nunca haya que adivinar
    por qué el bot se liberó (o no)."""
    _init_table()
    resultado = {"momento": datetime.now().isoformat(timespec="seconds"), "accion": "NADA"}

    if not risk_guardian.is_halted():
        resultado["accion"] = "SIN_CORTE_ACTIVO"
        return resultado

    motivo = risk_guardian.halt_reason()
    clasificacion = clasificar_causa(motivo)
    resultado.update({"motivo": motivo, "clasificacion": clasificacion})

    if clasificacion == "ESTRUCTURAL":
        resultado["accion"] = "REQUIERE_HUMANO"
        resultado["explicacion"] = (
            "El corte tiene causa financiera o de lógica de trading. El bot no se "
            "libera solo: hace falta que una persona lo confirme (botón de Telegram "
            "o r_clear_kill_switch.py).")
        return resultado

    if not AUTO_RECOVERY_ENABLED:
        resultado["accion"] = "AUTO_RECUPERACION_DESACTIVADA"
        return resultado

    # Cooldown: darle tiempo a la causa transitoria de resolverse sola.
    inicio = _momento_del_halt()
    if inicio and (datetime.now() - inicio) < timedelta(minutes=COOLDOWN_MINUTES):
        resultado["accion"] = "EN_COOLDOWN"
        resultado["explicacion"] = f"Esperando {COOLDOWN_MINUTES} minutos desde el corte."
        return resultado

    # Techo diario: si se corta y recupera todo el tiempo, no es transitorio.
    hoy = _recuperaciones_hoy()
    if hoy >= MAX_AUTO_RECOVERIES_PER_DAY:
        resultado["accion"] = "TECHO_DIARIO_ALCANZADO"
        resultado["explicacion"] = (
            f"Ya hubo {hoy} recuperaciones automáticas hoy (techo: "
            f"{MAX_AUTO_RECOVERIES_PER_DAY}). Un problema que vuelve tantas veces "
            "dejó de ser transitorio: queda detenido hasta revisión humana.")
        _guardar_recuperacion(motivo, clasificacion, "TECHO_DIARIO", {})
        if notifier:
            try:
                notifier.send_telegram(
                    "🔴 *EL BOT SE CORTÓ DEMASIADAS VECES HOY*\n"
                    f"{hoy} recuperaciones automáticas en el día por causas técnicas.\n"
                    f"Último motivo: {motivo}\n\n"
                    "Queda detenido hasta que lo revises. Algo de la infraestructura "
                    "no está bien y auto-recuperar otra vez sería tapar el problema.")
            except Exception:
                pass
        return resultado

    # Verificaciones reales.
    checks = _verificar_condiciones(ppi, position_manager)
    resultado["verificaciones"] = {k: {"ok": v[0], "detalle": v[1]} for k, v in checks.items()}
    fallidas = [k for k, v in checks.items() if not v[0]]

    if fallidas:
        resultado["accion"] = "CONDICIONES_NO_DADAS"
        resultado["explicacion"] = f"Todavía no se cumplen: {', '.join(fallidas)}"
        return resultado

    # Todo verde: liberar.
    risk_guardian.clear_halt()
    _guardar_recuperacion(motivo, clasificacion, "RECUPERADO", resultado["verificaciones"])
    resultado["accion"] = "RECUPERADO"
    logger.warning("Kill switch liberado automáticamente. Motivo original: %s", motivo)
    if notifier:
        try:
            detalle = "\n".join(f"   ✓ {v['detalle']}" for v in resultado["verificaciones"].values())
            notifier.send_telegram(
                "🟢 *BOT RECUPERADO AUTOMÁTICAMENTE*\n"
                f"El corte era técnico y la causa se resolvió.\n"
                f"Motivo original: {motivo}\n\n"
                f"Verificaciones antes de reanudar:\n{detalle}\n\n"
                f"Recuperaciones automáticas hoy: {hoy + 1} de {MAX_AUTO_RECOVERIES_PER_DAY}.")
        except Exception:
            pass
    return resultado


def _guardar_recuperacion(motivo: str, clasificacion: str, resultado: str, checks: dict):
    try:
        with ac_db.connect() as conn:
            conn.execute(
                "INSERT INTO kill_switch_recoveries (motivo_original, clasificacion, resultado, "
                "verificaciones) VALUES (?, ?, ?, ?)",
                (motivo, clasificacion, resultado, json.dumps(checks, ensure_ascii=False)))
    except Exception as e:
        logger.error("No se pudo grabar la recuperación: %s", e)


def aprobar_desde_telegram(notifier=None) -> bool:
    """Libera un corte ESTRUCTURAL con confirmación humana, sin SSH. La llama
    el handler del botón de Telegram en b_notifiers.py. Devuelve True si
    liberó. Deja el mismo rastro auditado que el script de consola."""
    if not risk_guardian.is_halted():
        return False
    motivo = risk_guardian.halt_reason()
    risk_guardian.clear_halt()
    _guardar_recuperacion(motivo, clasificar_causa(motivo), "APROBADO_POR_HUMANO", {})
    logger.warning("Kill switch liberado por confirmación humana desde Telegram: %s", motivo)
    if notifier:
        try:
            notifier.send_telegram(
                "🟢 *KILL SWITCH LIBERADO POR VOS*\n"
                f"Corte original: {motivo}\n"
                "El bot retoma la evaluación de instrumentos en el próximo ciclo.")
        except Exception:
            pass
    return True


def estado() -> dict:
    """Para el panel: qué pasó con los cortes y las recuperaciones."""
    try:
        _init_table()
        with ac_db.connect() as conn:
            recs = conn.execute(
                "SELECT ocurrido_en, motivo_original, clasificacion, resultado "
                "FROM kill_switch_recoveries ORDER BY id DESC LIMIT 10").fetchall()
            drains = conn.execute(
                "SELECT ocurrido_en, motivo FROM kill_switch_drains ORDER BY id DESC LIMIT 5"
            ).fetchall()
        return {
            "halted": risk_guardian.is_halted(),
            "motivo_actual": risk_guardian.halt_reason(),
            "clasificacion_actual": clasificar_causa(risk_guardian.halt_reason()),
            "auto_recuperacion_habilitada": AUTO_RECOVERY_ENABLED,
            "recuperaciones_hoy": _recuperaciones_hoy(),
            "techo_diario": MAX_AUTO_RECOVERIES_PER_DAY,
            "ultimas_recuperaciones": [
                {"cuando": a, "motivo": b, "clasificacion": c, "resultado": d}
                for a, b, c, d in recs],
            "ultimos_drenajes": [{"cuando": a, "motivo": b} for a, b in drains],
        }
    except Exception as e:
        return {"error": str(e)}


class KillSwitchSupervisor:
    """Hilo daemon que revisa cada CHECK_INTERVAL_SECONDS si hay un corte
    transitorio en condiciones de levantarse."""

    def __init__(self, ppi, notifier, position_manager):
        self.ppi = ppi
        self.notifier = notifier
        self.position_manager = position_manager
        self._stop = threading.Event()

    def _loop(self):
        while not self._stop.wait(CHECK_INTERVAL_SECONDS):
            try:
                if risk_guardian.is_halted():
                    r = intentar_recuperacion(self.ppi, self.notifier, self.position_manager)
                    logger.info("Supervisor del kill switch: %s", r.get("accion"))
            except Exception as e:
                logger.error("Error en el supervisor del kill switch: %s", e)

    def start(self):
        _init_table()
        threading.Thread(target=self._loop, name="kill_switch_supervisor", daemon=True).start()
        logger.info("Supervisor del kill switch activo (auto-recuperación: %s, cada %ss).",
                    AUTO_RECOVERY_ENABLED, CHECK_INTERVAL_SECONDS)

    def stop(self):
        self._stop.set()
