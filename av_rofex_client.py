"""
av_rofex_client.py — Datos de contrato de futuros (v16.2)

QUÉ DESTRABA
------------
Los futuros venían marcados como "código completo, bloqueado por un dato
externo". El dato faltante eran dos números que el bróker de ejecución no
informa:

  · MULTIPLICADOR DE CONTRATO. Sin él no se puede calcular cuánto se pierde
    por punto de precio, y por lo tanto no se puede dimensionar. Un futuro de
    dólar cotiza en pesos por dólar, pero el contrato son 1.000 dólares: si
    el sistema dimensiona como si fuera una acción, se equivoca por tres
    órdenes de magnitud.

  · GARANTÍA INICIAL (margen). Sin ella no se puede anticipar una llamada de
    margen, y una posición apalancada sin ese número no tiene pérdida máxima
    acotada — que es exactamente el criterio por el que este sistema no opera
    opciones lanzadas en descubierto.

La conclusión de la versión anterior era correcta: no es una limitación del
código, es un dato que no llega. Lo que cambió es de dónde se lo pide.

DE DÓNDE SALE CADA NÚMERO
-------------------------
  · Multiplicador, tick y límites de precio: endpoint de DETALLE DE
    INSTRUMENTOS de la API Primary de Matba Rofex (`instruments/details`). El
    campo se llama `contractMultiplier` — en la documentación en castellano
    aparece como `TamCtra`, "tamaño del contrato".

  · Garantía inicial: NO viene en el endpoint de instrumentos. La propia
    documentación de Primary lo dice: márgenes y garantías se piden por la
    API de Post Trade. En la práctica, el reporte de cuenta
    (`get_account_report`) trae los márgenes vigentes, y los aforos por
    producto los publica Argentina Clearing.

ENTORNO DE PRUEBAS
------------------
reMarkets es el entorno de demo de Matba Rofex: credenciales gratuitas,
precios reales de mercado y capital virtual, pensado específicamente para
probar esto sin riesgo. Es el camino recomendado para validar el
dimensionamiento de futuros antes de tocar plata.

CÓMO DEGRADA ESTE MÓDULO
------------------------
Con criterio de dato faltante, igual que el resto del sistema: si la librería
no está, si no hay credenciales, si el mercado no responde o si falta
CUALQUIERA de los dos números, el instrumento queda NO OPERABLE con el motivo
exacto. Nunca se completa un hueco con un valor por convención.

Esto último merece énfasis porque es tentador hacer lo contrario: existen
tablas públicas con los tamaños de contrato estándar, y sería fácil
hardcodear "dólar futuro = 1.000". Pero un multiplicador de tabla es una
suposición sobre el contrato específico que se está por operar, y las
especificaciones cambian. La tabla se usa acá SOLO para contrastar y avisar
cuando el dato de la API se aparta de lo esperado, nunca para reemplazarlo.
"""

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger("rofex")

ROFEX_ENABLED = os.getenv("ROFEX_ENABLED", "false").lower() == "true"
ROFEX_ENVIRONMENT = os.getenv("ROFEX_ENVIRONMENT", "REMARKETS").strip().upper()
ROFEX_USER = os.getenv("ROFEX_USER", "")
ROFEX_PASSWORD = os.getenv("ROFEX_PASSWORD", "")
ROFEX_ACCOUNT = os.getenv("ROFEX_ACCOUNT", "")
CACHE_SECONDS = int(os.getenv("ROFEX_CACHE_SECONDS", "3600"))

# Tamaños de contrato de referencia. NO se usan para dimensionar: se usan para
# detectar que el dato de la API se apartó de lo esperado y avisar. Un
# multiplicador que cambia sin que nadie lo note es la clase de cosa que se
# descubre después de la pérdida.
_REFERENCIA_MULTIPLICADOR = {
    "DLR": 1000.0,    # dólar futuro: el contrato son USD 1.000
    "SOJ": 100.0,     # agrícolas Matba: 100 toneladas
    "MAI": 100.0,
    "TRI": 100.0,
}

_cache: Dict[str, Any] = {}
_cache_ts: Dict[str, float] = {}
_lock = threading.Lock()
_conectado = False
_motivo_desconexion = "No inicializado."


@dataclass
class ContratoFuturo:
    """Todo lo que hace falta para dimensionar un futuro. `operable` es False
    si falta cualquiera de las dos piezas."""
    simbolo: str
    multiplicador: Optional[float] = None
    tick_size: Optional[float] = None
    garantia_inicial: Optional[float] = None
    moneda: str = "ARS"
    vencimiento: Optional[str] = None
    operable: bool = False
    motivo: str = ""
    fuente_multiplicador: str = ""
    fuente_garantia: str = ""


def _conectar() -> bool:
    """Conexión perezosa. Se hace una sola vez y se recuerda el resultado:
    reintentar en cada consulta convertiría un mercado caído en una tormenta
    de reconexiones."""
    global _conectado, _motivo_desconexion
    if _conectado:
        return True
    if not ROFEX_ENABLED:
        _motivo_desconexion = ("ROFEX_ENABLED=false: el conector de futuros está "
                               "apagado a propósito.")
        return False
    if not (ROFEX_USER and ROFEX_PASSWORD and ROFEX_ACCOUNT):
        _motivo_desconexion = ("Faltan credenciales de Matba Rofex (ROFEX_USER, "
                               "ROFEX_PASSWORD, ROFEX_ACCOUNT). En reMarkets se "
                               "piden gratis y dan precios reales con capital "
                               "virtual.")
        return False
    try:
        import pyRofex
    except ImportError as e:
        _motivo_desconexion = (f"pyRofex no está instalado o no importa ({e}). "
                               "Los futuros quedan no operables.")
        return False
    try:
        entorno = (pyRofex.Environment.LIVE if ROFEX_ENVIRONMENT == "LIVE"
                   else pyRofex.Environment.REMARKET)
        pyRofex.initialize(user=ROFEX_USER, password=ROFEX_PASSWORD,
                           account=ROFEX_ACCOUNT, environment=entorno)
        _conectado = True
        _motivo_desconexion = ""
        logger.info("Conectado a Matba Rofex (%s) para datos de contrato de futuros.",
                    ROFEX_ENVIRONMENT)
        return True
    except Exception as e:
        _motivo_desconexion = f"No se pudo conectar a Matba Rofex: {type(e).__name__}: {e}"
        logger.warning(_motivo_desconexion)
        return False


def _cacheado(clave: str):
    ts = _cache_ts.get(clave, 0)
    if time.time() - ts < CACHE_SECONDS:
        return _cache.get(clave)
    return None


def _guardar(clave: str, valor):
    _cache[clave] = valor
    _cache_ts[clave] = time.time()
    return valor


def _raiz(simbolo: str) -> str:
    """Prefijo del producto, para contrastar contra la tabla de referencia."""
    return "".join(c for c in (simbolo or "").upper() if c.isalpha())[:3]


def _detalle_instrumento(simbolo: str) -> Optional[dict]:
    try:
        import pyRofex
        respuesta = pyRofex.get_instrument_details(ticker=simbolo)
    except Exception as e:
        logger.warning("Fallo al pedir el detalle de %s a Rofex: %s", simbolo, e)
        return None
    if not isinstance(respuesta, dict):
        return None
    if respuesta.get("status") == "ERROR":
        logger.warning("Rofex devolvió error para %s: %s", simbolo,
                       respuesta.get("description", ""))
        return None
    return respuesta.get("instrument") or respuesta.get("instruments") or respuesta


def _garantia_de_la_cuenta(simbolo: str) -> tuple:
    """(garantía_por_contrato, fuente). None si no se pudo establecer.

    Los márgenes llegan por el reporte de cuenta, no por el endpoint de
    instrumentos. Esta función es deliberadamente conservadora: si el reporte
    no trae un margen atribuible a este símbolo, devuelve None. Prorratear un
    margen agregado entre posiciones sería inventar el número más importante
    de la cuenta.
    """
    try:
        import pyRofex
        reporte = pyRofex.get_account_report(account=ROFEX_ACCOUNT)
    except Exception as e:
        return None, f"No se pudo leer el reporte de cuenta: {e}"
    if not isinstance(reporte, dict):
        return None, "El reporte de cuenta no tiene el formato esperado."

    detalle = (reporte.get("accountData") or reporte.get("account_data") or {})
    for campo in ("initialMargin", "margin", "detailedAccountReports"):
        valor = detalle.get(campo)
        if isinstance(valor, (int, float)) and valor > 0:
            return float(valor), f"reporte de cuenta ({campo})"
        if isinstance(valor, dict):
            por_simbolo = valor.get(simbolo)
            if isinstance(por_simbolo, (int, float)) and por_simbolo > 0:
                return float(por_simbolo), f"reporte de cuenta ({campo}.{simbolo})"

    override = os.getenv(f"FUTURO_GARANTIA_{_raiz(simbolo)}")
    if override:
        try:
            return float(override), "override manual del .env"
        except ValueError:
            pass
    return None, ("El reporte de cuenta no informa una garantía atribuible a este "
                  "contrato. Los aforos vigentes los publica Argentina Clearing; "
                  "se pueden cargar a mano con FUTURO_GARANTIA_<PRODUCTO>.")


def datos_de_contrato(simbolo: str) -> ContratoFuturo:
    """LA función del módulo: todo lo que hace falta para dimensionar, o el
    motivo exacto por el que no se puede."""
    with _lock:
        cacheado = _cacheado(f"contrato:{simbolo}")
        if cacheado is not None:
            return cacheado

        resultado = ContratoFuturo(simbolo=simbolo)

        if not _conectar():
            resultado.motivo = _motivo_desconexion
            return _guardar(f"contrato:{simbolo}", resultado)

        detalle = _detalle_instrumento(simbolo)
        if not detalle:
            resultado.motivo = ("El mercado no devolvió el detalle del contrato. "
                                "Sin multiplicador no hay dimensionamiento posible.")
            return _guardar(f"contrato:{simbolo}", resultado)

        for campo in ("contractMultiplier", "TamCtra", "contract_multiplier",
                      "contractSize"):
            valor = detalle.get(campo)
            if valor:
                try:
                    resultado.multiplicador = float(valor)
                    resultado.fuente_multiplicador = f"API Primary ({campo})"
                    break
                except (TypeError, ValueError):
                    continue

        for campo in ("minPriceIncrement", "tickSize", "tick_size"):
            valor = detalle.get(campo)
            if valor:
                try:
                    resultado.tick_size = float(valor)
                    break
                except (TypeError, ValueError):
                    continue

        resultado.moneda = detalle.get("currency") or detalle.get("Moneda") or "ARS"
        resultado.vencimiento = (detalle.get("maturityDate")
                                 or detalle.get("FechaVencimiento"))

        if resultado.multiplicador is None:
            resultado.motivo = ("El detalle del contrato no informa multiplicador. "
                                "No se dimensiona sobre un dato que no llegó.")
            return _guardar(f"contrato:{simbolo}", resultado)

        # Contraste contra la referencia. No corrige: avisa.
        esperado = _REFERENCIA_MULTIPLICADOR.get(_raiz(simbolo))
        if esperado and abs(resultado.multiplicador - esperado) / esperado > 0.01:
            logger.warning(
                "El multiplicador de %s (%s) no coincide con el valor de referencia "
                "(%s). Puede ser un cambio de especificación del contrato: "
                "verificarlo antes de operar.",
                simbolo, resultado.multiplicador, esperado)

        garantia, fuente = _garantia_de_la_cuenta(simbolo)
        resultado.garantia_inicial = garantia
        resultado.fuente_garantia = fuente
        if garantia is None:
            resultado.motivo = (
                "Multiplicador confirmado, pero falta la garantía inicial. Sin ese "
                "número no se puede anticipar una llamada de margen, y una posición "
                "apalancada sin pérdida máxima acotada no es dimensionable. " + fuente)
            return _guardar(f"contrato:{simbolo}", resultado)

        resultado.operable = True
        resultado.motivo = ""
        logger.info("Contrato %s operable: multiplicador %s (%s), garantía %s (%s).",
                    simbolo, resultado.multiplicador, resultado.fuente_multiplicador,
                    garantia, fuente)
        return _guardar(f"contrato:{simbolo}", resultado)


def estado() -> dict:
    """Para el semáforo de salud del panel."""
    if not ROFEX_ENABLED:
        return {"estado": "GRIS", "detalle": "Conector de futuros desactivado a propósito."}
    if _conectado:
        return {"estado": "VERDE", "detalle": f"Conectado a {ROFEX_ENVIRONMENT}.",
                "contratos_en_cache": len([k for k in _cache if k.startswith("contrato:")])}
    return {"estado": "ROJO", "detalle": _motivo_desconexion}
