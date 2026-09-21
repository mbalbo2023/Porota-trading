"""Lectura anual de performance desde el histórico canónico; solo lectura."""
from __future__ import annotations

import html
import math
import os
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from statistics import pstdev
from urllib.parse import quote
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
TABLE = "history_canonical_v2"


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _history_uri() -> str:
    path = Path(os.getenv("HIST_DB_PATH", "data/market_history.db")).expanduser().resolve()
    return "file:" + quote(str(path), safe="/") + "?mode=ro"


def _connect():
    connection = sqlite3.connect(_history_uri(), uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _catalog():
    with closing(_connect()) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if TABLE not in tables:
            return []
        return [dict(row) for row in connection.execute(
            f"""SELECT DISTINCT UPPER(instrument_type) AS family, symbol, market, settlement
                FROM {TABLE}
                WHERE TRIM(COALESCE(instrument_type,''))<>'' AND TRIM(COALESCE(symbol,''))<>''
                ORDER BY family, symbol, market, settlement""")]


def _identity(value):
    parts = str(value or "").split("|", 3)
    if len(parts) != 4:
        return None
    return tuple(part.strip() for part in parts)


def _bars(identity, cutoff):
    family, symbol, market, settlement = identity
    with _connect() as connection:
        return [dict(row) for row in connection.execute(
            f"""SELECT date, open, high, low, close, volume, source, adjusted
                FROM {TABLE}
                WHERE UPPER(instrument_type)=? AND symbol=? AND market=? AND settlement=?
                  AND date<=?
                ORDER BY date""",
            (family, symbol, market, settlement, cutoff.isoformat()))]


def _number(value):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _price(value):
    number = _number(value)
    return "—" if number is None else f"{number:,.4f}"


def _pct(value):
    return "—" if value is None else f"{value * 100:+.2f}%"


def _sma(values, count):
    subset = values[-count:]
    return sum(subset) / count if len(subset) == count else None


def _rsi(values, period=14):
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(len(values) - period, len(values))]
    gains = [max(0.0, value) for value in changes]
    losses = [max(0.0, -value) for value in changes]
    avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
    if avg_loss == 0:
        return 100.0 if avg_gain else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _atr(rows, period=14):
    ranges = []
    prior_close = None
    for row in rows:
        high, low, close = (_number(row.get(key)) for key in ("high", "low", "close"))
        if high is None or low is None or close is None or high < low:
            prior_close = close
            continue
        true_range = high - low
        if prior_close is not None:
            true_range = max(true_range, abs(high - prior_close), abs(low - prior_close))
        ranges.append(true_range)
        prior_close = close
    return sum(ranges[-period:]) / period if len(ranges) >= period else None


def _metric(label, value, detail=""):
    return ("<article class='paper-card'><h2>" + _e(label) + "</h2><p class='analysis-value'>" +
            _e(value) + "</p>" + (f"<p class='paper-muted'>{_e(detail)}</p>" if detail else "") + "</article>")


def _render_report(identity, bars, year):
    family, symbol, market, settlement = identity
    valid = [(row, _number(row.get("close"))) for row in bars]
    valid = [(row, close) for row, close in valid if close is not None and close > 0]
    if not valid:
        return ("<div class='paper-warning'><b>Sin cierres válidos</b> para esta identidad en el histórico canónico.</div>"
                "<p class='paper-muted'>No se generan precios ni indicadores sintéticos.</p>")
    rows, closes = [item[0] for item in valid], [item[1] for item in valid]
    latest = rows[-1]
    current_year = [i for i, row in enumerate(rows) if str(row.get("date", ""))[:4] == str(year)]
    if not current_year:
        return ("<div class='paper-warning'><b>No hay barras del año calendario "
                + _e(year) + "</b> para este papel.</div>"
                "<p class='paper-muted'>El selector refleja solo series presentes en el almacén canónico v2.</p>")
    first_i, last_i = current_year[0], current_year[-1]
    base_i = first_i - 1 if first_i > 0 else None
    base_close = closes[base_i] if base_i is not None else closes[first_i]
    annual_return = closes[last_i] / base_close - 1 if base_close else None
    year_returns = []
    for i in range(max(1, first_i), last_i + 1):
        if closes[i - 1] > 0:
            year_returns.append(closes[i] / closes[i - 1] - 1)
    volatility = pstdev(year_returns) * math.sqrt(252) if len(year_returns) >= 2 else None
    peak = base_close
    max_drawdown = 0.0
    for close in closes[first_i:last_i + 1]:
        peak = max(peak, close)
        if peak > 0:
            max_drawdown = min(max_drawdown, close / peak - 1)
    sma_values = {period: _sma(closes[:last_i + 1], period) for period in (20, 50, 200)}
    recent = rows[max(0, last_i - 251):last_i + 1]
    highs = [_number(row.get("high")) for row in recent]
    lows = [_number(row.get("low")) for row in recent]
    highs = [value for value in highs if value is not None]
    lows = [value for value in lows if value is not None]
    adjusted = {row.get("adjusted") for row in rows[first_i:last_i + 1]}
    adjustment = "Ajustado por proveedor" if adjusted == {1} else (
        "Sin ajustar" if adjusted == {0} else "Ajuste mixto o desconocido")
    full_ohlc = sum(1 for row in rows[first_i:last_i + 1]
                    if all(_number(row.get(key)) is not None for key in ("open", "high", "low", "close")))
    sources = sorted({str(row.get("source") or "UNKNOWN") for row in rows[first_i:last_i + 1]})
    base_label = "cierre previo al año" if base_i is not None else "primera barra disponible (serie parcial)"
    end_date = str(latest.get("date") or "—")
    series_first = str(rows[first_i].get("date") or "—")
    high_text = _price(max(highs)) if highs else "—"
    low_text = _price(min(lows)) if lows else "—"
    metrics = [
        _metric(f"Performance {year}", _pct(annual_return), f"Base: {base_label}; último cierre: {end_date}"),
        _metric("Máxima caída del año", _pct(max_drawdown), "Drawdown calculado con cierres diarios desde el máximo acumulado."),
        _metric("Volatilidad realizada anualizada", _pct(volatility), "Desviación de retornos diarios × √252; requiere al menos 2 retornos."),
        _metric("RSI (14)", "—" if _rsi(closes[:last_i + 1]) is None else f"{_rsi(closes[:last_i + 1]):.1f}", "Calculado sobre cierres diarios disponibles."),
        _metric("ATR (14)", _price(_atr(rows[:last_i + 1])), "Solo se calcula si hay 14 rangos verdaderos con OHLC válido."),
        _metric("Media móvil 20 ruedas", _price(sma_values[20])),
        _metric("Media móvil 50 ruedas", _price(sma_values[50])),
        _metric("Media móvil 200 ruedas", _price(sma_values[200])),
        _metric("Máximo / mínimo 252 barras", f"{high_text} / {low_text}", "Ventana máxima disponible; no representa necesariamente 12 meses completos."),
        _metric("Último cierre", _price(closes[last_i]), f"Fecha: {end_date}"),
    ]
    partial = base_i is None
    note = ("Serie anual parcial: no había cierre previo al 1 de enero." if base_i is None else
            "La performance usa el cierre anterior al primer día del año cuando está disponible.")
    return (
        f"<h2>{_e(symbol)} — {_e(family)}</h2>"
        f"<p class='paper-muted'>Mercado {_e(market)} · Liquidación {_e(settlement)} · "
        f"Serie {_e(series_first)} a {_e(end_date)}</p>"
        + ("<div class='paper-warning'><b>" + _e(note) + "</b></div>" if partial else "")
        + "<div class='analysis-grid'>" + "".join(metrics) + "</div>"
        + "<div class='paper-card'><h2>Cobertura y procedencia</h2><p>"
        + f"<b>Barras del año:</b> {last_i - first_i + 1} · <b>OHLC completo:</b> {full_ohlc} · "
        + f"<b>Ajuste:</b> {_e(adjustment)} · <b>Fuentes:</b> {_e(', '.join(sources))}</p>"
        + "<p class='paper-muted'>Se muestra la serie canónica v2; conserva identidad completa y procedencia. "
        "No se mezclan mercado ni liquidación. El ajuste depende del indicador guardado por la fuente.</p></div>"
        + "<div class='paper-notice'>Informe histórico informativo. No habilita operatoria, no altera READY/HOLD, "
        "señales, tamaño, gates ni órdenes. Si faltan datos, el indicador se marca como no disponible.</div>"
    )


def render_page(family="", instrument=""):
    year = datetime.now(TZ).year
    try:
        catalog = _catalog()
    except (sqlite3.Error, OSError, ValueError):
        return ("<h1>Análisis anual</h1><div class='paper-warning'><b>Histórico no disponible para lectura.</b> "
                "La pantalla no crea ni repara la base. Revisar ruta/esquema mediante el procedimiento del repositorio.</div>")
    families = sorted({str(row["family"]) for row in catalog if row.get("family")})
    family = str(family or "").strip().upper()
    if family not in families:
        family = ""
    family_rows = [row for row in catalog if row.get("family") == family]
    identities = sorted({(str(row["symbol"]), str(row["market"]), str(row["settlement"]))
                         for row in family_rows})
    wanted = _identity(instrument)
    if wanted and wanted[0].upper() != family:
        wanted = None
    if wanted and wanted[1:] not in identities:
        wanted = None
    family_options = "".join(
        f"<option value='{_e(value)}'{' selected' if value == family else ''}>{_e(value)}</option>"
        for value in families)
    instrument_options = "".join(
        f"<option value='{_e(family + '|' + symbol + '|' + market + '|' + settlement)}' "
        f"{'selected' if wanted == (family, symbol, market, settlement) else ''}>"
        f"{_e(symbol)} · {_e(market)} · {_e(settlement)}</option>"
        for symbol, market, settlement in identities)
    form = (
        "<form method='get' action='/analisis' class='paper-card analysis-form'>"
        "<label for='analysis-family'><b>Tipo de instrumento</b></label>"
        f"<select id='analysis-family' name='family' required onchange=\"this.form.elements.instrument.value='';this.form.submit()\">"
        f"<option value=''>Seleccionar tipo</option>{family_options}</select>"
        "<label for='analysis-instrument'><b>Papel</b></label>"
        f"<select id='analysis-instrument' name='instrument' required>"
        f"<option value=''>Seleccionar papel</option>{instrument_options}</select>"
        "<button class='paper-action' type='submit'>Generar informe</button></form>"
        "<p class='paper-notice'>El catálogo del selector se deriva de identidades con barras canónicas; "
        "no representa instrumentos habilitados para operar.</p>"
    )
    report = ""
    if wanted:
        try:
            bars = _bars((family, wanted[1], wanted[2], wanted[3]), datetime.now(TZ).date())
            report = _render_report((family, wanted[1], wanted[2], wanted[3]), bars, year)
        except (sqlite3.Error, OSError, ValueError):
            report = "<div class='paper-warning'><b>No se pudo leer la serie seleccionada.</b> No se modificó el histórico.</div>"
    elif not families:
        report = ("<div class='paper-warning'><b>Sin series canónicas disponibles.</b> "
                  "No se generará un informe a partir de datos ausentes.</div>")
    return (
        "<h1>Análisis</h1><p class='paper-muted'>Performance del año calendario "
        + str(year) + " por instrumento, usando histórico canónico v2.</p>"
        + form + report
    )
