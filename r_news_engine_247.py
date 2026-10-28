"""
r_news_engine_247.py — Noticias fuera de horario de mercado (v10.0)

ACEPTADO de la auditoría 9.1 (rev. 2), adaptado a los patrones ya
existentes del proyecto (timeout real de red, sin inventar fallback si
todo falla — mismo criterio que g_news_feed.py).

Por qué existe: g_news_feed.py lee noticias EN VIVO cuando el bot va a
evaluar una operación durante la rueda. Pero entre el cierre de una rueda
y la apertura de la siguiente (toda la noche, más el pre-market de
EE.UU.) pueden pasar cosas relevantes — una devaluación, un anuncio del
Fed, una crisis internacional — que conviene tener acumuladas y
disponibles ANTES de que abra el mercado, en vez de que el bot recién se
entere recién a las 11:00.

Se corre como una tarea periódica (ver systemd timer en la guía de
instalación) cada 30-60 minutos fuera del horario de rueda. No decide
nada por sí solo — solo acumula. f_gemini_decision_engine.py sigue
siendo el único lugar donde se decide si una noticia es motivo de veto.
"""

import logging
import os
import sqlite3
import feedparser
import requests
from datetime import datetime

from g_news_feed import _sanitize_headline  # NUEVO EN v10.5 — misma sanitización que g_news_feed.py
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

logger = logging.getLogger("news_engine_247")
DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")

FEEDS_247 = [
    "https://www.ambito.com/rss/home.xml",
    "https://www.cronista.com/files/rss/news.xml",
    "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
    "https://feeds.bloomberg.com/markets/news.rss",
]


def init_news_db():
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS global_news_247 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT UNIQUE,
            source TEXT,
            fetched_at TIMESTAMP,
            market_session TEXT,
            processed INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def run_continuous_scan():
    init_news_db()
    conn = ac_db.connect_raw()
    c = conn.cursor()
    for url in FEEDS_247:
        try:
            # Mismo criterio que g_news_feed.py: descarga con timeout real
            # antes de parsear, para no colgarse si el servidor no responde.
            response = requests.get(url, timeout=8)
            feed = feedparser.parse(response.content)
            for entry in feed.entries[:10]:
                title = getattr(entry, "title", None)
                title = _sanitize_headline(title)
                if title:
                    c.execute("""
                        INSERT OR IGNORE INTO global_news_247
                        (title, source, fetched_at, market_session)
                        VALUES (?, ?, ?, ?)
                    """, (title, url, datetime.now().isoformat(), "OFF_MARKET"))
        except Exception as e:
            logger.error("Error procesando feed %s: %s", url, e)
    conn.commit()
    conn.close()


def get_unprocessed_headlines(limit: int = 20) -> list:
    """Se llama desde g_news_feed.py al arrancar la rueda, para que las
    noticias acumuladas de la noche/madrugada entren en la primera
    evaluación del día, no se pierdan."""
    conn = ac_db.connect_raw()
    c = conn.cursor()
    c.execute("""
        SELECT id, title FROM global_news_247
        WHERE processed = 0 ORDER BY fetched_at DESC LIMIT ?
    """, (limit,))
    rows = c.fetchall()
    if rows:
        ids = [r[0] for r in rows]
        c.executemany("UPDATE global_news_247 SET processed = 1 WHERE id = ?", [(i,) for i in ids])
        conn.commit()
    conn.close()
    return [r[1] for r in rows]


if __name__ == "__main__":
    run_continuous_scan()
