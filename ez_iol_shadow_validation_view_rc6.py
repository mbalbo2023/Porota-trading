"""Compact IOL SHADOW evidence fragment for RC6 /validacion.

The fragment reads the local observation cache only. IOL has no synchronous
decision path and its findings cannot change PPI's authority, READY/HOLD, or
order handling.
"""
from __future__ import annotations

import bg_paper_dashboard as bg
import iol_shadow_observation_rc6 as observation

MAX_ROWS = 20


def _display_number(value, suffix=""):
    try:
        return f"{float(value):,.2f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _state_card_state(value):
    value = str(value or "").upper()
    return {"READY": "green", "INSUFFICIENT_DATA": "yellow",
            "UNAVAILABLE": "gray"}.get(value, "gray")


def _summary(rows):
    aligned = sum(row.get("primary_comparison") == "BACKGROUND_ALIGNED" for row in rows)
    divergent = sum(row.get("primary_comparison") == "BACKGROUND_DIVERGENCE" for row in rows)
    unavailable = sum(row.get("state") != "READY" for row in rows)
    return aligned, divergent, unavailable


def render() -> str:
    """Render bounded cache evidence; never calls IOL or the decision engine."""
    data = observation.collect()
    rows = data.get("symbols") or []
    aligned, divergent, unavailable = _summary(rows)
    state = str(data.get("state") or "UNAVAILABLE").upper()
    cache_note = (
        "Collector pendiente: no hay observación IOL válida todavía. "
        "La ausencia de cache no bloquea ni degrada PAPER."
        if state == "UNAVAILABLE" else
        "La evidencia se lee desde cache local. Esta carga no llama IOL ni consulta PPI."
    )
    cards = "".join((
        bg._card("Estado IOL", state, cache_note, _state_card_state(state)),
        bg._card("Instrumentos observados", len(rows),
                 "Máximo 20 filas visibles; sin efecto sobre elegibilidad", "gray"),
        bg._card("Comparación PPI/IOL", f"{aligned} alineados · {divergent} divergentes",
                 "Divergencia = diagnóstico background, nunca HOLD/READY", "yellow" if divergent else "green"),
        bg._card("No disponibles", unavailable,
                 "Errores o faltantes del collector; no cambian el motor", "gray"),
    ))
    table_rows = []
    for row in rows[:MAX_ROWS]:
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
        comparison = row.get("primary_comparison") or "SIN_COMPARACIÓN"
        reason = row.get("reason") or "—"
        table_rows.append(
            "<tr>"
            f"<td><b>{bg._e(row.get('symbol') or '—')}</b></td>"
            f"<td>{bg._e(row.get('market') or '—')}</td>"
            f"<td>{bg._e(row.get('state') or '—')}</td>"
            f"<td>{_display_number(quote.get('last'))}</td>"
            f"<td>{_display_number(quote.get('spread_pct'), '%')}</td>"
            f"<td>{bg._e(comparison)}</td>"
            f"<td class='paper-muted'>{bg._e(reason)}</td>"
            "</tr>"
        )
    rows_html = "".join(table_rows) or (
        "<tr><td colspan='7' class='paper-muted'>Aún no hay instrumentos IOL observados. "
        "Cuando exista collector, se mostrará sólo evidencia cacheada.</td></tr>"
    )
    return (
        "<section class='paper-card'>"
        "<h2>IOL — evidencia SHADOW</h2>"
        "<div class='paper-warning'><b>Observación únicamente.</b> PPI conserva la autoridad primaria. "
        "IOL no puede cambiar READY/HOLD, tamaños, entradas, salidas ni órdenes; tampoco habilita dinero real.</div>"
        f"<p class='paper-muted'>Fuente: {bg._e(data.get('source') or 'IOL_MCP')} · "
        f"modo: {bg._e(data.get('mode') or 'SHADOW')} · efecto: {bg._e(data.get('decision_effect') or 'OBSERVE_ONLY')} · "
        f"última cache: {bg._e(data.get('refreshed_at') or 'pendiente')}</p>"
        f"<div class='paper-grid'>{cards}</div>"
        "<div class='paper-table-wrap'><table><thead><tr>"
        "<th>Especie</th><th>Mercado</th><th>Estado</th><th>Último IOL</th>"
        "<th>Spread</th><th>Comparación primaria</th><th>Detalle</th>"
        "</tr></thead><tbody>" + rows_html + "</tbody></table></div>"
        "<p class='paper-muted'>Dependencia pendiente: collector OAuth read-only, universo acotado, "
        "rate-limit, reintentos, checkpoint e idempotencia. No se ejecutan desde esta vista.</p>"
        "</section>"
    )
