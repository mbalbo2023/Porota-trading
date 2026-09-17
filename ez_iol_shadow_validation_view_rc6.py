"""Cache-only IOL SHADOW quality and counterfactual fragment for RC6 /validacion.

Presentation only. It reads the latest isolated IOL cache through
observation.collect and cannot invoke IOL, change PPI data, READY/HOLD, signals,
sizing, or any order path. Unknown or incomplete cache fields remain explicit.
"""
from __future__ import annotations

from html import escape
from typing import Any

import bg_paper_dashboard as bg
import iol_shadow_observation_rc6 as observation

MAX_ROWS = 20
UNKNOWN = "UNKNOWN"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _number(value: Any, suffix: str = "") -> str:
    try:
        return f"{float(value):,.2f}{suffix}"
    except (TypeError, ValueError):
        return "—"


def _text(value: Any, fallback: str = "—") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _card_state(value: Any) -> str:
    state = _text(value, UNKNOWN).upper()
    if state in {"READY", "ALIGNED", "VERIFIED", "COMPLETE", "GOOD"}:
        return "green"
    if state in {"DEGRADED", "DIVERGENCE", "STALE", "IN_PROGRESS", INSUFFICIENT_EVIDENCE}:
        return "yellow"
    return "gray"


def _metric(data: dict[str, Any], name: str) -> Any:
    telemetry = data.get("telemetry") if isinstance(data.get("telemetry"), dict) else {}
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    return telemetry.get(name, metrics.get(name))


def _progress(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("progress") if isinstance(data.get("progress"), dict) else {}
    scheduled = value.get("scheduled", value.get("target"))
    completed = value.get("completed")
    if scheduled is None or completed is None:
        return {"state": UNKNOWN, "label": "Sin plan de ingesta publicado"}
    try:
        scheduled_n, completed_n = int(scheduled), int(completed)
    except (TypeError, ValueError):
        return {"state": INSUFFICIENT_EVIDENCE, "label": "Progreso no verificable"}
    if scheduled_n <= 0:
        return {"state": INSUFFICIENT_EVIDENCE, "label": "Universo objetivo inválido"}
    pct = min(100, max(0, round(completed_n * 100 / scheduled_n)))
    return {"state": "COMPLETE" if completed_n >= scheduled_n else "IN_PROGRESS",
            "label": f"{completed_n}/{scheduled_n} instrumentos · {pct}%"}


def _row_quality(row: dict[str, Any]) -> tuple[str, str]:
    freshness = _text(row.get("freshness_state") or row.get("freshness"), UNKNOWN).upper()
    quality = _text(row.get("data_quality") or row.get("quality"), UNKNOWN).upper()
    return freshness, quality


def _counterfactual(row: dict[str, Any]) -> tuple[str, str]:
    value = row.get("counterfactual") if isinstance(row.get("counterfactual"), dict) else {}
    evidence = _text(value.get("evidence_state"), INSUFFICIENT_EVIDENCE).upper()
    candidate = _text(value.get("candidate_id"), "")
    original = _text(value.get("original_paper_decision"), "")
    outcome = _text(value.get("shadow_outcome"), "")
    rationale = _text(value.get("rationale"), "")
    if evidence != "VERIFIED" or not candidate or not original or not outcome:
        return (INSUFFICIENT_EVIDENCE,
                "Sin candidato, decisión PAPER original y evidencia temporal verificable; no se infiere un resultado.")
    return (outcome, f"Candidato {candidate}; PAPER original: {original}. {_text(rationale, "Sin detalle adicional.")}")


def _summary(rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    aligned = sum(_text(row.get("primary_comparison"), "").upper() == "BACKGROUND_ALIGNED" for row in rows)
    divergent = sum(_text(row.get("primary_comparison"), "").upper() == "BACKGROUND_DIVERGENCE" for row in rows)
    unavailable = sum(_text(row.get("state"), UNKNOWN).upper() != "READY" for row in rows)
    return aligned, divergent, unavailable


def render() -> str:
    """Render bounded cache evidence only; no network, refresh, or decision calls."""
    data = observation.collect()
    rows = [row for row in (data.get("symbols") or []) if isinstance(row, dict)]
    state = _text(data.get("state"), UNKNOWN).upper()
    aligned, divergent, unavailable = _summary(rows)
    progress = _progress(data)
    call_count = _metric(data, "calls_total")
    rate_limited = _metric(data, "calls_429")
    errors = _metric(data, "errors_total")
    cache_hits = _metric(data, "cache_hits")
    cache_label = (
        f"llamadas: {_number(call_count)} · 429: {_number(rate_limited)} · "
        f"errores: {_number(errors)} · cache hits: {_number(cache_hits)}"
        if any(value is not None for value in (call_count, rate_limited, errors, cache_hits))
        else "Métricas MCP aún no publicadas por el collector."
    )
    cache_note = (
        "No hay cache IOL válida. La ausencia de IOL no bloquea ni degrada PAPER."
        if state in {"UNAVAILABLE", UNKNOWN} else
        "Lectura local cacheada: esta página no consulta IOL ni PPI."
    )
    cards = "".join((
        bg._card("Estado IOL", state, cache_note, _card_state(state)),
        bg._card("Progreso de ingesta", progress["label"],
                 "Plan publicado por collector; nunca inicia una consulta desde esta vista.", _card_state(progress["state"])),
        bg._card("Calidad MCP", cache_label,
                 "Las métricas se muestran sólo si fueron registradas en el cache aislado.", "gray"),
        bg._card("Reconciliación PPI/IOL", f"{aligned} alineados · {divergent} divergentes",
                 "Divergencia es diagnóstico background, nunca cambia HOLD/READY.", "yellow" if divergent else "green"),
        bg._card("No disponibles", unavailable,
                 "Faltantes y errores se mantienen visibles; no se completan con supuestos.", "gray"),
    ))
    rendered_rows = []
    for row in rows[:MAX_ROWS]:
        quote = row.get("quote") if isinstance(row.get("quote"), dict) else {}
        freshness, quality = _row_quality(row)
        comparison = _text(row.get("primary_comparison"), UNKNOWN)
        difference = row.get("difference_pct", row.get("ppi_iol_difference_pct"))
        what_if, what_if_detail = _counterfactual(row)
        rendered_rows.append(
            "<tr>"
            f"<td><b>{escape(_text(row.get('symbol')))}</b></td>"
            f"<td>{escape(_text(row.get('market')))}</td>"
            f"<td>{escape(_text(row.get('state'), UNKNOWN))}</td>"
            f"<td>{_number(quote.get('last'))}</td>"
            f"<td>{escape(freshness)}</td>"
            f"<td>{escape(quality)}</td>"
            f"<td>{escape(comparison)} · {_number(difference, '%')}</td>"
            f"<td>{escape(what_if)}<br><span class='paper-muted'>{escape(what_if_detail)}</span></td>"
            "</tr>"
        )
    rows_html = "".join(rendered_rows) or (
        "<tr><td colspan='8' class='paper-muted'>Aún no hay evidencia IOL cacheada. "
        "Se mostrará progreso, calidad y reconciliación cuando el collector publique datos.</td></tr>"
    )
    return (
        "<section class='paper-card'>"
        "<h2>IOL — calidad, reconciliación y contrafactual SHADOW</h2>"
        "<div class='paper-warning'><b>Observación únicamente.</b> PPI conserva la autoridad primaria. "
        "Esta evidencia no puede cambiar READY/HOLD, señales, tamaños, entradas, salidas ni órdenes.</div>"
        f"<p class='paper-muted'>Fuente: {escape(_text(data.get('source'), 'IOL_MCP'))} · "
        f"modo: {escape(_text(data.get('mode'), 'SHADOW'))} · "
        f"efecto: {escape(_text(data.get('decision_effect'), 'OBSERVE_ONLY'))} · "
        f"última cache: {escape(_text(data.get('refreshed_at'), 'pendiente'))}</p>"
        f"<div class='paper-grid'>{cards}</div>"
        "<div class='paper-table-wrap'><table><thead><tr>"
        "<th>Especie</th><th>Mercado</th><th>Estado</th><th>Último IOL</th>"
        "<th>Freshness</th><th>Calidad</th><th>PPI/IOL</th><th>Qué habría pasado</th>"
        "</tr></thead><tbody>" + rows_html + "</tbody></table></div>"
        "<p class='paper-muted'>“Qué habría pasado” sólo se muestra como VERIFIED cuando existe el candidato, "
        "la decisión PAPER original y evidencia temporal comparable. En cualquier otro caso figura "
        "INSUFFICIENT_EVIDENCE. Es explicación posterior; jamás reescribe la decisión histórica ni ejecuta acciones.</p>"
        "</section>"
    )
