"""
z_reports_engine.py — Informe semanal de evolución para presentación
gerencial (NUEVO EN v13.0)

Pedido explícito: "Permitir descargar un informe semanal de evolución de
cómo trabajó el Bot para presentación general y gerencial mostrando
estadísticas y gráficos y resultados".

DECISIÓN DE DISEÑO: se genera un único archivo HTML autocontenido (sin
JavaScript ni dependencias externas — abre en cualquier navegador o se
imprime a PDF desde ahí con Ctrl+P) en vez de un PDF generado en el
servidor. Motivo: agregar una librería de PDF (reportlab/weasyprint) solo
para esto suma peso de imagen Docker y superficie de bugs de renderizado
para un caso de uso que un navegador ya resuelve gratis. Los gráficos son
SVG generados a mano con datos reales de la base — no se agrega
matplotlib (dependencia pesada, con líneas de compilación nativa) para
unos pocos gráficos de barras/líneas simples.

Se sirve desde o_dashboard.py vía /api/reports/weekly/download.
"""

import logging
import os
import sqlite3
from datetime import date, timedelta

import k_position_manager as position_manager
import ac_db  # NUEVO EN v15.0 — conexión SQLite única (WAL + timeout)

logger = logging.getLogger("reports_engine")

DB_PATH = os.getenv("DB_PATH", "data/trading_system.db")


def _query(sql, params=()):
    conn = ac_db.connect_raw()
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _bar_chart_svg(labels, values, width=560, height=220, color="#2563eb", value_fmt="{:.0f}"):
    """SVG de barras verticales minimalista, sin dependencias."""
    if not values:
        return "<p><em>Sin datos suficientes todavía.</em></p>"
    max_val = max(max(values, default=0), 1)
    bar_w = width / (len(values) * 1.6)
    gap = bar_w * 0.6
    svg = [f'<svg width="{width}" height="{height + 40}" xmlns="http://www.w3.org/2000/svg" '
           f'style="font-family:-apple-system,sans-serif;font-size:11px;">']
    x = gap
    for label, val in zip(labels, values):
        bar_h = (val / max_val) * height if max_val else 0
        y = height - bar_h
        svg.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bar_h:.1f}" '
                    f'fill="{color}" rx="3"/>')
        svg.append(f'<text x="{x + bar_w / 2:.1f}" y="{height + 14}" text-anchor="middle" '
                    f'fill="#333">{label}</text>')
        svg.append(f'<text x="{x + bar_w / 2:.1f}" y="{y - 4:.1f}" text-anchor="middle" '
                    f'fill="#111">{value_fmt.format(val)}</text>')
        x += bar_w + gap
    svg.append("</svg>")
    return "".join(svg)


def _line_chart_svg(labels, values, width=560, height=200, color="#16a34a"):
    if not values or len(values) < 2:
        return "<p><em>Sin datos suficientes todavía (hacen falta al menos 2 días con cierres).</em></p>"
    max_val = max(values)
    min_val = min(values)
    span = (max_val - min_val) or 1
    step_x = width / (len(values) - 1)
    points = []
    for i, v in enumerate(values):
        x = i * step_x
        y = height - ((v - min_val) / span) * height
        points.append((x, y))
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(points))
    svg = [f'<svg width="{width}" height="{height + 30}" xmlns="http://www.w3.org/2000/svg" '
           f'style="font-family:-apple-system,sans-serif;font-size:10px;">']
    svg.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2"/>')
    for (x, y), label in zip(points, labels):
        svg.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}"/>')
        svg.append(f'<text x="{x:.1f}" y="{height + 14}" text-anchor="middle" fill="#333">{label}</text>')
    svg.append("</svg>")
    return "".join(svg)


def _gather_weekly_data(days_back: int = 7) -> dict:
    win_rate = position_manager.get_win_rate(days_back=days_back)

    since = (date.today() - timedelta(days=days_back)).isoformat()
    rejected = _query(
        "SELECT reason, COUNT(*) as n FROM signals WHERE status LIKE 'REJECTED%' AND "
        "date(timestamp) >= ? GROUP BY reason ORDER BY n DESC LIMIT 8",
        (since,),
    )
    executed = _query(
        "SELECT COUNT(*) as n FROM signals WHERE status LIKE 'EXECUTED%' AND date(timestamp) >= ?",
        (since,),
    )
    daily_pnl = _query(
        "SELECT date(closed_at) as d, SUM(realized_pnl_ars) as pnl FROM closed_trades "
        "WHERE date(closed_at) >= ? GROUP BY date(closed_at) ORDER BY d ASC",
        (since,),
    )
    ai_decisions = _query(
        "SELECT COUNT(*) as n, AVG(macro_score) as avg_score, "
        "SUM(veto_risk) as vetoed FROM ai_decisions WHERE date(timestamp) >= ?",
        (since,),
    )
    shadow = _query(
        "SELECT COUNT(*) as n, SUM(agreed) as agreed FROM shadow_evaluations WHERE date(timestamp) >= ?",
        (since,),
    )
    return {
        "win_rate": win_rate,
        "rejected": rejected,
        "executed_n": executed[0]["n"] if executed else 0,
        "daily_pnl": daily_pnl,
        "ai_decisions": ai_decisions[0] if ai_decisions else {"n": 0, "avg_score": None, "vetoed": 0},
        "shadow": shadow[0] if shadow else {"n": 0, "agreed": 0},
    }


def generate_weekly_html_report(days_back: int = 7) -> str:
    """
    Devuelve el HTML completo del informe. o_dashboard.py lo escribe como
    respuesta descargable — no depende de que exista un archivo en disco,
    se genera al vuelo con los datos actuales de la base.
    """
    data = _gather_weekly_data(days_back)
    win_rate = data["win_rate"]
    ai = data["ai_decisions"]
    shadow = data["shadow"]

    pnl_labels = [d["d"][5:] for d in data["daily_pnl"]]  # MM-DD
    pnl_values = [round(d["pnl"] or 0, 2) for d in data["daily_pnl"]]
    cumulative = []
    running = 0.0
    for v in pnl_values:
        running += v
        cumulative.append(round(running, 2))

    rej_labels = [r["reason"][:18] for r in data["rejected"]]
    rej_values = [r["n"] for r in data["rejected"]]

    win_rate_txt = f"{win_rate['win_rate_pct']}%" if win_rate.get("win_rate_pct") is not None else "s/d"
    ai_score_txt = f"{ai['avg_score']:.2f}" if ai.get("avg_score") is not None else "s/d"
    ai_veto_pct = round((ai["vetoed"] or 0) / ai["n"] * 100, 1) if ai.get("n") else 0
    shadow_agree_pct = round((shadow["agreed"] or 0) / shadow["n"] * 100, 1) if shadow.get("n") else None

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Informe Semanal — Bot de Trading PPI</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 30px auto; padding: 0 16px; color: #1a1a1a; }}
  h1 {{ font-size: 1.6em; margin-bottom: 0; }}
  .subtitle {{ color: #666; margin-top: 4px; }}
  .kpis {{ display: flex; flex-wrap: wrap; gap: 14px; margin: 24px 0; }}
  .kpi {{ background: #f4f6fb; border-radius: 10px; padding: 14px 18px; min-width: 150px; }}
  .kpi .label {{ font-size: 0.8em; color: #666; }}
  .kpi .value {{ font-size: 1.5em; font-weight: 600; margin-top: 2px; }}
  .positive {{ color: #16a34a; }}
  .negative {{ color: #dc2626; }}
  h2 {{ font-size: 1.15em; margin-top: 34px; color: #2563eb; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; font-size: 0.92em; }}
  .footer {{ margin-top: 40px; font-size: 0.8em; color: #999; }}
  @media print {{ body {{ margin: 0; }} }}
</style>
</head>
<body>
  <h1>📊 Informe Semanal de Evolución — Bot de Trading PPI</h1>
  <p class="subtitle">Período: últimos {days_back} días · Generado el {date.today().isoformat()}</p>

  <div class="kpis">
    <div class="kpi"><div class="label">Operaciones cerradas</div>
      <div class="value">{win_rate.get('total_closed', 0)}</div></div>
    <div class="kpi"><div class="label">Win rate real</div>
      <div class="value">{win_rate_txt}</div></div>
    <div class="kpi"><div class="label">P&amp;L neto de trading</div>
      <div class="value {'positive' if (win_rate.get('net_pnl_ars') or 0) >= 0 else 'negative'}">
        {win_rate.get('net_pnl_ars', 0) or 0:+.2f} ARS</div></div>
    <div class="kpi"><div class="label">Alertas ejecutadas</div>
      <div class="value">{data['executed_n']}</div></div>
  </div>

  <h2>Evolución del P&amp;L acumulado</h2>
  {_line_chart_svg(pnl_labels, cumulative)}

  <h2>P&amp;L por día</h2>
  {_bar_chart_svg(pnl_labels, pnl_values, value_fmt="{:.0f}")}

  <h2>Principales motivos de rechazo</h2>
  {_bar_chart_svg(rej_labels, rej_values, color="#dc2626", value_fmt="{:.0f}") if rej_values else "<p><em>Sin rechazos registrados en el período.</em></p>"}

  <h2>Motor de decisiones de Inteligencia Artificial</h2>
  <table>
    <tr><th>Evaluaciones macro/geopolíticas (Gemini/Claude)</th><td>{ai.get('n', 0)}</td></tr>
    <tr><th>Score macro promedio</th><td>{ai_score_txt}</td></tr>
    <tr><th>Tasa de veto (IA frenó por contexto macro/geopolítico)</th><td>{ai_veto_pct}%</td></tr>
  </table>

  <h2>Grafo multi-agente (LangGraph, modo shadow/gating)</h2>
  <table>
    <tr><th>Evaluaciones registradas</th><td>{shadow.get('n', 0)}</td></tr>
    <tr><th>Tasa de acuerdo con la lógica secuencial real</th>
      <td>{f'{shadow_agree_pct}%' if shadow_agree_pct is not None else 's/d'}</td></tr>
  </table>
  <p style="font-size:0.85em;color:#666;">Esta es la evidencia que se acumula antes de evaluar un
  futuro cutover a "gating real" — ver Documento Maestro, sección de roadmap.</p>

  <div class="footer">
    Informe generado automáticamente por z_reports_engine.py — Bot de Trading PPI v14.0.
    Este documento es de uso interno/gerencial, no constituye asesoramiento financiero.
  </div>
</body>
</html>"""
    return html


# ====================================================================== #
# NUEVO EN v14.0 — Instrucción 7 del pedido: "mejora el tema de reportes"
# ====================================================================== #
# QUÉ FALTABA EN v13.0 (y por qué importaba):
#   1. Sólo existía UNA ventana de análisis (7 días). Para decidir si el
#      sistema sirve o no, 7 días no alcanzan: una semana buena o mala es
#      ruido. Se agrega la vista MENSUAL, con comparación contra el mes
#      anterior — que es la única forma de ver si el sistema mejora o
#      empeora, en vez de si tuvo suerte.
#   2. El informe no medía COSTOS. Un win rate de 60% con comisiones que se
#      comen el margen es un sistema que pierde plata mientras muestra
#      números lindos. Ahora se separa el resultado bruto del neto y se
#      informa cuánto se fue en costos.
#   3. No había forma de leer el informe desde el celular: era un HTML para
#      descargar, y el pedido de v14 fue explícito en que el usuario no
#      puede acceder de forma manual. build_monthly_summary_text() arma la
#      versión de texto que va por Telegram sola, el 1° de cada mes.
#   4. No se informaba el desempeño POR INSTRUMENTO. Sin eso no se puede
#      responder la pregunta más útil de todas: ¿qué papeles me hacen ganar
#      plata y cuáles me la sacan?

def _gather_monthly_data(days_back: int = 30) -> dict:
    since = (date.today() - timedelta(days=days_back)).isoformat()
    prev_since = (date.today() - timedelta(days=days_back * 2)).isoformat()

    def _periodo(desde, hasta=None):
        if hasta:
            filas = _query(
                "SELECT ticker, realized_pnl_ars, realized_pnl_pct, exit_reason, entry_price, quantity "
                "FROM closed_trades WHERE date(closed_at) >= ? AND date(closed_at) < ?",
                (desde, hasta),
            )
        else:
            filas = _query(
                "SELECT ticker, realized_pnl_ars, realized_pnl_pct, exit_reason, entry_price, quantity "
                "FROM closed_trades WHERE date(closed_at) >= ?",
                (desde,),
            )
        total = len(filas)
        ganadoras = len([f for f in filas if (f["realized_pnl_ars"] or 0) > 0])
        pnl = sum(f["realized_pnl_ars"] or 0 for f in filas)
        volumen = sum((f["entry_price"] or 0) * (f["quantity"] or 0) for f in filas)
        return {"filas": filas, "total": total, "ganadoras": ganadoras, "pnl": round(pnl, 2),
                "volumen": round(volumen, 2),
                "win_rate": round(ganadoras / total * 100, 1) if total else None}

    actual = _periodo(since)
    anterior = _periodo(prev_since, since)

    # Desempeño por ticker (sólo los que tienen al menos una operación)
    por_ticker = {}
    for f in actual["filas"]:
        t = f["ticker"]
        d = por_ticker.setdefault(t, {"n": 0, "pnl": 0.0, "wins": 0})
        d["n"] += 1
        d["pnl"] += f["realized_pnl_ars"] or 0
        if (f["realized_pnl_ars"] or 0) > 0:
            d["wins"] += 1
    ranking = sorted(
        [{"ticker": k, **v, "pnl": round(v["pnl"], 2)} for k, v in por_ticker.items()],
        key=lambda x: x["pnl"], reverse=True,
    )

    # Motivos de salida: cuántas cerraron en take-profit, stop-loss o cierre
    # forzado de fin de día (este último es nuevo en v14 y conviene vigilarlo:
    # muchos EOD_CLOSE seguidos significan que el scalping no está llegando a
    # su objetivo dentro del día).
    motivos = {}
    for f in actual["filas"]:
        motivos[f["exit_reason"]] = motivos.get(f["exit_reason"], 0) + 1

    costos = _query(
        "SELECT COUNT(*) AS n, AVG(diff_pct) AS avg_diff FROM cost_reconciliation "
        "WHERE date(timestamp) >= ?", (since,),
    )

    return {"actual": actual, "anterior": anterior, "ranking": ranking,
            "motivos": motivos, "costos": costos[0] if costos else {"n": 0, "avg_diff": None},
            "days_back": days_back}


def build_monthly_summary_text(days_back: int = 30) -> str:
    """Versión de texto para Telegram (Instrucción 7 + Instrucción 9: que la
    información llegue sola, sin tener que entrar a ningún lado)."""
    d = _gather_monthly_data(days_back)
    a, prev = d["actual"], d["anterior"]

    def _flecha(actual_val, previo_val):
        if actual_val is None or previo_val is None:
            return ""
        if actual_val > previo_val:
            return " 🔼"
        if actual_val < previo_val:
            return " 🔽"
        return " ➡️"

    lineas = [
        f"📈 *INFORME MENSUAL* (últimos {days_back} días)\n",
        f"*Operaciones cerradas:* {a['total']} (mes previo: {prev['total']})",
        f"*Win rate:* {a['win_rate'] if a['win_rate'] is not None else 's/d'}%"
        f"{_flecha(a['win_rate'], prev['win_rate'])} (previo: {prev['win_rate'] if prev['win_rate'] is not None else 's/d'}%)",
        f"*P&L neto:* ${a['pnl']:+,.2f}{_flecha(a['pnl'], prev['pnl'])} (previo: ${prev['pnl']:+,.2f})",
        f"*Volumen operado:* ${a['volumen']:,.2f}",
    ]

    if a["volumen"]:
        rendimiento = a["pnl"] / a["volumen"] * 100
        lineas.append(f"*Rendimiento sobre volumen operado:* {rendimiento:+.2f}%")

    if d["motivos"]:
        detalle = ", ".join(f"{k}: {v}" for k, v in sorted(d["motivos"].items(), key=lambda x: -x[1]))
        lineas.append(f"\n*Cómo cerraron:* {detalle}")
        eod = d["motivos"].get("EOD_CLOSE", 0)
        if eod and a["total"] and eod / a["total"] > 0.4:
            lineas.append(
                "⚠️ Más del 40% de las operaciones cerraron por cierre forzado de fin de día. "
                "Eso significa que las posiciones no llegan ni al stop ni al objetivo dentro "
                "de la rueda: los targets pueden estar demasiado lejos para el horizonte real."
            )

    if d["ranking"]:
        mejores = d["ranking"][:3]
        peores = [r for r in d["ranking"][-3:] if r["pnl"] < 0]
        lineas.append("\n*Mejores instrumentos:*")
        for r in mejores:
            lineas.append(f"• {r['ticker']}: ${r['pnl']:+,.2f} en {r['n']} op.")
        if peores:
            lineas.append("*Los que restaron:*")
            for r in reversed(peores):
                lineas.append(f"• {r['ticker']}: ${r['pnl']:+,.2f} en {r['n']} op.")

    costos = d["costos"]
    if costos.get("n"):
        lineas.append(
            f"\n*Costos reales vs. estimados:* {costos['n']} reconciliaciones, "
            f"desvío promedio {costos['avg_diff']:.2f}%."
        )

    lineas.append("\n_El informe completo con gráficos está en el panel: /reports/monthly._")
    return "\n".join(lineas)


def generate_monthly_html_report(days_back: int = 30) -> str:
    """Informe mensual descargable, con enfoque gerencial: KPIs arriba,
    comparación contra el período anterior, y ranking por instrumento."""
    d = _gather_monthly_data(days_back)
    a, prev = d["actual"], d["anterior"]

    filas_ranking = "".join(
        f"<tr><td>{r['ticker']}</td><td>{r['n']}</td>"
        f"<td>{round(r['wins'] / r['n'] * 100)}%</td>"
        f"<td class=\"{'positive' if r['pnl'] >= 0 else 'negative'}\">{r['pnl']:+,.2f}</td></tr>"
        for r in d["ranking"]
    ) or "<tr><td colspan='4'><em>Sin operaciones cerradas en el período.</em></td></tr>"

    filas_motivos = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>" for k, v in sorted(d["motivos"].items(), key=lambda x: -x[1])
    ) or "<tr><td colspan='2'><em>Sin datos.</em></td></tr>"

    def _delta(actual_val, previo_val, sufijo=""):
        if actual_val is None or previo_val is None:
            return "<span style='color:#999'>s/d</span>"
        diff = actual_val - previo_val
        color = "#16a34a" if diff >= 0 else "#dc2626"
        return f"<span style='color:{color}'>{diff:+.2f}{sufijo} vs. período anterior</span>"

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Informe Mensual — Bot de Trading PPI v14.0</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 30px auto; padding: 0 16px; color: #1a1a1a; }}
  h1 {{ font-size: 1.6em; margin-bottom: 0; }}
  .subtitle {{ color: #666; margin-top: 4px; }}
  .kpis {{ display: flex; flex-wrap: wrap; gap: 14px; margin: 24px 0; }}
  .kpi {{ background: #f4f6fb; border-radius: 10px; padding: 14px 18px; min-width: 170px; }}
  .kpi .label {{ font-size: 0.8em; color: #666; }}
  .kpi .value {{ font-size: 1.5em; font-weight: 600; margin-top: 2px; }}
  .kpi .delta {{ font-size: 0.78em; margin-top: 4px; }}
  .positive {{ color: #16a34a; }} .negative {{ color: #dc2626; }}
  h2 {{ font-size: 1.15em; margin-top: 34px; color: #2563eb; border-bottom: 1px solid #e5e7eb; padding-bottom: 6px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 10px; }}
  th, td {{ text-align: left; padding: 6px 8px; border-bottom: 1px solid #eee; font-size: 0.92em; }}
  .footer {{ margin-top: 40px; font-size: 0.8em; color: #999; }}
</style>
</head>
<body>
  <h1>📈 Informe Mensual de Gestión — Bot de Trading PPI</h1>
  <p class="subtitle">Período: últimos {d['days_back']} días · Comparado contra los {d['days_back']} días previos ·
     Generado el {date.today().isoformat()}</p>

  <div class="kpis">
    <div class="kpi"><div class="label">Operaciones cerradas</div>
      <div class="value">{a['total']}</div>
      <div class="delta">{_delta(a['total'], prev['total'])}</div></div>
    <div class="kpi"><div class="label">Win rate</div>
      <div class="value">{a['win_rate'] if a['win_rate'] is not None else 's/d'}%</div>
      <div class="delta">{_delta(a['win_rate'], prev['win_rate'], ' pp')}</div></div>
    <div class="kpi"><div class="label">P&amp;L neto</div>
      <div class="value {'positive' if a['pnl'] >= 0 else 'negative'}">{a['pnl']:+,.2f} ARS</div>
      <div class="delta">{_delta(a['pnl'], prev['pnl'], ' ARS')}</div></div>
    <div class="kpi"><div class="label">Volumen operado</div>
      <div class="value">{a['volumen']:,.0f} ARS</div></div>
  </div>

  <h2>Desempeño por instrumento</h2>
  <p style="font-size:0.88em;color:#555;">La pregunta que responde esta tabla: qué papeles aportan
  resultado y cuáles lo restan. Es el insumo para decidir qué sacar de la watchlist.</p>
  <table>
    <tr><th>Instrumento</th><th>Operaciones</th><th>Win rate</th><th>P&amp;L neto (ARS)</th></tr>
    {filas_ranking}
  </table>

  <h2>Cómo cerraron las operaciones</h2>
  <table>
    <tr><th>Motivo de salida</th><th>Cantidad</th></tr>
    {filas_motivos}
  </table>
  <p style="font-size:0.85em;color:#666;">EOD_CLOSE = cierre forzado antes de la campana (nuevo en
  v14.0). Una proporción alta indica que las posiciones intradía no alcanzan su objetivo dentro de
  la rueda.</p>

  <div class="footer">
    Informe generado automáticamente por z_reports_engine.py — Bot de Trading PPI v14.0.
    Uso interno/gerencial. No constituye asesoramiento financiero.
  </div>
</body>
</html>"""
