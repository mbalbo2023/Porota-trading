"""
b_notifiers.py — Notificador (v7.0)

NUEVO EN v7.0: soporte de botones de confirmación en Telegram (Confirmar /
Cancelar) para el flujo de "un solo toque" antes de colocar una orden real
— ver l_order_confirmation.py para la explicación completa de por qué el
botón vive en Telegram y no dentro de la app de PPI.
"""

import os
import json
import logging
import sqlite3
import time
import datetime
import requests
from dotenv import load_dotenv
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

load_dotenv()
logger = logging.getLogger("notifiers")
DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")


def _save_failed_notification(message: str, error: str):
    """NUEVO EN v10.5 (segunda revisión) — ver docstring de send_telegram()."""
    try:
        conn = ac_db.connect_raw()
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS failed_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                failed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                message TEXT,
                error TEXT
            )
        """)
        c.execute("INSERT INTO failed_notifications (message, error) VALUES (?, ?)", (message, error))
        conn.commit()
        conn.close()
        logger.critical("No se pudo enviar por Telegram tras 3 intentos — guardado en "
                        "failed_notifications para revisión manual. Error: %s", error)
    except Exception as e:
        # Si hasta guardar el registro de la falla falla, no hay mucho más
        # que hacer acá salvo dejarlo bien claro en el log del proceso.
        logger.critical("Notificación perdida y no se pudo ni registrar el fallo: %s / mensaje: %s", e, message)


def get_failed_notifications(limit: int = 20) -> list:
    """Para revisar desde el dashboard o a mano qué avisos no llegaron."""
    try:
        conn = ac_db.connect_raw()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT * FROM failed_notifications ORDER BY failed_at DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return rows
    except sqlite3.OperationalError:
        return []


class MultiChannelNotifier:
    def __init__(self):
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

    def send(self, message: str, reply_markup=None, parse_mode: str = "Markdown"):
        """Envía un mensaje Telegram con un teclado opcional.

        ``ao_startup_gate`` necesita tres botones y por eso no puede reutilizar
        la confirmación genérica de dos botones. Este es el contrato común que
        faltaba entre el portón de arranque y el notificador real.
        """
        if not self.telegram_token or not self.telegram_chat_id:
            logger.warning("Telegram no configurado (falta TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID).")
            return False
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": parse_mode,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        last_error = None
        for intento in range(3):
            try:
                res = requests.post(url, json=payload, timeout=5)
                try:
                    respuesta = res.json()
                except Exception:
                    respuesta = {}
                if res.status_code == 200 and respuesta.get("ok") is True:
                    return True
                last_error = f"HTTP {res.status_code}: {res.text}"
                logger.error("Telegram respondió %s: %s", res.status_code, res.text)
            except Exception as e:
                last_error = str(e)
                logger.error("Error enviando Telegram (intento %d/3): %s", intento + 1, e)
            if intento < 2:
                time.sleep(2 * (intento + 1))
        _save_failed_notification(message, last_error)
        return False

    def send_telegram(self, message: str):
        """
        AMPLIADO EN v10.5 (segunda revisión) — hallazgo real: antes, si
        Telegram fallaba (corte de red, rate limit, timeout), el mensaje
        se logueaba como error y se perdía para siempre — incluyendo
        avisos importantes como "orden auto-ejecutada" o "resultado
        final". Ahora se reintenta un par de veces con una pausa corta
        (no es una cola en background con reintentos indefinidos — eso
        sumaría un hilo más y complejidad que no está pedida; es un
        reintento inmediato, razonable para un blip corto de red) y, si
        las tres veces fallan, el mensaje se guarda en la tabla
        failed_notifications en vez de perderse — se puede revisar
        después con get_failed_notifications() o desde el dashboard.
        """
        if not self.telegram_token or not self.telegram_chat_id:
            logger.warning("Telegram no configurado (falta TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID).")
            return False
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "Markdown"}
        last_error = None
        for intento in range(3):
            try:
                res = requests.post(url, json=payload, timeout=5)
                try:
                    respuesta = res.json()
                except Exception:
                    respuesta = {}
                if res.status_code == 200 and respuesta.get("ok") is True:
                    return True
                last_error = f"HTTP {res.status_code}: {res.text}"
                logger.error("Telegram respondió %s: %s", res.status_code, res.text)
            except Exception as e:
                last_error = str(e)
                logger.error("Error enviando Telegram (intento %d/3): %s", intento + 1, e)
            if intento < 2:
                time.sleep(2 * (intento + 1))  # 2s, luego 4s
        _save_failed_notification(message, last_error)
        return False

    def send_telegram_confirmation(self, message: str, order_id: str):
        """Igual que send_telegram, pero agrega dos botones (Confirmar/Cancelar)
        con el id de la orden pendiente codificado en el callback_data."""
        if not self.telegram_token or not self.telegram_chat_id:
            logger.warning("Telegram no configurado.")
            return False
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "reply_markup": json.dumps({
                "inline_keyboard": [[
                    {"text": "✅ Confirmar compra", "callback_data": f"CONFIRM:{order_id}"},
                    {"text": "❌ Cancelar", "callback_data": f"CANCEL:{order_id}"},
                ]]
            }),
        }
        try:
            res = requests.post(url, json=payload, timeout=5)
            try:
                respuesta = res.json()
            except Exception:
                respuesta = {}
            if res.status_code != 200 or respuesta.get("ok") is not True:
                logger.error("Telegram (confirmación) respondió %s: %s", res.status_code, res.text)
                return False
            return True
        except Exception as e:
            logger.error("Error enviando confirmación por Telegram: %s", e)
            return False

    def send_telegram_generic_confirmation(self, message: str, confirm_data: str, cancel_data: str,
                                           confirm_text: str = "✅ Confirmar",
                                           cancel_text: str = "❌ Cancelar"):
        """NUEVO EN v14.0 — mismo mecanismo que send_telegram_confirmation,
        pero con el callback_data libre en vez de fijo a "CONFIRM:<id>".

        Hizo falta al implementar la confirmación de reinicio por Telegram
        (Instrucción 1): esa confirmación no es una orden de compra, así que
        no puede reusar los prefijos CONFIRM/CANCEL —los procesa
        l_order_confirmation buscando una propuesta en pending_orders y no
        encontraría ninguna—. Con el callback_data parametrizado, cada
        módulo usa su propio prefijo (ENVOK/ENVNO para reinicios) y el
        despacho por prefijo los separa sin ambigüedad.
        """
        if not self.telegram_token or not self.telegram_chat_id:
            logger.warning("Telegram no configurado.")
            return False
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": message,
            "parse_mode": "Markdown",
            "reply_markup": json.dumps({
                "inline_keyboard": [[
                    {"text": confirm_text, "callback_data": confirm_data},
                    {"text": cancel_text, "callback_data": cancel_data},
                ]]
            }),
        }
        try:
            res = requests.post(url, json=payload, timeout=5)
            try:
                respuesta = res.json()
            except Exception:
                respuesta = {}
            if res.status_code != 200 or respuesta.get("ok") is not True:
                logger.error("Telegram (confirmación genérica) respondió %s: %s", res.status_code, res.text)
                return False
            return True
        except Exception as e:
            logger.error("Error enviando confirmación genérica por Telegram: %s", e)
            return False

    def get_telegram_button_taps(self, offset: int = 0):
        """LECTOR ÚNICO de getUpdates de todo el sistema (v16.2).

        Devuelve (taps, next_offset). Cada elemento de `taps` es o bien un tap
        de botón —claves `callback_id` y `data`— o bien un mensaje escrito
        —clave `text`—. En los dos casos viaja `sender_id`, ya validado.

        POR QUÉ TIENE QUE HABER UN SOLO LECTOR: Telegram entrega las
        actualizaciones con un offset incremental y las consume al
        entregarlas. Si dos módulos llaman a getUpdates por su cuenta, cada
        uno se come los mensajes del otro, de forma no determinística. El
        síntoma no es un error visible: son botones de confirmación de orden
        que a veces funcionan y a veces no, y propuestas que vencen sin
        motivo aparente. Hasta la versión anterior había exactamente eso —
        este lector y un segundo bucle en ar_telegram_commands—, que además
        desempaquetaba mal la tupla y fallaba en cada vuelta con
        AttributeError, absorbido por su propio except.

        El único consumidor autorizado es
        l_order_confirmation.process_button_taps(), que despacha por prefijo.

        Consulta corta (long-poll con timeout 0) — next_offset hay que
        next_offset hay que guardarlo y volver a pasarlo la próxima vez para no
        reprocesar el mismo tap dos veces.

        CORRECCIÓN v10.5 — Bug #5 de la auditoría 2 (rev. 2), CRÍTICA DE
        SEGURIDAD, confirmada en el código real: no se validaba que
        callback_query["from"]["id"] coincidiera con TELEGRAM_CHAT_ID antes
        de aceptar el tap. Cualquiera que descubriera el bot (o interceptara
        el mensaje) podía presionar "Confirmar" y ejecutar una orden real
        con la cuenta del dueño. Ahora se rechaza (y se loguea) cualquier
        tap cuyo remitente no coincida exactamente con el chat autorizado.
        Esto sigue siendo relevante en v10.5 aunque el modo de ejecución
        automática (SANDBOX) ya no dependa de botones — en PRODUCTION, con
        confirmación por Telegram, este control sigue siendo obligatorio."""
        if not self.telegram_token:
            return [], offset
        url = f"https://api.telegram.org/bot{self.telegram_token}/getUpdates"
        try:
            res = requests.get(url, params={"offset": offset, "timeout": 0}, timeout=10)
            data = res.json()
            taps = []
            next_offset = offset
            for update in data.get("result", []):
                next_offset = update["update_id"] + 1

                # --- Taps de botones inline ---
                cq = update.get("callback_query")
                if cq and "data" in cq:
                    sender_id = str(cq.get("from", {}).get("id", ""))
                    if self.telegram_chat_id and sender_id != str(self.telegram_chat_id):
                        logger.warning("Tap de Telegram NO AUTORIZADO rechazado (sender_id=%s).", sender_id)
                        continue
                    taps.append({"callback_id": cq["id"], "data": cq["data"],
                                 "sender_id": sender_id})
                    continue

                # ----------------------------------------------------------
                # NUEVO EN v16.2 — MENSAJES DE TEXTO
                # ----------------------------------------------------------
                # Este lector solo recolectaba callback_query. Los comandos
                # ESCRITOS (PARADA, ESTADO, LIQUIDAR, CONFIRMO CORTE) nunca se
                # leían: la parada de emergencia, que es la funcionalidad
                # insignia y el único control que se tiene lejos de la
                # computadora, no respondía a nada.
                #
                # Los botones inline fallan en clientes viejos de Telegram, y
                # quedarse sin forma de frenar el bot por un problema de
                # interfaz sería exactamente el peor momento. Por eso los dos
                # caminos tienen que funcionar.
                msg = update.get("message") or update.get("edited_message") or {}
                texto = (msg.get("text") or "").strip()
                if texto:
                    sender_id = str(msg.get("from", {}).get("id", ""))
                    if self.telegram_chat_id and sender_id != str(self.telegram_chat_id):
                        logger.warning(
                            "Mensaje de Telegram NO AUTORIZADO rechazado (sender_id=%s, "
                            "texto=%r).", sender_id, texto[:40])
                        continue
                    taps.append({"text": texto, "sender_id": sender_id})
            return taps, next_offset
        except Exception as e:
            logger.error("Error consultando updates de Telegram: %s", e)
            return [], offset

    def answer_telegram_callback(self, callback_id: str, text: str = ""):
        """Obligatorio llamarlo tras procesar un tap — si no, Telegram muestra
        el botón con un ícono de 'cargando' infinito en el celular del usuario."""
        if not self.telegram_token:
            return
        url = f"https://api.telegram.org/bot{self.telegram_token}/answerCallbackQuery"
        try:
            requests.post(url, json={"callback_query_id": callback_id, "text": text}, timeout=5)
        except Exception as e:
            logger.error("Error respondiendo callback de Telegram: %s", e)

    def notify_order_auto_executed(self, ticker: str, asset_class: str, quantity: int,
                                    price: float, amount_ars: float, stop_loss_price: float,
                                    take_profit_price: float, net_return_pct: float,
                                    win_rate_info: dict, reason: str, ppi_order_id, scalping: bool = False):
        """
        NUEVO EN v10.5 — pedido explícito del usuario: en SANDBOX, con
        ORDER_EXECUTION_MODE=auto, no hay botón de Confirmar/Cancelar — la
        orden se manda sola a PPI apenas se aprueba, y esto es lo único que
        avisa que se ejecutó. Incluye win rate estimado real (de
        k_position_manager.get_win_rate(), no un número inventado), la
        oportunidad detectada, cómo se ejecutó y cuánto dinero se puso en
        juego — tal como se pidió.
        """
        modo = "SCALPING" if scalping else asset_class
        if win_rate_info and win_rate_info.get("total_closed"):
            wr_txt = (f"{win_rate_info['win_rate_pct']}% "
                      f"({win_rate_info['wins']}/{win_rate_info['total_closed']} cerradas, "
                      f"últimos {win_rate_info.get('lookback_days', '?')} días)")
        else:
            wr_txt = "sin muestra suficiente todavía (menos de 1 operación cerrada histórica)"
        self.send_telegram(
            f"🤖 *ORDEN AUTO-EJECUTADA* ({modo}) — SANDBOX\n"
            f"Oportunidad detectada: {ticker}\n"
            f"Motivo: {reason}\n"
            f"Win rate estimado del sistema: {wr_txt}\n"
            f"Ejecución: COMPRA {quantity} x {ticker} @ ${price:,.2f}\n"
            f"Monto invertido: ${amount_ars:,.2f} ARS\n"
            f"Stop-loss: ${stop_loss_price:,.2f}  |  Take-profit: ${take_profit_price:,.2f}\n"
            f"Neto estimado: +{net_return_pct}%\n"
            f"ID de orden en PPI: {ppi_order_id}\n"
            f"(Ejecución automática por estar en ambiente SANDBOX — no mueve dinero real.)"
        )

    def notify_order_result(self, ticker: str, reason: str, pnl_ars: float, pnl_pct: float,
                             entry_price: float, exit_price: float, quantity: int, scalping: bool = False):
        """NUEVO EN v10.5 — mensaje de cierre enriquecido: se agrega al aviso
        de salida ya existente en k_position_manager.py el detalle de
        entrada/salida, para que "cómo finalizó la transacción" quede
        completo en un solo mensaje (pedido explícito del usuario)."""
        emoji = "🟢" if pnl_ars > 0 else "🔴"
        modo = "SCALPING" if scalping else "POSICIÓN"
        self.send_telegram(
            f"{emoji} *RESULTADO FINAL — {reason}* ({modo})\n"
            f"{ticker} x{quantity}: entrada ${entry_price:,.2f} → salida ${exit_price:,.2f}\n"
            f"Resultado real: {pnl_ars:+,.2f} ARS ({pnl_pct:+.2f}%)"
        )

    def notify_error(self, error_msg: str):
        # NUEVO EN v12.0 (Instrucción 7 / auditoría "Gestión de Secretos"):
        # error_msg puede venir directo de una excepción de la librería
        # oficial de PPI, que en algunos casos incluye el response HTTP
        # completo (headers con el Bearer token). Se ofusca acá, en el
        # borde de salida hacia Telegram, sin tocar el valor real usado
        # para autenticar en el resto del sistema.
        from c_ppi_client import obfuscate_secret
        self.send_telegram(
            f"⚠️ *ALERTA SISTEMA TRADING*\nDetalle: {obfuscate_secret(error_msg)}\n"
            "Estado: Intentando autocuración..."
        )

    def notify_recovery(self, service_name: str):
        self.send_telegram(
            f"✅ *SERVICIO RESTABLECIDO*\nEl servicio {service_name} volvió a estar disponible. "
            "Reanudando escaneo."
        )

    def notify_critical_event(self, event_type: str, details: str):
        """
        NUEVO EN v13.0 — implementación real de la sugerencia de
        Sugerencias_mockup_versión_2.pdf ("Solución de Notificación
        Crítica"). Se registra como callback en p_risk_guardian.py (ver
        set_critical_notifier(), llamado desde j_main.main()) para que
        CUALQUIER trigger_halt() — kill switch manual, por riesgo, o
        disparado automáticamente por el motor SRE ante un crash — mande
        este aviso de inmediato, con formato de alta prioridad, además de
        quedar grabado en risk_halts.

        Reusa obfuscate_secret() (igual que notify_error) por las dudas
        de que 'details' incluya algo sensible arrastrado desde una
        excepción — un halt por crash del motor SRE puede traer el texto
        crudo de la excepción original.
        """
        from c_ppi_client import obfuscate_secret
        message = (
            f"🔴 *ALERTA CRÍTICA: {event_type}*\n"
            f"🕒 Hora: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"📋 Detalles: {obfuscate_secret(details)}\n\n"
            "El bot dejó de emitir alertas nuevas y quedó en modo seguro. Revisá el motivo antes "
            "de liberar el kill switch (r_clear_kill_switch.py)."
        )
        self.send_telegram(message)
