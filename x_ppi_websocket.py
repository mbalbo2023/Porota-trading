"""
x_ppi_websocket.py — Streams de tiempo real de PPI (Market Data + Account Data).
REESCRITO POR COMPLETO EN v14.0.

QUÉ ERA ESTO EN v13.0 Y POR QUÉ ESTABA MAL
==========================================
La versión anterior abría un WebSocket crudo (paquete `websocket-client`)
contra una URL que el propio archivo admitía no haber podido confirmar
("LÍMITE HONESTO"), mandaba una suscripción con un formato inventado
({"action": "subscribe", ...}) y esperaba ticks con un formato también
inventado ({"ticker": ..., "price": ...}). Estaba apagado por default, así
que nunca falló — pero tampoco habría funcionado nunca si se encendía. La
auditoría de la v13 lo marcó como "Falso WebSocket" y tiene razón en el
diagnóstico.

QUÉ SE VERIFICÓ AHORA (v14.0) CONTRA LA DOCUMENTACIÓN OFICIAL
=============================================================
Se revisó la documentación oficial de PPI
(https://itatppi.github.io/ppi-official-api-docs/api/documentacionPython/),
secciones "Realtime Subscription to Market Data" y "Realtime Subscription
to Account Data". Resultado: PPI SÍ ofrece tiempo real, y la propia
librería oficial `ppi-client` (la que este proyecto ya usa para el REST) lo
expone. No hace falta ningún WebSocket casero ni armar el hub de SignalR a
mano:

    ppi.realtime.connect_to_market_data(onconnect, ondisconnect, onmarketdata)
    ppi.realtime.subscribe_to_element(Instrument("AAPL", "CEDEARS", "A-48HS"))
    ppi.realtime.connect_to_account(onconnect, ondisconnect, onaccountdata)
    ppi.realtime.subscribe_to_account_data(account_number)
    ppi.realtime.start_connections()

El formato real de cada mensaje también está documentado y es el que se
parsea más abajo: Ticker, Price, Settlement, Type, Trade, Bids[], Offers[],
VolumeTotalAmount, Date.

MATIZ IMPORTANTE SOBRE LA AUDITORÍA v13 (parcialmente correcta)
===============================================================
La auditoría acertó el diagnóstico ("el WebSocket es simulado") pero su
solución propuesta —armar a mano un HubConnectionBuilder de signalrcore
contra "wss://api.test.portfoliopersonal.com/marketdatahub" y escuchar un
evento "BroadcastMarketData"— reemplazaba un contrato inventado por OTRO
contrato inventado: ni esa URL ni ese nombre de evento aparecen en la
documentación oficial. Implementarla habría dejado el mismo problema con
más código encima. Se toma el hallazgo y se descarta la implementación
sugerida: la vía correcta es la librería oficial, que ya resuelve la
negociación del hub, el token y la reconexión, y que se mantiene sola si
PPI cambia el protocolo por dentro.

QUÉ APORTA DE VERDAD (no es sólo "latencia más baja")
=====================================================
1) MARKET DATA: precio y puntas sin gastar llamadas del rate limit. Con 40
   instrumentos y un límite de 2 req/s, una vuelta completa de polling se
   va decenas de segundos solo en pedir precios. Con el stream, esos
   precios ya están en memoria y el REST queda libre para lo que sí lo
   necesita (órdenes, saldos, books profundos). Es la diferencia entre que
   el modo scalping sea viable o no.
2) ACCOUNT DATA: PPI avisa por push cada cambio de estado de una orden
   (Ticker, OrderId, QuantityExecuted, Status). Eso permite resolver una
   ejecución parcial en el momento en que ocurre, en vez de repreguntar el
   estado cada 2 segundos como hacía v13.0 (ver
   l_order_confirmation._resolve_filled_quantity, que ahora consulta
   primero esta caché).

RED DE SEGURIDAD (el stream nunca es la única fuente de un dato)
================================================================
- Cada tick guarda el instante en que llegó. get_cached_price() devuelve
  None si el dato tiene más de PPI_STREAM_MAX_TICK_AGE_SECONDS: el que
  pregunta cae al polling HTTP de siempre, sin enterarse.
- Un watchdog avisa por Telegram si el stream estaba vivo y se quedó mudo
  — exactamente el escenario que marcaba la auditoría ("si el hilo muere,
  el bot lee información vieja").
- Si la librería no expone `realtime` (versión vieja de ppi-client) o la
  conexión falla, se loguea y el bot sigue igual que sin este módulo. Nada
  de esto puede tumbar el proceso principal.
"""

import json
import logging
import os
import random
import threading
import time
from typing import Optional

logger = logging.getLogger("ppi_realtime")

# ACLARACIÓN DE NOMBRE: el archivo conserva el nombre x_ppi_websocket.py (en
# vez de renombrarse) a propósito: está referenciado por nombre en el
# Documento Maestro, en c_ppi_client.py y en el pipeline de CI. El
# transporte por debajo sigue siendo WebSocket (SignalR lo usa), así que el
# nombre no miente; lo que cambió es que ahora habla el protocolo real.

STREAM_ENABLED = os.getenv("PPI_STREAM_ENABLED", os.getenv("PPI_WEBSOCKET_ENABLED", "true")).lower() == "true"
STREAM_ACCOUNT_ENABLED = os.getenv("PPI_STREAM_ACCOUNT_ENABLED", "true").lower() == "true"
MAX_TICK_AGE_SECONDS = int(os.getenv("PPI_STREAM_MAX_TICK_AGE_SECONDS", "5"))
STALE_ALERT_SECONDS = int(os.getenv("PPI_STREAM_STALE_ALERT_SECONDS", "120"))
WATCHDOG_INTERVAL_SECONDS = int(os.getenv("PPI_STREAM_WATCHDOG_INTERVAL_SECONDS", "60"))
MAX_SUBSCRIPTIONS = int(os.getenv("PPI_STREAM_MAX_SUBSCRIPTIONS", "60"))
RECONNECT_BACKOFF_SECONDS = [5, 15, 30, 60, 120]
# NUEVO EN v15.0 — la auditoría de v14 marcó "carencia de retroceso
# exponencial" en este archivo. El hallazgo es un FALSO POSITIVO PARCIAL: el
# backoff escalonado ya existía desde v14.0 (la tabla de acá arriba) y su
# efecto es equivalente al 2**intento que proponía la auditoría. Pero al
# revisarlo aparecieron DOS problemas reales que la auditoría no menciona y
# que sí se corrigen ahora:
#
#   1) SIN JITTER. Con una tabla fija, los dos streams (market data y account
#      data) que se caen juntos —que es lo normal, porque se caen por la
#      misma razón— reintentan en el mismo segundo exacto, una y otra vez.
#      Eso es justamente el patrón que dispara el rate limiting del bróker
#      que la auditoría quería evitar. Ahora se agrega un jitter aleatorio.
#   2) SIN ESCALADA. Los reintentos eran infinitos y silenciosos: un stream
#      que no vuelve nunca reintentaba para siempre, y lo único que avisaba
#      era el watchdog de ticks vencidos. Ahora, después de
#      MAX_SILENT_RETRIES intentos fallidos consecutivos, se avisa por
#      Telegram; y si el stream de CUENTA (el que reporta las ejecuciones de
#      órdenes) queda caído con posiciones abiertas, se dispara el kill
#      switch — porque operar sin enterarse de los fills sí es peligroso.
#
# Lo que NO se implementa de la propuesta de la auditoría: gatillar el kill
# switch tras 5 reintentos del stream de MARKET DATA. Ese stream tiene
# fallback automático a polling HTTP (ver docstring de arriba): cortar la
# operatoria por su caída sería apagar el bot por un problema que el propio
# bot ya sabe sortear.
RECONNECT_JITTER_SECONDS = float(os.getenv("PPI_STREAM_RECONNECT_JITTER", "5"))
MAX_SILENT_RETRIES = int(os.getenv("PPI_STREAM_MAX_SILENT_RETRIES", "5"))

# Caché de precios: {(ticker, instrument_type): {...}}
_tick_cache = {}
_cache_lock = threading.Lock()

# Caché de estado de órdenes recibido por push: {str(order_id): {...}}
_order_cache = {}
_order_lock = threading.Lock()

_stats = {
    "connected": False,
    "account_connected": False,
    "ticks_received": 0,
    "last_tick_epoch": 0.0,
    "subscribed": 0,
    "failed_subscriptions": [],
    "last_error": "",
    "connected_since": 0.0,
}
_stats_lock = threading.Lock()



def _persist(state: str, detail: str = ""):
    """Deja constancia del estado del stream en la base, para que el
    dashboard —que corre en OTRO proceso y no ve esta memoria— pueda
    mostrar algo real en vez de inventar un estado en vivo."""
    try:
        import c_ppi_client
        c_ppi_client._persist_system_event("STREAM", state, detail)
    except Exception:
        pass


# ---------------------------------------------------------------------- #
# Lectura (lo que consume el resto del sistema)
# ---------------------------------------------------------------------- #
def get_cached_price(ticker: str, instrument_type: str) -> Optional[dict]:
    """Consultada por c_ppi_client.get_market_data() antes de salir a hacer
    polling HTTP. Devuelve None —nunca un dato viejo en silencio— si el tick
    está vencido, y el que llama cae al REST como si este módulo no
    existiera."""
    if not STREAM_ENABLED:
        return None
    with _cache_lock:
        entry = _tick_cache.get((ticker, (instrument_type or "").upper()))
    if not entry:
        return None
    if (time.time() - entry["epoch_recv"]) > MAX_TICK_AGE_SECONDS:
        return None
    return entry


def get_order_status_from_stream(order_id) -> Optional[dict]:
    """NUEVO EN v14.0 — estado de una orden recibido por push desde la cuenta.
    Formato normalizado igual al de c_ppi_client.get_order_status() para que
    l_order_confirmation pueda usar cualquiera de las dos fuentes sin
    ramificar su lógica."""
    if not (STREAM_ENABLED and STREAM_ACCOUNT_ENABLED) or order_id is None:
        return None
    with _order_lock:
        entry = _order_cache.get(str(order_id))
    return dict(entry) if entry else None


def get_stream_status() -> dict:
    """Lo usan el dashboard (sección SRE) y el parte diario de salud."""
    with _stats_lock:
        stats = dict(_stats)
    edad = round(time.time() - stats["last_tick_epoch"], 1) if stats["last_tick_epoch"] else None
    return {
        "enabled": STREAM_ENABLED,
        "connected": stats["connected"],
        "account_connected": stats["account_connected"],
        "subscribed": stats["subscribed"],
        "ticks_received": stats["ticks_received"],
        "last_tick_age_seconds": edad,
        "healthy": bool(stats["connected"] and edad is not None and edad < STALE_ALERT_SECONDS),
        "last_error": stats["last_error"],
    }


# ---------------------------------------------------------------------- #
# Cliente
# ---------------------------------------------------------------------- #
class PPIRealTimeClient:
    """Mantiene los streams de PPI vivos en hilos daemon. Nunca bloquea el
    loop principal ni propaga excepciones hacia afuera.

    Se le pasa el ResilientPPIClient ya logueado (no las credenciales
    sueltas): el stream tiene que viajar sobre la MISMA sesión autenticada
    que el REST — abrir un segundo login solo para el tiempo real duplicaría
    sesiones contra el bróker sin necesidad.
    """

    def __init__(self, ppi_client, notifier=None, instruments: Optional[list] = None):
        self.ppi_client = ppi_client
        self.notifier = notifier
        self.instruments = list(instruments or [])   # [(ticker, instrument_type, settlement), ...]
        self._stop = threading.Event()
        self._threads = []
        self._alerted_stale = False

    # ---------------- suscripciones ---------------- #
    def set_instruments(self, instruments: list):
        """Se llama después de resolver el universo del día (j_main). Guardar
        la lista acá y suscribir en el on_connect es más simple —y más fácil
        de razonar— que ir suscribiendo de a uno sobre una conexión viva."""
        self.instruments = list(instruments)[:MAX_SUBSCRIPTIONS]

    # ---------------- market data ---------------- #
    def _on_market_data(self, data):
        try:
            msg = json.loads(data) if isinstance(data, (str, bytes)) else data
            ticker = msg.get("Ticker")
            if not ticker:
                return
            instrument_type = (msg.get("Type") or "CEDEARS").upper()
            price = msg.get("Price")

            bid = ask = None
            bids, offers = msg.get("Bids") or [], msg.get("Offers") or []
            if bids:
                bid = bids[0].get("Price")
            if offers:
                ask = offers[0].get("Price")

            # Los mensajes con Trade=False son actualizaciones de book: pueden
            # venir sin Price. En ese caso se conserva el último precio
            # operado y solo se refrescan las puntas — pisar el precio con un
            # 0 sería peor que no actualizar nada, porque j_main usa ese
            # precio para dimensionar órdenes reales.
            with _cache_lock:
                previo = _tick_cache.get((ticker, instrument_type), {})
                precio_final = price if price else previo.get("price")
                if not precio_final:
                    return
                _tick_cache[(ticker, instrument_type)] = {
                    "price": float(precio_final),
                    "bid": bid if bid is not None else previo.get("bid"),
                    "ask": ask if ask is not None else previo.get("ask"),
                    "settlement": msg.get("Settlement") or previo.get("settlement"),
                    "volume": msg.get("VolumeTotalAmount") or previo.get("volume"),
                    "epoch_recv": time.time(),
                    "source": "stream",
                }
            with _stats_lock:
                _stats["ticks_received"] += 1
                _stats["last_tick_epoch"] = time.time()
            self._alerted_stale = False
        except Exception as e:
            logger.debug("Tick de market data con formato inesperado, se ignora: %s", e)

    def _on_market_connect(self):
        logger.info("Stream de Market Data conectado. Suscribiendo %s instrumentos...", len(self.instruments))
        with _stats_lock:
            _stats["connected"] = True
            _stats["connected_since"] = time.time()
        try:
            from ppi_client.models.instrument import Instrument
        except ImportError as e:
            logger.error("No se pudo importar el modelo Instrument de ppi-client: %s", e)
            return
        # MEJORA DE v16.1 — reintento de las suscripciones que fallan.
        #
        # Antes, si al reconectar una suscripción individual fallaba, se
        # registraba el aviso y se seguía de largo. El stream quedaba
        # "conectado" pero ciego para ese ticker, y esa es la peor falla
        # posible del subsistema: no hay error, el semáforo da verde, y el
        # bot simplemente deja de ver ese instrumento hasta la próxima
        # reconexión. Ahora las fallidas se reintentan y, si alguna no entra,
        # queda registrada por nombre para que el panel pueda mostrarla.
        suscritos, fallidas = self._suscribir(Instrument, self.instruments)

        if fallidas:
            logger.info("Reintentando %s suscripción(es) que fallaron...", len(fallidas))
            time.sleep(1.0)
            reintentados, fallidas = self._suscribir(Instrument, fallidas)
            suscritos += reintentados

        with _stats_lock:
            _stats["subscribed"] = suscritos
            _stats["failed_subscriptions"] = [f[0] for f in fallidas]

        if fallidas:
            logger.error("Quedaron %s instrumento(s) SIN suscribir tras el reintento: %s. "
                         "El bot no va a recibir precios en vivo de esos y va a caer a REST.",
                         len(fallidas), ", ".join(f[0] for f in fallidas))
            if self.notifier:
                try:
                    self.notifier.send(
                        f"⚠️ El stream quedó sin suscribir {len(fallidas)} instrumento(s): "
                        f"{', '.join(f[0] for f in fallidas)}. Se consultan por REST, más lento "
                        f"y con más consumo de cuota.")
                except Exception:
                    pass

        logger.info("Suscripción completa: %s de %s instrumentos.", suscritos, len(self.instruments))
        _persist("CONNECTED", f"{suscritos} instrumentos suscritos (market data).")

    def _suscribir(self, Instrument, lista) -> tuple:
        """Suscribe una lista y devuelve (cuántas entraron, cuáles fallaron)."""
        ok, fallidas = 0, []
        for ticker, instrument_type, settlement in lista:
            try:
                self.ppi_client.client.realtime.subscribe_to_element(
                    Instrument(ticker, instrument_type, settlement)
                )
                ok += 1
            except Exception as e:
                logger.warning("No se pudo suscribir %s (%s): %s", ticker, instrument_type, e)
                fallidas.append((ticker, instrument_type, settlement))
        return ok, fallidas

    def _on_market_disconnect(self):
        logger.warning("Stream de Market Data desconectado.")
        with _stats_lock:
            _stats["connected"] = False
        _persist("DISCONNECTED", "Stream de Market Data desconectado.")

    # ---------------- account data ---------------- #
    def _on_account_data(self, data):
        """Notificaciones de cuenta. Lo que más importa es el tipo
        ORDER_NOTIFICATION: el aviso en tiempo real de que una orden cambió
        de estado (y con cuánto se ejecutó), que es justo el dato por el que
        v13.0 tenía que repreguntar en un bucle."""
        try:
            msg = json.loads(data) if isinstance(data, (str, bytes)) else data
            tipo = msg.get("Type")
            try:
                from ppi_client.api.constants import ACCOUNTDATA_TYPE_ORDER_NOTIFICATION
                es_orden = tipo == ACCOUNTDATA_TYPE_ORDER_NOTIFICATION
            except ImportError:
                es_orden = "OrderId" in msg

            if es_orden and msg.get("OrderId") is not None:
                with _order_lock:
                    _order_cache[str(msg["OrderId"])] = {
                        "status": msg.get("Status"),
                        "filledQuantity": msg.get("QuantityExecuted", 0),
                        "ticker": msg.get("Ticker"),
                        "operation": msg.get("Operation"),
                        "epoch_recv": time.time(),
                        "source": "stream",
                    }
                logger.info("Push de orden %s: estado=%s ejecutado=%s",
                            msg.get("OrderId"), msg.get("Status"), msg.get("QuantityExecuted"))
            elif self.notifier and msg.get("Message"):
                # Avisos del bróker al titular de la cuenta (acreditaciones,
                # mensajes operativos). Se reenvían tal cual: PPI ya decidió
                # que eran importantes.
                self.notifier.send_telegram(
                    f"🏦 Aviso de PPI: {msg.get('Title', '')} {msg['Message']}".strip()
                )
        except Exception as e:
            logger.debug("Mensaje de account data no interpretable, se ignora: %s", e)

    def _on_account_connect(self):
        logger.info("Stream de Account Data conectado.")
        with _stats_lock:
            _stats["account_connected"] = True
        try:
            self.ppi_client.client.realtime.subscribe_to_account_data(self.ppi_client.account_number)
        except Exception as e:
            logger.warning("No se pudo suscribir al stream de cuenta: %s", e)

    def _on_account_disconnect(self):
        logger.warning("Stream de Account Data desconectado.")
        with _stats_lock:
            _stats["account_connected"] = False

    # ---------------- ciclo de vida ---------------- #
    def _run(self):
        intento = 0
        while not self._stop.is_set():
            try:
                realtime = getattr(self.ppi_client.client, "realtime", None)
                if realtime is None:
                    logger.warning(
                        "La versión instalada de ppi-client no expone 'realtime'. El bot sigue "
                        "funcionando con polling HTTP. Actualizá con: pip install -U ppi-client"
                    )
                    return

                realtime.connect_to_market_data(
                    self._on_market_connect, self._on_market_disconnect, self._on_market_data
                )
                if STREAM_ACCOUNT_ENABLED and self.ppi_client.account_number:
                    realtime.connect_to_account(
                        self._on_account_connect, self._on_account_disconnect, self._on_account_data
                    )
                intento = 0
                # start_connections() se queda adentro mientras los streams
                # viven; por eso todo esto corre en su propio hilo daemon.
                realtime.start_connections()
                # Si volvió sin excepción, la conexión se cerró limpiamente
                # del otro lado: se reintenta igual, con la pausa mínima.
                self._stop.wait(RECONNECT_BACKOFF_SECONDS[0])
            except Exception as e:
                with _stats_lock:
                    _stats["connected"] = False
                    _stats["account_connected"] = False
                    _stats["last_error"] = str(e)[:200]
                base = RECONNECT_BACKOFF_SECONDS[min(intento, len(RECONNECT_BACKOFF_SECONDS) - 1)]
                espera = base + random.uniform(0, RECONNECT_JITTER_SECONDS)
                intento += 1
                logger.warning("Stream de PPI caído (%s). Reintento %s en %.1fs.", e, intento, espera)
                self._escalar_si_corresponde(intento, e)
                self._stop.wait(espera)

    def _escalar_si_corresponde(self, intento: int, error: Exception):
        """NUEVO EN v15.0 — que un stream muerto no siga muriendo en silencio."""
        if intento != MAX_SILENT_RETRIES:
            return  # se avisa UNA vez, en el intento N, no en cada reintento
        detalle = f"{type(error).__name__}: {str(error)[:150]}"
        _persist("RETRY_ESCALATION", f"{intento} reintentos fallidos. {detalle}")
        if self.notifier:
            try:
                self.notifier.send_telegram(
                    f"⚠️ *STREAM DE PPI SIN RECONECTAR*\n"
                    f"{intento} intentos fallidos seguidos.\nÚltimo error: {detalle}\n\n"
                    "Market data cae a polling HTTP automáticamente, así que el bot sigue "
                    "operando con precios reales — más lento y gastando rate limit."
                )
            except Exception:
                pass
        # El stream de CUENTA sí es crítico: sin él, una orden puede ejecutarse
        # en PPI y el bot no enterarse hasta el próximo poll.
        if STREAM_ACCOUNT_ENABLED:
            try:
                import k_position_manager as pm
                import p_risk_guardian as risk_guardian
                abiertas = pm.get_open_positions() or []
                if abiertas and not _stats.get("account_connected"):
                    risk_guardian.trigger_halt(
                        f"WS_TIMEOUT: el stream de cuenta de PPI lleva {intento} reintentos "
                        f"fallidos y hay {len(abiertas)} posiciones abiertas sin confirmación "
                        "de ejecuciones en tiempo real."
                    )
            except Exception as e:
                logger.error("No se pudo evaluar la escalada del stream de cuenta: %s", e)

    def _watchdog(self):
        """Avisa por Telegram si el stream se quedó mudo. No intenta arreglar
        nada por su cuenta (de eso se ocupa el backoff de _run): su trabajo es
        que un stream muerto no pase inadvertido, que era exactamente el
        riesgo señalado por la auditoría."""
        while not self._stop.wait(WATCHDOG_INTERVAL_SECONDS):
            try:
                estado = get_stream_status()
                if not estado["connected"]:
                    continue
                edad = estado["last_tick_age_seconds"]
                if edad is not None and edad > STALE_ALERT_SECONDS and not self._alerted_stale:
                    self._alerted_stale = True
                    logger.warning("Watchdog de stream: sin ticks hace %ss.", int(edad))
                    _persist("STALE", f"Sin ticks hace {int(edad)}s — el bot cayó a polling HTTP.")
                    if self.notifier:
                        self.notifier.send_telegram(
                            f"⚠️ *STREAM DE TIEMPO REAL SIN DATOS*\n"
                            f"Hace {int(edad)}s que no llega ningún tick de PPI.\n"
                            "El bot ya está operando con precios por HTTP (fallback automático), "
                            "así que no hay riesgo de decidir con datos viejos — pero si esto pasa "
                            "en horario de rueda, algo del stream no está bien."
                        )
            except Exception as e:
                logger.debug("Watchdog de stream: %s", e)

    def start(self):
        if not STREAM_ENABLED:
            logger.info("PPI_STREAM_ENABLED=false — se usa polling HTTP para todo.")
            return
        if not self.ppi_client or not getattr(self.ppi_client, "client", None):
            logger.warning("Cliente PPI no autenticado — el stream de tiempo real no arranca.")
            return
        for objetivo, nombre in ((self._run, "ppi_realtime"), (self._watchdog, "ppi_realtime_watchdog")):
            hilo = threading.Thread(target=objetivo, name=nombre, daemon=True)
            hilo.start()
            self._threads.append(hilo)

    def stop(self):
        self._stop.set()
        try:
            realtime = getattr(self.ppi_client.client, "realtime", None)
            if realtime and hasattr(realtime, "close_connections"):
                realtime.close_connections()
        except Exception:
            pass


# Alias de compatibilidad: el código de v12/v13 que instanciaba
# PPIWebSocketClient sigue funcionando sin cambios.
PPIWebSocketClient = PPIRealTimeClient
