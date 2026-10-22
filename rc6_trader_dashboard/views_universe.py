"""Readiness, eligibility, tradeability and discovery remain separate."""
from .components import fields, metric, funnel
from .datasets import shadow_rows, family_summary
from .view_common import render_table, OPPORTUNITIES, OPPORTUNITY_DETAIL, CAPACITY, CAPACITY_DETAIL, committed_funnel
from .views_trading import STRATEGIES, STRATEGY_DETAIL


def render(p, destination, tab):
    if tab == "resumen":
        # Universe eligibility belongs to the UNIVERSE_SHADOW cohort; the
        # default NATIVE_FACTUAL group only measures signal evaluations.
        p.filters.setdefault("channel", "UNIVERSE_SHADOW")
        counts = p.counts()
        group = p.funnel_group
        scope = f"{group['currency']} / {group['channel']} · corte SHADOW" if group else "corte SHADOW sin cohorte"
        cards = "".join(metric(label, counts.get(key), source, kind="number") for key, label, source in (
            ("READY", "Catalog READY", "candidate_identity_v2"), ("ELIGIBLE", "Strategy eligible", scope),
            ("TRADEABLE", "Tradeable", scope + "; no señal direccional"), ("HOT", "HOT", scope + "; observación")))
        return "<div class='metric-grid'>" + cards + "</div>" + committed_funnel(p) + render_table(
            p, destination, tab, family_summary(p), "Distribución por familia", fields("family|Familia;candidates|Identidades|number;ready|RUNTIME_READY|number"),
            fields("as_of|Readiness as_of|time;scope|Granularidad;reason|Alcance"), aggregate=True)
    if tab == "familias":
        page = shadow_rows(p, "families")
        if not page.rows:
            page = family_summary(p)
        return render_table(p, destination, tab, page, "Routing & policy por familia", STRATEGIES,
                            (*STRATEGY_DETAIL, *fields("cadence_class|Cadence class;deep_analysis_eligibility|Deep-analysis eligibility;reason|Por qué OBSERVE_ONLY")))
    if tab == "coverage":
        return render_table(p, destination, tab, shadow_rows(p, "capacity"), "Coverage causal", CAPACITY, CAPACITY_DETAIL,
                            note="Untouched/late se informa sólo con evidencia causal. No se certifica detección completa ni capacidad por benchmark fuera de rueda.")
    kind = {"discovery": "discovery", "tradeability": "tradeability", "excluidos": "exclusions"}[tab]
    return render_table(p, destination, tab, shadow_rows(p, kind), dict(destination.tabs)[tab], OPPORTUNITIES, OPPORTUNITY_DETAIL,
                        filters=True, note="Tradeability NO es señal direccional ni probabilidad. HOT ≠ BUY; DISCOVERY/WARM no tienen entry_authority.")
