"""
t_model_guardian.py — Guardián de modelos de Gemini (v10.0)

VERSIÓN SEGURA de lo que proponía la auditoría 9.1. También acá hubo que
rechazar una parte del diseño original — te explico por qué.

LO QUE LA AUDITORÍA PROPONÍA (rechazado): consultar la lista de modelos
disponibles de Google en tiempo real y, si el modelo configurado no
aparece en esa lista, auto-seleccionar "el primer modelo que contenga la
palabra 'flash'" y reescribir el .env solo, sin avisar ni pedir
confirmación.

POR QUÉ SE RECHAZA: se investigó esto específicamente y hay tres
problemas concretos. Primero, Google desaconseja explícitamente elegir
modelos por alias/nombre parcial en producción, precisamente porque no
es predecible a qué versión apuntan. Segundo, hay un caso documentado de
Google re-apuntando un nombre de modelo con forma de versión fija a un
modelo distinto después de un apagado (gemini-3-pro-preview pasó a
apuntar a 3.1-pro-preview en marzo de 2026) — un selector automático no
tiene forma de detectar ese tipo de cambio. Tercero, un modelo distinto
puede devolver las respuestas en un formato distinto (algunos modelos
más nuevos ni siquiera aceptan los mismos parámetros) y romper el
parseo de f_gemini_decision_engine.py sin que nadie se entere hasta que
empiece a fallar en producción. Cambiar qué IA decide si conviene o no
una operación es un cambio de comportamiento real del bot — amerita que
una persona lo vea antes, como cualquier otro cambio de código.

LO QUE SÍ SE IMPLEMENTA: este módulo consulta la lista de modelos
disponibles, la compara contra una lista blanca chica de modelos que ya
se probaron y se sabe que funcionan bien con este proyecto, y SOLO
avisa por Telegram si conviene revisar algo — nunca edita el .env,
nunca cambia el modelo en caliente.
"""

import os
import logging
from datetime import datetime
import json
import requests

logger = logging.getLogger("model_guardian")

# Lista blanca — modelos que ya se probaron y se sabe que funcionan bien
# con el formato de prompts/JSON que usa este proyecto. Agregar un modelo
# acá es una decisión humana (probarlo primero), no algo que el bot decida.
KNOWN_GOOD_MODELS = ["gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite"]


def check_model_status(notifier):
    """
    Se corre semanalmente (ver scheduler en j_main.py). Si el modelo
    configurado ya no aparece en la lista de modelos disponibles de
    Google, o si hay un modelo más nuevo en la lista blanca que todavía
    no se está usando, avisa por Telegram — la decisión de cambiar queda
    en vos (y en mí, ayudándote a probarlo antes de subirlo).
    """
    api_key = os.getenv("GEMINI_API_KEY")
    current_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    if not api_key:
        return

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
        res = requests.get(url, timeout=10)
        if res.status_code != 200:
            logger.warning("No se pudo consultar la lista de modelos de Gemini (status %s).", res.status_code)
            return
        available = [m["name"].replace("models/", "") for m in res.json().get("models", [])]
    except Exception as e:
        logger.error("Error consultando modelos de Gemini: %s", e)
        return

    if current_model not in available:
        notifier.send_telegram(
            f"⚠️ *REVISAR MODELO DE IA*\nEl modelo configurado ({current_model}) ya no "
            "aparece en la lista de modelos disponibles de Google — puede haber sido "
            "retirado. El bot sigue usando el que tiene guardado hasta que lo cambies a "
            "mano en el .env. Traeme este aviso al chat y vemos juntos a cuál conviene "
            "pasar."
        )
        return

    better_known = [m for m in KNOWN_GOOD_MODELS if m != current_model and m in available]
    if better_known and current_model not in KNOWN_GOOD_MODELS:
        notifier.send_telegram(
            f"ℹ️ Hay modelos en la lista blanca ({', '.join(better_known)}) distintos del "
            f"que estás usando ({current_model}). No es urgente — el bot sigue funcionando "
            "con el actual. Si querés evaluarlo, charlamos en el chat."
        )


# ===========================================================================
# GUARDIÁN POSINFERENCIA — segundo cinturón sobre el veredicto de la IA
# ===========================================================================

MAX_EXPOSICION_POR_TICKER_PCT = float(os.getenv("MAX_TICKER_EXPOSURE_PCT", "40"))


def validate_against_catastrophe(veredicto: dict, payload: dict) -> dict:
    """
    Revisa el veredicto de la IA contra límites de riesgo que no se negocian.

    QUÉ ES Y QUÉ NO ES. No corrige el criterio del modelo ni discute su lectura
    del mercado: si la IA dice que la oportunidad es floja, el sistema le hace
    caso. Lo único que hace es impedir que una aprobación viole un límite duro
    que ninguna narrativa puede justificar. Es un cinturón de seguridad, no un
    copiloto.

    POR QUÉ EXISTE SI YA SE FILTRÓ ANTES. En el camino normal, una oportunidad
    con retorno neto negativo nunca llega a la IA: d_economics la descarta
    antes. Este guardián cubre el caso en que el orden se altere —un cambio de
    código que mueva el filtro, una ruta nueva que llame al motor directo— y
    el precio de tenerlo es una comparación numérica por decisión. Las
    protecciones que solo funcionan mientras nadie toque el código no son
    protecciones.

    Cada intervención se registra, porque un guardián que interviene seguido
    es la señal de que algo aguas arriba está mal.
    """
    if not isinstance(veredicto, dict):
        return veredicto
    if veredicto.get("veto_risk"):
        return veredicto  # ya venía rechazada; no hay nada que frenar

    retorno_neto = payload.get("net_expected_return_pct")
    exposicion_pct = payload.get("ticker_exposure_pct", 0.0)
    intervenciones = []

    if retorno_neto is not None and retorno_neto < 0:
        intervenciones.append(
            f"retorno neto esperado negativo ({retorno_neto:.3f}%) después de comisiones, "
            f"derechos e IVA")

    if exposicion_pct and exposicion_pct > MAX_EXPOSICION_POR_TICKER_PCT:
        intervenciones.append(
            f"la exposición en este papel llegaría al {exposicion_pct:.1f}%, por encima del "
            f"máximo de {MAX_EXPOSICION_POR_TICKER_PCT}%")

    if not intervenciones:
        return veredicto

    motivo = " y ".join(intervenciones)
    logger.warning("GUARDIÁN POSINFERENCIA: se revierte una aprobación de la IA porque %s.", motivo)

    veredicto["veto_risk"] = True
    veredicto["impact_level"] = "CRITICAL"
    veredicto["guardian_override"] = True
    veredicto["reason_for_voice"] = (
        (veredicto.get("reason_for_voice", "") or "")
        + f" [Vetado por el guardián de riesgo: {motivo}.]"
    ).strip()

    try:
        import ac_db
        conn = ac_db.connect_raw()
        conn.execute("""CREATE TABLE IF NOT EXISTS guardian_overrides (
            momento TEXT, ticker TEXT, motivo TEXT, veredicto_original TEXT)""")
        conn.execute("INSERT INTO guardian_overrides VALUES (?,?,?,?)",
                     (datetime.now().isoformat(timespec="seconds"),
                      payload.get("ticker", ""), motivo,
                      json.dumps(veredicto, ensure_ascii=False)[:2000]))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("No se pudo registrar la intervención del guardián: %s", e)

    return veredicto
