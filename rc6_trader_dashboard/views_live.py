"""The live wheel: observation, factual decisions, exits and coverage."""
from .components import metric, funnel, timeline, notice
from .datasets import shadow_rows
from .views_home import preview
from .view_common import (render_table, OPPORTUNITIES, OPPORTUNITY_DETAIL, POSITIONS, POSITION_DETAIL,
                          CLOSES, CLOSE_DETAIL, DECISIONS, DECISION_DETAIL, CAPACITY, CAPACITY_DETAIL,
                          WORKERS, WORKER_DETAIL, SOURCES, SOURCE_DETAIL, committed_funnel)


def render(p, destination, tab):
    if tab == "resumen":
        counts = p.counts()
        cards = "".join(metric(label, counts.get(key), source, kind="number") for key, label, source in (
            ("open_positions", "Operaciones abiertas", "Spot OPEN + FUTUROS ACTIVE · corte exacto"),
            ("closed_today", "Cerradas hoy", "Cierres factuales en ART"), ("HOT", "HOT alcanzados en el scope", str(counts.get("funnel_scope")) + " · HOT ≠ BUY"),
            ("SIGNAL", "Señales candidatas", str(counts.get("funnel_scope")))))
        summary = p.performance(today=True)
        cards += "".join(metric("PnL neto hoy · " + r["currency"], r.get("net_pnl"), r["strategy"], kind="money", currency=r["currency"]) for r in summary.rows)
        workers = p.workers()
        verified = sum(r.get("freshness") == "FRESH" and r.get("state") in {"RUNNING", "OK"} for r in workers.rows)
        cards += metric("Workers críticos frescos", verified, f"De {len(workers.rows)} · revisar cobertura", kind="number")
        content = "<div class='metric-grid'>" + cards + "</div>" + committed_funnel(p)
        content += "<div class='split-panels'>" + preview(shadow_rows(p, "opportunities"), "Oportunidades", "/en-vivo/oportunidades") + timeline(shadow_rows(p, "events").rows) + "</div>"
        content += "<div class='split-panels'>" + preview(p.positions(limit=5), "1. Operaciones abiertas ahora", "/en-vivo/posiciones") + preview(p.positions(closed=True, today=True, limit=5), "2. Operaciones cerradas hoy", "/en-vivo/cierres") + "</div>"
        content += preview(p.decisions(), "3. Decisiones en vivo — BUY / HOLD / abstenciones", "/en-vivo/decisiones")
        content += preview(shadow_rows(p, "capacity"), "Capacidad & Coverage", "/en-vivo/capacidad", key="engine")
        content += "<a class='text-link' href='/scalping'>Abrir Scalping en Trading</a>"
        return content
    if tab == "oportunidades":
        return (render_table(p, destination, tab, shadow_rows(p, "opportunities"), "Mesa de oportunidades", OPPORTUNITIES, OPPORTUNITY_DETAIL,
                             filters=True, note="HOT ≠ BUY. DISCOVERY/WARM son observación. Tradeability no es señal direccional ni probabilidad.") + timeline(shadow_rows(p, "events").rows))
    if tab == "decisiones":
        return render_table(p, destination, tab, p.decisions(), "Decisiones factuales PAPER", DECISIONS, DECISION_DETAIL,
                            note="Cada decisión conserva signal, economics y risk. Un HOLD requiere su motivo; score_is_probability=false.")
    if tab == "posiciones":
        return render_table(p, destination, tab, p.positions(), "Posiciones y seguridad de salida", POSITIONS, POSITION_DETAIL, filters=True,
                            note="Un mark contable no acredita un fill ejecutable. MFE/MAE sin trayectoria validada = NO_MEDIDO.")
    if tab == "cierres":
        return render_table(p, destination, tab, p.positions(closed=True, today=True), "Cierres de la rueda · ART", CLOSES, CLOSE_DETAIL, filters=True)
    if tab == "capacidad":
        return render_table(p, destination, tab, shadow_rows(p, "capacity"), "Capacidad & Coverage", CAPACITY, CAPACITY_DETAIL,
                            note="La capacidad real exige evidencia OPEN. Un benchmark fuera de rueda no certifica capacidad operativa.")
    if tab == "workers":
        return render_table(p, destination, tab, p.workers(), "Workers críticos de la rueda", WORKERS, WORKER_DETAIL)
    return render_table(p, destination, tab, p.sources(), "Fuentes relevantes a la rueda", SOURCES, SOURCE_DETAIL,
                        note="PPI es autoridad primaria. IOL complementa; BYMA público observa. SOURCE_UNAVAILABLE siempre se limita a su scope.")
