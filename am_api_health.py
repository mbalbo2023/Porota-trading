"""
am_api_health.py — Chequeo de salud con semáforos de APIs y módulos

QUÉ RESUELVE
---------------------------------------------------------------------------
Antes, saber si una API externa estaba respondiendo bien exigía leer los
logs. Eso tiene un problema de fondo: los logs cuentan lo que pasó cuando
alguien fue a mirarlos, y las fallas de integración se descubrían recién
cuando ya habían costado una oportunidad o una orden rechazada.

Este módulo hace lo contrario: sondea activamente cada dependencia y publica
un estado que el panel muestra como un círculo de color. La regla de lectura
es siempre la misma y vale para todos los renglones:

  VERDE     el servicio respondió correctamente dentro del tiempo esperado.
  AMARILLO  respondió, pero con una salvedad: lento, con datos viejos, en
            modo degradado o con una función secundaria caída. Se puede
            operar; conviene mirarlo.
  ROJO      no respondió, o respondió algo inservible. Si es una dependencia
            crítica, el sistema no debería estar operando.
  GRIS      no configurado a propósito (por ejemplo, IOL antes de que exista
            la cuenta). No es una falla y no debe alarmar.

POR QUÉ CADA CHEQUEO ES BARATO Y NO INVASIVO
---------------------------------------------------------------------------
Un chequeo de salud que consume rate limit o que manda órdenes de prueba es
peor que no tenerlo: se convierte él mismo en la causa de la falla que
pretende detectar. Por eso cada sonda usa el endpoint más liviano que exista
—una consulta de saldo, un ping de modelo, una lectura del archivo local— y
se cachea el resultado durante HEALTH_CACHE_SECONDS. El panel puede
refrescarse cada diez segundos sin que eso se traduzca en diez veces más
tráfico contra el bróker.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("api_health")

HEALTH_CACHE_SECONDS = int(os.getenv("HEALTH_CACHE_SECONDS", "60"))
HEALTH_SLOW_MS = int(os.getenv("HEALTH_SLOW_MS", "2500"))

VERDE, AMARILLO, ROJO, GRIS = "VERDE", "AMARILLO", "ROJO", "GRIS"

# Emoji de círculo por estado. Se define acá, en un solo lugar, para que el
# panel, los informes en PDF y los mensajes de Telegram usen exactamente el
# mismo símbolo — antes cada uno elegía el suyo y en algunos renderizadores
# los semáforos salían como cuadrados negros.
CIRCULO = {VERDE: "🟢", AMARILLO: "🟡", ROJO: "🔴", GRIS: "⚪"}


@dataclass
class Chequeo:
    nombre: str
    critico: bool
    estado: str = GRIS
    detalle: str = ""
    latencia_ms: Optional[int] = None
    verificado: Optional[str] = None
    extras: dict = field(default_factory=dict)

    @property
    def circulo(self) -> str:
        return CIRCULO.get(self.estado, "⚪")


_cache: Dict[str, tuple] = {}


def _con_cache(clave: str, fn: Callable[[], Chequeo]) -> Chequeo:
    ahora = time.time()
    if clave in _cache:
        guardado, resultado = _cache[clave]
        if ahora - guardado < HEALTH_CACHE_SECONDS:
            return resultado
    try:
        resultado = fn()
    except Exception as e:
        # Que un sondeo falle no puede tumbar el panel: se reporta como rojo
        # con el motivo, que es información útil, y se sigue.
        resultado = Chequeo(nombre=clave, critico=False, estado=ROJO,
                            detalle=f"El propio chequeo falló: {e}")
    resultado.verificado = datetime.now().isoformat(timespec="seconds")
    _cache[clave] = (ahora, resultado)
    return resultado


def _medir(fn: Callable):
    inicio = time.perf_counter()
    resultado = fn()
    return resultado, int((time.perf_counter() - inicio) * 1000)


# ---------------------------------------------------------------------------
# Sondas
# ---------------------------------------------------------------------------

def chequear_ppi(ppi_client) -> Chequeo:
    """PPI es la única dependencia sin la cual el sistema no tiene sentido:
    es el precio con el que se dimensiona y el canal por el que se ejecuta."""
    c = Chequeo("PPI — REST (bróker)", critico=True)
    if ppi_client is None:
        c.estado, c.detalle = ROJO, "El cliente de PPI no está inicializado."
        return c
    if getattr(ppi_client, "_circuit_open", False):
        c.estado, c.detalle = ROJO, "El circuit breaker está abierto por errores repetidos."
        return c

    saldos, ms = _medir(ppi_client.get_available_balance)
    c.latencia_ms = ms
    if saldos is None:
        c.estado, c.detalle = ROJO, "No devolvió saldos: sesión caída o token vencido."
    elif ms > HEALTH_SLOW_MS:
        c.estado, c.detalle = AMARILLO, f"Responde, pero lento ({ms} ms)."
    else:
        c.estado, c.detalle = VERDE, f"Sesión activa, saldos legibles ({ms} ms)."
    return c


def chequear_stream_ppi() -> Chequeo:
    """El stream puede estar 'conectado' y no entregar un solo tick. Por eso se
    mira la antigüedad del último tick y no el estado del socket: un socket
    abierto sin datos es la falla más engañosa que tiene este sistema."""
    c = Chequeo("PPI — WebSocket (tiempo real)", critico=False)
    try:
        import x_ppi_websocket as ws
        ultimo = getattr(ws, "get_last_tick_epoch", lambda: None)()
    except Exception as e:
        c.estado, c.detalle = ROJO, f"No se pudo consultar el stream: {e}"
        return c

    if not ultimo:
        c.estado, c.detalle = AMARILLO, ("Sin ticks todavía. Normal fuera de rueda; "
                                         "en horario de mercado, revisar la suscripción.")
        return c
    antiguedad = time.time() - ultimo
    c.extras["antiguedad_segundos"] = int(antiguedad)
    if antiguedad < 60:
        c.estado, c.detalle = VERDE, f"Último tick hace {int(antiguedad)} s."
    elif antiguedad < 600:
        c.estado, c.detalle = AMARILLO, f"Último tick hace {int(antiguedad / 60)} min."
    else:
        c.estado, c.detalle = ROJO, ("El stream no entrega datos hace más de 10 minutos. "
                                     "El bot está cayendo a consultas REST.")
    return c


def chequear_gemini() -> Chequeo:
    """Se consulta el registro de modelos en vez de gastar una inferencia real:
    el registro ya sabe qué modelo está activo y si hubo degradación."""
    c = Chequeo("Google Gemini — motor de decisión", critico=True)
    try:
        import af_model_registry as reg
        estado = reg.get_estado_actual() if hasattr(reg, "get_estado_actual") else {}
    except Exception as e:
        c.estado, c.detalle = ROJO, f"No se pudo leer el registro de modelos: {e}"
        return c

    modelo = estado.get("modelo_activo")
    degradado = estado.get("degradado", False)
    if not modelo:
        c.estado, c.detalle = ROJO, "Ningún modelo disponible."
    elif degradado:
        c.estado, c.detalle = AMARILLO, f"Operando con modelo de respaldo: {modelo}."
    else:
        c.estado, c.detalle = VERDE, f"Modelo activo: {modelo}."
    c.extras.update(estado)
    return c


def chequear_telegram(notifier) -> Chequeo:
    """Telegram no es crítico para operar, pero sí para enterarse. Si está
    caído, el kill switch puede saltar sin que nadie se entere — que es
    exactamente el escenario que el kill switch existe para evitar."""
    c = Chequeo("Telegram — notificaciones y control", critico=False)
    if notifier is None:
        c.estado, c.detalle = GRIS, "Notificador no inicializado."
        return c
    fallidas = getattr(notifier, "pending_failed_count", lambda: 0)()
    if fallidas == 0:
        c.estado, c.detalle = VERDE, "Sin notificaciones fallidas pendientes."
    elif fallidas < 5:
        c.estado, c.detalle = AMARILLO, f"{fallidas} notificaciones fallidas en cola."
    else:
        c.estado, c.detalle = ROJO, (f"{fallidas} notificaciones fallidas: es probable que no te "
                                     "esté llegando nada. Verificar el token y el chat.")
    return c


def chequear_macro() -> Chequeo:
    """Las series macro alimentan el piso de rentabilidad. Publicadas con
    frecuencia mensual o diaria, así que la vara de frescura es en días."""
    c = Chequeo("BCRA / INDEC / ArgentinaDatos — series macro", critico=False)
    try:
        import ad_macro_history as macro
        ctx = macro.get_macro_context(30)
    except Exception as e:
        c.estado, c.detalle = ROJO, f"No se pudo leer el contexto macro: {e}"
        return c

    if not ctx or not ctx.get("disponible", True):
        c.estado, c.detalle = ROJO, "Sin series macro disponibles."
        return c
    edad = ctx.get("antiguedad_horas")
    if edad is None:
        c.estado, c.detalle = AMARILLO, "Series presentes, sin marca de antigüedad."
    elif edad <= 36:
        c.estado, c.detalle = VERDE, f"Series actualizadas hace {int(edad)} h."
    else:
        c.estado, c.detalle = AMARILLO, (f"Las series tienen {int(edad)} h. El piso de rentabilidad "
                                         "se calcula con datos de hace más de un día y medio.")
    return c


def chequear_archivo_historico() -> Chequeo:
    c = Chequeo("Archivo histórico de precios", critico=False)
    try:
        import al_historical_ingest as hist
        estado = hist.estado_del_archivo()
    except Exception as e:
        c.estado, c.detalle = ROJO, f"No se pudo leer el archivo: {e}"
        return c
    c.estado = estado["semaforo"]
    c.extras.update(estado)
    if estado["instrumentos_archivados"] == 0:
        c.detalle = "Vacío: todavía no se corrió ninguna ingesta."
    else:
        c.detalle = (f"{estado['instrumentos_archivados']} instrumentos, "
                     f"{estado['velas_totales']} velas, atraso de {estado['atraso_dias']} días.")
    return c


def chequear_iol() -> Chequeo:
    c = Chequeo("IOL — histórico y validación cruzada", critico=False)
    if os.getenv("IOL_ENABLED", "false").lower() != "true":
        c.estado = GRIS
        c.detalle = "Desactivado a propósito hasta que exista la cuenta. No es una falla."
        return c
    try:
        import ak_iol_client
        cliente = ak_iol_client.IOLClient()
        if not cliente.enabled:
            c.estado, c.detalle = AMARILLO, "Activado pero sin credenciales cargadas."
            return c
        cot, ms = _medir(lambda: cliente.get_cotizacion("GGAL"))
        c.latencia_ms = ms
        if cot:
            c.estado, c.detalle = VERDE, f"Autenticación y consulta correctas ({ms} ms)."
        else:
            c.estado, c.detalle = ROJO, "Autenticó pero no devolvió cotización."
    except Exception as e:
        c.estado, c.detalle = ROJO, f"Error consultando IOL: {e}"
    return c


def chequear_base_datos() -> Chequeo:
    c = Chequeo("Base de datos operativa (SQLite WAL)", critico=True)
    try:
        import ac_db
        salud = ac_db.healthcheck()
        modo = (salud or {}).get("journal_mode", "").lower()
        if modo == "wal":
            c.estado, c.detalle = VERDE, "Responde en modo WAL."
        elif salud:
            c.estado, c.detalle = AMARILLO, f"Responde, pero en modo '{modo}' en vez de WAL."
        else:
            c.estado, c.detalle = ROJO, "No responde."
    except Exception as e:
        c.estado, c.detalle = ROJO, f"Error de base: {e}"
    return c


def chequear_vigia_api_ppi() -> Chequeo:
    """El vigía compara la firma del esquema de PPI contra la última conocida.
    Un cambio detectado no es una falla del bot: es un aviso de que el bróker
    modificó algo y hay que mirarlo antes de que rompa una orden."""
    c = Chequeo("Vigía de cambios en la API de PPI", critico=False)
    try:
        import ae_ppi_api_watch as watch
        estado = watch.get_ultimo_estado() if hasattr(watch, "get_ultimo_estado") else {}
    except Exception as e:
        c.estado, c.detalle = AMARILLO, f"No se pudo leer el vigía: {e}"
        return c

    cambios = (estado or {}).get("cambios_detectados", [])
    if not estado:
        c.estado, c.detalle = AMARILLO, "El vigía todavía no corrió."
    elif cambios:
        c.estado = AMARILLO
        c.detalle = (f"{len(cambios)} cambio(s) detectados en el esquema de PPI. "
                     "Revisar si afectan el armado de órdenes antes de seguir operando.")
        c.extras["cambios"] = cambios
    else:
        c.estado, c.detalle = VERDE, "Sin cambios respecto de la última firma conocida."
    return c


# ---------------------------------------------------------------------------
# Tablero completo
# ---------------------------------------------------------------------------

def tablero(ppi_client=None, notifier=None) -> dict:
    """Corre todas las sondas y devuelve el tablero listo para el panel.

    El estado general NO es el peor de todos indiscriminadamente: se calcula
    dando peso a la criticidad. Un IOL en gris o un archivo histórico
    amarillo no deben pintar de rojo un sistema que está operando
    perfectamente, porque un tablero que está siempre en rojo deja de mirarse
    — y ese es el modo más común en que fallan los sistemas de monitoreo.
    """
    chequeos: List[Chequeo] = [
        _con_cache("ppi", lambda: chequear_ppi(ppi_client)),
        _con_cache("stream", chequear_stream_ppi),
        _con_cache("gemini", chequear_gemini),
        _con_cache("db", chequear_base_datos),
        _con_cache("telegram", lambda: chequear_telegram(notifier)),
        _con_cache("macro", chequear_macro),
        _con_cache("historico", chequear_archivo_historico),
        _con_cache("iol", chequear_iol),
        _con_cache("vigia_ppi", chequear_vigia_api_ppi),
    ]

    criticos_rojos = [c for c in chequeos if c.critico and c.estado == ROJO]
    criticos_amarillos = [c for c in chequeos if c.critico and c.estado == AMARILLO]
    otros_rojos = [c for c in chequeos if not c.critico and c.estado == ROJO]

    if criticos_rojos:
        general, resumen = ROJO, f"{len(criticos_rojos)} dependencia(s) crítica(s) caída(s). No se debería operar."
    elif criticos_amarillos or otros_rojos:
        general, resumen = AMARILLO, "El sistema opera, pero hay componentes degradados."
    else:
        general, resumen = VERDE, "Todas las dependencias responden con normalidad."

    return {
        "estado_general": general,
        "circulo_general": CIRCULO[general],
        "resumen": resumen,
        "verificado": datetime.now().isoformat(timespec="seconds"),
        "chequeos": [
            {"nombre": c.nombre, "estado": c.estado, "circulo": c.circulo,
             "critico": c.critico, "detalle": c.detalle,
             "latencia_ms": c.latencia_ms, "verificado": c.verificado,
             "extras": c.extras}
            for c in chequeos
        ],
    }
