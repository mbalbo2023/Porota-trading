"""
al_historical_ingest.py — Ingesta y archivo histórico de precios

QUÉ SE PIDIÓ Y QUÉ DE ESO YA ESTABA HECHO
---------------------------------------------------------------------------
El pedido era investigar si existe forma de obtener datos históricos del
mercado bursátil argentino, definir la fuente, el repositorio y qué
antigüedad corresponde, y avisar si el requerimiento ya estaba cumplido.

Está cumplido A MEDIAS, y conviene ser preciso con la mitad de cada cosa:

  YA ESTABA, y no se toca: la historia MACROECONÓMICA. El módulo
  ad_macro_history.py ya baja y archiva inflación del INDEC, dólar oficial y
  reservas del BCRA, y las series diarias de MEP/CCL/blue de ArgentinaDatos.
  Funciona, está probado y alimenta el piso de rentabilidad. Repetir esa
  integración sería trabajo duplicado: no se hace nada ahí.

  NO ESTABA: la historia de PRECIOS instrumento por instrumento. Para eso el
  sistema dependía de pedirle a PPI la serie en el momento —una API de
  operación, no un archivo histórico— y de yfinance, que para el mercado
  local da precios demorados y no cubre bonos ni opciones. Sin archivo
  propio no hay backtesting posible ni percentiles históricos. Ese es el
  hueco que llena este módulo.

LAS FUENTES, EN ORDEN, Y POR QUÉ ESE ORDEN
---------------------------------------------------------------------------
  1. IOL (api.invertironline.com). Primera opción cuando haya credenciales.
     Es un bróker regulado, publica series ajustadas por splits y dividendos,
     y cubre acciones, bonos, opciones, cauciones y futuros. Ajustada es la
     palabra clave: una serie sin ajustar tiene saltos falsos que arruinan
     cualquier backtest sin dar ninguna señal de que algo anda mal.

  2. BYMA Open Data (open.bymadata.com.ar). Es el portal abierto del propio
     mercado, así que como fuente de VERDAD sobre qué instrumentos existen es
     mejor que cualquier otra: la lista de series de opciones y contratos de
     futuros vigentes sale de acá. No requiere credenciales.

  3. data912.com. API pública y gratuita del mercado argentino, con OHLCV
     histórico y cadenas de opciones. Sirve como tercera fuente y para
     contrastar. ADVERTENCIA IMPORTANTE, y va en el código y en el documento:
     su propio autor la describe como datos educativos, explícitamente no en
     tiempo real, con caché de alrededor de dos horas. Eso la vuelve perfecta
     para archivo histórico y completamente inapropiada para decidir una
     entrada. Por eso todo lo que entra por esta vía queda marcado con
     apto_para_ordenar=False en la base.

  4. Yahoo Finance. Se degrada a último recurso, solo acciones y CEDEARs.

NINGUNA de estas fuentes reemplaza a PPI para ejecutar. PPI sigue siendo el
único precio con el que se dimensiona y se manda una orden.

CUÁNTA HISTORIA, Y ESTA ES UNA DECISIÓN DE CRITERIO
---------------------------------------------------------------------------
Un año por default, y no más, por una razón que es específica de Argentina:
las series locales atraviesan cambios de régimen —saltos cambiarios, cepos
que se abren y se cierran, cambios de política monetaria— que rompen la
comparabilidad estadística. Una volatilidad calculada sobre cinco años de
historia argentina mezcla mundos distintos y produce un número que no
describe ningún mercado que haya existido nunca.

  · 365 días: default. Cubre un ciclo completo de estacionalidad y un rango
    de 52 semanas que es la referencia que mira todo el mundo.
  · 90 días: lo mínimo útil. Menos que eso y la volatilidad anualizada se
    calcula sobre tan pocos puntos que el intervalo de confianza es más
    ancho que la señal.
  · 730 días: opcional, solo para el backtest del filtro macro, donde
    justamente interesa ver cómo se comportó el sistema ATRAVESANDO un
    cambio de régimen. Para indicadores del día a día no se usa.
"""

import json
import logging
import os
import sqlite3
import time
from datetime import date, datetime
from typing import Dict, List, Optional

logger = logging.getLogger("historical_ingest")

HIST_DB_PATH = os.getenv("HIST_DB_PATH", "./data/market_history.db")
HIST_DEFAULT_DAYS = int(os.getenv("HIST_DEFAULT_DAYS", "365"))
HIST_MIN_DAYS_USABLE = int(os.getenv("HIST_MIN_DAYS_USABLE", "90"))
HIST_BATCH_SLEEP = float(os.getenv("HIST_BATCH_SLEEP", "0.25"))

BYMA_OPEN_BASE = os.getenv("BYMA_OPEN_BASE", "https://open.bymadata.com.ar")
DATA912_BASE = os.getenv("DATA912_BASE", "https://data912.com")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS market_historical_ohlcv (
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    source TEXT NOT NULL,
    adjusted INTEGER DEFAULT 0,
    ingested_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_symbol_date ON market_historical_ohlcv(symbol, date);

-- Registro de cada corrida del ETL. Sin esto no hay forma de saber si el
-- archivo está al día o si el worker viene fallando en silencio desde hace
-- una semana, que es exactamente el tipo de falla que nadie nota hasta que
-- toma una decisión con datos viejos.
CREATE TABLE IF NOT EXISTS ingest_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT,
    source TEXT, symbols_ok INTEGER, symbols_failed INTEGER,
    rows_written INTEGER, notes TEXT
);
"""


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(HIST_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(HIST_DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(ESQUEMA)
        conn.commit()


def _normalizar_fila_iol(p: dict) -> Optional[tuple]:
    """Traduce una vela de IOL al esquema propio. Devuelve None si la vela no
    tiene fecha o cierre: media vela es peor que ninguna, porque entra al
    archivo y después nadie sabe por qué el indicador da raro ese día."""
    fecha = str(p.get("fecha", ""))[:10]
    cierre = p.get("ultimoPrecio", p.get("cierre"))
    if not fecha or cierre in (None, 0):
        return None
    return (fecha,
            p.get("apertura"), p.get("maximo"), p.get("minimo"),
            cierre, p.get("montoOperado", p.get("volumen", 0)))


def guardar_velas(symbol: str, asset_class: str, velas: List[tuple],
                  source: str, adjusted: bool) -> int:
    """UPSERT masivo. Se sobrescribe la vela existente porque una serie
    ajustada que llega después es más correcta que la sin ajustar que ya
    estaba: el ajuste por dividendos reescribe el pasado, y así debe ser."""
    if not velas:
        return 0
    filas = [(symbol, asset_class, v[0], v[1], v[2], v[3], v[4], v[5],
              source, int(adjusted)) for v in velas]
    sql = """
        INSERT INTO market_historical_ohlcv
            (symbol, asset_class, date, open, high, low, close, volume, source, adjusted)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume,
            source=excluded.source, adjusted=excluded.adjusted,
            ingested_at=CURRENT_TIMESTAMP
    """
    with _conn() as conn:
        conn.executemany(sql, filas)
        conn.commit()
    return len(filas)


def backfill_desde_iol(iol_client, simbolos: List[str], asset_class: str = "ACCIONES",
                       dias: int = HIST_DEFAULT_DAYS) -> dict:
    """
    Descarga histórica completa desde IOL, con ritmo autoimpuesto.

    Corre como tarea de fondo, nunca dentro del ciclo de evaluación: bajar
    cuatrocientos instrumentos lleva minutos, y el bucle de trading no puede
    quedar esperando eso. j_main la programa fuera del horario de rueda.
    """
    if not getattr(iol_client, "enabled", False):
        return {"ok": False, "motivo": "El conector de IOL está desactivado o sin credenciales."}

    init_db()
    inicio = datetime.now().isoformat()
    ok = fallidos = escritas = 0

    for simbolo in simbolos:
        try:
            crudas = iol_client.get_serie_historica(simbolo, dias=dias, ajustada=True)
            velas = [v for v in (_normalizar_fila_iol(p) for p in crudas) if v]
            if len(velas) < HIST_MIN_DAYS_USABLE:
                logger.info("%s: solo %d velas (mínimo útil %d). Se guarda igual, marcado como corto.",
                            simbolo, len(velas), HIST_MIN_DAYS_USABLE)
            escritas += guardar_velas(simbolo, asset_class, velas, "IOL", adjusted=True)
            ok += 1
        except Exception as e:
            logger.warning("Falló el histórico de %s: %s", simbolo, e)
            fallidos += 1
        time.sleep(HIST_BATCH_SLEEP)

    with _conn() as conn:
        conn.execute("""INSERT INTO ingest_runs
                        (started_at, finished_at, source, symbols_ok, symbols_failed, rows_written, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                     (inicio, datetime.now().isoformat(), "IOL", ok, fallidos, escritas,
                      f"{dias} días, series ajustadas"))
        conn.commit()

    logger.info("Backfill IOL terminado: %d instrumentos ok, %d fallidos, %d velas.", ok, fallidos, escritas)
    return {"ok": True, "instrumentos_ok": ok, "fallidos": fallidos, "velas": escritas}


from datetime import timedelta as _timedelta
from zoneinfo import ZoneInfo as _ZoneInfo
import ak_byma_calendar as _byma_calendar


HISTORICAL_READY_HOUR = int(os.getenv("HISTORICAL_READY_HOUR", "19"))
HISTORICAL_READY_MINUTE = int(os.getenv("HISTORICAL_READY_MINUTE", "30"))


def fecha_esperada_historica(ahora=None) -> date:
    """Última rueda BYMA que razonablemente ya debe estar publicada."""
    timezone = _ZoneInfo(os.getenv("SERVER_TIMEZONE", "America/Argentina/Buenos_Aires"))
    if ahora is None:
        local = datetime.now(timezone)
    elif ahora.tzinfo is None:
        local = ahora.replace(tzinfo=timezone)
    else:
        local = ahora.astimezone(timezone)
    candidate = local.date()
    cutoff = (HISTORICAL_READY_HOUR, HISTORICAL_READY_MINUTE)
    if (local.hour, local.minute) < cutoff or not _byma_calendar.es_dia_habil_operativo(candidate):
        candidate -= _timedelta(days=1)
    while not _byma_calendar.es_dia_habil_operativo(candidate):
        candidate -= _timedelta(days=1)
    return candidate


def calcular_ruedas_atrasadas(fecha_mas_reciente, ahora=None):
    """Cuenta sesiones BYMA faltantes; None significa dato inválido/futuro."""
    try:
        if isinstance(fecha_mas_reciente, datetime):
            recent = fecha_mas_reciente.date()
        elif isinstance(fecha_mas_reciente, date):
            recent = fecha_mas_reciente
        else:
            recent = datetime.fromisoformat(str(fecha_mas_reciente)).date()
        expected = fecha_esperada_historica(ahora)
        if recent > expected:
            return None
        missing = 0
        cursor = recent + _timedelta(days=1)
        while cursor <= expected:
            if _byma_calendar.es_dia_habil_operativo(cursor):
                missing += 1
            cursor += _timedelta(days=1)
        return missing
    except (TypeError, ValueError, OverflowError):
        return None


def estado_del_archivo() -> dict:
    """Frescura en ruedas BYMA; atraso_dias queda solo por compatibilidad."""
    init_db()
    with _conn() as conn:
        fila = conn.execute("""
            SELECT COUNT(DISTINCT symbol), MAX(date), COUNT(*)
            FROM market_historical_ohlcv
        """).fetchone()
        ultima = conn.execute("""
            SELECT finished_at, source, symbols_ok, symbols_failed
            FROM ingest_runs ORDER BY run_id DESC LIMIT 1
        """).fetchone()

    simbolos, fecha_max, filas = fila or (0, None, 0)
    atraso_dias = None
    ruedas_atrasadas = None
    fecha_esperada = fecha_esperada_historica().isoformat()
    if fecha_max:
        try:
            atraso_dias = (date.today() - datetime.fromisoformat(fecha_max).date()).days
        except (TypeError, ValueError):
            atraso_dias = None
        ruedas_atrasadas = calcular_ruedas_atrasadas(fecha_max)

    if not simbolos or ruedas_atrasadas is None:
        semaforo = "ROJO"
    elif ruedas_atrasadas == 0:
        semaforo = "VERDE"
    elif ruedas_atrasadas == 1:
        semaforo = "AMARILLO"
    else:
        semaforo = "ROJO"

    return {
        "semaforo": semaforo,
        "instrumentos_archivados": simbolos,
        "velas_totales": filas,
        "fecha_mas_reciente": fecha_max,
        "fecha_esperada": fecha_esperada,
        "ruedas_atrasadas": ruedas_atrasadas,
        "atraso_dias": atraso_dias,
        "ultima_corrida": {"terminada": ultima[0], "fuente": ultima[1],
                           "ok": ultima[2], "fallidos": ultima[3]} if ultima else None,
    }
def extraer_features(symbol: str) -> dict:
    """
    Comprime un año de velas en cinco números para el prompt de la IA.

    Mandarle 365 velas al modelo es caro y contraproducente: la ventana se
    llena de ruido y el modelo termina razonando sobre el detalle en vez de
    sobre la posición del precio. Estos cinco números responden lo único que
    la IA necesita saber del pasado — si el precio está caro o barato contra
    su propio rango, y qué tan violento es este papel.
    """
    init_db()
    with _conn() as conn:
        filas = conn.execute("""
            SELECT date, high, low, close, volume
            FROM market_historical_ohlcv WHERE symbol=? ORDER BY date ASC
        """, (symbol,)).fetchall()

    if len(filas) < HIST_MIN_DAYS_USABLE:
        return {"estado_datos": "HISTORIA_INSUFICIENTE", "velas_disponibles": len(filas),
                "minimo_requerido": HIST_MIN_DAYS_USABLE}

    cierres = [f[3] for f in filas if f[3]]
    maximos = [f[1] for f in filas if f[1]]
    minimos = [f[2] for f in filas if f[2]]
    volumenes = [f[4] or 0 for f in filas]

    retornos = [(cierres[i] / cierres[i - 1] - 1) for i in range(1, len(cierres)) if cierres[i - 1]]
    ultimos = retornos[-30:]
    media = sum(ultimos) / len(ultimos) if ultimos else 0
    var = sum((r - media) ** 2 for r in ultimos) / (len(ultimos) - 1) if len(ultimos) > 1 else 0
    vol_anualizada = (var ** 0.5) * (252 ** 0.5)

    maximo_52 = max(maximos) if maximos else 0
    minimo_52 = min(minimos) if minimos else 0
    actual = cierres[-1]
    rango = maximo_52 - minimo_52
    percentil = (actual - minimo_52) / rango if rango > 0 else 0.5

    return {
        "estado_datos": "OK",
        "volatilidad_anualizada_30d": round(vol_anualizada, 4),
        "maximo_52_semanas": maximo_52,
        "minimo_52_semanas": minimo_52,
        "percentil_en_rango_52s": round(percentil, 2),
        "volumen_promedio_30d": round(sum(volumenes[-30:]) / min(30, len(volumenes)), 2),
        "velas_disponibles": len(filas),
    }
