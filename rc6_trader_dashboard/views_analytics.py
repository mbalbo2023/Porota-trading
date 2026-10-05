"""Economics, samples, factual exits and explicitly limited experiments."""
from .components import fields, funnel, metric, notice
from .datasets import shadow_rows, artifact
from .view_common import render_table, PERFORMANCE, PERFORMANCE_DETAIL, CLOSES, CLOSE_DETAIL, committed_funnel


def render(p, destination, tab):
    if tab == "performance":
        return render_table(p, destination, tab, p.performance(), "Performance por moneda / estrategia / familia", PERFORMANCE, PERFORMANCE_DETAIL,
                            note="Win rate se acompaña de muestra, costos, neto, expectancy y profit factor cuando está definido. Edge y calibración OOS permanecen NO_VERIFICADO sin evidencia.")
    if tab == "senales":
        return render_table(p, destination, tab, shadow_rows(p, "signals"), "Señales & cohorts · score_is_probability=false",
                            fields("family|Familia;strategy|Estrategia;state|OOS|status;sample_size|Muestra|number;score|Score · NO PROBABILIDAD|score"),
                            fields("score_bucket|Bucket de score;hit_rate|Hit rate|percent;forward_returns|Forward returns;mfe|MFE validada;mae|MAE validada;auc|AUC dataset validado;calibration|Calibración OOS;symbol|Ticker;hour|Hora ART;regime|Régimen;registry_version|Registry / versión"),
                            note="Score NO PROBABILIDAD. AUC/calibración requieren dataset correcto; no se promueven evaluadores automáticamente.")
    if tab == "salidas":
        return render_table(p, destination, tab, p.positions(closed=True), "Salidas factuales PAPER", CLOSES,
                            (*CLOSE_DETAIL, *fields("shadow_alternative|Alternativa SHADOW;counterfactual|Same-entry counterfactual;trailing|Experimento trailing;break_even|Experimento break-even")),
                            note="TP / SL / EOD / MaxHold factuales se conservan. Alternativas SHADOW no modifican la política de salida.")
    if tab == "historico":
        return render_table(p, destination, tab, p.history(), "Calidad & coverage histórica",
                            fields("symbol|Ticker;family|Familia;row_count|Filas históricas|number;downloaded_at|Captura|time;freshness|Freshness histórica|status"),
                            fields("date_from|Desde;date_to|Hasta;coverage|Coverage;gaps|Gaps;depth|Profundidad;candles|Velas;readiness_authority|Autoridad sobre readiness;source|Fuente"),
                            note="Histórico es calidad de datos; nunca gobierna RUNTIME_READY ni autoriza estrategias.")
    if tab == "experimentos":
        return render_table(p, destination, tab, shadow_rows(p, "experiments"), "Registro de experimentos SHADOW / validación",
                            fields("registry_version|Registry / versión;strategy|Evaluator;state|OOS|status;sample_size|Observaciones|number;entry_authority|Autoridad de entrada"),
                            fields("preregistered|Preregistro;economic_exit_lab|Economic/exit lab;observations|Observaciones;reason|Limitación;promotion|Promoción automática"),
                            note="Experimentos preregistrados. NO automatic promotion. OBSERVE_ONLY/SHADOW conservan entry_authority=false.")
    if tab == "cierre-diario":
        counts = p.counts()
        return committed_funnel(p) + render_table(p, destination, tab, p.performance(today=True), "Journal de la rueda · ART", PERFORMANCE,
                                           (*PERFORMANCE_DETAIL, *fields("exit_reasons|Motivos de salida;why_no_trade|Why no trade;late_missed|Late/missed discovery causal;no_trade_baseline|No-trade baseline;cash_opportunity_cost|Costo de oportunidad caja|money;caucion_opportunity_cost|Costo de oportunidad caución|money;alerts|Alertas / incidentes")),
                                           note="No-trade baseline y late/missed requieren evidencia causal. Se conservan moneda y atribución; no se reconstruyen oportunidades perdidas por intuición.")
    page = p.store.page("report_registry", order="id DESC" if "id" in p.store.columns("report_registry") else "", offset=p.offset)
    for row in page.rows:
        row["download"] = f"/api/reports/{row['id']}/pdf" if row.get("pdf_path") and row.get("id") else None
        row["report_type"] = row.get("report_type") or row.get("period_type")
    result = render_table(p, destination, tab, page, "Reportes versionados",
                          fields("id|Reporte;report_type|Tipo;created_at|Publicado|time;state|Estado|status"),
                          fields("period_start|Desde;period_end|Hasta;download|Descarga autenticada"))
    return result + "<a class='text-link' href='/api/reports/weekly/download'>Descargar reporte semanal disponible</a>"
