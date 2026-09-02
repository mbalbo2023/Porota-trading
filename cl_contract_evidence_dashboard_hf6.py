"""Adds contract-source visibility to the existing HF6 Universo operativo page."""
from __future__ import annotations

import json

import bg_paper_dashboard as bg
import bh_universe_dashboard_hf6 as universe


_installed = False


def _status_badge(status):
    value = str(status or "UNKNOWN")
    if value.startswith("VERIFIED"):
        return bg._status("OK")
    if value.startswith("PPI_") or "SUPPORT" in value:
        return bg._status("AMARILLO")
    if value.startswith("POROTA_") or "INTEGRATION" in value:
        return bg._status("PENDIENTE")
    return bg._status("AMARILLO")


def _time_answer(status):
    value = str(status or "")
    if value in {
        "PPI_SEARCH_HTTP200_EMPTY_SUPPORT_REQUIRED",
        "PPI_FIELD_PRESENT_SEMANTICS_UNDOCUMENTED",
        "PPI_CONTRACT_FIELDS_NOT_RETURNED",
        "PPI_DOCUMENTED_ESTIMATE_COLLECTED_SEMANTICS_PENDING",
    }:
        return "NO — esperar más ingesta no completa el dato"
    if value == "POROTA_DISCOVERY_NOT_IMPLEMENTED":
        return "NO — falta implementar la consulta en Porota"
    if value in {"COLLECTION_INCOMPLETE", "DISCOVERY_FILTER_EMPTY_REVIEW_REQUIRED"}:
        return "REVISAR — hay que corregir o ampliar la recolección"
    if value.startswith("VERIFIED"):
        return "NO APLICA — contrato ya verificado para PAPER"
    return "NO DETERMINADO"


def _contract_section():
    if not bg._table("contract_evidence"):
        return (
            "<div class='paper-card'><h2>Evidencia contractual</h2>"
            "<p class='paper-muted'>El colector contractual aún no ejecutó su primera corrida.</p></div>"
        )

    groups = bg._rows("""
        SELECT instrument_type,status,owner,source,missing_fields_json,detail,
               COUNT(*) instruments,MAX(checked_at) checked_at
        FROM contract_evidence
        GROUP BY instrument_type,status,owner,source,missing_fields_json,detail
        ORDER BY instrument_type,status,owner
    """)

    latest = (
        bg._rows("""
            SELECT total,verified,blocked_porota,blocked_provider,finished_at
            FROM contract_evidence_runs
            ORDER BY finished_at DESC LIMIT 1
        """)
        if bg._table("contract_evidence_runs") else []
    )
    run = latest[0] if latest else {}

    rows = []
    caucion_state = "SIN_EVIDENCIA"
    caucion_detail = "Todavía no existe evaluación contractual de cauciones."

    for row in groups:
        family = row.get("instrument_type")
        status = row.get("status")
        owner = row.get("owner")
        try:
            missing = json.loads(row.get("missing_fields_json") or "[]")
        except Exception:
            missing = ["INVALID_MISSING_FIELDS_JSON"]
        missing_text = ", ".join(map(str, missing)) or "ninguno"
        if family == "CAUCIONES":
            caucion_state = str(status)
            caucion_detail = str(row.get("detail") or "")
        rows.append(
            "<tr>"
            f"<td><b>{bg._e(family)}</b></td>"
            f"<td>{int(row.get('instruments') or 0)}</td>"
            f"<td>{_status_badge(status)}<br>{bg._e(status)}</td>"
            f"<td>{bg._e(owner)}</td>"
            f"<td>{bg._e(row.get('source'))}</td>"
            f"<td>{bg._e(missing_text)}</td>"
            f"<td>{bg._e(_time_answer(status))}</td>"
            f"<td>{bg._e(row.get('detail'))}</td>"
            f"<td>{bg._local_time(row.get('checked_at'))}</td>"
            "</tr>"
        )

    cash_sweep_status = (
        "PREPARADO / HOLD"
        if caucion_state != "VERIFIED_PROVIDER_CONTRACT"
        else "EVIDENCIA CONTRACTUAL DISPONIBLE — requiere gate PAPER final"
    )
    cash_sweep_reason = (
        "La política de tesorería PAPER existe, pero no colocará cauciones hasta que "
        "PPI provea identidad, tasa, plazo, lado colocador, profundidad/capital, mínimo, "
        "step, base de días, vencimiento y costos explícitos. No se inmoviliza caja con datos inferidos."
        if caucion_state != "VERIFIED_PROVIDER_CONTRACT"
        else "La fuente contractual está disponible; la asignación sigue su política de caja libre y reservas."
    )

    cards = "".join((
        bg._card("Evidencias", run.get("total", 0), "Registros/familias evaluados", "green" if run else "yellow"),
        bg._card("Verificados", run.get("verified", 0), "Contrato ya habilitado PAPER", "green"),
        bg._card("Pendiente Porota", run.get("blocked_porota", 0), "Recolección o adaptador nuestro", "yellow"),
        bg._card("Pendiente proveedor", run.get("blocked_provider", 0), "Dato/semántica requiere PPI o fuente oficial", "yellow"),
        bg._card("Cash sweep cauciones", cash_sweep_status, "Sólo saldo liquidado libre; nunca tomadora", "yellow"),
    ))

    return (
        "<div class='paper-card'>"
        "<h2>Evidencia contractual — quién debe resolver cada bloqueo</h2>"
        "<p class='paper-muted'>Esta sección responde expresamente si falta tiempo de ingesta, "
        "una recolección de Porota o información/documentación del proveedor. Ningún valor se infiere "
        "del ticker ni del nombre de un campo.</p>"
        f"<div class='paper-grid'>{cards}</div>"
        "<table class='paper-table'><tr>"
        "<th>Familia</th><th>Instrumentos</th><th>Estado</th><th>Responsable</th>"
        "<th>Fuente consultada</th><th>Campos faltantes</th><th>¿Más tiempo?</th>"
        "<th>Diagnóstico</th><th>Última comprobación</th>"
        "</tr>" + "".join(rows) + "</table>"
        "</div>"
        "<div class='paper-card'>"
        "<h2>Política de caja al cierre — caución colocadora PAPER</h2>"
        f"<p><b>Estado:</b> {bg._e(cash_sweep_status)}</p>"
        f"<p>{bg._e(cash_sweep_reason)}</p>"
        f"<p class='paper-muted'>Diagnóstico proveedor actual: {bg._e(caucion_detail)}</p>"
        "</div>"
    )


def install():
    global _installed
    if _installed:
        return
    _installed = True
    original = universe._page
    if getattr(original, "_porota_contract_evidence_wrapped", False):
        return

    def wrapped_page():
        html = original()
        section = _contract_section()
        marker = "</body>"
        if marker in html:
            return html.replace(marker, section + marker, 1)
        return html + section

    wrapped_page._porota_contract_evidence_wrapped = True
    universe._page = wrapped_page


install()
