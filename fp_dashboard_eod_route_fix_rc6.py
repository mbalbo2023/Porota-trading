"""RC6 P0 dashboard route repair for Trading -> Estrategias.

The EOD/Overnight evaluator is already part of the PAPER dashboard, but the
Wave8 presentation layer replaces ``bg.trading_page`` later in the import
chain.  This final, read-only overlay is installed *after* Wave8 and restores
the evaluator to the page that is actually served to the operator.

Presentation only: no database writes, network calls, policy changes or order
execution are introduced here.
"""
from __future__ import annotations

import bg_paper_dashboard as bg
import fo_eod_overnight_dashboard_rc6 as eod_dashboard

_installed = False
_MARKER = "id='rc6-eod-overnight-final-route'"


def _append_before_main_end(page: str, fragment: str) -> str:
    marker = "</main>"
    return page.replace(marker, fragment + marker, 1) if marker in page else page + fragment


def install() -> None:
    """Decorate the final Trading route after Wave8 has installed its wrapper."""
    global _installed
    if _installed:
        return
    _installed = True

    old_trading = bg.trading_page

    def trading_page_with_eod(section: str = "") -> str:
        page = old_trading(section)
        if str(section or "").strip().lower() != "estrategias":
            return page
        if _MARKER in page:
            return page
        fragment = (
            "<section id='rc6-eod-overnight-final-route' "
            "data-authority='SHADOW_READ_ONLY'>"
            + eod_dashboard.render()
            + "</section>"
        )
        return _append_before_main_end(page, fragment)

    bg.trading_page = trading_page_with_eod
