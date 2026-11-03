"""
at_model_discovery.py — Descubrimiento dinámico de modelos de IA (v16.2)

EL PROBLEMA QUE RESUELVE
------------------------
Hasta esta versión la cadena de modelos era una lista fija escrita en el
código: si el proveedor retiraba uno, el sistema bajaba al siguiente de la
lista, y si retiraba varios se quedaba sin motor. Fijar nombres de modelo
adentro del código es exactamente lo que rompió versiones anteriores de este
proyecto, y el problema no se arregla escribiendo una lista mejor: se arregla
dejando de escribir la lista.

Este módulo pregunta al proveedor qué modelos existen AHORA y elige el mejor
disponible según una política declarada. Cuando Google retire un modelo, el
sistema se entera en el siguiente chequeo y se reacomoda solo.

LA POLÍTICA, TAL COMO SE PIDIÓ
------------------------------
Apuntar SIEMPRE al mejor expertise disponible: primero Pro o superior,
después Flash, y Flash-Lite solo como último recurso. Dentro de cada nivel,
la generación más nueva primero.

UNA ACLARACIÓN QUE CORRESPONDE HACER, PORQUE CONTRADICE UN SUPUESTO
-------------------------------------------------------------------
La instrucción de esta versión decía que se quería Gemini 3.1 Pro porque
"Flash 3.7 no está habilitado todavía". Verificado contra la documentación
oficial de Google al 21/08/2026, eso no es así:

  · Gemini 3.1 Pro EXISTE, con el identificador `gemini-3.1-pro-preview`.
  · Gemini 3.7 Flash TAMBIÉN existe, y está en disponibilidad general desde
    el 13/08/2026.

Es decir que los dos están habilitados. La conclusión operativa igual se
respeta —el sistema apunta a Pro antes que a Flash— pero conviene que quede
escrito por qué, porque la razón real no es la disponibilidad sino la
capacidad de razonamiento, y esa razón tiene un costo asociado que sí hay que
tener presente: Pro es entre dos y cuatro veces más caro por token que Flash,
es más lento, y desde abril de 2026 los modelos Pro quedaron fuera de la capa
gratuita de la API. Con el timeout de decisión en 15 segundos, un Pro con
razonamiento profundo puede no entrar en la ventana.

Por eso la política prefiere Pro pero el sistema mide: si la latencia de las
decisiones se va del presupuesto, degrada a Flash y avisa. Preferir el modelo
más capaz no sirve de nada si llega tarde a la rueda.

QUÉ NO HACE ESTE MÓDULO
-----------------------
No decide nada de trading y no conoce el dominio. Devuelve una lista ordenada
de identificadores. Quien valida que el modelo respete el contrato JSON sigue
siendo af_model_registry, que es el que tiene la prueba de contrato — un
modelo que existe en el catálogo no es lo mismo que un modelo que funciona.
"""

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Dict

logger = logging.getLogger("model_discovery")

DISCOVERY_ENABLED = os.getenv("MODEL_DISCOVERY_ENABLED", "true").lower() == "true"
DISCOVERY_CACHE_HOURS = float(os.getenv("MODEL_DISCOVERY_CACHE_HOURS", "12"))
PREFERENCIA = os.getenv("MODEL_TIER_PREFERENCE", "pro,flash,flash-lite")

# Cadena de respaldo. Se usa SOLO si el descubrimiento no está disponible (sin
# red, sin llave, proveedor caído). Son los identificadores vigentes al
# 21/08/2026 y por definición van a envejecer: ese envejecimiento es
# precisamente el motivo por el que existe el descubrimiento dinámico.
CADENA_DE_EMERGENCIA = [
    "gemini-3.1-pro-preview",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
]

# Modelos que NO deben elegirse aunque el catálogo los liste: retirados,
# deprecados con fecha de apagado cercana, o variantes especializadas que no
# sirven para este uso.
_EXCLUIR = re.compile(
    r"(embedding|aqa|imagen|veo|tts|audio|vision-only|learnlm|gemma|"
    r"gemini-1\.|gemini-2\.0|gemini-2\.5)", re.I)

_lock = threading.Lock()
_cache: Dict[str, object] = {"modelos": None, "ts": 0.0, "fuente": ""}


@dataclass(frozen=True)
class ModeloDisponible:
    """Un modelo del catálogo, ya clasificado."""
    id: str
    nivel: str          # "pro" | "flash" | "flash-lite" | "otro"
    generacion: float   # 3.1, 3.7... — 0.0 si no se pudo leer
    es_preview: bool
    limite_entrada: Optional[int] = None


def _clasificar(identificador: str, limite_entrada: Optional[int] = None) -> ModeloDisponible:
    """Deduce nivel y generación del identificador.

    Es deliberadamente tolerante: el objetivo es que un nombre con un formato
    que hoy no existe —una serie 4, un sufijo nuevo— se clasifique razonable
    en vez de romper el descubrimiento entero. Un modelo mal clasificado
    baja en el orden; un descubrimiento roto deja al sistema sin motor.
    """
    ident = identificador.lower()
    if "flash-lite" in ident or "flashlite" in ident:
        nivel = "flash-lite"
    elif "flash" in ident:
        nivel = "flash"
    elif "pro" in ident or "ultra" in ident or "opus" in ident:
        nivel = "pro"
    else:
        nivel = "otro"

    generacion = 0.0
    m = re.search(r"(\d+)\.(\d+)", ident)
    if m:
        try:
            generacion = float(f"{m.group(1)}.{m.group(2)}")
        except ValueError:
            generacion = 0.0
    else:
        m2 = re.search(r"gemini-(\d+)", ident)
        if m2:
            generacion = float(m2.group(1))

    return ModeloDisponible(
        id=identificador, nivel=nivel, generacion=generacion,
        es_preview=("preview" in ident or "exp" in ident),
        limite_entrada=limite_entrada,
    )


def _orden_de_preferencia() -> List[str]:
    return [t.strip().lower() for t in PREFERENCIA.split(",") if t.strip()]


def _ranking(m: ModeloDisponible) -> tuple:
    """Clave de orden. Menor es mejor.

    Criterio, en orden de importancia:
      1. El nivel, según la preferencia declarada (Pro antes que Flash).
      2. La generación más nueva primero.
      3. Los estables antes que los preview, a igualdad de todo lo demás. Un
         preview puede deprecarse con dos semanas de aviso; un estable de
         larga disponibilidad tiene doce meses. Para un sistema que opera
         solo, esa diferencia importa más que un punto de benchmark.
    """
    orden = _orden_de_preferencia()
    try:
        pos_nivel = orden.index(m.nivel)
    except ValueError:
        pos_nivel = len(orden) + 1
    return (pos_nivel, -m.generacion, 1 if m.es_preview else 0, m.id)


def _consultar_catalogo_gemini() -> List[ModeloDisponible]:
    """Pregunta al proveedor qué modelos hay. Lista vacía si no se pudo."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("Sin GEMINI_API_KEY: no se puede descubrir el catálogo de modelos.")
        return []
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        encontrados = []
        for m in client.models.list():
            identificador = (getattr(m, "name", "") or "").replace("models/", "")
            if not identificador or _EXCLUIR.search(identificador):
                continue
            # Solo sirven los que pueden generar contenido: el catálogo
            # incluye modelos de embeddings y de otras modalidades.
            acciones = getattr(m, "supported_actions", None) or \
                getattr(m, "supported_generation_methods", None) or []
            if acciones and not any("generatecontent" in str(a).lower().replace("_", "")
                                    for a in acciones):
                continue
            encontrados.append(_clasificar(
                identificador, getattr(m, "input_token_limit", None)))
        return encontrados
    except Exception as e:
        logger.warning("No se pudo consultar el catálogo de modelos de Gemini: %s", e)
        return []


def cadena_de_modelos(forzar: bool = False) -> List[str]:
    """Identificadores ordenados de mejor a peor, según la política.

    Es la función que consume af_model_registry en lugar de leer una lista
    fija del .env.
    """
    with _lock:
        vencido = (time.time() - float(_cache["ts"])) > DISCOVERY_CACHE_HOURS * 3600
        if _cache["modelos"] and not vencido and not forzar:
            return list(_cache["modelos"])

        # Un override manual siempre gana. Si alguien tiene un motivo para
        # fijar un modelo concreto, el sistema no debe discutírselo — pero
        # queda registrado en el log que se salteó el descubrimiento.
        manual = os.getenv("GEMINI_MODEL_CHAIN", "").strip()
        if manual:
            cadena = [m.strip() for m in manual.split(",") if m.strip()]
            logger.info("Cadena de modelos fijada a mano en GEMINI_MODEL_CHAIN: %s. "
                        "El descubrimiento dinámico queda anulado.", cadena)
            _cache.update({"modelos": cadena, "ts": time.time(), "fuente": "manual"})
            return list(cadena)

        if not DISCOVERY_ENABLED:
            _cache.update({"modelos": CADENA_DE_EMERGENCIA, "ts": time.time(),
                           "fuente": "emergencia (descubrimiento desactivado)"})
            return list(CADENA_DE_EMERGENCIA)

        catalogo = _consultar_catalogo_gemini()
        if not catalogo:
            logger.warning(
                "El descubrimiento de modelos no devolvió nada. Se usa la cadena de "
                "emergencia, que está fijada en el código y envejece: si esto se "
                "repite, revisar la llave y la conectividad.")
            _cache.update({"modelos": CADENA_DE_EMERGENCIA, "ts": time.time(),
                           "fuente": "emergencia"})
            return list(CADENA_DE_EMERGENCIA)

        ordenados = sorted(catalogo, key=_ranking)
        cadena = [m.id for m in ordenados]
        logger.info("Catálogo descubierto (%d modelos). Cadena resultante: %s",
                    len(catalogo), cadena[:5])
        _cache.update({"modelos": cadena, "ts": time.time(), "fuente": "descubrimiento"})
        return list(cadena)


def mejor_modelo() -> Optional[str]:
    cadena = cadena_de_modelos()
    return cadena[0] if cadena else None


def cadena_anthropic() -> List[str]:
    """Lo mismo para el motor de respaldo.

    El respaldo importa más de lo que parece: si el principal y el respaldo
    caen a la vez, el sistema no abre posiciones nuevas. Que el respaldo se
    quede apuntando a un modelo retirado es una forma silenciosa de quedarse
    sin respaldo.
    """
    manual = os.getenv("ANTHROPIC_MODEL_CHAIN", "").strip()
    if manual:
        return [m.strip() for m in manual.split(",") if m.strip()]

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return []
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        encontrados = []
        for m in client.models.list().data:
            identificador = getattr(m, "id", "")
            if not identificador:
                continue
            encontrados.append(_clasificar(identificador))
        if encontrados:
            return [m.id for m in sorted(encontrados, key=_ranking)]
    except Exception as e:
        logger.warning("No se pudo consultar el catálogo de Anthropic: %s", e)
    return ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"]


# ---------------------------------------------------------------------------
# Presupuesto de latencia
# ---------------------------------------------------------------------------
# Preferir el modelo más capaz no sirve de nada si llega tarde a la rueda.

LATENCIA_OBJETIVO_S = float(os.getenv("MODEL_LATENCY_BUDGET_SECONDS", "12"))
MUESTRAS_PARA_DEGRADAR = int(os.getenv("MODEL_LATENCY_SAMPLES", "10"))
_latencias: Dict[str, List[float]] = {}


def registrar_latencia(modelo: str, segundos: float) -> None:
    _latencias.setdefault(modelo, []).append(segundos)
    if len(_latencias[modelo]) > 50:
        _latencias[modelo] = _latencias[modelo][-50:]


def conviene_degradar(modelo: str) -> tuple:
    """(sí_o_no, motivo). Mira la mediana, no el promedio: una sola llamada
    lenta por un pico de red no debería cambiar el motor, pero una mediana
    por encima del presupuesto significa que el modelo no entra en la
    ventana de decisión de forma sistemática."""
    muestras = _latencias.get(modelo, [])
    if len(muestras) < MUESTRAS_PARA_DEGRADAR:
        return False, ""
    ordenadas = sorted(muestras[-MUESTRAS_PARA_DEGRADAR:])
    mediana = ordenadas[len(ordenadas) // 2]
    if mediana > LATENCIA_OBJETIVO_S:
        return True, (f"La latencia mediana de {modelo} es {mediana:.1f}s, por encima "
                      f"del presupuesto de {LATENCIA_OBJETIVO_S:.0f}s por decisión.")
    return False, ""


def estado() -> dict:
    """Para el panel: qué se descubrió, de dónde y cuándo."""
    return {
        "fuente": _cache.get("fuente", ""),
        "actualizado_hace_horas": round((time.time() - float(_cache["ts"])) / 3600, 1)
        if _cache["ts"] else None,
        "cadena": list(_cache.get("modelos") or []),
        "preferencia": _orden_de_preferencia(),
        "latencias_medianas": {
            m: round(sorted(v)[len(v) // 2], 2) for m, v in _latencias.items() if v
        },
    }
