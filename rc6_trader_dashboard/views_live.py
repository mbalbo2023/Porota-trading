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
        cards = "".join(metric(label, counts.get(key), source, kind="count") for key, label, source in (
            ("open_positions", "Operaciones abiertas", "paper_positions · OPEN"),
            ("closed_today", "Cerradas hoy", "Cierres factuales en ART"), ("HOT", "HOT en corte fresco", "Observación SHADOW; HOT ≠ BUY"),
            ("SIGNAL", "Señales candidatas", "Cut SHADOW comprometido")))
        summary = p.performance(today=True)
        cards += "".join(metric("PnL de cierres PAPER hoy · " + r["currency"], r.get("net_pnl"), r["strategy"], kind="money", currency=r["currency"], result=True) for r in summary.rows)
        workers = p.workers()
        # DailyRisk is a risk state, not a heartbeat-bearing worker.
        watched = [r for r in workers.rows if r.get("source") != "paper_daily_risk"]
        verified = sum(r.get("freshness") == "FRESH" and r.get("state") in {"RUNNING", "OK"} for r in watched)
        cards += metric("Workers críticos en RUNNING u OK", verified,
                        f"De {len(watched)} con heartbeat exigido · incluye sólo heartbeats vigentes", kind="count")
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
                             filters=True, note="HOT indica prioridad de observación, no BUY. Si capacidad OPEN no está demostrada, una fila HOT conserva señal NOT_READY y SCANNER_CAPACITY. Tradeability no es señal direccional ni probabilidad.") + timeline(events.rows, verified_empty=events.state == "AVAILABLE"))
    if tab == "decisiones":
        return render_table(p, destination, tab, p.decisions(), "Decisiones factuales PAPER", DECISIONS, DECISION_DETAIL,
                            note="Cada decisión conserva signal, economics y risk. HOLD sin gate de compra muestra NOT_EVALUATED para economics/risk. La verificación de BUY exige un recibo del gate; cada HOLD muestra su motivo publicado. El score no es una probabilidad.")
    if tab == "posiciones":
        return render_table(p, destination, tab, p.positions(), "Posiciones y seguridad de salida", POSITIONS, POSITION_DETAIL, filters=True,
                            note="La ganancia o pérdida PAPER no realizada usa el bid fresco, la entrada, la cantidad remanente, el multiplicador y el costo histórico. Un fill ejecutable requiere evidencia vigente y coherente. MFE y MAE requieren una trayectoria validada.")
    if tab == "cierres":
        stats = p.close_win_rate_today()
        detail = (f"{stats['wins']}/{stats['sample']} cierres con PnL neto positivo · empates en la muestra"
                  if stats["state"] == "AVAILABLE" else
                  "Sin cierres medidos en el alcance y filtros actuales" if stats["state"] == "EMPTY" else
                  "Revisá el PnL neto o la lectura del ledger para medir los resultados.")
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
        'Hasta 10 identidades de los últimos 500 registros locales, con prioridad para posiciones abiertas. El diagnóstico compara los relojes de libro, negocio y recepción con una vigencia de 120 s. '+cohort.reason)
    for row in cohort.rows:
        identity = " / ".join(str(row[k]) if row.get(k) and row[k] != "NO_VERIFICADO" else label
                              for k, label in (("symbol", "Sin ticker"), ("family", "Sin familia"),
                                               ("market", "Sin mercado"), ("currency", "Sin moneda"),
                                               ("settlement", "Sin plazo de liquidación")))
        panel+="<details><summary>"+e(identity)+"</summary>"+definition_list(row,fields('book_freshness|Book|status;trade_freshness|Trade|status;receipt_freshness|Recepción|status;book_at|Book|time;trade_at|Trade|time;received_at|Recepción|time;source|Fuente'))+"</details>"
    panel+="</section>"
    return panel+render_table(p, destination, tab, p.sources(), "Fuentes relevantes a la rueda", SOURCES, SOURCE_DETAIL,
                        note=("IOL REST es la fuente operativa. Se conserva la procedencia histórica de PPI. "
                              if getattr(p, "market_provider", "PPI") == "IOL_REST" else "PPI es autoridad primaria. IOL complementa y BYMA público observa. ") +
                            "PAPER_ECONOMIC_GATE_SHADOW muestra la salud del chequeo. La entrada PAPER exige su propio recibo de admisión.",
                        identity=False)
