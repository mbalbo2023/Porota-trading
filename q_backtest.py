"""
q_backtest.py — Experimento legado NO VALIDADO (v17)

RETIRADO como vía de validación: mezcla subyacente/CEDEAR, moneda y fecha de
ejecución, y su partición posterior NO es walk-forward. Las funciones públicas
fallan antes de descargar datos. Consultar bx_execution_replay.py para el replay
offline nuevo; tampoco ese replay aprueba estrategias. Código histórico
conservado sólo para auditoría. Las afirmaciones originales debajo no están
validadas.

RESUELVE el pendiente más importante y repetido en todas las revisiones
anteriores (v6.0 a v8.0) y en las auditorías 7.1 y 8.1: el sistema nunca
había sido probado contra datos históricos. Todos los umbrales
(score_tech ≥0.70, score_macro ≥0.70, multiplicador de ATR) eran valores
de arranque razonables, nunca validados.

QUÉ SIMULA: la capa TÉCNICA (mismas fórmulas RSI/EMA/ATR que
e_technical_engine.py) + la capa de COSTOS Y HURDLE (mismas fórmulas de
comisión/IVA/BYMA/hurdle dinámico que d_economics.py) contra el precio
histórico diario del subyacente de cada CEDEAR (yfinance), con
walk-forward: la ventana de evaluación avanza en el tiempo, nunca mira
"hacia adelante" para decidir una entrada.

LIMITACIÓN HONESTA E IMPORTANTE — léela antes de confiar en los
resultados: este backtest NO simula la capa de IA (Gemini evaluando
noticias reales del día). No existe un archivo histórico de qué noticia
habría leído el bot cada día pasado, ni de qué habría respondido Gemini
en ese momento — inventar esa respuesta sería peor que no simularla,
porque daría una falsa sensación de haber validado algo que en realidad
no se validó.

Este backtest responde: "¿el patrón técnico + el filtro de costos, SOLOS,
tuvieron una ventaja estadística histórica?" — NO responde "¿el sistema
completo, con IA incluida, es rentable?". Para esa segunda pregunta, la
única forma honesta es SHADOW TRADING: dejar correr el bot completo en
SANDBOX durante varias semanas y mirar el win rate real que reporta
i_auto_tuner.py — que es lo que el Documento Maestro ya venía
recomendando como paso previo a operar con dinero real. Backtest y
shadow trading no son alternativas, son complementarios: uno valida la
parte matemática rápido y barato, el otro valida el sistema completo
(incluida la IA) más lento pero de forma realista.

Requiere: pip install yfinance pandas numpy (ya instalados para el bot).
Uso: python3 q_backtest.py AAPL   (o el ticker que quieras probar)
"""

import sys
import json
import logging
from datetime import date

import numpy as np
import pandas as pd

import e_technical_engine as tech
import os
import d_economics as economics

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest")

# CORRECCIÓN (encontrada en la revisión de "¿qué archivos tienen variables
# que deberían estar en el .env?"): estos 6 valores estaban fijos acá
# adentro, DUPLICADOS de los que ya existen en el .env y usa el bot real
# (j_main.py). Si cambiabas el riesgo por operación en el .env, el
# backtest seguía probando con el valor viejo sin que nadie se diera
# cuenta — un backtest que en silencio prueba una configuración distinta
# de la que corre en vivo no sirve para nada. Ahora los primeros tres
# (RISK_PCT_PER_TRADE, STOP_LOSS_ATR_MULT, TAKE_PROFIT_ATR_MULT) leen la
# MISMA variable de entorno que usa j_main.py — cambiar una la cambia
# para las dos partes del sistema. DEFAULT_MIN_SCORE_TECH también pasa a
# ser compartido con el valor de arranque de j_main.py. Los últimos dos
# (capital inicial y días máximos de tenencia en la simulación) son
# propios del backtest —no existe un equivalente en el bot en vivo— así
# que quedan con nombre propio, pero igual configurables desde el .env.
INITIAL_CAPITAL_ARS = float(os.getenv("BACKTEST_INITIAL_CAPITAL_ARS", "1000000"))
RISK_PCT_PER_TRADE = float(os.getenv("RISK_PCT_PER_TRADE", "1.0"))
# NUEVO EN v10.5 (segunda revisión) — antes este 0.1% de spread estaba
# hardcodeado en la llamada de más abajo. Una auditoría externa señaló
# (con la fórmula incorrecta — "no simula comisiones", cuando en
# realidad SÍ las simula con economics.calculate_trade_costs_pct(), la
# misma fórmula real que usa el bot en vivo) que el backtest no refleja
# slippage real de libro de órdenes. Eso es cierto para el SPREAD
# asumido, no para las comisiones: PPI no expone históricamente la
# profundidad del libro de órdenes de días pasados, así que no hay forma
# de medir el spread real de cada día pasado — 0.1% es una estimación
# fija razonable, no un dato medido. Se deja configurable acá para poder
# correr el backtest con distintos supuestos de spread y ver qué tan
# sensible es el resultado a ese número, en vez de esconder el supuesto
# dentro del código.
BACKTEST_ASSUMED_SPREAD_PCT = float(os.getenv("BACKTEST_ASSUMED_SPREAD_PCT", "0.1"))
MIN_SCORE_TECH = float(os.getenv("DEFAULT_MIN_SCORE_TECH", "0.70"))
STOP_LOSS_ATR_MULT = float(os.getenv("STOP_LOSS_ATR_MULT", "1.0"))
TAKE_PROFIT_ATR_MULT = float(os.getenv("TAKE_PROFIT_ATR_MULT", "2.0"))
MAX_HOLD_DAYS = int(os.getenv("BACKTEST_MAX_HOLD_DAYS", "10"))  # si no tocó stop ni target, se cierra "a mercado"


def _fetch_history(ticker: str, period: str = "2y") -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(ticker).history(interval="1d", period=period)
    if df is None or df.empty:
        raise RuntimeError(f"Sin datos históricos para {ticker}")
    return df


def _compute_daily_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Reutiliza EXACTAMENTE las mismas fórmulas que usa el bot en vivo
    (e_technical_engine._rsi/_ema/_atr), para que el backtest mida lo
    mismo que decide el sistema real — no una aproximación distinta."""
    df = df.copy()
    df["ema_fast"] = tech._ema(df["Close"], 20)
    df["ema_slow"] = tech._ema(df["Close"], 50)
    df["rsi"] = tech._rsi(df["Close"])
    df["atr"] = tech._atr(df)
    df["trend_up"] = df["ema_fast"] > df["ema_slow"]
    # Score simplificado a 2 de las 3 condiciones que usa el bot en vivo
    # (no hay dato multi-timeframe intradía histórico disponible vía
    # yfinance más allá de unos días — ver limitación equivalente en
    # e_technical_engine.evaluate_technical_local). Documentado, no oculto.
    df["score_tech"] = 0.0
    df.loc[df["trend_up"], "score_tech"] += 0.5
    df.loc[(df["rsi"] >= 35) & (df["rsi"] <= 65), "score_tech"] += 0.5
    return df


def run_backtest(ticker: str, period: str = "2y") -> dict:
    raise RuntimeError("LEGACY_BACKTEST_UNVERIFIED: usar replay offline v17; no habilita promoción")


def _legacy_run_backtest_unverified(ticker: str, period: str = "2y") -> dict:
    df = _fetch_history(ticker, period)
    df = _compute_daily_scores(df)

    capital = INITIAL_CAPITAL_ARS
    trades = []
    i = 50  # arranca después de tener suficiente historia para EMA50/RSI/ATR válidos
    n = len(df)

    while i < n - 1:
        row = df.iloc[i]
        if row["score_tech"] < MIN_SCORE_TECH or pd.isna(row["atr"]) or row["atr"] <= 0:
            i += 1
            continue

        entry_price = row["Close"]
        atr_pct = (row["atr"] / row["ema_fast"]) * 100 if row["ema_fast"] else 0
        stop_price = entry_price - entry_price * (atr_pct / 100) * STOP_LOSS_ATR_MULT
        target_price = entry_price + entry_price * (atr_pct / 100) * TAKE_PROFIT_ATR_MULT

        # Filtro económico: mismas fórmulas que en vivo (costos reales +
        # hurdle). Acá se usa el hurdle DEFAULT de arranque, ya que no hay
        # forma honesta de reconstruir la inflación/devaluación real de
        # cada día pasado sin una base de datos histórica de esos
        # indicadores — otra limitación documentada, no oculta.
        expected_gross = atr_pct * TAKE_PROFIT_ATR_MULT
        net_return = economics.calculate_net_return_pct(expected_gross, spread_pct=0.1)
        hurdle_check = economics.passes_hurdle(net_return, MAX_HOLD_DAYS,
                                                economics.DEFAULT_RISK_PREMIUM_PCT + 3.0)
        if not hurdle_check["approved"]:
            i += 1
            continue

        # Simular el resto de la ventana buscando cuál nivel se toca primero.
        exit_price, exit_reason, exit_idx = None, None, None
        for j in range(i + 1, min(i + 1 + MAX_HOLD_DAYS, n)):
            day = df.iloc[j]
            if day["Low"] <= stop_price:
                exit_price, exit_reason, exit_idx = stop_price, "STOP_LOSS", j
                break
            if day["High"] >= target_price:
                exit_price, exit_reason, exit_idx = target_price, "TAKE_PROFIT", j
                break
        if exit_price is None:
            exit_idx = min(i + MAX_HOLD_DAYS, n - 1)
            exit_price, exit_reason = df.iloc[exit_idx]["Close"], "TIMEOUT"

        qty = economics.calculate_position_size(capital, entry_price, stop_price, RISK_PCT_PER_TRADE)
        if qty <= 0:
            i = exit_idx + 1
            continue

        costs_pct = economics.calculate_trade_costs_fraction(spread_pct=BACKTEST_ASSUMED_SPREAD_PCT)
        gross_pnl = (exit_price - entry_price) * qty
        cost_ars = entry_price * qty * costs_pct
        net_pnl = gross_pnl - cost_ars
        capital += net_pnl

        trades.append({
            "entry_date": str(df.index[i].date()), "exit_date": str(df.index[exit_idx].date()),
            "entry_price": round(float(entry_price), 2), "exit_price": round(float(exit_price), 2),
            "quantity": qty, "exit_reason": exit_reason, "net_pnl": round(float(net_pnl), 2),
            "capital_after": round(float(capital), 2),
        })
        i = exit_idx + 1

    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    peak = INITIAL_CAPITAL_ARS
    max_dd = 0.0
    for t in trades:
        peak = max(peak, t["capital_after"])
        max_dd = max(max_dd, (peak - t["capital_after"]) / peak * 100 if peak else 0)

    return {
        "promotion_allowed": False,
        "validation_status": "LEGACY_UNVERIFIED",
        "ticker": ticker,
        "period": period,
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "net_pnl_ars": round(capital - INITIAL_CAPITAL_ARS, 2),
        "return_pct": round((capital - INITIAL_CAPITAL_ARS) / INITIAL_CAPITAL_ARS * 100, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "avg_win_ars": round(np.mean([t["net_pnl"] for t in wins]), 2) if wins else None,
        "avg_loss_ars": round(np.mean([t["net_pnl"] for t in losses]), 2) if losses else None,
        "trades": trades,
    }


def run_walk_forward(ticker: str, total_period: str = "3y", window_days: int = 180):
    """
    Walk-forward simplificado (no-anclado): parte el período total en
    ventanas sucesivas de window_days y corre run_backtest en cada una por
    separado, para ver si el resultado se mantiene parecido de ventana a
    ventana (una estrategia real no debería funcionar solo en un tramo y
    fallar en los demás — eso sería señal de sobreajuste al período
    completo en vez de un patrón real).
    """
    raise RuntimeError("POSTHOC_PARTITION_IS_NOT_WALK_FORWARD: validación pendiente")

    df = _fetch_history(ticker, total_period)
    windows = []
    start = 0
    while start + window_days < len(df):
        windows.append((df.index[start].date(), df.index[min(start + window_days, len(df) - 1)].date()))
        start += window_days

    logger.info("Walk-forward de %s en %d ventanas de %d días.", ticker, len(windows), window_days)
    results = []
    full_result = run_backtest(ticker, total_period)
    # Partimos los trades ya calculados por ventana de fecha, en vez de
    # re-descargar datos por ventana (más simple y evita pedirle a
    # yfinance el mismo ticker muchas veces seguidas).
    for w_start, w_end in windows:
        w_trades = [t for t in full_result["trades"] if str(w_start) <= t["entry_date"] <= str(w_end)]
        wins = [t for t in w_trades if t["net_pnl"] > 0]
        results.append({
            "window_start": str(w_start), "window_end": str(w_end),
            "trades": len(w_trades),
            "win_rate_pct": round(len(wins) / len(w_trades) * 100, 1) if w_trades else None,
            "net_pnl_ars": round(sum(t["net_pnl"] for t in w_trades), 2) if w_trades else 0,
        })
    return results


if __name__ == "__main__":
    print(json.dumps({"status": "BLOCKED_LEGACY_UNVERIFIED", "promotion_allowed": False,
                      "reason": "El experimento antiguo no valida CEDEARs ni walk-forward. "
                                "Usar bx_execution_replay.py con datos locales explícitos."},
                     ensure_ascii=False))
    sys.exit(2)
