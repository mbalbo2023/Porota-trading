"""RC6 P0 dashboard route repair for Trading -> Estrategias.

Wave8 replaces ``bg.trading_page`` with a family/instrument strategy overview.
That content belongs to the instrument/family views, not to Trading ->
Estrategias.  The operator-facing Estrategias route must show the EOD/Overnight
strategy evaluator directly, without the redundant cards for Acciones, Renta
fija, Cauciones, Opciones, Futuros, FCI or Licitaciones.

Presentation only: no database writes, network calls, policy changes or order
execution are introduced here. CURRENT_EOD remains authoritative and the
Overnight evaluator remains SHADOW/read-only.
"""
from __future__ import annotations

import bg_paper_dashboard as bg
import fo_eod_overnight_dashboard_rc6 as eod_dashboard

_installed = False
_MARKER = "id='rc6-eod-overnight-final-route'"


def _strategies_eod_page() -> str:
    body = (
        "<h1>Trading — Estrategias</h1>"
        "<p class='paper-muted'>Estrategias de posición y evaluación de permanencia. "
        "Las familias de instrumentos se consultan en sus vistas específicas; "
        "Scalping permanece exclusivamente en su menú principal.</p>"
        "<section id='rc6-eod-overnight-final-route' data-authority='SHADOW_READ_ONLY'>"
        + eod_dashboard.render()
        + "</section>"
    )
    return bg._document("Trading — Estrategias", body)


def install() -> None:
    """Replace only the final served Estrategias route after Wave8 wiring."""
    global _installed
    if _installed:
        return
    _installed = True

    old_trading = bg.trading_page

    def trading_page_with_eod(section: str = "") -> str:
        if str(section or "").strip().lower() == "estrategias":
            return _strategies_eod_page()
        return old_trading(section)

    bg.trading_page = trading_page_with_eod
