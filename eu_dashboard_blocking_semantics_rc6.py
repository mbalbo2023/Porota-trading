"""RC6 dashboard truth-preserving blocking semantics.

Presentation-only layer.  It does not change the persisted component state.
A component may be AMARILLO and still be non-blocking for the PAPER go-live;
that distinction belongs in the explanation, not by repainting YELLOW as GRAY.
"""
from __future__ import annotations

import bg_paper_dashboard as bg
import es_dashboard_go_live_ux_rc6 as go_live

_installed = False
_NONBLOCKING_PAPER_KEYS = {
    "PPI_PRODUCTION_HISTORY",
    "PPI_BACKGROUND_INGEST",
    "PAPER_SIGNAL_ROTATION",
    "BYMA_INSTRUMENTS_API",
    "ROFEX_MARKETDATA",
}


def _truth_with_blocking_semantics():
    # Use the renderer source captured before the go-live overlay changed it.
    # This preserves VERDE/AMARILLO/ROJO/PENDIENTE exactly as calculated by
    # the canonical health layer.
    rows = go_live._original["health_components"]()
    result = []
    for item in rows:
        row = dict(item)
        key = str(row.get("key") or "").upper()
        state = str(row.get("state") or "").upper()
        if key in _NONBLOCKING_PAPER_KEYS and state not in {"ROJO", "ERROR", "FAILED"}:
            row["paper_blocking"] = False
            row["detail"] = (
                "No bloquea por sí solo el Go Live PAPER; conservar estado real: "
                + str(row.get("detail") or "")
            )
        else:
            row["paper_blocking"] = True
        result.append(row)
    return result


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    bg._health_components = _truth_with_blocking_semantics
