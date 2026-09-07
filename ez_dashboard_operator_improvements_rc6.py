"""RC6 operator-requested dashboard improvements for 07-Sep-2026.

Presentation/read-only only. This module never mutates SQLite, calls broker
APIs, changes strategy/gates, restarts the observer, or enables order capability.

Changes:
- panel: last five BYMA operational daily summaries only;
- motor-trading: remove five operator-declutter sections;
- universo operativo: expose current market snapshots explicitly and clarify
  that the PAPER operations table is a fill ledger, not a quote ticker;
- sistema: keep the section menu horizontal and sticky below the main nav.
"""
from __future__ import annotations

from datetime import datetime
import ast
import inspect
import sqlite3

import ak_byma_calendar as byma_calendar
import bg_paper_dashboard as bg
import bh_universe_dashboard_hf6 as universe
import df_daily_operation_summary_hf6 as daily_summary
import dg_dashboard_daily_result_ux_hf6 as daily_ux

_installed = False
_original = {}

SYSTEM_TOP_NAV_CSS = r"""
<style id='porota-rc6-system-horizontal-nav'>
/* Operator invariant: system sections remain visible at the top while scrolling. */
.system-layout{display:block!important;min-width:0!important}
.system-nav{
  position:sticky!important;
  top:58px!important;
  z-index:25!important;
  display:flex!important;
  flex-direction:row!important;
  flex-wrap:nowrap!important;
  align-items:center!important;
  gap:7px!important;
  width:100%!important;
  max-width:100%!important;
  overflow-x:auto!important;
  overflow-y:hidden!important;
  white-space:nowrap!important;
  -webkit-overflow-scrolling:touch!important;
  overscroll-behavior-x:contain!important;
  padding:8px 4px!important;
  margin:0 0 12px!important;
  background:var(--paper-bg,#f5f7fb)!important;
  border-bottom:1px solid var(--line,#d8dee9)!important;
}
.system-nav a{flex:0 0 auto!important;white-space:nowrap!important}
.system-content{min-width:0!important;width:100%!important}
@media(max-width:760px){.system-nav{top:54px!important}}
</style>
"""


def _day(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def filter_operational_days(days: list[dict], limit: int = 5) -> list[dict]:
    """Newest-first daily cards restricted to normal BYMA trading sessions."""
    out = []
    for item in days:
        day = _day(item.get("day"))
        if day is None or not byma_calendar.es_dia_habil_operativo(day):
            continue
        out.append(item)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _daily_results_panel() -> str:
    """Render exactly the latest five normal BYMA sessions, read-only."""
    try:
        c = sqlite3.connect(f"file:{bg.DB_PATH}?mode=ro", uri=True, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        try:
            # Read enough persisted days to survive weekends/holidays, then filter.
            days = daily_summary.summarize(c, limit_days=31)
        finally:
            c.close()
    except Exception:
        days = []
    return daily_ux.daily_results_html(filter_operational_days(days, 5))


def _hidden_panel() -> str:
    """Presentation suppression only; underlying evidence remains persisted."""
    return ""


def _market_freshness(value) -> tuple[str, str]:
    try:
        stamp = bg.aware_datetime(value).astimezone(bg.TZ)
        age = (datetime.now(bg.TZ) - stamp).total_seconds()
        if 0 <= age <= 180:
            return "ACTUAL", "OK"
        if age >= 0:
            return "STALE", "AMARILLO"
    except Exception:
        pass
    return "SIN_HORA", "GRIS"


def _current_market_panel() -> str:
    if not bg._table("market_snapshots"):
        return ("<div class='paper-card'><h2>Spot PAPER — mercado actual</h2>"
                "<div class='paper-warning'>No existe la tabla de snapshots de mercado.</div></div>")
    rows = bg._rows("""
      SELECT m.symbol,m.asset_class,m.market,m.currency,m.settlement,
             m.last,m.bid,m.ask,m.bid_size,m.ask_size,
             m.observed_at,m.book_at,m.trade_at,m.last_kind
      FROM market_snapshots m
      JOIN (
        SELECT symbol,asset_class,market,currency,settlement,MAX(id) max_id
        FROM market_snapshots
        GROUP BY symbol,asset_class,market,currency,settlement
      ) latest ON latest.max_id=m.id
      ORDER BY julianday(m.observed_at) DESC,m.symbol
      LIMIT 100
    """)
    body = []
    for row in rows:
        label, state = _market_freshness(row.get("observed_at"))
        body.append(
            "<tr>"
            f"<td><b>{bg._e(row.get('symbol'))}</b></td>"
            f"<td>{bg._e(row.get('asset_class'))}</td>"
            f"<td>{bg._e(row.get('market'))}</td>"
            f"<td>{bg._e(row.get('currency'))}</td>"
            f"<td>{bg._e(row.get('settlement'))}</td>"
            f"<td>{bg._e(row.get('last'))}</td>"
            f"<td>{bg._e(row.get('bid'))} × {bg._e(row.get('bid_size'))}</td>"
            f"<td>{bg._e(row.get('ask'))} × {bg._e(row.get('ask_size'))}</td>"
            f"<td>{bg._local_time(row.get('book_at') or row.get('observed_at'))}</td>"
            f"<td>{bg._status(state)} {bg._e(label)}</td>"
            "</tr>"
        )
    tr = "".join(body) or "<tr><td colspan='10'>Todavía no hay snapshots de mercado.</td></tr>"
    return (
        "<div class='paper-card' id='spot-paper-mercado-actual'>"
        "<h2>Spot PAPER — mercado actual</h2>"
        "<p class='paper-muted'>Última fotografía recibida por identidad completa. Esta tabla sí se actualiza con mercado; no implica una operación PAPER.</p>"
        "<table class='paper-table'><tr>"
        "<th>Ticker</th><th>Familia</th><th>Mercado</th><th>Moneda</th><th>Plazo</th>"
        "<th>Último</th><th>Bid × cant.</th><th>Ask × cant.</th><th>Libro</th><th>Frescura</th>"
        f"</tr>{tr}</table></div>"
    )


def _universe_page() -> str:
    html = _original["universe_page"]()
    current = _current_market_panel()
    marker = "<div class='paper-card'><h2>Observaciones de mercado sin identidad AVAILABLE actual</h2>"
    if marker in html:
        html = html.replace(marker, current + marker, 1)
    else:
        html = html.replace("</main>", current + "</main>", 1)
    html = html.replace(
        "<h2>Observaciones de mercado sin identidad AVAILABLE actual</h2>",
        "<h2>Observaciones no conciliadas con el catálogo actual — diagnóstico histórico</h2>",
        1,
    )
    html = html.replace(
        "<h2>Todas las operaciones spot PAPER</h2>",
        "<h2>Operaciones spot PAPER — ledger de fills</h2>"
        "<p class='paper-muted'>Esta tabla cambia sólo cuando existe una apertura/cierre simulado. Para cotizaciones actuales usar “Spot PAPER — mercado actual”.</p>",
        1,
    )
    return html


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True

    _original.update({
        "daily_results_panel": getattr(bg, "_daily_results_panel", None),
        "daily_risk_panel": getattr(bg, "_daily_risk_panel", None),
        "exit_supervision_panel": getattr(bg, "_exit_supervision_panel", None),
        "rejection_funnel": getattr(bg, "_rejection_funnel", None),
        "universe_execution_panel": getattr(bg, "_universe_execution_panel", None),
        "balances_panel": getattr(bg, "_balances_panel", None),
        "universe_page": universe._page,
    })

    # Panel: exactly five normal market sessions; weekends/holidays disappear.
    bg._daily_results_panel = _daily_results_panel

    # Motor-trading: presentation declutter requested by operator. Data remains.
    bg._daily_risk_panel = _hidden_panel
    bg._exit_supervision_panel = _hidden_panel
    bg._rejection_funnel = _hidden_panel
    bg._universe_execution_panel = _hidden_panel
    bg._balances_panel = _hidden_panel

    # Universo operativo: explicit current-market view + truthful ledger wording.
    universe._page = _universe_page

    # Sistema: top horizontal sticky sub-navigation on tablet and desktop.
    if "porota-rc6-system-horizontal-nav" not in bg.THEME:
        bg.THEME += SYSTEM_TOP_NAV_CSS


def assert_operator_ux_invariants() -> None:
    # 05-Sep is Saturday; 06-Sep Sunday; 17-Aug is a BYMA holiday.
    sample = [
        {"day":"2026-09-07"}, {"day":"2026-09-06"}, {"day":"2026-09-05"},
        {"day":"2026-09-04"}, {"day":"2026-08-17"}, {"day":"2026-09-03"},
        {"day":"2026-09-02"}, {"day":"2026-09-01"}, {"day":"2026-08-31"},
    ]
    selected = [x["day"] for x in filter_operational_days(sample, 5)]
    if selected != ["2026-09-07","2026-09-04","2026-09-03","2026-09-02","2026-09-01"]:
        raise AssertionError(f"operational-day filter changed: {selected}")
    if "flex-direction:row!important" not in SYSTEM_TOP_NAV_CSS or "position:sticky!important" not in SYSTEM_TOP_NAV_CSS:
        raise AssertionError("system navigation must remain horizontal and sticky")

    # Structural capability check: comments/docstrings must not trip the guard,
    # and the module must not import network/broker clients.
    tree = ast.parse(inspect.getsource(__import__(__name__)))
    banned_roots = {"requests", "urllib", "ak_iol_client", "c_ppi_client", "bd_ppi_readonly_guard"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split('.')[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    if imported & banned_roots:
        raise AssertionError(f"dashboard UX module gained forbidden external capability: {sorted(imported & banned_roots)}")
