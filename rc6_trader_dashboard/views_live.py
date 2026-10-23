"""The live wheel: observation, factual decisions, exits and coverage."""
from .components import metric, funnel, timeline, notice, fields, definition_list, e
from .datasets import shadow_rows
from .views_home import preview
from .view_common import (render_table, OPPORTUNITIES, OPPORTUNITY_DETAIL, POSITIONS, POSITION_DETAIL,
                          CLOSES, CLOSE_DETAIL, DECISIONS, DECISION_DETAIL, CAPACITY, CAPACITY_DETAIL,
                          WORKERS, WORKER_DETAIL, SOURCES, SOURCE_DETAIL, committed_funnel)


def render(p, destination, tab):
    if tab == "resumen":
        events = shadow_rows(p, "events")
        counts = p.counts()
        cards = "".join(metric(label, counts.get(key), source, kind="number") for key, label, source in (
            ("open_positions", "Operaciones abiertas", "paper_positions · OPEN"),
            ("closed_today", "Cerradas hoy", "Cierres factuales en ART"), ("HOT", "HOT en corte fresco", "Observación SHADOW; HOT ≠ BUY"),
            ("SIGNAL", "Señales candidatas", "Cut SHADOW comprometido")))
        summary = p.performance(today=True)
        cards += "".join(metric("PnL neto hoy · " + r["currency"], r.get("net_pnl"), r["strategy"], kind="money", currency=r["currency"], result=True) for r in summary.rows)
        workers = p.workers()
        # DailyRisk is a risk state, not a heartbeat-bearing worker.
        watched = [r for r in workers.rows if r.get("source") != "paper_daily_risk"]
        verified = sum(r.get("freshness") == "FRESH" and r.get("state") in {"RUNNING", "OK"} for r in watched)
        cards += metric("Workers críticos frescos", verified, f"De {len(watched)} · revisar cobertura", kind="number")
        content = "<div class='metric-grid'>" + cards + "</div>" + committed_funnel(p)
        content += "<div class='split-panels'>" + preview(shadow_rows(p, "opportunities"), "Oportunidades", "/en-vivo/oportunidades") + timeline(events.rows, verified_empty=events.state == "AVAILABLE") + "</div>"
        content += "<div class='split-panels'>" + preview(p.positions(limit=5), "1. Operaciones abiertas ahora", "/en-vivo/posiciones") + preview(p.positions(closed=True, today=True, limit=5), "2. Operaciones cerradas hoy", "/en-vivo/cierres") + "</div>"
        content += preview(p.decisions(), "3. Decisiones en vivo — BUY / HOLD / abstenciones", "/en-vivo/decisiones")
        content += preview(shadow_rows(p, "capacity"), "Capacidad & Coverage", "/en-vivo/capacidad", key="engine")
        content += "<a class='text-link' href='/scalping'>Abrir Scalping en Trading</a>"
        return content
    if tab == "oportunidades":
        events = shadow_rows(p, "events")
        return (render_table(p, destination, tab, shadow_rows(p, "opportunities"), "Mesa de oportunidades", OPPORTUNITIES, OPPORTUNITY_DETAIL,
                             filters=True, note="HOT ≠ BUY. DISCOVERY/WARM son observación. Tradeability no es señal direccional ni probabilidad.") + timeline(events.rows, verified_empty=events.state == "AVAILABLE"))
    if tab == "decisiones":
        return render_table(p, destination, tab, p.decisions(), "Decisiones factuales PAPER", DECISIONS, DECISION_DETAIL,
                            note="Cada decisión conserva signal, economics y risk. HOLD sin gate de compra muestra NOT_EVALUATED para economics/risk; BUY sin gate sigue NO_VERIFICADO. Un HOLD requiere su motivo; score_is_probability=false.")
    if tab == "posiciones":
        return render_table(p, destination, tab, p.positions(), "Posiciones y seguridad de salida", POSITIONS, POSITION_DETAIL, filters=True,
                            note="Un mark contable no acredita un fill ejecutable. MFE/MAE sin trayectoria validada = NO_MEDIDO.")
    if tab == "cierres":
        stats = p.close_win_rate_today()
        detail = (f"{stats['wins']}/{stats['sample']} cierres con PnL neto positivo · empates en la muestra"
                  if stats["state"] == "AVAILABLE" else
                  "Sin cierres medidos en el alcance y filtros actuales" if stats["state"] == "EMPTY" else
                  "NO_VERIFICADO · revisar PnL neto o lectura del ledger")
        summary = ("<div class='metric-grid'>" +
                   metric("Win rate de cierres hoy", stats["win_rate"], detail, kind="percent") +
                   metric("Muestra de cierres hoy", stats["sample"],
                          "Posiciones cerradas PAPER · rueda ART · mismos filtros", kind="number") + "</div>")
        return summary + render_table(p, destination, tab, p.positions(closed=True, today=True),
                                      "Cierres de la rueda · ART", CLOSES, CLOSE_DETAIL, filters=True)
    if tab == "capacidad":
        return render_table(p, destination, tab, shadow_rows(p, "capacity"), "Capacidad & Coverage", CAPACITY, CAPACITY_DETAIL,
                            note="La capacidad real exige evidencia OPEN. Un benchmark fuera de rueda no certifica capacidad operativa.")
    if tab == "workers":
        return render_table(p, destination, tab, p.workers(), "Workers críticos de la rueda", WORKERS, WORKER_DETAIL)
    cohort=p.market_freshness_cohort()
    panel="<section class='panel'><h2>Relojes por identidad · muestra local</h2>"+notice(
        'Hasta 10 identidades de los últimos 500 registros locales; posiciones abiertas primero. Un heartbeat global fresco no acredita book/trade fresco. Ausencia = NO_VERIFICADO; diagnóstico de 120s sin autoridad de entrada. '+cohort.reason)
    for row in cohort.rows:
        panel+="<details><summary>"+e(' / '.join(str(row.get(k) or 'NO_VERIFICADO') for k in ('symbol','family','market','currency','settlement')))+"</summary>"+definition_list(row,fields('book_freshness|Book|status;trade_freshness|Trade|status;receipt_freshness|Recepción|status;book_at|Book|time;trade_at|Trade|time;received_at|Recepción|time;source|Fuente'))+"</details>"
    panel+="</section>"
    return panel+render_table(p, destination, tab, p.sources(), "Fuentes relevantes a la rueda", SOURCES, SOURCE_DETAIL,
                        note="PPI es autoridad primaria. IOL complementa; BYMA público observa. SOURCE_UNAVAILABLE siempre se limita a su scope.",
                        identity=False)

