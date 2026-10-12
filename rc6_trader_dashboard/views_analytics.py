"""Economics, samples, factual exits and explicitly limited experiments."""
from .components import fields, funnel, metric, notice, e
from .datasets import shadow_rows, artifact
from .view_common import render_table, PERFORMANCE, PERFORMANCE_DETAIL, CLOSES, CLOSE_DETAIL, committed_funnel


def render(p, destination, tab):
    if tab == "performance":
        return render_table(p, destination, tab, p.performance(), "Performance por moneda / estrategia / familia", PERFORMANCE, PERFORMANCE_DETAIL,
                            note="Win rate se acompaña de muestra, costos, neto, expectancy y profit factor cuando está definido. Edge y calibración OOS permanecen NO_VERIFICADO sin evidencia.")
    if tab == "senales":
        return render_table(p, destination, tab, shadow_rows(p, "signals"), "Señales & cohorts · score_is_probability=false",
                            fields("family|Familia;strategy|Estrategia;state|OOS|status;sample_size|Muestra|number;score|Score · NO PROBABILIDAD|score"),
                            fields("variant|Evaluador;horizon_seconds|Horizonte factual (s)|number;score_bucket|Bucket de score;hit_rate|Hit rate bruto|percent;forward_returns|Forward returns brutos;mfe|MFE continua validada;mae|MAE continua validada;mfe_observed|Excursiones favorables observadas;mae_observed|Excursiones adversas observadas;auc|AUC bruto del cohort;calibration|Calibración OOS;symbol|Ticker;hour|Hora ART;regime|Régimen;registry_version|Registry / versión;distinct_native_market_inputs|Inputs de mercado distintos|number;sample_is_independent|Muestra independiente;path_censored_observations|Observaciones censuradas|number"),
                            note="Score NO PROBABILIDAD. AUC/calibración requieren dataset correcto; no se promueven evaluadores automáticamente.")
    if tab == "salidas":
        from urllib.parse import urlencode
        selected_lab = p.filters.get("lab") == "shadow"
        base = {k: v for k, v in p.filters.items() if k not in {"offset", "lab"}}
        selector = ("<nav class='pager' aria-label='Evidencia de salidas'>"
                    f"<a class='button' href='?{e(urlencode(base))}'>Cierres factuales PAPER</a>"
                    f"<a class='button' href='?{e(urlencode({**base, 'lab': 'shadow'}))}'>Laboratorio de salidas SHADOW</a></nav>")
        if selected_lab:
            return selector + render_table(p, destination, tab, shadow_rows(p, "exits"), "Laboratorio económico de salidas · misma entrada",
                fields("symbol|Ticker;family|Familia;currency|Moneda;state|SHADOW / OOS|status;sample_size|Observaciones de trayectoria|number"),
                fields("entry|Entrada nativa;registered_at|Registro prospectivo|time;variants|Alternativas SHADOW;forward_label|Label prospectivo;native_clocks|Clocks nativos;entry_evidence_sha256|Evidencia de entrada;reason|Limitación"),
                note="Same-entry counterfactuals separados de cierres factuales. La muestra observada no certifica MFE/MAE continuo ni edge; entry_authority=false.")
        return selector + render_table(p, destination, tab, p.positions(closed=True), "Salidas factuales PAPER", CLOSES,
                            (*CLOSE_DETAIL, *fields("shadow_alternative|Alternativa SHADOW;counterfactual|Same-entry counterfactual;trailing|Experimento trailing;break_even|Experimento break-even")),
                            note="TP / SL / EOD / MaxHold factuales se conservan. Alternativas SHADOW no modifican la política de salida.")
    if tab == "historico":
        return render_table(p, destination, tab, p.history(), "Calidad & coverage histórica",
                            fields("symbol|Ticker;family|Familia;currency|Moneda;row_count|Barras históricas|number;version_known_at|Versión conocida|time;freshness|Frescura de versión histórica|status"),
                            fields("concept|Concepto;price_basis|Base de precio;adjustment_basis|Base del ajuste;date_from|Desde;date_to|Fecha de evento hasta;observed_at|Observación versión|time;last_checked_at|Último chequeo · no refresca precio|time;coverage|Coverage;gaps|Gaps;readiness_authority|Autoridad sobre readiness;decision_input_authority|Autoridad sobre decisión;source|Fuente"),
                            note="Historia, velas, market_snapshots, input de decisión y fill/lifecycle PAPER conservan fuentes y cortes propios. Freshness del panel histórico no prueba el input consumido por el motor; production_history registra descargas.")
    if tab == "experimentos":
        return render_table(p, destination, tab, shadow_rows(p, "experiments"), "Registro de experimentos SHADOW / validación",
                            fields("registry_version|Registry / versión;strategy|Evaluator;state|OOS|status;sample_size|Observaciones|number;entry_authority|Autoridad de entrada"),
                            fields("registered_at|Registro prospectivo|time;input|Inputs nativos congelados;variants|Evaluadores;labels|Labels prospectivos;source_payload_sha256|Digest del snapshot factual;observations|Observaciones;reason|Limitación;promotion|Promoción automática"),
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
