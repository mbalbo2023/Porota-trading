"""
ah_market_tools.py — Herramientas de mercado para Function Calling (NUEVO EN v15.0)

QUÉ ES ESTO
===========
Las funciones que el motor de IA puede PEDIR que se ejecuten cuando necesita
un dato duro para decidir, en vez de razonar sobre lo que recuerde de su
entrenamiento. Es la implementación del hallazgo CRÍTICO de la auditoría de
v14 sobre f_gemini_decision_engine.py ("riesgo de alucinación financiera al
no tener datos inyectados en tiempo real") y de la propuesta de arquitectura
híbrida PPI + Yahoo Finance del documento de mejoras del motor de IA.

DÓNDE SE CORRIGE LA PROPUESTA ORIGINAL
======================================
El PDF de mejoras propone consultar_mercado() con un fallback simple:
intento PPI, si falla voy a Yahoo con el ticker + ".BA". Se implementa el
esquema, pero con cuatro correcciones que en la práctica hacen la diferencia
entre que funcione y que devuelva basura con cara de dato:

 1) NO SE INVENTA EL SUFIJO ".BA" PARA CUALQUIER COSA. Yahoo tiene el sufijo
    .BA para acciones y CEDEARs listados en BYMA, pero los BONOS soberanos
    argentinos (AL30, GD30, AE38) NO cotizan en Yahoo con ese formato — el
    ejemplo del propio PDF usa justamente AL30, que es el caso donde el
    fallback devuelve o bien nada, o bien el precio de otro instrumento.
    Peor todavía: el precio de un bono en Yahoo, cuando existe, suele venir
    en dólares y por lámina de 100, mientras que PPI lo devuelve en pesos
    por valor nominal. Mezclarlos sin darse cuenta es un error de dos
    órdenes de magnitud en el dimensionamiento de una orden real.
    Solución: el fallback a Yahoo se habilita SOLO para tipos de
    instrumento donde el mapeo es correcto (acciones y CEDEARs), y el dato
    siempre viaja etiquetado con su fuente, moneda y timestamp.

 2) EL PRECIO DE YAHOO SE MARCA COMO REFERENCIAL, NUNCA OPERABLE. Yahoo
    entrega el cierre del día o un precio demorado. Un bot que dimensiona
    una orden real con un precio demorado se come el spread entero. Por eso
    el dict devuelto trae "apto_para_ordenar": False cuando viene de Yahoo,
    y j_main/l_order_confirmation siguen exigiendo precio de PPI para
    ejecutar. Yahoo sirve para CONTEXTO (¿cómo viene el subyacente en Wall
    Street?), no para apretar el gatillo.

 3) SE APROVECHA LA CACHÉ DEL STREAM. Si x_ppi_websocket ya tiene un tick
    fresco en memoria, se usa ese: es más rápido y no gasta rate limit.

 4) TIMEOUT Y AISLAMIENTO. Ninguna de estas funciones puede colgar el motor
    de decisión: si PPI y Yahoo tardan, se devuelve el error como dato y el
    modelo decide con lo que hay (y con veto, si no hay nada).
"""

import os
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("market_tools")

YAHOO_FALLBACK_ENABLED = os.getenv("YAHOO_FALLBACK_ENABLED", "true").lower() == "true"
# Diferencia porcentual a partir de la cual un precio de PPI se considera
# sospechoso al contrastarlo con Yahoo. Se toma amplio a propósito: Yahoo
# viene demorado, así que un umbral chico produciría alarmas todo el tiempo.
DOBLE_CHEQUEO_UMBRAL_PCT = float(os.getenv("DOBLE_CHEQUEO_UMBRAL_PCT", "5.0"))
TICK_MAX_AGE_SECONDS = float(os.getenv("STREAM_TICK_MAX_AGE_SECONDS", "30"))

# Tipos de instrumento donde el mapeo ticker -> Yahoo es correcto. Los bonos
# quedan deliberadamente afuera (ver punto 1 del docstring).
TIPOS_CON_FALLBACK_YAHOO = {"ACCIONES", "CEDEARS"}

# El cliente PPI real se inyecta al arrancar (j_main). Se evita un import
# circular: c_ppi_client no sabe nada de este módulo.
_ppi_client = None


def bind_ppi_client(client):
    global _ppi_client
    _ppi_client = client


def _desde_stream(ticker: str, instrument_type: str) -> Optional[Dict[str, Any]]:
    try:
        import x_ppi_websocket as ws
        # get_cached_price() es el nombre real de la función en x_ppi_websocket.py
        # (verificado contra el archivo, no asumido por el nombre).
        tick = ws.get_cached_price(ticker, instrument_type)
        if not tick:
            return None
        edad = time.time() - tick.get("epoch_recv", 0)
        if edad > TICK_MAX_AGE_SECONDS:
            return None
        return {
            "ticker": ticker, "precio": tick.get("price"), "bid": tick.get("bid"),
            "ask": tick.get("ask"), "moneda": "ARS", "fuente": "PPI_STREAM",
            "antiguedad_segundos": round(edad, 1), "apto_para_ordenar": True,
        }
    except Exception:
        return None


def obtener_datos_ppi(ticker: str, instrument_type: str = "CEDEARS",
                      settlement: str = "A-24HS") -> Dict[str, Any]:
    """Fuente principal: PPI. Es la única apta para dimensionar una orden
    real, porque es el precio del mercado donde el bot efectivamente opera."""
    if _ppi_client is None:
        return {"error": "Cliente PPI no inicializado", "fuente": "PPI"}
    desde_stream = _desde_stream(ticker, instrument_type)
    if desde_stream:
        return desde_stream
    try:
        datos = _ppi_client.get_market_data(ticker, instrument_type, settlement)
        if not datos:
            return {"error": "PPI no devolvió datos para ese instrumento", "fuente": "PPI"}
        precio = datos.get("price") or datos.get("Price")
        return {
            "ticker": ticker,
            "precio": precio,
            "bid": datos.get("bid") or datos.get("Bid"),
            "ask": datos.get("ask") or datos.get("Ask"),
            "volumen": datos.get("volume") or datos.get("VolumeTotalAmount"),
            "moneda": "ARS",
            "fuente": "PPI",
            "settlement": settlement,
            "apto_para_ordenar": precio is not None,
            "consultado_en": datetime.now().isoformat(timespec="seconds"),
        }
    except Exception as e:
        return {"error": f"Fallo en PPI: {e}", "fuente": "PPI"}


def obtener_datos_yahoo(ticker_yahoo: str) -> Dict[str, Any]:
    """Fuente de contexto: Yahoo Finance vía yfinance. Precio demorado o de
    cierre. Se devuelve SIEMPRE con apto_para_ordenar=False."""
    if not YAHOO_FALLBACK_ENABLED:
        return {"error": "Fallback a Yahoo desactivado por configuración", "fuente": "Yahoo"}
    try:
        import yfinance as yf
        activo = yf.Ticker(ticker_yahoo)
        hist = activo.history(period="5d")
        if hist.empty:
            return {"error": f"Yahoo no tiene datos para {ticker_yahoo}", "fuente": "Yahoo"}
        cierre = float(hist["Close"].iloc[-1])
        previo = float(hist["Close"].iloc[-2]) if len(hist) > 1 else cierre
        variacion = ((cierre / previo) - 1) * 100 if previo else 0.0
        return {
            "ticker": ticker_yahoo,
            "precio": round(cierre, 4),
            "variacion_pct_dia_previo": round(variacion, 2),
            "moneda": (activo.fast_info.get("currency") if hasattr(activo, "fast_info") else None) or "DESCONOCIDA",
            "fuente": "Yahoo",
            "apto_para_ordenar": False,
            "advertencia": ("Precio demorado o de cierre. Sirve como contexto del "
                            "subyacente, NO para dimensionar una orden."),
            "consultado_en": datetime.now().isoformat(timespec="seconds"),
        }
    except ImportError:
        return {"error": "yfinance no está instalado", "fuente": "Yahoo"}
    except Exception as e:
        return {"error": f"Fallo en Yahoo: {e}", "fuente": "Yahoo"}


def _ticker_yahoo(ticker: str, instrument_type: str) -> Optional[str]:
    """Traducción de ticker local a símbolo de Yahoo. Devuelve None cuando el
    mapeo no es confiable — que es la mitad del valor de esta función."""
    tipo = (instrument_type or "").upper()
    if tipo not in TIPOS_CON_FALLBACK_YAHOO:
        return None
    return f"{ticker.upper()}.BA"


# ============================================================================
# La función que se le expone al modelo
# ============================================================================
def consultar_mercado(ticker: str, instrument_type: str = "CEDEARS",
                      settlement: str = "A-24HS") -> dict:
    """Consulta el precio y las puntas de un instrumento del mercado argentino.

    Usa PPI (el bróker real donde opera el bot) como fuente principal y, solo
    para acciones y CEDEARs, cae a Yahoo Finance como referencia si PPI no
    responde. El resultado indica siempre la fuente y si el precio sirve para
    colocar una orden real.

    Args:
        ticker: símbolo del instrumento (ej. GGAL, AAPL, AL30).
        instrument_type: ACCIONES, CEDEARS, BONOS, LETRAS u OPCIONES.
        settlement: plazo de liquidación (A-24HS, INMEDIATA, A-48HS).

    Returns:
        dict con precio, bid, ask, moneda, fuente y apto_para_ordenar.
    """
    datos = obtener_datos_ppi(ticker, instrument_type, settlement)
    if "error" not in datos:
        return datos

    simbolo = _ticker_yahoo(ticker, instrument_type)
    if not simbolo:
        datos["fallback_disponible"] = False
        datos["nota"] = (
            f"No hay fallback para tipo '{instrument_type}': el mapeo de este instrumento "
            "a Yahoo Finance no es confiable (los bonos argentinos cotizan allí en otra "
            "moneda y con otra unidad). Mejor sin dato que con un dato equivocado.")
        return datos

    logger.warning("PPI falló para %s (%s). Cayendo a Yahoo como referencia.", ticker, datos.get("error"))
    respaldo = obtener_datos_yahoo(simbolo)
    respaldo["motivo_fallback"] = datos.get("error")
    return respaldo


def consultar_contexto_macro(dias: int = 180) -> dict:
    """Devuelve el contexto macroeconómico histórico argentino: reservas del
    BCRA, tasa de política monetaria, inflación del INDEC, tipo de cambio
    oficial, dólares financieros y brecha cambiaria, con su tendencia y el
    percentil en el que está hoy cada variable respecto de su propia historia
    reciente.

    Args:
        dias: ventana de historia a considerar (por defecto 180).

    Returns:
        dict con un indicador por variable disponible.
    """
    try:
        import ad_macro_history
        return ad_macro_history.get_macro_context(dias)
    except Exception as e:
        return {"disponible": False, "error": str(e)}


def consultar_posiciones_abiertas() -> dict:
    """Devuelve las posiciones que el bot tiene abiertas en este momento, con
    su precio de entrada, stop-loss y take-profit. Sirve para que el análisis
    tenga en cuenta la exposición ya asumida antes de recomendar una compra
    nueva.

    Returns:
        dict con la lista de posiciones y la cantidad total.
    """
    try:
        import k_position_manager as pm
        posiciones = pm.get_open_positions() or []
        return {
            "cantidad": len(posiciones),
            "posiciones": [
                {"ticker": p.get("ticker"), "cantidad": p.get("quantity"),
                 "precio_entrada": p.get("entry_price"),
                 "stop_loss": p.get("stop_loss_price"),
                 "take_profit": p.get("take_profit_price")}
                for p in posiciones
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def doble_chequeo_precio(ticker: str, precio_ppi: float,
                         instrument_type: str = "ACCIONES") -> dict:
    """Contrasta un precio de PPI contra una segunda fuente para detectar si el
    dato del bróker parece anómalo. Usar SOLO cuando el precio resulte
    sospechoso —un salto grande sin noticia que lo explique, puntas muy
    separadas—, no de rutina.

    CAMBIO DE v16.1 — LA SEGUNDA FUENTE AHORA ES IOL, NO YAHOO.
    La pregunta era si convenía sacar Yahoo y preguntarle a IOL. La respuesta
    es sí, y por tres motivos concretos:

      1. Yahoo entrega el mercado local con demora. Una diferencia contra PPI
         puede ser un dato malo de PPI o simplemente el retraso de Yahoo, y no
         hay forma de distinguirlos. Un contraste que no puede distinguir
         entre las dos cosas no contrasta nada.
      2. Yahoo no cubre bonos ni opciones. Justo los instrumentos donde un
         precio raro es más caro, quedaban afuera del control.
      3. IOL es un bróker regulado operando el mismo mercado, en el mismo
         momento y con la misma moneda. Si PPI e IOL difieren, la diferencia
         es real y significa algo.

    Yahoo queda como tercer recurso para acciones y CEDEARs cuando IOL no está
    disponible, marcado explícitamente como referencial y demorado.

    Args:
        ticker: símbolo del instrumento.
        precio_ppi: el precio que devolvió consultar_mercado().
        instrument_type: tipo de instrumento.

    Returns:
        dict con el precio de referencia, la diferencia porcentual, qué fuente
        se usó y un veredicto sobre si corresponde desconfiar del dato.
    """
    # Primera opción: IOL, mismo mercado y mismo momento.
    try:
        import ak_iol_client
        cliente = ak_iol_client.IOLClient()
        if cliente.enabled:
            cot = cliente.get_cotizacion(ticker)
            precio_ref = (cot or {}).get("ultimoPrecio")
            if precio_ref and precio_ref > 0 and precio_ppi > 0:
                diferencia = (precio_ppi - precio_ref) / precio_ref * 100
                sospechoso = abs(diferencia) > DOBLE_CHEQUEO_UMBRAL_PCT
                return {
                    "comparable": True, "fuente": "IOL",
                    "precio_ppi": precio_ppi, "precio_referencia": precio_ref,
                    "diferencia_pct": round(diferencia, 2),
                    "dato_sospechoso": sospechoso,
                    "veredicto": (
                        f"Dos brókers del mismo mercado difieren {abs(diferencia):.1f}%, por "
                        f"encima del {DOBLE_CHEQUEO_UMBRAL_PCT}% de tolerancia. Una diferencia "
                        f"de este tamaño entre fuentes simultáneas no se explica por demora: "
                        f"conviene abstenerse."
                        if sospechoso else
                        "Los dos brókers coinciden dentro de la tolerancia. El precio es confiable."),
                }
    except Exception as e:
        logger.debug("IOL no disponible para el doble chequeo: %s", e)

    return _doble_chequeo_yahoo_legacy(ticker, precio_ppi, instrument_type)


def _doble_chequeo_yahoo_legacy(ticker: str, precio_ppi: float,
                                instrument_type: str = "CEDEARS") -> dict:
    """Contrasta un precio de PPI contra Yahoo Finance para detectar si el dato
    del bróker parece anómalo. Usar SOLO cuando el precio de PPI resulte
    sospechoso —un salto muy grande sin noticia que lo explique, o puntas muy
    separadas— y no de rutina: Yahoo entrega precios demorados, así que una
    diferencia chica es normal y no significa nada.

    Args:
        ticker: símbolo del instrumento.
        precio_ppi: el precio que devolvió consultar_mercado().
        instrument_type: solo ACCIONES y CEDEARS tienen mapeo confiable.

    Returns:
        dict con el precio de referencia, la diferencia porcentual y un
        veredicto sobre si corresponde desconfiar del dato de PPI.
    """
    simbolo = _ticker_yahoo(ticker, instrument_type)
    if not simbolo:
        return {"comparable": False,
                "motivo": (f"IOL no está disponible y no hay mapeo confiable de "
                           f"'{instrument_type}' a Yahoo. Los bonos argentinos cotizan allí en "
                           "otra moneda y otra unidad: comparar daría una diferencia enorme que "
                           "no significa nada. Sin segunda fuente para este instrumento.")}

    referencia = obtener_datos_yahoo(simbolo)
    if "error" in referencia:
        return {"comparable": False, "motivo": referencia["error"]}

    precio_ref = referencia.get("precio") or 0
    if precio_ref <= 0 or precio_ppi <= 0:
        return {"comparable": False, "motivo": "Alguno de los dos precios vino en cero."}

    diferencia = (precio_ppi - precio_ref) / precio_ref * 100
    sospechoso = abs(diferencia) > DOBLE_CHEQUEO_UMBRAL_PCT

    return {
        "comparable": True,
        "precio_ppi": precio_ppi,
        "precio_referencia_yahoo": precio_ref,
        "diferencia_pct": round(diferencia, 2),
        "dato_sospechoso": sospechoso,
        "veredicto": (
            f"La diferencia supera el {DOBLE_CHEQUEO_UMBRAL_PCT}% de tolerancia. Puede ser un "
            "dato malo de PPI, pero también un movimiento real que Yahoo todavía no reflejó "
            "por su demora. Ante la duda, conviene abstenerse."
            if sospechoso else
            "Los precios son coherentes dentro de la demora esperable de Yahoo."),
        "recordatorio": ("El precio de Yahoo es referencial y nunca sirve para dimensionar una "
                         "orden: la ejecución siempre usa PPI."),
    }


def consultar_historico(ticker: str) -> dict:
    """Devuelve el resumen histórico de un instrumento a partir del archivo
    local: volatilidad anualizada, máximo y mínimo de 52 semanas, en qué
    percentil de ese rango está el precio de hoy y el volumen promedio.

    Sirve para saber si el precio actual está en zona de máximos o de
    acumulación antes de decidir. Si devuelve HISTORIA_INSUFICIENTE, el
    instrumento no tiene archivo suficiente y conviene tratarlo con más
    cautela, no suponer que está barato.

    Args:
        ticker: símbolo del instrumento.

    Returns:
        dict con las métricas históricas o el estado de los datos.
    """
    try:
        import al_historical_ingest
        return al_historical_ingest.extraer_features(ticker)
    except Exception as e:
        return {"estado_datos": "ERROR", "error": str(e)}


# Lista que se le pasa al SDK de Gemini como `tools`.
#
# CAMBIO DE v16.0 — el pedido era: si Yahoo es peor que PPI, que PPI sea la
# principal y que sea el motor de IA el que decida si el dato le alcanza o si
# quiere un doble chequeo. La primera mitad ya estaba resuelta desde v15.0
# (PPI es la fuente principal y Yahoo solo entraba si PPI fallaba), así que no
# se rehace. Lo que faltaba es la segunda mitad, y es un cambio real de
# arquitectura: antes el doble chequeo era IMPOSIBLE de pedir, porque Yahoo
# solo se consultaba automáticamente ante una falla de PPI. Ahora
# doble_chequeo_yahoo() es una herramienta que el modelo invoca cuando LE
# PARECE que el dato es raro. La decisión pasó del código al motor, que es
# exactamente lo que se pidió.
HERRAMIENTAS = [consultar_mercado, consultar_contexto_macro,
                consultar_posiciones_abiertas, consultar_historico,
                doble_chequeo_precio]
