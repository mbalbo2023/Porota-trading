"""
technical_engine.py — Motor cuantitativo técnico (v2.2)

CORRIGE el hallazgo CRÍTICO "score_tech hardcodeado" de la auditoría.
Ahora calcula de verdad: alineación de tendencia 1H/15M, retroceso a la
media en 5M, RSI y ATR — tal como estaba prometido en el documento
original pero nunca implementado.

DECISIÓN DE DISEÑO — de dónde salen los datos de precio para el análisis:
PPI expone "Intraday Market Data" pero solo para la sesión del día en
curso (no hay minute-bars históricos de varios días), lo cual no alcanza
para construir series 1H/15M/5M con suficiente profundidad. Como los
tickers de este bot son CEDEARs cuyo activo subyacente cotiza en EE.UU.
(AAPL, SPY, NVDA...), el análisis técnico por default se hace sobre el
subyacente vía yfinance (que sí tiene historial intradiario de varias
semanas/meses) — la forma de la tendencia y el momentum del subyacente y
del CEDEAR son prácticamente la misma serie (co-integrados, salvo el
drift del CCL).

CORRECCIÓN v10.4 — TECHNICAL_DATA_SOURCE_CEDEARS: se agregó la
posibilidad de invertir esta prioridad. yfinance es un servicio externo,
gratuito, sin garantía contractual, y no está pensado ni regulado para
el mercado argentino — es una herramienta de datos, no una fuente
"oficial". Si preferís que la fuente PRIMARIA sea siempre PPI (el propio
bróker regulado en Argentina) y yfinance quede como respaldo —invirtiendo
el orden que tenía por default— poné TECHNICAL_DATA_SOURCE_CEDEARS=ppi
en el .env. La contrapartida honesta: PPI no tiene historial intradiario
de varios días, así que en modo "ppi" el análisis de CEDEARs pasa a ser
de un solo timeframe diario (igual que ya se hace para acciones/bonos
locales), no de tres timeframes (1H/15M/5M) como con yfinance. Ninguna
de las dos opciones es "la correcta" en abstracto — es una decisión
entre más detalle (yfinance) o depender solo de la fuente regulada local
(PPI). El default sigue siendo yfinance-primero, para no cambiar el
comportamiento de quien no toque esta variable.

IMPORTANTE: esto es solo para decidir SI el patrón técnico es favorable.
El precio real de entrada, el CCL y el costo de la operación siempre se
calculan contra la cotización real de PPI (ver ppi_client.py y
economics.py) — nunca se ejecuta ni se alerta con el precio del
subyacente en dólares, sin importar qué fuente se haya usado para el
análisis técnico.

Requiere: pip install yfinance pandas numpy
"""

import os
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional, Dict, Any

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger("technical_engine")

TECHNICAL_DATA_SOURCE_CEDEARS = os.getenv("TECHNICAL_DATA_SOURCE_CEDEARS", "yfinance").lower()

# Mapeo CEDEAR -> ticker subyacente en EE.UU. La ratio de conversión del
# CEDEAR no afecta el análisis (trabajamos en % de retorno, no en precio
# absoluto), pero el símbolo puede diferir del ticker local en algunos
# casos raros (ADRs con clase distinta, etc.) — completar/ajustar acá.
# AMPLIADO v10.5 — auditorías 2 y 3, hallazgo "mapeo incompleto de
# CEDEARs": la línea de abajo (evaluate_technical) YA usa
# CEDEAR_UNDERLYING_MAP.get(ticker, ticker) — es decir, para cualquier
# ticker no listado acá, cae por default a usar el ticker tal cual en
# yfinance. Esto YA cubre la mayoría de los CEDEARs descubiertos
# automáticamente, porque el ticker de CEDEAR en BYMA coincide con el
# símbolo del subyacente en EE.UU. en la gran mayoría de los casos (MELI,
# KO, TSLA, GOOGL, MSFT, BABA, DISN, AMZN...). Igual se amplía la tabla acá
# con los CEDEARs más líquidos del panel general, por prolijidad y para
# dejar explícitos los pocos casos donde el ticker SÍ difiere del
# subyacente (ej. GLOB, que cotiza como CEDEAR de Globant con el mismo
# símbolo, se deja igual para que quede documentado que se revisó). Si el
# descubrimiento automático trae un ticker no listado y no coincide con el
# subyacente real, el resultado es que yfinance no trae datos para ese
# símbolo — y evaluate_technical() ya tiene el respaldo automático a la
# serie histórica diaria de PPI para ese caso (ver más abajo), así que el
# instrumento no queda sin analizar, solo pierde el detalle intradiario.
CEDEAR_UNDERLYING_MAP = {
    "AAPL": "AAPL", "SPY": "SPY", "NVDA": "NVDA", "MELI": "MELI", "KO": "KO",
    "TSLA": "TSLA", "GOOGL": "GOOGL", "MSFT": "MSFT", "BABA": "BABA",
    "AMZN": "AMZN", "DISN": "DIS", "GLOB": "GLOB", "PBR": "PBR", "XOM": "XOM",
    "JPM": "JPM", "NFLX": "NFLX", "AMD": "AMD", "BA": "BA", "V": "V",
    "WMT": "WMT", "PFE": "PFE", "INTC": "INTC", "QQQ": "QQQ", "DIA": "DIA",
}


@dataclass
class TimeframeSignal:
    timeframe: str
    ema_fast: float
    ema_slow: float
    rsi: float
    trend_up: bool
    detail: str = ""


@dataclass
class TechnicalResult:
    ticker: str
    score_tech: float  # 0.0 - 1.0
    atr_1h: Optional[float]
    signals: Dict[str, TimeframeSignal] = field(default_factory=dict)
    data_ok: bool = True
    reason: str = ""


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _ema(close: pd.Series, span: int) -> pd.Series:
    return close.ewm(span=span, adjust=False).mean()


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


# ---------------------------------------------------------------------- #
# NUEVO EN v14.0 — Descarga concurrente + caché corta de velas
# ---------------------------------------------------------------------- #
# HALLAZGO DE LA AUDITORÍA v13 (aceptado en el diagnóstico, corregido con
# otra implementación): el análisis técnico bajaba las velas de un
# instrumento por vez, en serie. Con 40 CEDEARs y 3 marcos temporales cada
# uno, eso son 120 descargas secuenciales: minutos entre que se detecta
# una oportunidad y que se puede actuar sobre ella. En modo scalping, donde
# la ventana útil es de minutos, ese retraso se come la oportunidad entera.
#
# POR QUÉ NO SE USÓ LA SOLUCIÓN QUE PROPONÍA LA AUDITORÍA (asyncio +
# aiohttp): esa propuesta pegaba contra una URL de yfinance inventada
# ("api.yfinance.com/v8/finance/chart/...") y, sobre todo, yfinance es una
# librería SINCRÓNICA — no se la puede "await-ear". Envolverla en asyncio
# habría requerido igual un executor de hilos por debajo, con toda la
# maquinaria async encima sin ganancia. Como la descarga es I/O puro (el
# proceso está esperando a la red, no calculando), un ThreadPoolExecutor
# consigue exactamente la misma concurrencia con una fracción del cambio y
# sin tocar el resto del código, que sigue siendo sincrónico.
#
# IMPORTANTE — esto NO se aplica a las llamadas a PPI: ahí el cuello de
# botella es deliberado (PPI_MAX_REQUESTS_PER_SECOND, para que el bróker no
# nos bloquee la cuenta). Paralelizar contra PPI sería cambiar un problema
# de latencia por uno de bloqueo de cuenta. La concurrencia acá es solo
# contra yfinance, que no tiene ese límite y no es el bróker.
TECHNICAL_MAX_WORKERS = int(os.getenv("TECHNICAL_MAX_WORKERS", "6"))
TECHNICAL_CACHE_SECONDS = int(os.getenv("TECHNICAL_CACHE_SECONDS", "45"))

_bars_cache = {}          # {(underlying, interval, period): (epoch, df)}
_bars_cache_lock = threading.Lock()


def _fetch_bars(underlying: str, interval: str, period: str) -> Optional[pd.DataFrame]:
    """Con caché corta (TECHNICAL_CACHE_SECONDS): dentro de una misma vuelta
    de escaneo, el mismo marco temporal del mismo activo se pide una sola
    vez. Antes, evaluate_technical() y calculate_fair_value_premium_pct()
    podían bajar las mismas velas dos veces seguidas para un mismo ticker.
    La caché es deliberadamente corta: pasado ese tiempo se vuelve a pedir,
    porque operar con velas viejas es peor que esperar."""
    clave = (underlying, interval, period)
    ahora = time.time()
    with _bars_cache_lock:
        entrada = _bars_cache.get(clave)
        if entrada and (ahora - entrada[0]) < TECHNICAL_CACHE_SECONDS:
            return entrada[1]

    try:
        df = yf.Ticker(underlying).history(interval=interval, period=period)
        if df is None or df.empty or len(df) < 20:
            logger.warning("Datos insuficientes para %s (%s/%s): %s filas",
                            underlying, interval, period, 0 if df is None else len(df))
            return None
        with _bars_cache_lock:
            _bars_cache[clave] = (ahora, df)
            # Poda simple para que la caché no crezca sin techo durante una
            # sesión larga: se descartan las entradas ya vencidas.
            for k in [k for k, v in _bars_cache.items() if (ahora - v[0]) > TECHNICAL_CACHE_SECONDS * 4]:
                _bars_cache.pop(k, None)
        return df
    except Exception as e:
        logger.error("Error descargando datos de %s (%s/%s): %s", underlying, interval, period, e)
        return None


def prefetch_bars(cedear_tickers: list, scalping: bool = False) -> int:
    """Baja EN PARALELO todas las velas que va a necesitar la vuelta de
    escaneo, antes de empezar a evaluar. Cuando después evaluate_technical()
    pida cada marco temporal, ya va a estar en la caché y la evaluación pasa
    a ser puro cálculo local.

    Devuelve cuántas descargas se completaron con datos. Los errores
    individuales no se propagan: un ticker que falla se descarga después de
    la forma tradicional (la caché simplemente no lo va a tener).
    """
    combinaciones = []
    marcos = [("5m", "1d"), ("15m", "5d"), ("1h", "1mo")] if not scalping else [("1m", "1d"), ("5m", "1d")]
    for ticker in cedear_tickers:
        subyacente = CEDEAR_UNDERLYING_MAP.get(ticker, ticker)
        for interval, period in marcos:
            combinaciones.append((subyacente, interval, period))

    if not combinaciones:
        return 0

    completadas = 0
    with ThreadPoolExecutor(max_workers=TECHNICAL_MAX_WORKERS) as pool:
        futuros = {pool.submit(_fetch_bars, u, i, p): (u, i) for u, i, p in combinaciones}
        for futuro in as_completed(futuros):
            try:
                if futuro.result() is not None:
                    completadas += 1
            except Exception as e:
                logger.debug("Prefetch falló para %s: %s", futuros[futuro], e)
    logger.info("Prefetch técnico: %s/%s descargas con datos (%s hilos).",
                completadas, len(combinaciones), TECHNICAL_MAX_WORKERS)
    return completadas


def _analyze_timeframe(df: pd.DataFrame, timeframe: str, ema_fast_n: int, ema_slow_n: int) -> TimeframeSignal:
    close = df["Close"]
    ema_fast = _ema(close, ema_fast_n)
    ema_slow = _ema(close, ema_slow_n)
    rsi = _rsi(close)
    trend_up = bool(ema_fast.iloc[-1] > ema_slow.iloc[-1])
    return TimeframeSignal(
        timeframe=timeframe,
        ema_fast=float(ema_fast.iloc[-1]),
        ema_slow=float(ema_slow.iloc[-1]),
        rsi=float(rsi.iloc[-1]),
        trend_up=trend_up,
        detail=f"EMA{ema_fast_n}={ema_fast.iloc[-1]:.2f} EMA{ema_slow_n}={ema_slow.iloc[-1]:.2f} RSI={rsi.iloc[-1]:.1f}",
    )


def evaluate_technical(cedear_ticker: str, ppi_client=None) -> TechnicalResult:
    """
    Devuelve un score_tech en [0, 1] a partir de:
      - Tendencia 1H (EMA20 > EMA50): confirma la macro-tendencia del día.
      - Tendencia 15M (EMA9 > EMA21) + RSI en zona de retroceso saludable
        (35-60, ni sobrecomprado ni en caída libre): confirma que el pullback
        es una oportunidad de entrada y no el inicio de una reversión.
      - Momentum 5M: RSI recuperándose (cruzando hacia arriba desde <45),
        que es la señal de timing de entrada.

    Cada condición que se cumple suma 1/3 al score. Esto reemplaza el
    score_tech = 0.75 hardcodeado del draft original.

    NUEVO EN v10.3 — convierte en código la "Debilidad" que quedaba
    documentada en el FODA (dependencia de yfinance, un servicio externo
    gratuito sin garantía contractual). Si yfinance no responde o no
    trae suficiente historial, en vez de simplemente rendirse (data_ok
    =False, sin analizar nada), el sistema cae a un análisis de respaldo
    usando el historial diario de la propia PPI para ese CEDEAR — menos
    detallado (un solo timeframe en vez de tres, mismo límite que ya
    tenían las acciones/bonos locales), pero mejor que no tener ningún
    dato. Para esto hace falta pasarle el cliente de PPI (ppi_client) —
    si no se lo pasan, se comporta como antes (sin respaldo).
    """
    underlying = CEDEAR_UNDERLYING_MAP.get(cedear_ticker, cedear_ticker)

    # Si TECHNICAL_DATA_SOURCE_CEDEARS=ppi, PPI es la fuente PRIMARIA (no
    # el respaldo): ni siquiera se intenta yfinance. Ver docstring del
    # módulo para la explicación completa de esta decisión.
    if TECHNICAL_DATA_SOURCE_CEDEARS == "ppi":
        if ppi_client is None:
            return TechnicalResult(
                ticker=cedear_ticker, score_tech=0.0, atr_1h=None, data_ok=False,
                reason="TECHNICAL_DATA_SOURCE_CEDEARS=ppi pero no se recibió cliente de PPI.",
            )
        result = evaluate_technical_local(cedear_ticker, ppi_client, "CEDEARS", "A-24HS")
        result.reason = f"[Fuente primaria: PPI, por configuración] {result.reason}"
        return result

    df_1h = _fetch_bars(underlying, "1h", "3mo")
    df_15m = _fetch_bars(underlying, "15m", "1mo")
    df_5m = _fetch_bars(underlying, "5m", "5d")

    if df_1h is None or df_15m is None or df_5m is None:
        if ppi_client is not None:
            logger.warning("yfinance no trajo datos para %s — usando respaldo con historial de PPI.",
                            cedear_ticker)
            fallback = evaluate_technical_local(cedear_ticker, ppi_client, "CEDEARS", "A-24HS")
            fallback.reason = f"[Respaldo por caída de yfinance] {fallback.reason}"
            return fallback
        return TechnicalResult(
            ticker=cedear_ticker, score_tech=0.0, atr_1h=None,
            data_ok=False, reason="Datos de mercado insuficientes para análisis técnico.",
        )

    sig_1h = _analyze_timeframe(df_1h, "1H", 20, 50)
    sig_15m = _analyze_timeframe(df_15m, "15M", 9, 21)
    sig_5m = _analyze_timeframe(df_5m, "5M", 9, 21)

    atr_1h = float(_atr(df_1h).iloc[-1])

    score = 0.0
    reasons = []

    if sig_1h.trend_up:
        score += 1 / 3
        reasons.append("tendencia 1H alcista")
    else:
        reasons.append("tendencia 1H bajista/neutra")

    if sig_15m.trend_up and 35 <= sig_15m.rsi <= 60:
        score += 1 / 3
        reasons.append("retroceso 15M saludable")
    else:
        reasons.append("15M sin retroceso saludable")

    rsi_5m_prev = _rsi(df_5m["Close"]).iloc[-2]
    if sig_5m.rsi > rsi_5m_prev and sig_5m.rsi < 70:
        score += 1 / 3
        reasons.append("momentum 5M recuperándose")
    else:
        reasons.append("5M sin señal de timing de entrada")

    return TechnicalResult(
        ticker=cedear_ticker,
        score_tech=round(score, 2),
        atr_1h=atr_1h,
        signals={"1H": sig_1h, "15M": sig_15m, "5M": sig_5m},
        data_ok=True,
        reason="; ".join(reasons),
    )


def evaluate_technical_scalping(cedear_ticker: str) -> TechnicalResult:
    """
    NUEVO EN v10.5 — pedido explícito del usuario: modo scalping para
    probar el motor en SANDBOX (no se opera con dinero real, así que sirve
    para ver el comportamiento del sistema con ciclos mucho más cortos).

    DIFERENCIA vs. evaluate_technical() (swing, 1H/15M/5M): acá se mira
    solo 5M/1M — marcos de tiempo mucho más cortos, pensados para
    mantener la posición minutos, no días. El criterio de aprobación es
    más estricto en frecuencia (RSI de 1M cruzando desde sobreventa <35,
    con la EMA9 de 5M ya por encima de la EMA21 de 5M como filtro de
    contexto), a propósito, porque en timeframes tan cortos el ruido es
    mayor y una señal débil genera muchas más operaciones falsas que en
    swing.

    LÍMITE HONESTO: yfinance solo ofrece velas de 1 minuto de los últimos
    7 días — alcanza para probar el motor, pero no para un backtest largo
    en este timeframe (q_backtest.py sigue trabajando en diario). Si no
    hay datos de 1M/5M suficientes, se devuelve data_ok=False en vez de
    aproximar con otro timeframe — para scalping, usar el timeframe
    equivocado es peor que no operar.
    """
    underlying = CEDEAR_UNDERLYING_MAP.get(cedear_ticker, cedear_ticker)
    df_5m = _fetch_bars(underlying, "5m", "5d")
    df_1m = _fetch_bars(underlying, "1m", "1d")

    if df_5m is None or df_1m is None:
        return TechnicalResult(
            ticker=cedear_ticker, score_tech=0.0, atr_1h=None, data_ok=False,
            reason="Datos de 1M/5M insuficientes para scalping (yfinance no trajo suficiente historial intradiario).",
        )

    sig_5m = _analyze_timeframe(df_5m, "5M", 9, 21)
    rsi_1m = _rsi(df_1m["Close"])
    rsi_1m_now = float(rsi_1m.iloc[-1])
    rsi_1m_prev = float(rsi_1m.iloc[-2])
    atr_5m = float(_atr(df_5m).iloc[-1])

    score = 0.0
    reasons = []
    if sig_5m.trend_up:
        score += 0.5
        reasons.append("contexto 5M alcista (EMA9>EMA21)")
    else:
        reasons.append("contexto 5M sin tendencia alcista")

    if rsi_1m_now > rsi_1m_prev and rsi_1m_now < 45 and rsi_1m_prev < 40:
        score += 0.5
        reasons.append(f"rebote de sobreventa en 1M (RSI {rsi_1m_prev:.1f}→{rsi_1m_now:.1f})")
    else:
        reasons.append(f"sin señal de rebote de sobreventa en 1M (RSI={rsi_1m_now:.1f})")

    return TechnicalResult(
        ticker=cedear_ticker,
        score_tech=round(score, 2),
        atr_1h=atr_5m,  # se reutiliza el campo atr_1h (nombre histórico) como ATR de 5M para scalping
        signals={"5M": sig_5m},
        data_ok=True,
        reason="[SCALPING] " + "; ".join(reasons),
    )


def evaluate_technical_local(ticker: str, ppi_client, instrument_type: str, settlement: str,
                              days_back: int = 180, deflact_ccl: bool = False) -> TechnicalResult:
    """
    NUEVO EN v8.0 — análisis técnico para instrumentos que NO tienen un
    subyacente cotizando en EE.UU. (ACCIONES argentinas, BONOS): en vez de
    yfinance, usa la serie histórica DIARIA que expone la propia PPI
    (c_ppi_client.get_historical_series).

    LIMITACIÓN HONESTA respecto a evaluate_technical() (CEDEARs): PPI no
    ofrece velas intradiarias de varios días atrás en su API pública, así
    que acá solo hay UN timeframe (diario), no los tres (1H/15M/5M) que sí
    se pueden armar para CEDEARs vía yfinance. El score es más simple:
    tendencia (EMA20 > EMA50 diaria) + RSI en zona saludable — dos
    condiciones en vez de tres. Es una limitación de los datos disponibles,
    no una simplificación arbitraria.

    NUEVO EN v12.0 (Instrucción 3) — deflact_ccl: si es True, la serie de
    precios en pesos se deflacta por el CCL histórico del mismo período
    ANTES de calcular EMAs/RSI/ATR, para no confundir una devaluación con
    una ruptura técnica alcista real (hallazgo MEDIO de la auditoría v11,
    "Adaptación a la Microestructura del Mercado Argentino"). Default
    False por compatibilidad — j_main.py decide cuándo activarlo (tiene
    más sentido para BONOS/ACCIONES en pesos que para instrumentos que ya
    cotizan en USD)."""
    hist = ppi_client.get_historical_series(ticker, instrument_type, settlement, days_back)
    if not hist or len(hist) < 30:
        return TechnicalResult(
            ticker=ticker, score_tech=0.0, atr_1h=None, data_ok=False,
            reason=f"Historial insuficiente en PPI para {ticker} ({0 if not hist else len(hist)} barras).",
        )

    df = pd.DataFrame(hist)
    df = df.rename(columns={"price": "Close", "max": "High", "min": "Low"})
    for col in ["Close", "High", "Low"]:
        if col not in df.columns:
            return TechnicalResult(ticker=ticker, score_tech=0.0, atr_1h=None, data_ok=False,
                                    reason=f"Respuesta de PPI sin columna {col} para {ticker}.")

    if deflact_ccl:
        try:
            import d_economics as economics
            ccl_hist = ppi_client.get_historical_series("AL30", "BONOS", "INMEDIATA", days_back)
            usd_hist = ppi_client.get_historical_series("AL30D", "BONOS", "INMEDIATA", days_back)
            if ccl_hist and usd_hist and "date" in df.columns:
                ars_map = {h["date"]: h["price"] for h in ccl_hist if "date" in h and "price" in h}
                usd_map = {h["date"]: h["price"] for h in usd_hist if "date" in h and "price" in h}
                ccl_by_date = {d: (ars_map[d] / usd_map[d]) for d in ars_map
                               if d in usd_map and usd_map[d] > 0}
                fx_series = [ccl_by_date.get(d) for d in df["date"]]
                if all(fx is not None for fx in fx_series):
                    for col in ["Close", "High", "Low"]:
                        df[col] = economics.deflact_price_series(df[col].tolist(), fx_series)
                else:
                    logger.warning("evaluate_technical_local(%s): fechas de CCL incompletas, "
                                    "se sigue con la serie nominal en pesos.", ticker)
            else:
                logger.warning("evaluate_technical_local(%s): no se pudo obtener CCL histórico "
                                "para deflactar; se sigue con la serie nominal en pesos.", ticker)
        except Exception as e:
            logger.error("evaluate_technical_local(%s): error deflactando por CCL (%s); "
                          "se sigue con la serie nominal en pesos.", ticker, e)

    ema_fast = _ema(df["Close"], 20)
    ema_slow = _ema(df["Close"], 50)
    rsi = _rsi(df["Close"])
    atr = _atr(df)

    trend_up = bool(ema_fast.iloc[-1] > ema_slow.iloc[-1])
    rsi_now = float(rsi.iloc[-1])

    score = 0.0
    reasons = []
    if trend_up:
        score += 0.5
        reasons.append("tendencia diaria alcista (EMA20>EMA50)")
    else:
        reasons.append("tendencia diaria bajista/neutra")

    if 35 <= rsi_now <= 65:
        score += 0.5
        reasons.append(f"RSI diario en zona saludable ({rsi_now:.1f})")
    else:
        reasons.append(f"RSI diario fuera de zona saludable ({rsi_now:.1f})")

    return TechnicalResult(
        ticker=ticker,
        score_tech=round(score, 2),
        atr_1h=float(atr.iloc[-1]),  # nombre del campo se mantiene por compatibilidad, acá es ATR diario
        signals={"1D": TimeframeSignal("1D", float(ema_fast.iloc[-1]), float(ema_slow.iloc[-1]),
                                        rsi_now, trend_up, "serie diaria vía PPI")},
        data_ok=True,
        reason="; ".join(reasons),
    )


def calculate_fair_value_premium_pct(local_price: float, underlying_price_usd: float,
                                      ccl_rate: float, ratio: float) -> Optional[float]:
    """
    NUEVO EN v8.0 — recomendación de la auditoría 7.2 ("Valor Relativo"):
    compara el precio local del CEDEAR contra su "valor justo" implícito
    (precio del subyacente en dólares x CCL / ratio de conversión).

    ALCANCE DELIBERADAMENTE ACOTADO (para no sumar complejidad de más,
    como pediste): esto es INFORMATIVO en v8.0, no un gate que bloquee ni
    apruebe operaciones por sí solo, y no persiste el estado de la
    divergencia en el tiempo (la auditoría proponía esperar una
    divergencia "persistente", lo cual requiere trackear el premium a lo
    largo de varios días — una segunda estrategia completa, no un ajuste
    de la actual). Se agrega al mensaje de alerta como dato extra para
    que lo veas, y es la base sobre la que se podría construir una
    segunda estrategia más adelante si el bot original te resulta útil.

    Requiere el "ratio" de conversión del CEDEAR (cuántos CEDEARs equivalen
    a 1 acción del subyacente) cargado en n_instrument_watchlist.json — NO
    se adivina ni se asume 1:1. Si el ratio no está cargado (0 o ausente),
    devuelve None en vez de un número engañoso.
    """
    if not ratio or ratio <= 0 or not underlying_price_usd or not ccl_rate:
        return None
    fair_value = (underlying_price_usd * ccl_rate) / ratio
    if fair_value <= 0:
        return None
    return round((local_price - fair_value) / fair_value * 100, 2)
