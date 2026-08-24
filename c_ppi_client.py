"""
c_ppi_client.py — Cliente resiliente de Portfolio Personal Inversiones (PPI)

BUG SERIO CORREGIDO EN ESTA REVISIÓN (arrastrado desde la v2.0, recién
detectado ahora): este archivo se llamaba antes "ppi_client.py" — EXACTAMENTE
el mismo nombre que el paquete instalado por pip ("pip install ppi-client",
que se importa como "ppi_client"). Python resuelve primero los archivos
locales de la carpeta del proyecto antes que las librerías instaladas, así
que la línea de más abajo ("from ppi_client.ppi import PPI") intentaba
importarse A SÍ MISMO en vez de a la librería oficial — y como este archivo
es un módulo plano, no un paquete con submódulos, esa importación fallaba
con ModuleNotFoundError apenas arrancaba el bot. Nunca había llegado a
producción para que se notara. Al renombrar el archivo a "c_ppi_client.py"
(por el orden alfabético pedido) el choque de nombres desaparece solo.

CORRECCIÓN vs. versión anterior:
La v2.0 original reimplementaba el REST a mano contra un contrato inventado
(headers, endpoints y valores de settlement que no existen en la API real).
Se verificó contra la documentación oficial de PPI
(https://itatppi.github.io/ppi-official-api-docs/) y se detectaron
diferencias que hacían fallar el login y toda consulta de mercado.

En vez de volver a reimplementar el REST a mano (y arriesgarse a otro
desvío del contrato), esta versión envuelve la librería oficial de PPI:

    pip install ppi-client

La librería ya resuelve por nosotros: headers de autenticación, formato
real de la respuesta de login (es una lista, no un objeto), renovación
de token y nombres correctos de parámetros/endpoints. Reduce la
superficie de bugs de integración a casi cero.

Valores válidos confirmados contra la documentación oficial (útiles al
armar llamadas desde main.py):
    instrument_type (Type): "BONOS", "LETRAS", "NOBAC", "LEBAC", "ON",
        "FCI", "CAUCIONES", "ACCIONES", "ETF", "CEDEARS", "OPCIONES",
        "FUTUROS", "ACCIONES-USA", "FCI-EXTERIOR"
    settlement: "INMEDIATA", "A-24HS", "A-48HS", "A-72HS"
        (OJO: la versión anterior usaba "CI", que NO es un valor válido
        del enum real — el equivalente correcto es "INMEDIATA".)

Nota sobre el dólar CCL: el cálculo AL30 (pesos) / AL30D (dólares) es la
metodología estándar de mercado, pero el ticker exacto de la pata en
dólares que PPI expone en su book (AL30D vs. otra convención) y el
settlement correcto para ese cálculo específico conviene confirmarlos
una vez con soporte de PPI o inspeccionando el book real en Sandbox,
antes de usarlo para decisiones de trading.
"""

import os
import re
import time
import threading
import logging
from enum import Enum
from typing import Optional, Dict, Any, Callable

from dotenv import load_dotenv
from ppi_client.ppi import PPI

import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)
import bb_runtime_status as runtime_status

DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")


def _persist_system_event(component: str, state: str, detail: str = ""):
    """
    NUEVO EN v13.0 — o_dashboard.py corre en un proceso SEPARADO del bot
    (ver ficha técnica de o_dashboard.py: "proceso independiente"), así
    que no puede leer el estado en memoria del Circuit Breaker ni de
    AIOps directamente. Se persiste cada transición relevante en una
    tabla chica para que la sección "SRE / Monitoreo" del dashboard
    (pedido explícito de v13) pueda mostrar el último estado CONOCIDO —
    no en tiempo real estricto, pero sí el historial real de eventos, en
    vez de nada. Best-effort: si la escritura falla (ej. DB bloqueada un
    instante), no debe interrumpir la operatoria real del cliente PPI.
    """
    runtime_status.record_event(component, state, detail)

load_dotenv()
logger = logging.getLogger("ppi_client_wrapper")


# ============================================================================
# NUEVO EN v12.0 — Ofuscación de secretos en logs/notificaciones
# ============================================================================
# CORRECCIÓN v12.0 (Instrucción 7 / Auditoría v10.5, hallazgo "Gestión de
# Secretos en Memoria" + Auditoría v11, hallazgo CRÍTICO "Gestión de Secretos
# y Rotación de Tokens JWT"): cualquier texto que se vuelque a logs o a
# notificaciones (Telegram/WhatsApp) puede terminar incluyendo, sin querer,
# el JWT completo si algún error de la librería oficial imprime el objeto
# response entero (headers incluidos) en su mensaje de excepción. Esta
# función se aplica en el borde de salida (logger y notifier), nunca en el
# borde de entrada, así el resto del código sigue trabajando con el valor
# real sin fricción.
_JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")

# BUG REAL CORREGIDO EN v15.0 (hallazgo propio, no reportado por ninguna
# auditoría externa — la auditoría de v14 apuntó al archivo equivocado)
# ---------------------------------------------------------------------------
# En v12/v13/v14 esta constante existía, con este mismo nombre, y NUNCA se
# usaba: la única sustitución que hacía obfuscate_secret() era la de
# _JWT_PATTERN. Consecuencia concreta: un JWT de PPI salía ofuscado, pero
# el API Secret de PPI, la GEMINI_API_KEY (formato AIza..., que NO tiene
# la estructura de tres partes de un JWT) y el token de Telegram (formato
# 123456789:AAxxxx) salían EN TEXTO PLANO por cualquier camino que pasara
# por logger/notifier — que es exactamente cómo terminaron transcriptas en
# el informe de auditoría de la v13. El código "tenía" la protección
# escrita pero no conectada.
#
# La corrección tiene tres capas, de la más precisa a la más general:
#   1) Patrones específicos de cada credencial que este proyecto maneja.
#   2) Enmascarado por VALOR: cualquier texto que coincida exactamente con
#      el valor de una variable de entorno sensible se reemplaza, sin
#      importar su forma. Es la capa que realmente cierra el agujero,
#      porque no depende de adivinar el formato del secreto.
#   3) El patrón genérico de string largo tipo base64, como último recurso.
# ---------------------------------------------------------------------------
_GEMINI_KEY_PATTERN = re.compile(r"AIza[A-Za-z0-9_\-]{30,}")
_TELEGRAM_TOKEN_PATTERN = re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,}\b")
_BEARER_PATTERN = re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{20,})")
_APIKEY_PATTERN = re.compile(r"\b[A-Za-z0-9+/=]{32,}\b")

# Nombres de variables de entorno cuyo VALOR nunca debe aparecer en un log.
_SENSITIVE_ENV_HINTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS", "CREDENTIAL", "AUTH")

MASK_LABEL = "[OFUSCADO]"


def _mask_token(token: str) -> str:
    if len(token) <= 12:
        return "***" + MASK_LABEL
    return f"{token[:6]}…{token[-4:]}{MASK_LABEL}"


def _sensitive_env_values() -> list:
    """Valores concretos de las variables sensibles del entorno, ordenados de
    más largo a más corto (para que un secreto que contiene a otro se
    reemplace primero y no queden fragmentos sueltos)."""
    valores = []
    for k, v in os.environ.items():
        if not v or len(v) < 8:
            continue
        if any(h in k.upper() for h in _SENSITIVE_ENV_HINTS):
            valores.append(v)
    return sorted(set(valores), key=len, reverse=True)


def obfuscate_secret(text: Optional[str]) -> str:
    """Reemplaza JWTs, API keys, tokens y cualquier valor de variable de
    entorno sensible por una versión truncada (primeros 6 + últimos 4
    caracteres) antes de loguear o notificar. No modifica el valor real
    usado para autenticar — solo lo que se imprime."""
    if not text:
        return ""
    s = str(text)

    def _mask(match: "re.Match") -> str:
        return _mask_token(match.group(0))

    # Capa 1 — formatos conocidos.
    s = _JWT_PATTERN.sub(_mask, s)
    s = _GEMINI_KEY_PATTERN.sub(_mask, s)
    s = _TELEGRAM_TOKEN_PATTERN.sub(_mask, s)
    s = _BEARER_PATTERN.sub(lambda m: m.group(1) + _mask_token(m.group(2)), s)

    # Capa 2 — enmascarado por valor exacto (la que cierra el agujero real).
    for valor in _sensitive_env_values():
        if valor in s:
            s = s.replace(valor, _mask_token(valor))

    # Capa 3 — red de arrastre para strings largos tipo base64 que no
    # matchearon nada de lo anterior. Se aplica último y con un umbral alto
    # (32 caracteres) para no destrozar hashes, IDs de orden o tracebacks
    # legítimos, que es el motivo por el que este patrón estaba desactivado.
    s = _APIKEY_PATTERN.sub(_mask, s)
    return s


def sanitize_env_dump(env_dict: dict) -> dict:
    """Sanitiza un diccionario ENTERO por nombre de clave. Es la función que
    proponía la auditoría de v14 para aa_env_guard.py, implementada acá (en
    el módulo que ya era dueño de la ofuscación) y exportada para que la usen
    m_introspection_engine.py, aa_env_guard.py y el panel web. Enmascara por
    nombre de clave Y por valor, así que cubre tanto {'GEMINI_API_KEY': ...}
    como {'headers': {'Authorization': 'Bearer ...'}} anidado en un repr."""
    limpio = {}
    for k, v in (env_dict or {}).items():
        if any(h in str(k).upper() for h in _SENSITIVE_ENV_HINTS):
            limpio[k] = "***MASKED***"
        else:
            limpio[k] = obfuscate_secret(str(v)) if v is not None else None
    return limpio


class CircuitState(Enum):
    """Estado del Circuit Breaker (Instrucción 7). CLOSED: opera normal.
    OPEN: se rechazan llamadas sin ni siquiera intentar (evita machacar
    a PPI durante una caída conocida). HALF_OPEN: se deja pasar UNA
    llamada de prueba para ver si el servicio volvió."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

# Backoff exponencial para el Nivel 2 de resiliencia (Circuit Breaker asíncrono
# descripto en el documento de arquitectura). Antes esto era un sleep fijo de
# 180s; ahora sigue la progresión declarada: 10s, 30s, 60s, 180s.
BACKOFF_SCHEDULE_SECONDS = [10, 30, 60, 180]

# CORRECCIÓN DE LA AUDITORÍA 8.1 (rev. 2), aceptada: antes solo había un
# sleep(5) entre TICKERS en el loop principal, pero dentro de la evaluación
# de un mismo ticker se hacían varias llamadas seguidas a la API sin pausa
# (cotización + book + saldo + a veces 2 llamadas más para el CCL) — en
# ráfaga eso puede superar el límite razonable de peticiones por segundo y
# la API de un bróker puede interpretarlo como abuso y bloquear la IP.
# Este límite frena CUALQUIER llamada a la API de PPI (sin importar desde
# qué método se dispare) a un máximo de PPI_MAX_REQUESTS_PER_SECOND.
PPI_MAX_REQUESTS_PER_SECOND = float(os.getenv("PPI_MAX_REQUESTS_PER_SECOND", "2"))
_MIN_INTERVAL_SECONDS = 1.0 / PPI_MAX_REQUESTS_PER_SECOND
_last_call_epoch = [0.0]  # lista para poder mutarlo desde una función módulo
_throttle_lock = threading.Lock()  # v10.0: ahora hay concurrencia real (hilo de
# confirmaciones de Telegram corriendo en paralelo al loop principal — ver
# j_main.confirmations_thread_loop) así que este freno necesita ser thread-safe.
# Antes no hacía falta (todo era secuencial); ahora sí, y este lock resuelve
# el hallazgo de la auditoría 9.1 (rev. 1) sobre el estado global mutable,
# sin necesitar nada tan pesado como un semáforo distribuido entre procesos.


def _throttle():
    # CORRECCIÓN v10.5 — Bug #1 de las auditorías 2 y 3 (rev. 2/3), CRÍTICO,
    # confirmado en el código real: el lock se mantenía adquirido DURANTE el
    # time.sleep(), lo cual bloqueaba por completo al hilo de confirmaciones
    # de Telegram (confirmations_thread_loop) mientras el hilo principal
    # esperaba su freno de velocidad — el comentario del código decía
    # "resuelto" pero la implementación no lo estaba. Ahora el cálculo de
    # cuánto esperar se hace DENTRO del lock (para que sea thread-safe), pero
    # el sleep() se ejecuta AFUERA, liberando el lock antes de dormir.
    # También se corrige el cálculo de "elapsed" para los hilos que quedan
    # en cola: en vez de comparar contra el _last_call_epoch de la última
    # llamada ya despachada, se reserva de antemano el próximo instante
    # disponible (_last_call_epoch[0] = now + wait), así dos hilos que piden
    # turno casi al mismo tiempo no recalculan el mismo wait ni se duplican
    # las esperas.
    with _throttle_lock:
        now = time.time()
        elapsed = now - _last_call_epoch[0]
        wait = _MIN_INTERVAL_SECONDS - elapsed
        if wait > 0:
            _last_call_epoch[0] = now + wait
        else:
            _last_call_epoch[0] = now
            wait = 0.0
    if wait > 0:
        time.sleep(wait)  # fuera del lock — el resto de los hilos no esperan a este sleep


# ---------------------------------------------------------------------- #
# NUEVO EN v10.5 — Adaptador de parámetros de orden por tipo de
# instrumento (auditoría 3, hallazgo #1, CRÍTICO): antes budget_order() y
# confirm_order() pasaban SIEMPRE quantity_type="PAPELES" y el settlement
# recibido, sin importar la clase de activo. Eso rechaza directo en el
# bróker para instrumentos que no operan "por papeles" (FCI se suscribe
# por MONTO) o que no usan los settlements A-24HS/A-48HS (CAUCIONES usa
# INMEDIATA). Esta tabla es la fuente única de esa correspondencia.
#
# LÍMITE HONESTO que se mantiene igual que en revisiones anteriores: la
# documentación pública de PPI no confirma, con una cuenta Sandbox real,
# el detalle fino de cómo se arma una orden de OPCIONES (strike/
# vencimiento) o de FUTUROS (margen) — por eso esos dos tipos NO están acá
# y siguen sin operarse (ver m_instrument_universe.OBSERVATION_ONLY_TYPES).
# CAUCIONES y FCI sí tienen un mapeo de parámetros razonablemente claro en
# la documentación oficial, así que se agregan acá, pero su colocación
# automática real queda además detrás de un interruptor propio, apagado
# por default (ver CAUCIONES_AUTO_PLACEMENT en l_order_confirmation.py) —
# no se activa solo por agregar el mapeo.
ORDER_PARAM_MAP = {
    "CEDEARS":  {"quantity_type": "PAPELES", "settlement_default": "A-24HS"},
    "ACCIONES": {"quantity_type": "PAPELES", "settlement_default": "A-24HS"},
    "BONOS":    {"quantity_type": "PAPELES", "settlement_default": "A-24HS"},
    "ETF":      {"quantity_type": "PAPELES", "settlement_default": "A-24HS"},
    "FCI":              {"quantity_type": "MONTO",   "settlement_default": "INMEDIATA"},
    "FCI-EXTERIOR":     {"quantity_type": "MONTO",   "settlement_default": "INMEDIATA"},
    "CAUCIONES":        {"quantity_type": "PAPELES", "settlement_default": "INMEDIATA"},
}


def order_param_adapter(instrument_type: str) -> Dict[str, str]:
    """Devuelve el quantity_type correcto para el tipo de instrumento dado.
    Si el tipo no está en la tabla (ej. OPCIONES/FUTUROS, que todavía no se
    operan), cae al comportamiento histórico (PAPELES) en vez de fallar —
    es un valor por default razonable, no una promesa de que ese tipo esté
    soportado para operar de verdad."""
    return ORDER_PARAM_MAP.get(instrument_type, {"quantity_type": "PAPELES", "settlement_default": "A-24HS"})


class ResilientPPIClient:
    def __init__(self, notifier):
        self.api_key = os.getenv("PPI_API_KEY")
        self.api_secret = os.getenv("PPI_API_SECRET")
        self.is_sandbox = os.getenv("ENVIRONMENT", "SANDBOX").upper() == "SANDBOX"
        self.account_number = os.getenv("PPI_ACCOUNT_NUMBER")  # requerido para saldos/órdenes

        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "Faltan PPI_API_KEY / PPI_API_SECRET en el .env. "
                "Se generan desde PPI > Gestiones > Gestión de servicio API."
            )

        self.notifier = notifier
        self.is_recovering = False
        self.client: Optional[PPI] = None
        self._authenticated = False
        self._auth_lock = threading.Lock()
        self._auth_blocked_until = 0.0
        self._auth_last_attempt = 0.0
        self._auth_last_error = ""
        self._last_call_latency_ms = None
        self._last_call_epoch = None
        # Sandbox informó explícitamente un máximo de 10 llamadas de login
        # por hora. Una falla de login abre este circuito compartido y evita
        # que cada ticker, health-check o WebSocket vuelva a autenticarse.
        self.AUTH_RETRY_COOLDOWN_SECONDS = int(
            os.getenv("PPI_AUTH_RETRY_COOLDOWN_SECONDS", "3600")
        )
        self.RATE_LIMIT_COOLDOWN_SECONDS = int(
            os.getenv("PPI_RATE_LIMIT_COOLDOWN_SECONDS", "3600")
        )

        # NUEVO EN v12.0 — Circuit Breaker real (Instrucción 7). Antes
        # (v11) cada llamada individual reintentaba su propia progresión
        # de backoff (10/30/60/180s) sin memoria entre llamadas distintas:
        # si PPI estaba caído, CADA ticker del loop de escaneo volvía a
        # pagar los ~280s completos de reintentos antes de rendirse, lo
        # que en una watchlist de 20+ instrumentos podía tardar horas en
        # notarse. Ahora el estado se comparte a nivel cliente: tras
        # CIRCUIT_FAILURE_THRESHOLD fallas consecutivas de red/servidor
        # (502/503/timeout), el circuito pasa a OPEN y CUALQUIER llamada
        # se corta al instante (sin reintentar) durante
        # CIRCUIT_OPEN_COOLDOWN_SECONDS. Pasado ese tiempo, se prueba UNA
        # sola llamada (HALF_OPEN); si funciona, vuelve a CLOSED, si
        # falla, vuelve a OPEN y reinicia el cooldown.
        self._circuit_state = CircuitState.CLOSED
        self._circuit_failures = 0
        self._circuit_opened_at = 0.0
        self._circuit_lock = threading.Lock()
        self.CIRCUIT_FAILURE_THRESHOLD = int(os.getenv("CIRCUIT_FAILURE_THRESHOLD", "5"))
        self.CIRCUIT_OPEN_COOLDOWN_SECONDS = int(os.getenv("CIRCUIT_OPEN_COOLDOWN_SECONDS", "60"))

        # NUEVO EN v15.0 — refresco PROACTIVO del token (hallazgo BAJA de la
        # auditoría de v14, aceptado). Hasta acá el token se renovaba de forma
        # REACTIVA: la primera llamada posterior al vencimiento fallaba con
        # 401, _classify_error la clasificaba como AUTH y recién ahí se hacía
        # relogin. Funcionaba, pero con un costo que no se veía en los logs:
        # esa primera llamada que se pierde puede ser justo el get_market_data
        # con el que se iba a dimensionar una orden, o —peor— el confirm_order
        # de una venta por stop-loss. Ahora el token se renueva SOLO, en
        # segundo plano, antes de vencer.
        self._token_obtenido_en = 0.0
        self._token_lock = threading.Lock()
        # PPI no documenta la vida del JWT en la respuesta de login, así que
        # se usa una ventana conservadora y configurable en vez de asumir un
        # valor. Si el token durase menos, el camino reactivo sigue estando
        # como red de seguridad — las dos capas conviven.
        self.TOKEN_TTL_MINUTES = float(os.getenv("PPI_TOKEN_TTL_MINUTES", "55"))
        self.TOKEN_REFRESH_MARGIN_MINUTES = float(os.getenv("PPI_TOKEN_REFRESH_MARGIN_MINUTES", "5"))

        self._login()

    # ------------------------------------------------------------------ #
    # Autenticación
    # ------------------------------------------------------------------ #
    def login(self) -> bool:
        """Reautentica explícitamente y devuelve un resultado verificable."""
        return self._login()

    def is_authenticated(self) -> bool:
        return bool(self._authenticated and self.client is not None)

    def auth_status(self) -> dict:
        """Estado local y barato para dashboard/AIOps; nunca toca la red."""
        remaining = max(0, int(self._auth_blocked_until - time.time()))
        return {
            "authenticated": self.is_authenticated(),
            "state": "OK" if self.is_authenticated() else (
                "COOLDOWN" if remaining else "DISCONNECTED"
            ),
            "cooldown_remaining_seconds": remaining,
            "last_attempt_epoch": self._auth_last_attempt or None,
            "last_error": self._auth_last_error,
            "circuit": self._circuit_state.value,
            "last_call_latency_ms": self._last_call_latency_ms,
            "last_call_epoch": self._last_call_epoch,
        }

    def _login(self) -> bool:
        now = time.time()
        if now < self._auth_blocked_until:
            return False
        # Un único hilo puede consumir el intento de login de la ventana.
        if not self._auth_lock.acquire(blocking=False):
            return False
        try:
            now = time.time()
            if now < self._auth_blocked_until:
                return False
            self._auth_last_attempt = now
            candidate = PPI(sandbox=self.is_sandbox)
            candidate.account.login_api(self.api_key, self.api_secret)
            self.client = candidate
            self._authenticated = True
            self._auth_last_error = ""
            self._auth_blocked_until = 0.0
            self._token_obtenido_en = time.time()
            _persist_system_event("PPI_AUTH", "OK", "Sesión autenticada.")
            if self.is_recovering:
                self.notifier.notify_recovery("API PPI")
                self.is_recovering = False
            logger.info("Login PPI exitoso (sandbox=%s).", self.is_sandbox)
            return True
        except Exception as e:
            safe_error = obfuscate_secret(str(e))
            rate_limited = self._classify_error(e) == "RATE_LIMIT"
            cooldown = (self.RATE_LIMIT_COOLDOWN_SECONDS if rate_limited
                        else self.AUTH_RETRY_COOLDOWN_SECONDS)
            self.client = None
            self._authenticated = False
            self._token_obtenido_en = 0.0
            self._auth_last_error = safe_error
            self._auth_blocked_until = time.time() + cooldown
            state = "RATE_LIMIT" if rate_limited else "ERROR"
            _persist_system_event(
                "PPI_AUTH", state,
                f"{safe_error}. Próximo intento: {runtime_status.epoch_iso(self._auth_blocked_until)}.",
            )
            logger.error(
                "Autenticación PPI falló (%s). Se bloquean nuevos logins por %ss: %s",
                state, cooldown, safe_error,
            )
            if not self.is_recovering:
                self.notifier.notify_error(
                    f"Falla de autenticación con PPI. No se abrirán posiciones y el próximo "
                    f"intento será dentro de {cooldown // 60} minutos."
                )
                self.is_recovering = True
            return False
        finally:
            self._auth_lock.release()

    def _diagnose_sandbox_clientkey(self):
        """Compatibilidad: el diagnóstico activo fue retirado.

        Nunca debe hacerse una segunda autenticación automática después de
        fallar la librería oficial: consume la misma cuota y su antiguo URL
        ni siquiera correspondía al Sandbox actual.
        """
        logger.info("Diagnóstico REST directo de PPI desactivado; revisar el estado persistido.")
        return None

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Estado de una orden ya colocada — necesario para resolver el caso
        "PartiallyFilled" (ver l_order_confirmation.py).

        BUG SERIO CORREGIDO EN v14.0 (arrastrado de v13.0, encontrado al
        verificar el código contra la documentación oficial de PPI):
        la versión anterior hacía `self.client.order.status(order_id)`
        protegido con un `hasattr`. Ese método NO EXISTE en la librería
        oficial — el objeto se llama `orders` (plural) y la operación es
        `get_order_detail(account_number, order_id, external_id)`. Como el
        acceso estaba envuelto en un hasattr(), nunca tiró un error
        visible: simplemente entraba SIEMPRE por la rama del `else`,
        logueaba un warning y devolvía None. Consecuencia real: toda la
        lógica de ejecución parcial de v13.0 —presentada como el hallazgo
        crítico resuelto de esa versión— quedaba desactivada en silencio.
        `_resolve_filled_quantity()` recibía status=None y, por diseño,
        asumía "Filled" con la cantidad solicitada: exactamente el
        comportamiento viejo que se creía corregido. Un fallback silencioso
        que oculta un método mal escrito es peor que un error ruidoso.

        NUEVO EN v14.0 — se consulta primero el stream de cuenta
        (x_ppi_websocket): PPI empuja por push cada cambio de estado de
        orden, con la cantidad efectivamente ejecutada. Ese dato llega
        antes que cualquier repregunta y, además, es el ÚNICO que informa
        QuantityExecuted (el detalle REST devuelve la cantidad solicitada,
        no la ejecutada). Si no hay dato en el stream, se cae al REST.
        """
        # 1) Push en tiempo real (preferido: más rápido y con cantidad ejecutada real)
        try:
            import x_ppi_websocket as ppi_stream
            desde_stream = ppi_stream.get_order_status_from_stream(order_id)
            if desde_stream:
                return desde_stream
        except ImportError:
            pass

        # 2) REST oficial: ppi.orders.get_order_detail(...)
        def _fetch():
            detalle = self.client.orders.get_order_detail(self.account_number, order_id, None)
            if not isinstance(detalle, dict):
                return None
            return {
                "status": detalle.get("status"),
                # El detalle REST NO informa la cantidad ejecutada, solo la
                # solicitada. Se deja explícito en None en vez de devolver
                # `quantity` haciéndola pasar por ejecutada — ese sería el
                # mismo tipo de mentira silenciosa que se corrigió arriba.
                "filledQuantity": None,
                "ticker": detalle.get("ticker"),
                "source": "rest",
            }

        return self._call_with_retry(_fetch, f"get_order_status({order_id})")

    def sell_at_market(self, ticker: str, quantity: int, instrument_type: str = "CEDEARS",
                        settlement: str = "A-24HS", external_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        NUEVO EN v14.0 — venta a precio de mercado. La necesita el cierre
        forzado de fin de día (k_position_manager.close_all_intraday_positions),
        pedido por la auditoría de v13 como riesgo CRÍTICO: una posición de
        scalping que quede abierta al cierre pasa a tener riesgo overnight,
        que es justo el riesgo que el scalping dice no tomar.

        Se usa PRECIO-DE-MERCADO y no PRECIO-LIMITE a propósito: en el
        cierre lo que importa es salir, no salir a un precio lindo. Un
        límite puede no ejecutarse, y una orden que no se ejecuta en este
        contexto es equivalente a no haber hecho nada.

        El flujo es el mismo que el de compra: budget primero (para obtener
        los disclaimers que PPI exige) y confirm después.
        """
        precio_referencia = 0.0
        cotizacion = self.get_market_data(ticker, instrument_type, settlement)
        if cotizacion:
            precio_referencia = float(cotizacion.get("price") or 0)

        budget = self.budget_order(
            self.account_number, quantity, precio_referencia, ticker,
            instrument_type=instrument_type, order_type="PRECIO-DE-MERCADO",
            term="POR-EL-DÍA", operation="VENTA", settlement=settlement,
        )
        if not budget:
            logger.error("No se pudo presupuestar la VENTA a mercado de %s x%s.", ticker, quantity)
            return None

        return self.confirm_order(
            self.account_number, quantity, precio_referencia, ticker,
            budget.get("disclaimers", []), instrument_type=instrument_type,
            order_type="PRECIO-DE-MERCADO", term="POR-EL-DÍA", operation="VENTA",
            settlement=settlement, external_id=external_id,
        )

    # ------------------------------------------------------------------ #
    # Wrapper genérico con reintentos + backoff exponencial (Nivel 2)
    # ------------------------------------------------------------------ #
    def force_circuit_open(self, reason: str = "forzado externamente"):
        """NUEVO EN v12.0 (Instrucción 6, corrección de autoauditoría): punto
        de entrada PÚBLICO para que código externo (u_aiops_watcher.py) fuerce
        el Circuit Breaker a OPEN preventivamente. Antes j_main.py manipulaba
        directamente los atributos "privados" _circuit_state/_circuit_opened_at
        del cliente desde afuera de la clase — funcionaba, pero rompía el
        encapsulamiento sin necesidad y quedaba frágil ante cualquier cambio
        interno de estos atributos. Se centraliza acá, con el lock
        correspondiente, igual que el resto de las transiciones de estado."""
        with self._circuit_lock:
            self._circuit_state = CircuitState.OPEN
            self._circuit_opened_at = time.time()
        logger.warning("Circuit Breaker forzado a OPEN externamente: %s", reason)
        _persist_system_event("CIRCUIT_BREAKER", "OPEN", reason)

    def _classify_error(self, e: Exception) -> str:
        """Distingue error de AUTENTICACIÓN (401/403 — el token venció o es
        inválido, se resuelve con relogin inmediato, no con espera) de error
        de SERVIDOR/RED (502/503/timeout — PPI está degradado, acá sí aplica
        el Circuit Breaker). NUEVO EN v12.0 (Instrucción 7): en v11 ambos
        casos se trataban igual (backoff fijo + reintentar login), lo cual
        desperdicia los 10s del primer paso de backoff en un caso (401) que
        se resuelve al instante con un nuevo login, y no distingue una falla
        de PPI real (503) de una credencial vencida."""
        msg = str(e)
        lower = msg.lower()
        if "429" in msg or "quota exceeded" in lower or "rate limit" in lower:
            return "RATE_LIMIT"
        if any(code in msg for code in ("401", "403", "Unauthorized", "Forbidden")):
            return "AUTH"
        if any(code in msg for code in ("502", "503", "504", "Timeout", "timeout", "ConnectionError")):
            return "SERVER"
        return "UNKNOWN"

    def _circuit_allow_call(self) -> bool:
        with self._circuit_lock:
            if self._circuit_state == CircuitState.CLOSED:
                return True
            if self._circuit_state == CircuitState.OPEN:
                if time.time() - self._circuit_opened_at >= self.CIRCUIT_OPEN_COOLDOWN_SECONDS:
                    self._circuit_state = CircuitState.HALF_OPEN
                    logger.info("Circuit Breaker: OPEN -> HALF_OPEN (cooldown cumplido, probando 1 llamada).")
                    return True
                return False
            return True  # HALF_OPEN: deja pasar la llamada de prueba

    def _circuit_record_success(self):
        with self._circuit_lock:
            if self._circuit_state != CircuitState.CLOSED:
                logger.info("Circuit Breaker: -> CLOSED (llamada de prueba exitosa).")
                _persist_system_event("CIRCUIT_BREAKER", "CLOSED", "Llamada de prueba exitosa tras HALF_OPEN.")
            self._circuit_state = CircuitState.CLOSED
            self._circuit_failures = 0

    def _circuit_record_failure(self):
        with self._circuit_lock:
            if self._circuit_state == CircuitState.HALF_OPEN:
                self._circuit_state = CircuitState.OPEN
                self._circuit_opened_at = time.time()
                logger.warning("Circuit Breaker: HALF_OPEN -> OPEN (la llamada de prueba también falló).")
                _persist_system_event("CIRCUIT_BREAKER", "OPEN", "La llamada de prueba en HALF_OPEN también falló.")
                return
            self._circuit_failures += 1
            if self._circuit_failures >= self.CIRCUIT_FAILURE_THRESHOLD:
                self._circuit_state = CircuitState.OPEN
                self._circuit_opened_at = time.time()
                logger.warning(
                    "Circuit Breaker: CLOSED -> OPEN (%s fallas consecutivas de servidor/red). "
                    "Se cortan llamadas por %ss sin reintentar.",
                    self._circuit_failures, self.CIRCUIT_OPEN_COOLDOWN_SECONDS,
                )
                _persist_system_event("CIRCUIT_BREAKER", "OPEN",
                                       f"{self._circuit_failures} fallas consecutivas de servidor/red.")

    def _refrescar_token_si_hace_falta(self):
        """NUEVO EN v15.0 — se llama al principio de CADA llamada a la API.
        Si al token le quedan menos de TOKEN_REFRESH_MARGIN_MINUTES de vida
        estimada, hace relogin ANTES de usarlo. Es barato (una comparación de
        floats en el 99,9% de las llamadas) y evita perder la primera llamada
        posterior al vencimiento, que es la que puede ser una orden real."""
        try:
            with self._token_lock:
                if not self._token_obtenido_en:
                    return
                edad_minutos = (time.time() - self._token_obtenido_en) / 60
                if edad_minutos < (self.TOKEN_TTL_MINUTES - self.TOKEN_REFRESH_MARGIN_MINUTES):
                    return
                logger.info("Token de PPI cerca de vencer (%.1f min de vida). Renovando antes de usarlo.",
                            edad_minutos)
            self._login()
        except Exception as e:
            # Si el refresco proactivo falla, no se corta nada: el camino
            # reactivo (401 -> relogin) sigue existiendo intacto.
            logger.warning("El refresco proactivo del token falló, queda el reactivo: %s", e)

    def _registrar_firma_api(self, label: str, resultado):
        """NUEVO EN v15.0 — alimenta el canal de "schema drift" de
        ae_ppi_api_watch.py con las respuestas REALES de PPI. Detecta que el
        bróker renombre o elimine un campo sin anunciarlo en ningún lado,
        que es la forma más silenciosa —y más cara— en que la API puede
        cambiar. No cuesta nada: es un hash de una lista de claves, sin red.
        Nunca puede tumbar una llamada real: todo va dentro de un try mudo."""
        try:
            import ae_ppi_api_watch
            endpoint = label.split("(")[0]
            mapa = {
                "get_market_data": "market_data", "get_order_status": "order_detail",
                "confirm_order": "confirm_order", "get_available_balance": "account_balance",
                "get_book": "book", "get_orders": "portfolio",
            }
            nombre = mapa.get(endpoint)
            if nombre and resultado is not None:
                ae_ppi_api_watch.registrar_respuesta(nombre, resultado, self.notifier)
        except Exception:
            pass

    def _call_with_retry(self, fn: Callable[[], Any], label: str) -> Optional[Any]:
        # La sesión es una precondición. Un método de mercado jamás debe
        # iniciar su propio festival de logins: consume como máximo el único
        # intento compartido que permite _login() en esta ventana.
        if not self.is_authenticated() and not self._login():
            logger.debug("PPI sin autenticar — '%s' omitido durante cooldown.", label)
            return None
        if not self._circuit_allow_call():
            logger.debug("Circuit Breaker OPEN — '%s' se corta sin llamar a PPI.", label)
            return None

        self._refrescar_token_si_hace_falta()  # v15.0 — refresco proactivo del JWT

        attempts_done = 0
        for attempt, wait_s in enumerate(BACKOFF_SCHEDULE_SECONDS, start=1):
            attempts_done = attempt
            try:
                _throttle()  # nunca más de PPI_MAX_REQUESTS_PER_SECOND, sin importar el método
                started = time.perf_counter()
                result = fn()
                self._last_call_latency_ms = (time.perf_counter() - started) * 1000
                self._last_call_epoch = time.time()
                self._circuit_record_success()
                _persist_system_event(
                    "PPI_REST", "OK",
                    f"{label}: {self._last_call_latency_ms:.0f} ms",
                )
                self._registrar_firma_api(label, result)  # v15.0 — vigilancia de cambios de la API
                return result
            except Exception as e:
                error_kind = self._classify_error(e)
                logger.warning("Intento %s/%s falló en %s [%s]: %s", attempt,
                                len(BACKOFF_SCHEDULE_SECONDS), label, error_kind,
                                obfuscate_secret(str(e)))

                if error_kind == "AUTH":
                    # Un 401 invalida la sesión completa. Se permite un solo
                    # relogin compartido; si falla, se termina esta llamada.
                    self._authenticated = False
                    self.client = None
                    if not self._login():
                        break
                    continue

                if error_kind == "RATE_LIMIT":
                    self._authenticated = False
                    self.client = None
                    self._token_obtenido_en = 0.0
                    self._auth_last_error = obfuscate_secret(str(e))
                    self._auth_blocked_until = time.time() + self.RATE_LIMIT_COOLDOWN_SECONDS
                    _persist_system_event("PPI_AUTH", "RATE_LIMIT", self._auth_last_error)
                    break

                if error_kind == "SERVER":
                    self._circuit_record_failure()
                    if self._circuit_state == CircuitState.OPEN:
                        # No tiene sentido seguir reintentando esta misma
                        # llamada si el circuito acaba de abrirse.
                        break

                # Un error desconocido NO demuestra que el token expiró.
                # Tampoco se repite a ciegas: puede ser un 4xx/quota nuevo que
                # la librería no exponga claramente. Un solo intento es el
                # comportamiento seguro hasta clasificar el contrato real.
                break
        if not self.is_recovering:
            self.notifier.notify_error(
                f"'{label}' falló tras {attempts_done} intento(s). "
                "Entrando en RECOVERY_MODE."
            )
            self.is_recovering = True
        return None

    # ------------------------------------------------------------------ #
    # Market data
    # ------------------------------------------------------------------ #
    def get_market_data(
        self,
        ticker: str,
        instrument_type: str = "CEDEARS",
        settlement: str = "A-24HS",
    ) -> Optional[Dict[str, Any]]:
        """Cotización actual. Devuelve el dict de la API + 'epoch_recv' para
        chequeo de frescura de datos en main.py.

        NUEVO EN v12.0 (decisión de experto, pendiente #5 resuelto): si
        x_ppi_websocket está habilitado (PPI_WEBSOCKET_ENABLED=true) y hay
        un tick reciente en caché para este ticker, se devuelve ESE dato
        (más rápido, sin gastar una llamada HTTP ni consumir el rate
        limit) en vez de salir a pedirlo por polling. Si no hay WS
        habilitado, no hay tick reciente, o cualquier otra cosa falla, cae
        exactamente al comportamiento HTTP de siempre — el WS nunca puede
        ser la única fuente de un dato crítico."""
        try:
            import x_ppi_websocket as ppi_ws
            cached = ppi_ws.get_cached_price(ticker, instrument_type)
            if cached:
                return {"price": cached["price"], "epoch_recv": cached["epoch_recv"], "source": "websocket"}
        except ImportError:
            pass  # módulo opcional; sin él, comportamiento normal de siempre

        # CORRECCIÓN v10.5 — Bug #2 de la auditoría 2 (rev. 2), CRÍTICO,
        # confirmado en el código real: marketdata.current() puede devolver
        # None o una lista (comportamiento documentado de PPI cuando el
        # ticker no cotiza o no existe), y la línea data["epoch_recv"]=...
        # asumía ciegamente un dict, lanzando un TypeError no controlado que
        # agotaba los 4 reintentos de backoff (hasta 280s) por cada ticker
        # ilíquido — congelando el loop de escaneo entero.
        def _fetch():
            data = self.client.marketdata.current(ticker, instrument_type, settlement)
            if isinstance(data, dict):
                data["epoch_recv"] = time.time()
                return data
            if isinstance(data, list) and data and isinstance(data[0], dict):
                res = data[0]
                res["epoch_recv"] = time.time()
                return res
            logger.warning("get_market_data formato inesperado para %s (%s): %s",
                            ticker, instrument_type, type(data))
            return None

        return self._call_with_retry(_fetch, f"get_market_data({ticker})")

    def get_book(self, ticker: str, instrument_type: str, settlement: str) -> Optional[Dict[str, Any]]:
        """Puntas de compra/venta — útil para estimar spread real antes de
        alertar (ver hallazgo de fricción en el informe de auditoría)."""
        return self._call_with_retry(
            lambda: self.client.marketdata.book(ticker, instrument_type, settlement),
            f"get_book({ticker})",
        )

    def get_book_with_fallback(self, ticker: str, instrument_type: str, primary_settlement: str,
                                fallback_settlement: str = "A-48HS") -> Optional[Dict[str, Any]]:
        """
        NUEVO EN v13.0 — hallazgo MEDIO de Simulación_y_pasos_a_tener_en_
        cuenta_para_corregir.pdf ("Fallback de Plazo de Liquidación"): en
        BONOS especialmente, a veces hay liquidez cargada en INMEDIATA y no
        en A-48HS, o viceversa — y el book vacío en Sandbox para un plazo
        no significa necesariamente que no haya oportunidad, puede ser solo
        que ese plazo puntual no tiene datos cargados en el ambiente de
        pruebas. Si el book del plazo primario viene sin puntas (bid/ask
        ambos ausentes), se reintenta UNA vez contra fallback_settlement
        antes de descartar el instrumento por falta de liquidez.
        """
        book = self.get_book(ticker, instrument_type, primary_settlement)
        # Formato real usado en este proyecto (ver j_main.evaluate_instrument):
        # book["bids"]/book["offers"] son listas de puntas; vacías las dos =
        # sin liquidez cargada para ese plazo en este ambiente.
        has_liquidity = bool(book and (book.get("bids") or book.get("offers")))
        if has_liquidity:
            if book is not None:
                book["settlement_used"] = primary_settlement
            return book
        logger.info("Sin liquidez en %s/%s para %s. Reintentando en %s...",
                    ticker, primary_settlement, instrument_type, fallback_settlement)
        fallback_book = self.get_book(ticker, instrument_type, fallback_settlement)
        if fallback_book:
            fallback_book["settlement_used"] = fallback_settlement
            return fallback_book
        return book  # ninguno de los dos plazos tiene liquidez — se devuelve el original (vacío)

    def get_ccl_rate(self) -> Optional[float]:
        """Dólar CCL implícito vía AL30 (ARS) / AL30D (USD), ambos INMEDIATA.
        Confirmar con PPI el ticker/settlement exacto de la pata en dólares
        que usan en su book antes de operar en real (ver docstring del módulo)."""
        al30_ars = self.get_market_data("AL30", "BONOS", "INMEDIATA")
        al30_usd = self.get_market_data("AL30D", "BONOS", "INMEDIATA")
        if not al30_ars or not al30_usd:
            return None
        try:
            p_ars = al30_ars.get("price", 0)
            p_usd = al30_usd.get("price", 0)
            if p_ars > 0 and p_usd > 0:
                return round(p_ars / p_usd, 2)
        except Exception as e:
            logger.error("Error calculando CCL: %s", e)
        return None

    # ------------------------------------------------------------------ #
    # Cuenta (necesario para validar fondos antes de alertar una compra)
    # ------------------------------------------------------------------ #
    def get_historical_series(
        self, ticker: str, instrument_type: str, settlement: str, days_back: int
    ) -> Optional[list]:
        """Serie histórica diaria (para medir devaluación real del CCL en
        una ventana de N días — ver economics.py)."""
        import datetime as _dt

        date_to = _dt.datetime.now()
        date_from = date_to - _dt.timedelta(days=days_back)
        return self._call_with_retry(
            lambda: self.client.marketdata.search(ticker, instrument_type, settlement, date_from, date_to),
            f"get_historical_series({ticker})",
        )

    def get_available_balance(self) -> Optional[list]:
        if not self.account_number:
            logger.warning("PPI_ACCOUNT_NUMBER no configurado; no se puede consultar saldo.")
            return None
        return self._call_with_retry(
            lambda: self.client.account.get_available_balance(self.account_number),
            "get_available_balance",
        )

    def get_portfolio(self) -> Optional[list]:
        """NUEVO EN v16.2 — tenencia valuada a mercado de la cuenta.

        POR QUÉ HACÍA FALTA: hasta esta versión el sistema solo sabía leer el
        renglón de EFECTIVO EN PESOS (get_available_balance). Con ese único
        dato, el kill switch medía caja y no patrimonio, y eso producía dos
        fallas silenciosas y opuestas: comprar reduce la caja, así que la
        primera operación del día parecía una pérdida y disparaba el corte; y
        una posición abierta que cae 30% no mueve la caja, así que la pérdida
        real no se veía. Sin este método no había forma de arreglar ninguna de
        las dos.

        Devuelve la lista cruda que informa PPI. La normalización (qué campo
        es la valuación, cómo se resuelve la moneda) vive en ax_equity.py, que
        es el único consumidor: acá no se interpreta nada, solo se trae.
        """
        if not self.account_number:
            logger.warning("PPI_ACCOUNT_NUMBER no configurado; no se puede consultar la tenencia.")
            return None
        return self._call_with_retry(
            lambda: self.client.account.get_balance(self.account_number),
            "get_portfolio",
        )

    def get_orders(self, date_from, date_to) -> Optional[list]:
        """Órdenes reales cargadas en PPI en un rango de fechas — se usa para
        cruzar contra las alertas emitidas y saber cuáles se ejecutaron de
        verdad, cuáles no se tomaron y cuáles fueron rechazadas."""
        if not self.account_number:
            logger.warning("PPI_ACCOUNT_NUMBER no configurado; no se pueden consultar órdenes.")
            return None
        return self._call_with_retry(
            lambda: self.client.orders.get_orders(self.account_number, date_from=date_from, date_to=date_to),
            "get_orders",
        )

    def cancel_order(self, order_id: str, external_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Cancela una orden por ID usando el método oficial de PPI."""
        if not self.account_number:
            logger.warning("PPI_ACCOUNT_NUMBER no configurado; no se puede cancelar la orden.")
            return None
        from ppi_client.models.order import Order

        return self._call_with_retry(
            lambda: self.client.orders.cancel_order(
                Order(order_id, self.account_number, external_id)
            ),
            f"cancel_order({order_id})",
        )

    # ------------------------------------------------------------------ #
    # Colocación de órdenes reales (v7.0) — SOLO se llaman desde
    # l_order_confirmation.py, después de que el usuario tocó "Confirmar"
    # en Telegram. NUNCA se llaman directamente desde el loop de escaneo.
    #
    # ADVERTENCIA IMPORTANTE: la firma exacta de OrderBudget/OrderConfirm
    # (orden y casing de los parámetros: "PAPELES" vs "Papeles", "COMPRA"
    # vs "Compra") está tomada del ejemplo oficial de PPI, pero la
    # documentación pública tiene casing inconsistente entre el ejemplo de
    # código y el listado de valores válidos (get_operations(),
    # get_quantity_types()). ANTES de poner ORDER_AUTO_EXECUTE=true en
    # producción: probar budget_order() contra el ambiente Sandbox primero
    # (no coloca nada real) y revisar que la respuesta tenga sentido. Si
    # PPI devuelve un error de validación, ese error casi siempre indica
    # cuál valor de enum está mal escrito.
    # ------------------------------------------------------------------ #
    def budget_order(self, account_number: str, quantity: int, price: float, ticker: str,
                      instrument_type: str = "CEDEARS", order_type: str = "PRECIO-LIMITE",
                      term: str = "VÁLIDA-HASTA-EL", operation: str = "COMPRA",
                      settlement: str = "A-24HS", quantity_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Presupuesto/simulación de la orden: PPI devuelve el detalle y los
        disclaimers que hay que aceptar antes de poder confirmar. NO coloca
        ninguna orden todavía — es seguro llamarlo para probar.

        CORRECCIÓN v10.5 — auditoría 3, hallazgo #1 (CRÍTICO): antes
        "PAPELES" se pasaba siempre igual, sin importar el tipo de
        instrumento — eso rompe para FCI (se suscribe por MONTO, no por
        PAPELES). Si no se pasa quantity_type explícito, se resuelve con
        order_param_adapter() según instrument_type."""
        from ppi_client.models.order_budget import OrderBudget

        if quantity_type is None:
            quantity_type = order_param_adapter(instrument_type)["quantity_type"]

        def _fetch():
            return self.client.orders.budget(OrderBudget(
                account_number, quantity, price, ticker, instrument_type, quantity_type,
                order_type, term, None, operation, settlement,
            ))

        return self._call_with_retry(_fetch, f"budget_order({ticker})")

    def confirm_order(self, account_number: str, quantity: int, price: float, ticker: str,
                       accepted_disclaimers: list, instrument_type: str = "CEDEARS",
                       order_type: str = "PRECIO-LIMITE", term: str = "VÁLIDA-HASTA-EL",
                       operation: str = "COMPRA", settlement: str = "A-24HS",
                       external_id: Optional[str] = None,
                       quantity_type: Optional[str] = None,
                       sandbox_probe: bool = False) -> Optional[Dict[str, Any]]:
        """Coloca la orden REAL. En PRODUCTION esto mueve dinero de verdad.
        En v10.5, en SANDBOX con ORDER_EXECUTION_MODE=auto se llama sin
        botón de confirmación previo (pedido explícito del usuario — ver
        l_order_confirmation.py); en PRODUCTION sigue habiendo un paso de
        confirmación humana salvo que se apague deliberadamente.

        Ver order_param_adapter() para el mapeo de quantity_type por tipo
        de instrumento (corrección v10.5, auditoría 3, hallazgo #1)."""
        # ------------------------------------------------------------------ #
        # NUEVO EN v16.0 — FRENO DE SIMULACIÓN
        # ------------------------------------------------------------------ #
        # Este es el ÚNICO punto por el que una orden sale al mercado, así que
        # es el único lugar donde hace falta poner el freno. Si el sistema
        # arrancó en modo simulación, la orden se registra en la traza de
        # testing y se devuelve un identificador simulado; nunca llega al
        # bróker. Ponerlo acá y no en cada módulo que puede ordenar es lo que
        # garantiza que no quede un camino olvidado por el que se escape una
        # orden real durante una prueba.
        if sandbox_probe:
            entorno = os.getenv("ENVIRONMENT", "").upper()
            if not self.is_sandbox or entorno != "SANDBOX" or not str(external_id or "").startswith(
                    "api-verifier-sandbox-"):
                raise RuntimeError(
                    "sandbox_probe solo admite ENVIRONMENT=SANDBOX y un external_id del verificador."
                )
        else:
            try:
                import ao_startup_gate as startup_gate
                simulada = startup_gate.interceptar_orden(
                    ticker, quantity, price, operation, instrument_type)
                if simulada is not None:
                    logger.info("Orden NO enviada (modo simulación): %s %s × %s a $%s",
                                operation, quantity, ticker, price)
                    return {"simulada": True, "externalId": simulada["id"],
                            "ticker": ticker, "quantity": quantity, "price": price,
                            "operation": operation, "status": "SIMULADA"}
            except ImportError:
                pass  # sin el portón instalado, el comportamiento es el de siempre

        from ppi_client.models.order_confirm import OrderConfirm
        from ppi_client.models.disclaimer import Disclaimer

        if quantity_type is None:
            quantity_type = order_param_adapter(instrument_type)["quantity_type"]

        def _fetch():
            accepted = [Disclaimer(d.get("code"), True) for d in accepted_disclaimers]
            return self.client.orders.confirm(OrderConfirm(
                account_number, quantity, price, ticker, instrument_type, quantity_type,
                order_type, term, None, operation, settlement, accepted, external_id,
            ))

        return self._call_with_retry(_fetch, f"confirm_order({ticker})")

    def get_movements(self, date_from, date_to) -> Optional[list]:
        """
        NUEVO EN v10.5 — auditoría 3, hallazgo "Medición Falsa de P&L
        Diario": envuelve ppi.account.get_movements(), que detalla
        compras/ventas/cupones/dividendos/transferencias reales de la
        cuenta. h_daily_report.py lo usa junto a closed_trades (que ya es
        la fuente principal de P&L por operación) para poder separar
        "resultado de trading" de "depósitos/retiros" en el resumen diario.
        """
        if not self.account_number:
            logger.warning("PPI_ACCOUNT_NUMBER no configurado; no se pueden consultar movimientos.")
            return None

        def _fetch():
            from ppi_client.models.account_movements import AccountMovements
            req = AccountMovements(self.account_number, date_from, date_to, None)
            return self.client.account.get_movements(req)

        return self._call_with_retry(_fetch, "get_movements")

    # ------------------------------------------------------------------ #
    # NUEVO EN v12.0 (Instrucción 3 y 7) — Cauciones bursátiles.
    # ------------------------------------------------------------------ #
    def get_caucion_rate(self, days: int = 1) -> Optional[float]:
        """Tasa anualizada (TNA %) de colocadora a `days` días, usada como
        benchmark dinámico de costo de oportunidad / tasa libre de riesgo
        en pesos (ver d_economics.get_caucion_benchmark_rate). Antes
        (v10.5/v11) CAUCIONES era OBSERVATION_ONLY: se descubría en el
        universo pero nunca se usaba como referencia de tasa ni se operaba.

        LÍMITE HONESTO: el ticker/nombre exacto que PPI usa para cada plazo
        de caución colocadora (1 día, 7 días, etc.) conviene confirmarlo
        inspeccionando search_instruments('CAUCIONES') contra Sandbox antes
        de asumir un ticker fijo — acá se busca por tipo y se toma el de
        menor plazo disponible como proxy de tasa "overnight"."""
        results = self.search_instruments("CAUCIONES")
        if not results:
            return None
        try:
            candidates = [r for r in results if getattr(r, "term_days", None) in (None, days)] or results
            best = candidates[0]
            data = self.get_market_data(best.ticker, "CAUCIONES", "INMEDIATA")
            if data and "rate" in data:
                return float(data["rate"])
            if data and "price" in data:
                return float(data["price"])
        except Exception as e:
            logger.error("Error obteniendo tasa de caución: %s", obfuscate_secret(str(e)))
        return None

    def place_caucion(self, account_number: str, amount_ars: float, days: int = 1,
                       accepted_disclaimers: Optional[list] = None) -> Optional[Dict[str, Any]]:
        """Coloca liquidez ociosa en pesos a tasa de caución (colocadora).
        NUEVO EN v12.0 — Instrucción 7. Detrás de un interruptor propio
        apagado por default (CAUCIONES_AUTO_PLACEMENT=false en .env), igual
        criterio de prudencia que se usó para el resto de las colocaciones
        automáticas nuevas (ver ORDER_PARAM_MAP más arriba): agregar la
        capacidad no implica activarla sola."""
        if os.getenv("CAUCIONES_AUTO_PLACEMENT", "false").lower() != "true":
            logger.info("CAUCIONES_AUTO_PLACEMENT desactivado — no se coloca caución automática.")
            return None
        results = self.search_instruments("CAUCIONES")
        if not results:
            logger.warning("No se encontraron instrumentos de CAUCIONES para colocar liquidez.")
            return None
        ticker = results[0].ticker
        return self.budget_order(
            account_number=account_number, quantity=int(amount_ars), price=0, ticker=ticker,
            instrument_type="CAUCIONES", operation="COLOCADORA", settlement="INMEDIATA",
        )

    def get_tax_report(self, date_from, date_to) -> Optional[list]:
        """NUEVO EN v12.0 (Instrucción 7) — reporte de impuestos, derechos de
        mercado y comisiones de Caja de Valores, para el cálculo milimétrico
        del PnL neto (antes k_position_manager.py estimaba comisiones con el
        % fijo de d_economics.PPI_COMMISSION_PCT en vez de la comisión real
        devengada). Envuelve get_movements(), que ya trae el detalle de cargos
        por movimiento — se filtra acá por los conceptos de impuestos/comisión."""
        movements = self.get_movements(date_from, date_to)
        if not movements:
            return None
        tax_keywords = ("comisi", "iva", "derecho", "arancel", "impuesto")
        return [m for m in movements if any(k in str(m.get("concept", "")).lower() for k in tax_keywords)]

    def search_instruments(self, instrument_type: str, ticker_query: str = "",
                            market: str = "BYMA") -> Optional[list]:
        """
        NUEVO EN v10.3 — convierte en código la "Oportunidad" que quedaba
        documentada en el FODA ("expandir el universo más allá de la
        watchlist curada"). Envuelve search_instrument() de la librería
        oficial, que permite buscar instrumentos por tipo sin necesidad
        de conocer de antemano el ticker exacto.

        LÍMITE HONESTO: no hay forma de confirmar, sin una cuenta activa
        contra Sandbox, si dejar ticker_query vacío devuelve TODOS los
        instrumentos de ese tipo o si la API exige algún filtro mínimo.
        Por eso m_instrument_universe.py usa esto como una fuente
        ADICIONAL que se intenta primero, y si no devuelve resultados
        útiles, cae solo al comportamiento anterior (la watchlist curada
        en n_instrument_watchlist.json) — nunca se queda sin instrumentos
        para analizar solo porque esta búsqueda no funcionó como se
        esperaba.
        """
        def _fetch():
            return self.client.marketdata.search_instrument(ticker_query, "", market, instrument_type)

        return self._call_with_retry(_fetch, f"search_instruments({instrument_type})")
