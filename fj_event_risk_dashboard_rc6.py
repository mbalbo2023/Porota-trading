"""RC6 standalone renderer for the future `Eventos / Riesgo Global` dashboard menu.

Pure presentation layer: no DB, network, broker, order or strategy imports.
It accepts already-normalized SHADOW event records and renders an accessible
semantic table. It never emits BUY/SELL recommendations and cannot block PAPER.
"""
from __future__ import annotations
from html import escape
from datetime import datetime, timezone

ALLOWED_RISK={"LOW","MODERATE","HIGH","EXTREME","UNKNOWN"}
ALLOWED_BIAS={"POSITIVE_BIAS","NEGATIVE_BIAS","MIXED","UNKNOWN"}
ALLOWED_CONFIRMATION={"RUMOR","MULTI_SOURCE","OFFICIAL_CONFIRMED","UNKNOWN"}


def _txt(value, default="—"):
    if value is None or value=="": return default
    return escape(str(value), quote=True)


def _join(values):
    if not values: return "—"
    return ", ".join(_txt(v) for v in values)


def normalize_row(row:dict)->dict:
    risk=str(row.get("risk") or "UNKNOWN").upper()
    bias=str(row.get("bias") or "UNKNOWN").upper()
    confirmation=str(row.get("confirmation") or "UNKNOWN").upper()
    return {
        "event_id":str(row.get("event_id") or "UNKNOWN"),
        "event_type":str(row.get("event_type") or "UNKNOWN"),
        "region":str(row.get("region") or "GLOBAL"),
        "confirmation":confirmation if confirmation in ALLOWED_CONFIRMATION else "UNKNOWN",
        "summary":str(row.get("summary") or "Sin resumen factual disponible."),
        "exposures":tuple(row.get("exposures") or ()),
        "identities":tuple(row.get("identities") or ()),
        "bias":bias if bias in ALLOWED_BIAS else "UNKNOWN",
        "confidence":row.get("confidence"),
        "freshness":str(row.get("freshness") or "UNKNOWN"),
        "source":str(row.get("source") or "UNKNOWN"),
        "source_tier":str(row.get("source_tier") or "UNKNOWN"),
        "available_to_engine_at":str(row.get("available_to_engine_at") or "UNKNOWN"),
        "provenance_url":str(row.get("provenance_url") or ""),
        "observed_return":row.get("observed_return"),
        "analog_result":row.get("analog_result"),
        "risk":risk if risk in ALLOWED_RISK else "UNKNOWN",
    }


def render_event_risk_page(rows, *, global_risk="UNKNOWN", updated_at=None)->str:
    risk=str(global_risk or "UNKNOWN").upper()
    if risk not in ALLOWED_RISK: risk="UNKNOWN"
    updated_at=updated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    normalized=[normalize_row(dict(r)) for r in rows]
    body=[]
    for r in normalized:
        provenance=(f"<a href='{_txt(r['provenance_url'])}' rel='noopener noreferrer'>Fuente</a>"
                    if r['provenance_url'].startswith(('https://','http://')) else "—")
        observed="—" if r['observed_return'] is None else _txt(r['observed_return'])
        analog="—" if r['analog_result'] is None else _txt(r['analog_result'])
        body.append("<tr>"
          f"<td>{_txt(r['event_type'])}</td>"
          f"<td>{_txt(r['region'])}</td>"
          f"<td>{_txt(r['confirmation'])}</td>"
          f"<td>{_txt(r['summary'])}</td>"
          f"<td>{_join(r['exposures'])}</td>"
          f"<td>{_join(r['identities'])}</td>"
          f"<td>{_txt(r['bias'])}</td>"
          f"<td>{_txt(r['confidence'])}</td>"
          f"<td>{_txt(r['freshness'])}</td>"
          f"<td>{_txt(r['source'])}<br><small>{_txt(r['source_tier'])}</small></td>"
          f"<td>{_txt(r['available_to_engine_at'])}</td>"
          f"<td>{observed}</td><td>{analog}</td><td>{provenance}</td>"
          "</tr>")
    if not body:
        body.append("<tr><td colspan='14'>Sin eventos SHADOW disponibles para el corte seleccionado.</td></tr>")
    return f"""<section id='eventos-riesgo-global' aria-labelledby='event-risk-title'>
<h1 id='event-risk-title'>Eventos / Riesgo Global</h1>
<p><strong>Modo:</strong> SHADOW · <strong>Puede bloquear PAPER:</strong> NO · <strong>Puede enviar órdenes:</strong> NO · <strong>Auto-promoción:</strong> NO</p>
<p><strong>GLOBAL_EVENT_RISK:</strong> {_txt(risk)} · <strong>Última actualización:</strong> {_txt(updated_at)}</p>
<p>Esta vista presenta contexto y evidencia. No contiene recomendaciones BUY/SELL.</p>
<table class='paper-table' aria-describedby='event-risk-note'>
<caption>Eventos globales observados por el motor SHADOW</caption>
<thead><tr><th scope='col'>Evento</th><th scope='col'>Región</th><th scope='col'>Confirmación</th><th scope='col'>Resumen factual</th><th scope='col'>Exposiciones</th><th scope='col'>Identidades POROTA</th><th scope='col'>Sesgo no operativo</th><th scope='col'>Confianza</th><th scope='col'>Freshness</th><th scope='col'>Fuente / tier</th><th scope='col'>Disponible al motor</th><th scope='col'>Retorno observado</th><th scope='col'>Análogo histórico</th><th scope='col'>Provenance</th></tr></thead>
<tbody>{''.join(body)}</tbody>
</table>
<p id='event-risk-note'>Las confirmaciones o retractaciones posteriores no se retrotraen en replay: se respeta available_to_engine_at.</p>
</section>"""


def assert_shadow_only():
    src=render_event_risk_page([],global_risk='LOW',updated_at='2026-09-07T16:00:00-03:00')
    assert 'Puede bloquear PAPER:</strong> NO' in src
    assert 'Puede enviar órdenes:</strong> NO' in src
    assert 'No contiene recomendaciones BUY/SELL' in src
