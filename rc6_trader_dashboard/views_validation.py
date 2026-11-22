"""Operator view of the daily campaign and its published PAPER evidence."""
from em_validation_campaign_rc6 import MILESTONES
import ez_iol_shadow_validation_view_rc6 as iol
import rc6_validation_projection as projection

from .components import badge, clock, detail, e, fields, metric, notice, table
from .projection import Page
from .views_analytics import _action4_card


CLAIMS = {
    "VERIFIED_CURRENT": "Verificado al corte",
    "VERIFIED_HISTORICAL": "Verificado histórico",
    "STALE": "Evidencia vencida",
    "PENDING": "Pendiente",
    "BLOCKED_BY_POLICY": "Bloqueado por política",
}
STATUS = {"GREEN": "VERDE", "YELLOW": "AMARILLO", "RED": "ROJO", "GRAY": "GRIS"}
DETAIL = fields(
    "objective|Objetivo;expected_evidence|Criterios de salida;criteria_status|Chequeos por criterio;"
    "observed_evidence|Evidencia observada;deviation|Brecha;blocker|Bloqueante;next_action|Próxima acción;"
    "implementation_status|Implementación;work_status|Trabajo;evidence_status|Estado de evidencia;"
    "evidence_ref|Referencia;evidence_at|Fecha de la evidencia|time;evaluated_at|Evaluado|time;owner|Responsable"
)


def _milestone_cards(rows):
    cards = []
    for index, milestone in enumerate(MILESTONES, start=1):
        row = rows.get(milestone.code, {})
        title = f"{milestone.code} · {milestone.name}"
        criteria = row.get("criteria_status")
        shown_criteria = (" · ".join(f"{name}: {criteria.get(name, 'NO_VERIFICADO')}"
                                     for name in milestone.exit_criteria)
                          if isinstance(criteria, dict) and criteria else None)
        facts = {**row, "objective": row.get("objective") or milestone.goal,
                 "expected_evidence": ", ".join(milestone.exit_criteria), "criteria_status": shown_criteria}
        default = "Bloqueado por política" if milestone.code == "M11" else "Pendiente"
        label = CLAIMS.get(row.get("claim_status"), default)
        pct = row.get("compliance_pct")
        score = f" · {pct} %" if type(pct) is int and 0 <= pct <= 100 else ""
        light = STATUS.get(row.get("state"))
        light_html = " " + badge(light) if light else ""
        cards.append(
            f"<article class='panel'><h3>{e(title)}</h3><p>{badge(label)}{light_html}{score}</p>"
            + detail(facts, DETAIL, f"validation-{milestone.code}", index, label=f"Detalle {title}")
            + "</article>"
        )
    return "<section aria-label='Hitos de validación'><h2>Objetivo, evidencia y brecha por hito</h2>" + "".join(cards) + "</section>"


def _published_table(snapshot, key, source, title, columns, extra=()):
    values = snapshot.get(key, [])
    rows = [row for row in values if isinstance(row, dict)][:10] if isinstance(values, list) else []
    return table(Page(source, rows, len(rows), "AVAILABLE" if key in snapshot else "NO_VERIFICADO"),
                 title, columns, extra, table_id=f"validacion-{key}", identity=False, evidence=False)


def _journeys(snapshot, *, campaign):
    url = "/sistema/validacion"
    current = " aria-current='page'"
    nav = ("<nav class='header-actions' aria-label='Informes por rueda'>"
           f"<a class='button' href='{url}'{'' if campaign else current}>Actividad PAPER</a>"
           f"<a class='button' href='{url}?channel=campaign'{current if campaign else ''}>Avance por rueda</a>"
           "</nav>")
    if campaign:
        return nav + _published_table(
            snapshot, "campaign_days", "Ledger encadenado · campaña RC6",
            "Historial de avances por rueda y versión",
            fields("session_date|Fecha de rueda;records|Hitos registrados|count;milestones_current|Hitos acreditados al corte|count;recorded_at|Registrado|time"),
            fields("candidate_sha|SHA candidato;deployment_sha|SHA deploy;evidence_ref|Referencia"))
    return nav + _published_table(
        snapshot, "activity", "Actividad PAPER · informe por rueda",
        "Actividad PAPER por jornada",
        fields("date_ar|Fecha de rueda;decisions|Decisiones|count;fills|Fills|count;opened|Aperturas|count;closed|Cierres|count"),
        fields("realized_by_currency|PnL por moneda;event_types|Eventos;cancelled_admin|Cancelaciones administrativas|count"))


def render(p):
    snapshot = projection.read(now=p.now)
    if snapshot is None:
        title = notice("Proyección de validación pendiente de publicación.")
        snapshot = {}
    else:
        release = snapshot.get("release")
        release = release if isinstance(release, dict) else {}
        summary = snapshot["summary"]
        grade = snapshot.get("snapshot_status") == "CURRENT"
        title = notice(f"Proyección {snapshot.get('session_date') or 'sin rueda informada'} · "
                       f"{clock(snapshot.get('generated_at'))} · "
                       f"{'vigente al corte' if grade else 'evidencia histórica o no vinculada'} · "
                       f"deploy {release.get('deployment_sha') or 'NO_VERIFICADO'} · "
                       f"{snapshot.get('source') or 'fuente NO_VERIFICADO'}")
        title += ("<div class='metric-grid'>"
                  + metric("Hitos completos al corte", f"{summary['milestones_green']}/{len(MILESTONES)}")
                  + "</div>")
    rule = "<div class='metric-grid'>" + metric("Hito M11", "BLOCKED", "Dinero real bloqueado por política RC6", kind="status") + "</div>"
    return (title + rule + _milestone_cards(snapshot.get("milestones", {}))
            + _action4_card(p)
            + _journeys(snapshot, campaign=p.filters.get("channel") == "campaign")
            + iol.render(terminal=True))
