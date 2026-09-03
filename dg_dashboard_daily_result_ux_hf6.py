"""Responsive HF6-v2 dashboard helpers for daily results and report registry.

Pure HTML rendering only. No broker/network/database mutation capability.
"""
from __future__ import annotations

import html
from decimal import Decimal, InvalidOperation


STATE_LABELS = {
    "GANANCIA": ("Ganancia", "positive"),
    "PERDIDA": ("Pérdida", "negative"),
    "MIXTO_POR_MONEDA": ("Resultado mixto", "neutral"),
    "NEUTRO": ("Neutro", "neutral"),
    "SIN_VALUACION": ("Sin valuación", "neutral"),
}


def _e(value) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def _number(value, decimals=2) -> str:
    try:
        raw=f"{Decimal(str(value)):,.{decimals}f}"
        return raw.replace(",", "_").replace(".", ",").replace("_", ".")
    except (InvalidOperation, ValueError, TypeError):
        return "—"


def _currency_result(row: dict) -> str:
    currency=_e(row.get("currency"))
    pnl=row.get("daily_pnl")
    pct=row.get("return_pct")
    if pnl in (None, ""):
        return f"<li><b>{currency}</b>: PnL no conciliado</li>"
    try:
        value=Decimal(str(pnl))
        cls="positive" if value > 0 else "negative" if value < 0 else "neutral"
    except (InvalidOperation, ValueError, TypeError):
        cls="neutral"
    pct_text="" if pct in (None, "") else f" · {_number(pct,4)}%"
    return f"<li><b>{currency}</b>: <span class='{cls}'>{_number(pnl)}{pct_text}</span></li>"


def daily_results_html(days: list[dict], *, max_symbols: int = 6) -> str:
    if not days:
        return ("<section class='paper-card'><h2>Resultado de las últimas jornadas</h2>"
                "<p class='paper-muted'>Aún no existe cierre diario conciliado.</p></section>")
    cards=[]
    for item in days:
        label,css=STATE_LABELS.get(str(item.get("state") or ""), ("Sin clasificar","neutral"))
        currencies="".join(_currency_result(row) for row in item.get("currencies",[])) or "<li>Sin resultado monetario conciliado.</li>"
        actions=item.get("actions") or {}
        action_text=" · ".join(f"{_e(k.replace('_',' ').title())}: {_e(v)}" for k,v in actions.items()) or "Sin ejecuciones PAPER"
        symbols=list(item.get("symbols") or [])
        shown=symbols[:max_symbols]
        suffix=f" +{len(symbols)-max_symbols}" if len(symbols)>max_symbols else ""
        symbol_text=", ".join(_e(s) for s in shown) + suffix if shown else "—"
        families=", ".join(_e(x) for x in item.get("families") or []) or "—"
        cards.append(
            "<article class='daily-result-card'>"
            f"<header><b>{_e(item.get('day'))}</b><span class='{css}'>{_e(label)}</span></header>"
            f"<ul class='daily-money'>{currencies}</ul>"
            f"<p><b>Operatoria:</b> {_e(action_text)}</p>"
            f"<p><b>Instrumentos:</b> {symbol_text}</p>"
            f"<p class='paper-muted'>Familias: {families} · fills: {_e(item.get('fills',0))} · posiciones cerradas: {_e(item.get('closed_positions',0))}</p>"
            "</article>"
        )
    return ("<section class='paper-card daily-results-section'><h2>Resultado de las últimas jornadas</h2>"
            "<p class='paper-muted'>PnL y retorno permanecen separados por moneda/plaza. No se suman ARS y USD.</p>"
            f"<div class='daily-results-grid'>{''.join(cards)}</div></section>")


def report_cards_html(rows: list[dict], *, report_link_builder=None) -> str:
    """Render report registry vertically so tablets never require horizontal scroll."""
    if not rows:
        return "<div class='paper-card'>Todavía no hay reportes disponibles.</div>"
    cards=[]
    for row in rows:
        report_id=row.get("id")
        links=[]
        if row.get("pdf_path"):
            href=(report_link_builder(report_id,"pdf") if report_link_builder else f"/api/reports/{report_id}/pdf")
            links.append(f"<a class='paper-action' href='{_e(href)}'>Descargar PDF</a>")
        if row.get("ai_path"):
            href=(report_link_builder(report_id,"ai") if report_link_builder else f"/api/reports/{report_id}/ai")
            links.append(f"<a class='paper-action' href='{_e(href)}'>Descargar datos</a>")
        buttons="".join(links) or "<span class='paper-muted'>Sin archivo descargable</span>"
        cards.append(
            "<article class='report-card'>"
            f"<div class='report-card-head'><b>{_e(row.get('period_type'))}</b><span>{_e(row.get('period_key'))}</span></div>"
            f"<p><b>Estado:</b> {_e(row.get('state'))}</p>"
            f"<p><b>Creado:</b> {_e(row.get('created_at'))}</p>"
            f"<p class='paper-muted'>{_e(row.get('detail'))}</p>"
            f"<div class='report-card-actions'>{buttons}</div>"
            "</article>"
        )
    return "<div class='report-card-grid'>"+"".join(cards)+"</div>"


RESPONSIVE_CSS = """
<style id='porota-hf6-v2-responsive-cards'>
.daily-results-grid,.report-card-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:12px}
.daily-result-card,.report-card{background:#fff;border:1px solid var(--line);border-radius:11px;padding:13px;min-width:0;overflow-wrap:anywhere}
.daily-result-card header,.report-card-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap;margin-bottom:8px}
.daily-money{margin:7px 0 10px;padding-left:19px}.daily-result-card p,.report-card p{margin:7px 0}
.report-card-actions{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}
.paper-table-responsive{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
.compact-list-limit{max-height:70vh;overflow:auto;overscroll-behavior:contain}
@media(max-width:700px){
  .daily-results-grid,.report-card-grid{grid-template-columns:1fr}
  .paper-grid{grid-template-columns:1fr}
  .paper-card{max-width:100%;overflow-wrap:anywhere}
  .paper-table.mobile-cards,.paper-table.mobile-cards tbody,.paper-table.mobile-cards tr,.paper-table.mobile-cards td{display:block;width:100%}
  .paper-table.mobile-cards thead{display:none}
  .paper-table.mobile-cards tr{border:1px solid var(--line);border-radius:9px;margin:9px 0;padding:8px;background:#fff}
  .paper-table.mobile-cards td{border:0;padding:5px 3px;white-space:normal;overflow-wrap:anywhere}
}
</style>
"""


def assert_responsive_ux_invariants() -> None:
    if "overflow-x:auto" not in RESPONSIVE_CSS:
        raise AssertionError("responsive fallback for genuinely tabular data missing")
    sample=report_cards_html([{"id":1,"period_type":"DIARIO","period_key":"2026-09-02","state":"VERDE"}])
    if "<table" in sample:
        raise AssertionError("report registry must not regress to a wide table")
