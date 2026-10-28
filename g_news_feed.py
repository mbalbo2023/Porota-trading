"""
g_news_feed.py — Extractor de noticias RSS (v8.0)

CORRECCIÓN APLICADA por la auditoría 7.1 (acepto el hallazgo, es correcto):
el fallback anterior devolvía 2 titulares GENÉRICOS INVENTADOS
("El Banco Central mantiene la estabilidad de tasas"...) cuando los 3
feeds fallaban. Eso es peligroso: Gemini recibía esos titulares como si
fueran reales y podía aprobar una operación creyendo que "no hay
noticias de riesgo", cuando en realidad el bot simplemente no pudo leer
las noticias del día. Silenciar un fallo de datos con un dato inventado
es peor que no tener el dato.

Ahora, si ningún feed responde, la función devuelve una lista vacía y un
flag news_unavailable=True — y f_gemini_decision_engine.py trata eso como
motivo de veto (no operar), no como "sin noticias de riesgo". Ver ese
archivo para el detalle.

También se agregó: timestamp de cada titular (para poder medir
antigüedad/TTL) y deduplicado entre feeds (a veces el mismo título
aparece repetido en más de una fuente).
"""

import calendar
import logging
import time
import feedparser
import requests
from typing import List, Dict

logger = logging.getLogger("news_feed")

RSS_FEEDS = [
    "https://www.ambito.com/rss/home.xml",
    "https://www.cronista.com/files/rss/news.xml",
    "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
]

# Antigüedad máxima de un titular para considerarlo "del día" — más allá de
# esto, una noticia vieja no debería influir en la decisión de hoy.
import os
NEWS_MAX_AGE_SECONDS = int(os.getenv("NEWS_MAX_AGE_SECONDS", str(6 * 3600)))  # 6 horas por default
NEWS_FEED_FAILURE_COOLDOWN_SECONDS = int(
    os.getenv("NEWS_FEED_FAILURE_COOLDOWN_SECONDS", "1800")
)
_feed_retry_after = {}
_feed_last_warning = {}

import re
import unicodedata

# NUEVO EN v10.5 — sanitización de titulares antes de que lleguen al
# prompt de la IA (f_gemini_decision_engine.py). Los titulares vienen de
# feeds RSS públicos, texto que no controlamos. Esto no es una limpieza
# de HTML para renderizar en un navegador (el bot nunca muestra estos
# titulares en una página web) — es una limpieza defensiva de dos cosas
# puntuales que sí importan para un texto que va a terminar dentro de un
# prompt: (1) caracteres de control/formato Unicode invisibles (categoría
# Cf — se usan para ocultar texto o partir palabras de forma que evadan
# un filtro simple), y (2) cualquier etiqueta tipo HTML/XML embebida
# (que en teoría feedparser ya debería haber decodificado como texto,
# pero se limpia igual por las dudas, sin agregar la dependencia pesada
# de BeautifulSoup para esto).
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _sanitize_headline(title: str) -> str:
    if not title:
        return title
    # Saca cualquier etiqueta tipo <...>
    title = _HTML_TAG_RE.sub("", title)
    # Saca caracteres de control y de formato Unicode invisibles (ej.
    # zero-width space/joiner, marcas de dirección de texto) — categorías
    # Cc (control) y Cf (formato), dejando el resto del texto intacto.
    title = "".join(ch for ch in title if unicodedata.category(ch) not in ("Cc", "Cf"))
    return title.strip()


def fetch_latest_headlines(max_headlines: int = 10) -> Dict:
    """
    Devuelve {"headlines": [...], "news_unavailable": bool, "fetched_at": epoch}.
    "headlines" puede venir vacía — eso es información válida (no hay
    noticias confiables ahora), no un error a esconder.
    """
    seen = set()
    headlines = []
    any_feed_ok = False

    for url in RSS_FEEDS:
        ahora = time.time()
        if ahora < _feed_retry_after.get(url, 0):
            continue
        try:
            # CORRECCIÓN — auditoría 9.1 (rev. 1), hallazgo válido:
            # feedparser.parse(url) no tiene timeout propio y puede
            # quedarse esperando indefinidamente si el servidor del feed
            # no responde, bloqueando el resto del escaneo. Se descarga
            # primero con requests (timeout de 8s) y recién ahí se lo pasa
            # a feedparser para parsear el contenido ya descargado.
            response = requests.get(url, timeout=8)
            response.raise_for_status()
            feed = feedparser.parse(response.content)
            if getattr(feed, "bozo", False) and not feed.entries:
                detalle = f"no parseable: {getattr(feed, 'bozo_exception', '')}"
                _feed_retry_after[url] = ahora + NEWS_FEED_FAILURE_COOLDOWN_SECONDS
                if ahora - _feed_last_warning.get(url, 0) >= NEWS_FEED_FAILURE_COOLDOWN_SECONDS:
                    logger.warning("Feed RSS %s; se pausa %ss: %s", url,
                                   NEWS_FEED_FAILURE_COOLDOWN_SECONDS, detalle)
                    _feed_last_warning[url] = ahora
                continue
            _feed_retry_after.pop(url, None)
            any_feed_ok = True
            for entry in feed.entries[:5]:
                title = getattr(entry, "title", None)
                title = _sanitize_headline(title)
                if not title or title in seen:
                    continue
                published = getattr(entry, "published_parsed", None)
                if published:
                    # CORRECCIÓN v10.5 — Bug #4 de la auditoría 2 (rev. 2),
                    # ALTA, confirmado en el código real: feedparser entrega
                    # published_parsed en UTC, pero time.mktime() lo
                    # interpreta como hora LOCAL del sistema. En Argentina
                    # (UTC-3) eso agregaba +3hs de antigüedad ficticia a
                    # cada noticia, descartando titulares recién publicados
                    # y dejando el feed vacío en la apertura. calendar.timegm()
                    # interpreta el struct_time como UTC, que es lo correcto.
                    age_seconds = time.time() - calendar.timegm(published)
                    if age_seconds > NEWS_MAX_AGE_SECONDS:
                        continue  # noticia vieja, no aporta contexto de "hoy"
                seen.add(title)
                headlines.append(title)
        except Exception as e:
            _feed_retry_after[url] = ahora + NEWS_FEED_FAILURE_COOLDOWN_SECONDS
            if ahora - _feed_last_warning.get(url, 0) >= NEWS_FEED_FAILURE_COOLDOWN_SECONDS:
                logger.warning("Error leyendo feed RSS %s; se pausa %ss: %s", url,
                               NEWS_FEED_FAILURE_COOLDOWN_SECONDS, e)
                _feed_last_warning[url] = ahora

    # NUEVO EN v10.0 — se suman los titulares acumulados durante la noche/
    # madrugada por r_news_engine_247.py (si ese proceso está corriendo),
    # para que la primera evaluación del día no dependa solo de lo que los
    # 3 feeds en vivo devuelvan en ese instante puntual.
    try:
        import r_news_engine_247
        overnight = r_news_engine_247.get_unprocessed_headlines(limit=10)
        for title in overnight:
            if title not in seen:
                seen.add(title)
                headlines.append(title)
                any_feed_ok = True
    except Exception as e:
        logger.info("Sin noticias overnight disponibles (r_news_engine_247 no corrió aún): %s", e)

    # CORREGIDO EN v16.0 — acá había una sola bandera para dos situaciones
    # que no se parecen: `news_unavailable = not any_feed_ok or not headlines`.
    # Ese `or not headlines` convertía CUALQUIER día tranquilo —feeds sanos,
    # simplemente sin titulares nuevos en la ventana de frescura— en el mismo
    # veto total que un apagón de infraestructura. Era el freno más silencioso
    # del sistema: no producía ningún error, el log decía "sin noticias
    # confiables" (que suena sensato) y el bot no operaba en el contexto más
    # común y más operable que existe. Ahora se devuelven las dos banderas por
    # separado y aj_trade_gate.py decide qué hacer con cada una.
    feeds_down = not any_feed_ok
    no_headlines = not headlines

    if feeds_down:
        logger.warning("Apagón de noticias: ningún feed respondió. Es una falla de "
                       "infraestructura nuestra, no una señal del mercado — el portón "
                       "operativo degrada el tamaño en vez de frenar todo.")
    elif no_headlines:
        logger.info("Feeds respondiendo, sin titulares nuevos en la ventana de frescura: "
                    "día sin novedades. No es motivo para no operar.")

    return {
        "headlines": headlines[:max_headlines],
        "feeds_down": feeds_down,
        "no_headlines": no_headlines,
        # Se mantiene la clave anterior por compatibilidad con los módulos que
        # todavía la leen, pero ahora significa lo que siempre debió significar:
        # se cayeron los feeds, no "el día estuvo tranquilo".
        "news_unavailable": feeds_down,
        "fetched_at": time.time(),
    }
